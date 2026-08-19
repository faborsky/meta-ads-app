"""API engine: auth, rate-limit guard, request runner, mutations with dry-run.

All mutable module state (rate-limit usage cache) lives here — access it via
module attribute (``api._rate_limit_usage``), never via ``from`` import.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
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
GRAPH_HOST = "https://graph.facebook.com"
API_BASE = f"{GRAPH_HOST}/{API_VERSION}"

USAGE_DIR = os.path.join(BASE_DIR, ".usage")

# Currencies Meta bills without a cents offset (offset 1, not 100) — the CLI's
# ×100 budget conversion would set 100× the intended budget on these accounts.
ZERO_DECIMAL_CURRENCIES = {
    "CLP", "COP", "CRC", "HUF", "IDR", "ISK", "JPY", "KRW", "PYG", "TWD", "VND",
}

# Secrets never belong in error output — requests exceptions embed the full
# URL, and paginated `next` URLs may carry a token in the query string.
_SECRET_PARAM_RE = re.compile(
    r"(access_token|client_secret|fb_exchange_token|input_token)=[^&\s'\"]+"
)


def _redact(text: str) -> str:
    """Replace secret query-param values in arbitrary text with REDACTED."""
    return _SECRET_PARAM_RE.sub(r"\1=REDACTED", text)


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {META_ACCESS_TOKEN}"}

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


def _write_json_atomic(path: str, data: dict) -> None:
    """Write JSON via temp file + os.replace so readers never see a torn file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _save_usage(data: dict) -> None:
    try:
        _write_json_atomic(_usage_file(), data)
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

    Only recent readings count (Meta windows are hourly and roll off). When the
    endpoint names an ad account (act_…), only that account's usage blocks —
    a hot account A must not block calls on account B. Endpoints without an
    account (object IDs, search) are guarded against any hot account
    (conservative). Override with METAADS_IGNORE_USAGE_GUARD=1 when you know
    the window has reset.
    """
    if os.getenv("METAADS_IGNORE_USAGE_GUARD"):
        return
    m = re.search(r"act_(\d+)", endpoint)
    target_account = m.group(1) if m else None
    data = _load_usage()
    for account_id, entry in data.items():
        acc = str(account_id)
        acc = acc[4:] if acc.startswith("act_") else acc
        if target_account and acc != target_account:
            continue
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
                # One header can carry several ads_* use cases per account
                # (ads_management + ads_insights). Record the MAX across all of
                # them, once — recording per entry would let a low entry
                # overwrite a hot one and silently disarm the hard stop.
                max_usage = None
                tier = None
                for entry in entries:
                    # BUC headers also carry page/messaging use cases — only
                    # ads-related usage matters for this CLI's guard.
                    if not str(entry.get("type", "ads_management")).startswith("ads"):
                        continue
                    call_count = entry.get("call_count", 0)
                    total_cputime = entry.get("total_cputime", 0)
                    total_time = entry.get("total_time", 0)
                    max_usage = max(call_count, total_cputime, total_time,
                                    max_usage if max_usage is not None else 0)
                    tier = entry.get("ads_api_access_tier") or tier
                if max_usage is None:
                    continue
                _record_usage(account_id, max_usage, tier)

                if max_usage > 90:
                    _err(f"⚠ Rate limit critical ({max_usage}%) for {account_id}. Throttling...")
                    time.sleep(2)
                elif max_usage > 75:
                    _err(f"⚠ Rate limit warning ({max_usage}%) for {account_id}.")
        except (json.JSONDecodeError, AttributeError, TypeError):
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


def _token_fingerprint() -> str:
    """Cache-invalidation key for the current token — a hash, never the token."""
    return hashlib.sha256(META_ACCESS_TOKEN.encode()).hexdigest()[:12]


def maybe_warn_token_expiry() -> None:
    """Warn on stderr when the token expires in < TOKEN_WARN_DAYS.

    Expiry is cached in .usage/token.json; the debug_token call runs at most
    once per 24 h so the warning costs nothing on normal usage. The probe is
    strictly best-effort: it bypasses _api_call so no failure mode (offline,
    captive portal, debug_token permission error) can kill the actual command.
    A failed probe is cached too (1 h back-off) so it can't re-fire every run.
    """
    cache: dict = {}
    try:
        with open(_token_cache_file()) as f:
            cache = json.load(f)
    except (OSError, json.JSONDecodeError):
        pass

    now = time.time()
    # Re-check when cache is stale or belongs to a different token.
    fingerprint = _token_fingerprint()
    max_age = 3600 if cache.get("check_failed") else 86400
    if cache.get("token_hash") != fingerprint or now - cache.get("checked_at", 0) > max_age:
        cache = {"token_hash": fingerprint, "checked_at": now}
        try:
            resp = requests.get(
                f"{API_BASE}/debug_token",
                params={"input_token": META_ACCESS_TOKEN},
                headers=_auth_headers(),
                timeout=15,
            )
            payload = resp.json()
            if "error" in payload:
                cache["check_failed"] = True
            else:
                cache["expires_at"] = payload.get("data", {}).get("expires_at", 0)
        except Exception:
            cache["check_failed"] = True
        try:
            _write_json_atomic(_token_cache_file(), cache)
        except OSError:
            pass

    expires_at = cache.get("expires_at", 0)
    if expires_at:
        days_left = (datetime.fromtimestamp(expires_at) - datetime.now()).days
        if days_left < 0:
            _err(
                f"⚠ META_ACCESS_TOKEN expired {-days_left} day(s) ago "
                f"({datetime.fromtimestamp(expires_at):%Y-%m-%d}). "
                "Generate a new token (Graph API Explorer) — expired tokens can't be extended."
            )
        elif days_left < TOKEN_WARN_DAYS:
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
    _skip_guard: bool = False,
    retry_transient_writes: bool = False,
) -> dict:
    """Make a Meta Marketing API call.

    method: HTTP method (GET, POST, DELETE)
    endpoint: API path (e.g., 'act_123/campaigns' or '12345') or a full URL
    params: query params for GET, form data for POST
    files: multipart files for upload
    retry_transient_writes: allow transient-error retries on POST — ONLY for
        idempotent writes (chunked upload transfer: the server tracks offsets,
        so re-sending a chunk cannot duplicate anything)

    The token travels in the Authorization header, never in the URL — so it
    can't leak through exception text, proxy logs or pagination links.
    """
    if endpoint.startswith("http"):
        # Only Meta's own pagination links ever arrive as full URLs.
        if not endpoint.startswith(GRAPH_HOST):
            _die(f"ERROR: refusing to call a non-Graph URL: {_redact(endpoint)}")
        url = endpoint
    else:
        url = f"{API_BASE}/{endpoint}"

    params = dict(params) if params else {}
    headers = _auth_headers()
    if "access_token=" in url:
        headers = {}  # token already in the URL (legacy paging link) — don't send two

    if not _skip_guard:
        _usage_guard(endpoint)

    try:
        if method == "GET":
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        elif method == "POST":
            if files:
                resp = requests.post(url, data=params, files=files, headers=headers, timeout=timeout)
            else:
                resp = requests.post(url, data=params, headers=headers, timeout=timeout)
        elif method == "DELETE":
            resp = requests.delete(url, params=params, headers=headers, timeout=timeout)
        else:
            _die(f"ERROR: Unknown HTTP method: {method}")
    except requests.exceptions.Timeout:
        _die(f"ERROR: Request timed out ({timeout}s) for {method} {_redact(endpoint)}")
    except requests.exceptions.ConnectionError as e:
        _die(f"ERROR: Connection failed for {method} {_redact(endpoint)}: {_redact(str(e))}")

    _parse_rate_limit_headers(resp.headers)

    try:
        data = resp.json()
    except json.JSONDecodeError:
        _err(f"ERROR: Non-JSON response from {method} {endpoint} (HTTP {resp.status_code})")
        if resp.status_code == 413:
            _err("  Payload too large — the file exceeds the single-request upload limit.")
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

        # Rate limiting — recoverable with backoff. These are rejections
        # (nothing was written), so retrying POSTs here is safe.
        if code in (4, 17, 32) or code in (80000, 80001, 80002, 80003, 80004, 80008, 80014):
            if _retry < len(_RETRY_DELAYS):
                delay = _RETRY_DELAYS[_retry]
                buc = resp.headers.get("X-Business-Use-Case-Usage") or resp.headers.get("x-business-use-case-usage")
                if buc:
                    try:
                        for entries in json.loads(buc).values():
                            for entry in entries:
                                if not str(entry.get("type", "ads_management")).startswith("ads"):
                                    continue
                                est = entry.get("estimated_time_to_regain_access", 0)
                                if est > 0:
                                    delay = max(delay, est * 60)  # minutes → seconds
                    except (json.JSONDecodeError, AttributeError, TypeError):
                        pass
                if delay > 300:
                    _die(
                        f"ERROR: Rate limited (code {code}) — Meta reports ~{delay // 60} min "
                        "until access returns. Not waiting that long; try again later "
                        "(check with: api-limits)."
                    )
                _err(f"Rate limited (code {code}), waiting {delay}s (retry {_retry + 1}/{len(_RETRY_DELAYS)})...")
                time.sleep(delay)
                return _api_call(method, endpoint, params, files, timeout, _retry + 1,
                                 retry_transient_writes=retry_transient_writes)
            _die(f"ERROR: Rate limit exceeded after {len(_RETRY_DELAYS)} retries: {message}")

        # QPS limit (613 / subcode 5044001)
        if code == 613 and subcode == 5044001:
            if _retry < len(_RETRY_DELAYS):
                delay = _RETRY_DELAYS[_retry]
                _err(f"QPS limit hit, waiting {delay}s (retry {_retry + 1})...")
                time.sleep(delay)
                return _api_call(method, endpoint, params, files, timeout, _retry + 1,
                                 retry_transient_writes=retry_transient_writes)

        # Budget change limits
        if code == 613 and subcode == 1487632:
            _die("ERROR: Ad set budget can only change 4 times per hour. Wait and retry.")

        # Transient errors — auto-retry READS only. Meta occasionally reports
        # is_transient AFTER a write actually landed; blindly re-sending a
        # POST can create the campaign/ad/copy twice. Idempotent writes
        # (chunked upload transfer) opt in via retry_transient_writes.
        if is_transient:
            if (method == "GET" or retry_transient_writes) and _retry < 3:
                delay = [2, 5, 15][_retry]
                _err(f"Transient error, retrying in {delay}s...")
                time.sleep(delay)
                return _api_call(method, endpoint, params, files, timeout, _retry + 1,
                                 retry_transient_writes=retry_transient_writes)
            if method != "GET":
                _err(f"ERROR: Transient error from Meta on {method} {_redact(endpoint)}: {message}")
                _die(
                    "  Not retrying automatically — the write MAY have landed despite the error.\n"
                    "  Check with the matching list command (campaigns/adsets/ads/creatives) before retrying."
                )

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
# Budget currency guard
# ---------------------------------------------------------------------------

def _accounts_cache_file() -> str:
    return os.path.join(USAGE_DIR, "accounts.json")


def get_account_currency(account_id: str) -> str | None:
    """Account currency, cached in .usage/accounts.json (currency ~never changes)."""
    cache: dict = {}
    try:
        with open(_accounts_cache_file()) as f:
            cache = json.load(f)
    except (OSError, json.JSONDecodeError):
        pass
    entry = cache.get(account_id) or {}
    if entry.get("currency"):
        return entry["currency"]
    try:
        data = _api_call("GET", account_id, {"fields": "currency"})
    except SystemExit:
        return None  # guard is best-effort; the budget call itself will fail loudly
    currency = data.get("currency")
    if currency:
        cache[account_id] = {"currency": currency, "ts": time.time()}
        try:
            _write_json_atomic(_accounts_cache_file(), cache)
        except OSError:
            pass
    return currency


def budget_currency_guard(account_id: str) -> None:
    """Refuse budget math on zero-decimal currencies (JPY, HUF, …).

    The CLI converts budgets with a ×100 offset. Meta bills these currencies
    with offset 1 — the conversion would set 100× the intended budget.
    """
    currency = get_account_currency(account_id)
    if currency in ZERO_DECIMAL_CURRENCIES:
        _die(
            f"ERROR: account {account_id} is billed in {currency}, which has no "
            "cents (offset 1). This CLI's budget conversion assumes offset 100 and "
            "would set 100× the intended amount.\n"
            "  Set budgets for this account in Ads Manager, or open a GitHub issue."
        )


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
