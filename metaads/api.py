"""API engine: auth, rate-limit guard, request runner, mutations with dry-run.

All mutable module state (rate-limit usage cache) lives here — access it via
module attribute (``api._rate_limit_usage``), never via ``from`` import.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

from metaads.formatting import _die, _err

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_AD_ACCOUNT_ID = os.getenv("META_AD_ACCOUNT_ID", "")
META_PAGE_ID = os.getenv("META_PAGE_ID", "")
META_APP_ID = os.getenv("META_APP_ID", "")
META_APP_SECRET = os.getenv("META_APP_SECRET", "")

API_VERSION = "v25.0"
API_BASE = f"https://graph.facebook.com/{API_VERSION}"

USAGE_DIR = os.path.join(BASE_DIR, ".usage")

# Hard-stop threshold (%) for the persistent usage guard. Meta limits are
# dynamic (hourly windows), so we guard on the last-seen usage percentage.
USAGE_HARD_STOP = 95
USAGE_GUARD_FRESH_SECS = 600  # only trust persisted usage newer than this

# Last-seen rate-limit usage per account (percentage), updated from headers.
_rate_limit_usage: dict[str, float] = {}
_access_tier: str | None = None

_RETRY_DELAYS = [5, 15, 60]  # seconds


def check_config() -> None:
    """Validate required environment variables."""
    if not META_ACCESS_TOKEN or META_ACCESS_TOKEN == "your-long-lived-token-here":
        _die("ERROR: META_ACCESS_TOKEN not set. Copy .env.example to .env and add your token.")
    if not META_AD_ACCOUNT_ID or not META_AD_ACCOUNT_ID.startswith("act_"):
        _die("ERROR: META_AD_ACCOUNT_ID not set or invalid (must start with 'act_').")


# ---------------------------------------------------------------------------
# Persistent usage guard (.usage/)
# ---------------------------------------------------------------------------

def _usage_file() -> str:
    return os.path.join(USAGE_DIR, "usage.json")


def _load_usage() -> dict:
    try:
        with open(_usage_file()) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_usage(data: dict) -> None:
    try:
        os.makedirs(USAGE_DIR, exist_ok=True)
        with open(_usage_file(), "w") as f:
            json.dump(data, f)
    except OSError:
        pass  # guard is best-effort; never break the actual call


def _record_usage(account_id: str, pct: float, tier: str | None = None) -> None:
    global _access_tier
    _rate_limit_usage[account_id] = pct
    if tier:
        _access_tier = tier
    data = _load_usage()
    data[account_id] = {"pct": pct, "ts": time.time(), "tier": tier or _access_tier}
    _save_usage(data)


def _usage_guard(endpoint: str) -> None:
    """Hard-stop before a call when last-seen usage crossed the threshold.

    Only recent readings count (Meta windows are hourly and roll off). Override
    with METAADS_IGNORE_USAGE_GUARD=1 when you know the window has reset.
    """
    if os.getenv("METAADS_IGNORE_USAGE_GUARD"):
        return
    data = _load_usage()
    for account_id, entry in data.items():
        pct = entry.get("pct", 0)
        age = time.time() - entry.get("ts", 0)
        if pct >= USAGE_HARD_STOP and age < USAGE_GUARD_FRESH_SECS:
            _die(
                f"ERROR: API usage for {account_id} was {pct:.0f}% "
                f"{int(age)}s ago — hard stop to avoid a lockout.\n"
                f"  Wait a few minutes (hourly window), or set "
                f"METAADS_IGNORE_USAGE_GUARD=1 to override."
            )


def _parse_rate_limit_headers(headers) -> None:
    """Parse Meta rate limit headers, persist usage, warn near limits."""
    buc = headers.get("X-Business-Use-Case-Usage") or headers.get("x-business-use-case-usage")
    if buc:
        try:
            usage_data = json.loads(buc)
            for account_id, entries in usage_data.items():
                for entry in entries:
                    # BUC headers also carry page/messaging use cases — only
                    # ads-related usage matters for this CLI's guard.
                    if not str(entry.get("type", "ads_management")).startswith("ads"):
                        continue
                    call_count = entry.get("call_count", 0)
                    total_cputime = entry.get("total_cputime", 0)
                    total_time = entry.get("total_time", 0)
                    max_usage = max(call_count, total_cputime, total_time)
                    _record_usage(account_id, max_usage, entry.get("ads_api_access_tier"))

                    if max_usage > 90:
                        _err(f"⚠ Rate limit critical ({max_usage}%) for {account_id}. Throttling...")
                        time.sleep(2)
                    elif max_usage > 75:
                        _err(f"⚠ Rate limit warning ({max_usage}%) for {account_id}.")
        except (json.JSONDecodeError, AttributeError):
            pass

    aau = headers.get("X-Ad-Account-Usage") or headers.get("x-ad-account-usage")
    if aau:
        try:
            usage = json.loads(aau)
            pct = usage.get("acc_id_util_pct", 0)
            if pct > 90:
                _err(f"⚠ Ad account API usage critical ({pct}%). Throttling...")
                time.sleep(2)
            elif pct > 75:
                _err(f"⚠ Ad account API usage warning ({pct}%).")
        except (json.JSONDecodeError, AttributeError):
            pass

    insights = headers.get("X-FB-Ads-Insights-Throttle") or headers.get("x-fb-ads-insights-throttle")
    if insights:
        try:
            usage = json.loads(insights)
            app_pct = usage.get("app_id_util_pct", 0)
            acc_pct = usage.get("acc_id_util_pct", 0)
            max_pct = max(app_pct, acc_pct)
            if max_pct > 90:
                _err(f"⚠ Insights rate limit critical ({max_pct}%). Throttling...")
                time.sleep(2)
            elif max_pct > 75:
                _err(f"⚠ Insights rate limit warning ({max_pct}%).")
        except (json.JSONDecodeError, AttributeError):
            pass


# ---------------------------------------------------------------------------
# Token expiry warning (cached, checked at most once a day)
# ---------------------------------------------------------------------------

TOKEN_WARN_DAYS = 7


def _token_cache_file() -> str:
    return os.path.join(USAGE_DIR, "token.json")


def maybe_warn_token_expiry() -> None:
    """Warn on stderr when the token expires in < TOKEN_WARN_DAYS.

    Expiry is cached in .usage/token.json; the debug_token call runs at most
    once per 24 h so the warning costs nothing on normal usage.
    """
    cache: dict = {}
    try:
        with open(_token_cache_file()) as f:
            cache = json.load(f)
    except (OSError, json.JSONDecodeError):
        pass

    now = time.time()
    # Re-check when cache is stale or belongs to a different token.
    token_tail = META_ACCESS_TOKEN[-12:]
    if cache.get("token_tail") != token_tail or now - cache.get("checked_at", 0) > 86400:
        try:
            data = _api_call("GET", "debug_token", {"input_token": META_ACCESS_TOKEN})
            expires_at = data.get("data", {}).get("expires_at", 0)
        except SystemExit:
            raise
        except Exception:
            return
        cache = {"token_tail": token_tail, "checked_at": now, "expires_at": expires_at}
        try:
            os.makedirs(USAGE_DIR, exist_ok=True)
            with open(_token_cache_file(), "w") as f:
                json.dump(cache, f)
        except OSError:
            pass

    expires_at = cache.get("expires_at", 0)
    if expires_at:
        days_left = (datetime.fromtimestamp(expires_at) - datetime.now()).days
        if days_left < TOKEN_WARN_DAYS:
            _err(
                f"⚠ META_ACCESS_TOKEN expires in {days_left} day(s) "
                f"({datetime.fromtimestamp(expires_at):%Y-%m-%d}). Run: token-extend"
            )


def token_days_left() -> int | None:
    """Days until token expiry from the local cache (None = unknown/never)."""
    try:
        with open(_token_cache_file()) as f:
            cache = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    expires_at = cache.get("expires_at", 0)
    if not expires_at:
        return None
    return (datetime.fromtimestamp(expires_at) - datetime.now()).days


# ---------------------------------------------------------------------------
# API call wrapper
# ---------------------------------------------------------------------------

def _api_call(
    method: str,
    endpoint: str,
    params: dict | None = None,
    files: dict | None = None,
    timeout: int = 60,
    _retry: int = 0,
) -> dict:
    """Make a Meta Marketing API call.

    method: HTTP method (GET, POST, DELETE)
    endpoint: API path (e.g., 'act_123/campaigns' or '12345') or a full URL
    params: query params for GET, form data for POST
    files: multipart files for upload
    """
    url = f"{API_BASE}/{endpoint}" if not endpoint.startswith("http") else endpoint

    if params is None:
        params = {}
    params["access_token"] = META_ACCESS_TOKEN

    _usage_guard(endpoint)

    try:
        if method == "GET":
            resp = requests.get(url, params=params, timeout=timeout)
        elif method == "POST":
            if files:
                resp = requests.post(url, data=params, files=files, timeout=timeout)
            else:
                resp = requests.post(url, data=params, timeout=timeout)
        elif method == "DELETE":
            resp = requests.delete(url, params=params, timeout=timeout)
        else:
            _die(f"ERROR: Unknown HTTP method: {method}")
    except requests.exceptions.Timeout:
        _die(f"ERROR: Request timed out ({timeout}s) for {method} {endpoint}")
    except requests.exceptions.ConnectionError as e:
        _die(f"ERROR: Connection failed for {method} {endpoint}: {e}")

    _parse_rate_limit_headers(resp.headers)

    try:
        data = resp.json()
    except json.JSONDecodeError:
        _err(f"ERROR: Non-JSON response from {method} {endpoint} (HTTP {resp.status_code})")
        _die(resp.text[:500])

    error = data.get("error")
    if error:
        code = error.get("code", 0)
        subcode = error.get("error_subcode", 0)
        message = error.get("message", "Unknown error")
        user_msg = error.get("error_user_msg", "")
        is_transient = error.get("is_transient", False)

        # Token expired / invalid
        if code == 190:
            _err("ERROR: Access token expired or invalid.")
            _die("  Generate a new token in Graph API Explorer and update .env")

        # Permission errors
        if code in (10, 200, 294):
            _die(f"ERROR: Permission denied (code {code}): {message}")

        # Rate limiting — recoverable with backoff
        if code in (4, 17, 32) or code in (80000, 80001, 80002, 80003, 80004, 80008, 80014):
            if _retry < len(_RETRY_DELAYS):
                delay = _RETRY_DELAYS[_retry]
                buc = resp.headers.get("X-Business-Use-Case-Usage") or resp.headers.get("x-business-use-case-usage")
                if buc:
                    try:
                        for entries in json.loads(buc).values():
                            for entry in entries:
                                est = entry.get("estimated_time_to_regain_access", 0)
                                if est > 0:
                                    delay = max(delay, est * 60)  # minutes → seconds
                    except (json.JSONDecodeError, AttributeError):
                        pass
                _err(f"Rate limited (code {code}), waiting {delay}s (retry {_retry + 1}/{len(_RETRY_DELAYS)})...")
                time.sleep(delay)
                return _api_call(method, endpoint, params, files, timeout, _retry + 1)
            _die(f"ERROR: Rate limit exceeded after {len(_RETRY_DELAYS)} retries: {message}")

        # QPS limit (613 / subcode 5044001)
        if code == 613 and subcode == 5044001:
            if _retry < len(_RETRY_DELAYS):
                delay = _RETRY_DELAYS[_retry]
                _err(f"QPS limit hit, waiting {delay}s (retry {_retry + 1})...")
                time.sleep(delay)
                return _api_call(method, endpoint, params, files, timeout, _retry + 1)

        # Budget change limits
        if code == 613 and subcode == 1487632:
            _die("ERROR: Ad set budget can only change 4 times per hour. Wait and retry.")

        # Transient errors — auto-retry
        if is_transient and _retry < 3:
            delay = [2, 5, 15][_retry]
            _err(f"Transient error, retrying in {delay}s...")
            time.sleep(delay)
            return _api_call(method, endpoint, params, files, timeout, _retry + 1)

        # Validation errors — show blame fields + user message
        if code == 100:
            # error_data may arrive as a JSON string instead of an object
            err_data = error.get("error_data", {})
            if isinstance(err_data, str):
                try:
                    err_data = json.loads(err_data)
                except json.JSONDecodeError:
                    err_data = {}
            blame = err_data.get("blame_field_specs", []) if isinstance(err_data, dict) else []
            _err(f"ERROR: Invalid parameter: {message}")
            if user_msg:
                _err(f"  Detail: {error.get('error_user_title', '')}: {user_msg}")
            if blame:
                _err(f"  Blame fields: {blame}")
            sys.exit(1)

        _err(f"ERROR ({code}/{subcode}): {message}")
        if user_msg:
            _err(f"  Detail: {error.get('error_user_title', '')}: {user_msg}")
        fbtrace = error.get("fbtrace_id")
        if fbtrace:
            _err(f"  Trace ID: {fbtrace}")
        sys.exit(1)

    return data


# ---------------------------------------------------------------------------
# Pagination helper
# ---------------------------------------------------------------------------

def _paginate(endpoint: str, params: dict, max_items: int = 100) -> list:
    """Fetch all items from a paginated endpoint up to max_items."""
    items: list = []
    current_endpoint = endpoint
    current_params = dict(params)

    while current_endpoint and len(items) < max_items:
        data = _api_call("GET", current_endpoint, current_params)
        items.extend(data.get("data", []))

        paging = data.get("paging", {})
        next_url = paging.get("next")
        if next_url:
            current_endpoint = next_url
            current_params = {}  # next URL has params already
        else:
            break

    return items[:max_items]


# ---------------------------------------------------------------------------
# Mutations with validate_only dry-run
# ---------------------------------------------------------------------------

def mutate(
    endpoint: str,
    params: dict,
    confirm: bool,
    validate_supported: bool = True,
    method: str = "POST",
) -> tuple[dict | None, bool]:
    """Run a mutation; without confirm, dry-run via execution_options=validate_only.

    Returns (response_data, executed). For endpoints without validate_only
    support the dry-run makes no API call and returns (None, False) — the
    caller prints its own plan.
    """
    if confirm:
        return _api_call(method, endpoint, params), True
    if validate_supported:
        dry = dict(params)
        dry["execution_options"] = json.dumps(["validate_only"])
        data = _api_call(method, endpoint, dry)
        return data, False
    return None, False


def print_mutation_result(data: dict | None, executed: bool, done_msg: str, plan: dict | None = None) -> None:
    """Standard human output for mutate() results."""
    if executed:
        print(done_msg)
        return
    if data is not None:
        print("✅ VALIDACE OK (dry-run: nothing was written). Add --confirm to execute.")
    else:
        print("DRY-RUN (this endpoint has no validate_only — no API call made).")
        if plan:
            print("Plan: " + json.dumps(plan, ensure_ascii=False, default=str))
        print("Add --confirm to execute.")
