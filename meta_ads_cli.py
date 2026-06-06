#!/usr/bin/env python3
"""Meta Ads CLI — manage Facebook & Instagram ad campaigns via Marketing API.

SAFETY: Destructive operations require --confirm flag.
Budgets: CLI accepts currency amounts, internally converts to cents (×100).
"""

from __future__ import annotations

import argparse
import base64
import copy
import json
import os
import sys
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_AD_ACCOUNT_ID = os.getenv("META_AD_ACCOUNT_ID", "")
META_PAGE_ID = os.getenv("META_PAGE_ID", "")
META_APP_ID = os.getenv("META_APP_ID", "")
META_APP_SECRET = os.getenv("META_APP_SECRET", "")

API_BASE = "https://graph.facebook.com/v25.0"
API_VERSION = "v25.0"

# Rate limit tracking
_rate_limit_usage: dict[str, float] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_config() -> None:
    """Validate required environment variables."""
    if not META_ACCESS_TOKEN or META_ACCESS_TOKEN == "your-long-lived-token-here":
        print("ERROR: META_ACCESS_TOKEN not set. Copy .env.example to .env and add your token.", file=sys.stderr)
        sys.exit(1)
    if not META_AD_ACCOUNT_ID or not META_AD_ACCOUNT_ID.startswith("act_"):
        print("ERROR: META_AD_ACCOUNT_ID not set or invalid (must start with 'act_').", file=sys.stderr)
        sys.exit(1)


def _output_json(data: object) -> None:
    """Print JSON to stdout."""
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _budget_to_cents(value: float) -> int:
    """Convert currency amount to cents (API format)."""
    return int(round(value * 100))


def _cents_to_budget(cents: int | str | None) -> float | None:
    """Convert cents to currency amount (display format)."""
    if cents is None:
        return None
    return int(cents) / 100.0


def _format_budget(cents: int | str | None, currency: str = "") -> str:
    """Format cents as currency string."""
    if cents is None:
        return "---"
    val = int(cents) / 100.0
    return f"{val:,.2f} {currency}".strip()


def _truncate(text: str | None, max_len: int = 40) -> str:
    """Truncate text for table display."""
    if not text:
        return "---"
    if len(text) <= max_len:
        return text
    return text[:max_len - 1] + "…"


# ---------------------------------------------------------------------------
# API call wrapper
# ---------------------------------------------------------------------------

_RETRY_DELAYS = [5, 15, 60]  # seconds for retries


def _parse_rate_limit_headers(headers: dict) -> None:
    """Parse Meta rate limit headers and warn if approaching limits."""
    global _rate_limit_usage

    # X-Business-Use-Case-Usage: JSON with per-account usage
    buc = headers.get("X-Business-Use-Case-Usage") or headers.get("x-business-use-case-usage")
    if buc:
        try:
            usage_data = json.loads(buc)
            for account_id, entries in usage_data.items():
                for entry in entries:
                    call_count = entry.get("call_count", 0)
                    total_cputime = entry.get("total_cputime", 0)
                    total_time = entry.get("total_time", 0)
                    max_usage = max(call_count, total_cputime, total_time)
                    _rate_limit_usage[account_id] = max_usage

                    if max_usage > 90:
                        print(f"⚠ Rate limit critical ({max_usage}%) for {account_id}. Throttling...", file=sys.stderr)
                        time.sleep(2)
                    elif max_usage > 75:
                        print(f"⚠ Rate limit warning ({max_usage}%) for {account_id}.", file=sys.stderr)
        except (json.JSONDecodeError, AttributeError):
            pass

    # X-Ad-Account-Usage
    aau = headers.get("X-Ad-Account-Usage") or headers.get("x-ad-account-usage")
    if aau:
        try:
            usage = json.loads(aau)
            pct = usage.get("acc_id_util_pct", 0)
            if pct > 90:
                print(f"⚠ Ad account API usage critical ({pct}%). Throttling...", file=sys.stderr)
                time.sleep(2)
            elif pct > 75:
                print(f"⚠ Ad account API usage warning ({pct}%).", file=sys.stderr)
        except (json.JSONDecodeError, AttributeError):
            pass

    # X-FB-Ads-Insights-Throttle
    insights = headers.get("X-FB-Ads-Insights-Throttle") or headers.get("x-fb-ads-insights-throttle")
    if insights:
        try:
            usage = json.loads(insights)
            app_pct = usage.get("app_id_util_pct", 0)
            acc_pct = usage.get("acc_id_util_pct", 0)
            max_pct = max(app_pct, acc_pct)
            if max_pct > 90:
                print(f"⚠ Insights rate limit critical ({max_pct}%). Throttling...", file=sys.stderr)
                time.sleep(2)
            elif max_pct > 75:
                print(f"⚠ Insights rate limit warning ({max_pct}%).", file=sys.stderr)
        except (json.JSONDecodeError, AttributeError):
            pass


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
    endpoint: API path (e.g., 'act_123/campaigns' or '12345')
    params: query params for GET, form data for POST
    files: multipart files for upload
    """
    url = f"{API_BASE}/{endpoint}" if not endpoint.startswith("http") else endpoint

    # Inject access token
    if params is None:
        params = {}
    params["access_token"] = META_ACCESS_TOKEN

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
            print(f"ERROR: Unknown HTTP method: {method}", file=sys.stderr)
            sys.exit(1)
    except requests.exceptions.Timeout:
        print(f"ERROR: Request timed out ({timeout}s) for {method} {endpoint}", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.ConnectionError as e:
        print(f"ERROR: Connection failed for {method} {endpoint}: {e}", file=sys.stderr)
        sys.exit(1)

    # Parse rate limit headers
    _parse_rate_limit_headers(resp.headers)

    # Parse response
    try:
        data = resp.json()
    except json.JSONDecodeError:
        print(f"ERROR: Non-JSON response from {method} {endpoint} (HTTP {resp.status_code})", file=sys.stderr)
        print(resp.text[:500], file=sys.stderr)
        sys.exit(1)

    # Check for errors
    error = data.get("error")
    if error:
        code = error.get("code", 0)
        subcode = error.get("error_subcode", 0)
        message = error.get("message", "Unknown error")
        is_transient = error.get("is_transient", False)

        # Token expired / invalid
        if code == 190:
            print(f"ERROR: Access token expired or invalid.", file=sys.stderr)
            print("  Generate a new token in Graph API Explorer and update .env", file=sys.stderr)
            sys.exit(1)

        # Permission errors
        if code in (10, 200, 294):
            print(f"ERROR: Permission denied (code {code}): {message}", file=sys.stderr)
            sys.exit(1)

        # Rate limiting — recoverable with backoff
        if code in (4, 17, 32) or code in (80000, 80001, 80002, 80003, 80004, 80008, 80014):
            if _retry < len(_RETRY_DELAYS):
                # Check for estimated_time_to_regain_access in BUC header
                delay = _RETRY_DELAYS[_retry]
                buc = resp.headers.get("X-Business-Use-Case-Usage") or resp.headers.get("x-business-use-case-usage")
                if buc:
                    try:
                        for entries in json.loads(buc).values():
                            for entry in entries:
                                est = entry.get("estimated_time_to_regain_access", 0)
                                if est > 0:
                                    delay = max(delay, est * 60)
                    except (json.JSONDecodeError, AttributeError):
                        pass
                print(f"Rate limited (code {code}), waiting {delay}s (retry {_retry + 1}/{len(_RETRY_DELAYS)})...", file=sys.stderr)
                time.sleep(delay)
                return _api_call(method, endpoint, params, files, timeout, _retry + 1)
            else:
                print(f"ERROR: Rate limit exceeded after {len(_RETRY_DELAYS)} retries: {message}", file=sys.stderr)
                sys.exit(1)

        # QPS limit (613 / subcode 5044001)
        if code == 613 and subcode == 5044001:
            if _retry < len(_RETRY_DELAYS):
                delay = _RETRY_DELAYS[_retry]
                print(f"QPS limit hit, waiting {delay}s (retry {_retry + 1})...", file=sys.stderr)
                time.sleep(delay)
                return _api_call(method, endpoint, params, files, timeout, _retry + 1)

        # Budget change limits
        if code == 613 and subcode == 1487632:
            print(f"ERROR: Ad set budget can only change 4 times per hour. Wait and retry.", file=sys.stderr)
            sys.exit(1)

        # Transient errors — auto-retry
        if is_transient and _retry < 3:
            delay = [2, 5, 15][_retry]
            print(f"Transient error, retrying in {delay}s...", file=sys.stderr)
            time.sleep(delay)
            return _api_call(method, endpoint, params, files, timeout, _retry + 1)

        # Validation errors — show blame fields
        if code == 100:
            blame = error.get("error_data", {}).get("blame_field_specs", [])
            print(f"ERROR: Invalid parameter: {message}", file=sys.stderr)
            if blame:
                print(f"  Blame fields: {blame}", file=sys.stderr)
            sys.exit(1)

        # All other errors
        print(f"ERROR ({code}/{subcode}): {message}", file=sys.stderr)
        fbtrace = error.get("fbtrace_id")
        if fbtrace:
            print(f"  Trace ID: {fbtrace}", file=sys.stderr)
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

        # Next page URL includes all params
        paging = data.get("paging", {})
        next_url = paging.get("next")
        if next_url:
            current_endpoint = next_url
            current_params = {}  # next URL has params already
        else:
            break

    return items[:max_items]


# ---------------------------------------------------------------------------
# Account status mapping
# ---------------------------------------------------------------------------

ACCOUNT_STATUS = {
    1: "ACTIVE",
    2: "DISABLED",
    3: "UNSETTLED",
    7: "PENDING_RISK_REVIEW",
    8: "PENDING_SETTLEMENT",
    9: "IN_GRACE_PERIOD",
    100: "PENDING_CLOSURE",
    101: "CLOSED",
    201: "ANY_ACTIVE",
    202: "ANY_CLOSED",
}

EFFECTIVE_STATUS = {
    "ACTIVE": "ACTIVE",
    "PAUSED": "PAUSED",
    "DELETED": "DELETED",
    "ARCHIVED": "ARCHIVED",
    "PENDING_REVIEW": "PENDING_REVIEW",
    "DISAPPROVED": "DISAPPROVED",
    "PREAPPROVED": "PREAPPROVED",
    "PENDING_BILLING_INFO": "PENDING_BILLING_INFO",
    "CAMPAIGN_PAUSED": "CAMPAIGN_PAUSED",
    "ADSET_PAUSED": "ADSET_PAUSED",
    "IN_PROCESS": "IN_PROCESS",
    "WITH_ISSUES": "WITH_ISSUES",
}


# ---------------------------------------------------------------------------
# Command: account
# ---------------------------------------------------------------------------

def cmd_account(args: argparse.Namespace) -> None:
    """Show ad account info."""
    account_id = args.account_id or META_AD_ACCOUNT_ID
    fields = "name,account_id,account_status,currency,timezone_name,balance,amount_spent,spend_cap,min_daily_budget"
    data = _api_call("GET", account_id, {"fields": fields})

    if args.json:
        _output_json(data)
    else:
        status_code = data.get("account_status", 0)
        status = ACCOUNT_STATUS.get(status_code, f"UNKNOWN ({status_code})")
        currency = data.get("currency", "")

        print(f"Ad Account: {data.get('name', '---')}")
        print(f"  ID:         {data.get('account_id', data.get('id', '---'))}")
        print(f"  Status:     {status}")
        print(f"  Currency:   {currency}")
        print(f"  Timezone:   {data.get('timezone_name', '---')}")
        print(f"  Spent:      {_format_budget(data.get('amount_spent'), currency)}")
        print(f"  Balance:    {_format_budget(data.get('balance'), currency)}")
        if data.get("spend_cap"):
            print(f"  Spend cap:  {_format_budget(data.get('spend_cap'), currency)}")
        if data.get("min_daily_budget"):
            print(f"  Min daily:  {_format_budget(data.get('min_daily_budget'), currency)}")


# ---------------------------------------------------------------------------
# Command: pages
# ---------------------------------------------------------------------------

def cmd_pages(args: argparse.Namespace) -> None:
    """List Facebook pages available for ad creatives."""
    account_id = args.account_id or META_AD_ACCOUNT_ID
    fields = "id,name,instagram_business_account{id,name,username}"

    pages = _paginate(f"{account_id}/promote_pages", {"fields": fields}, max_items=200)

    if args.json:
        _output_json(pages)
    else:
        if not pages:
            print("No pages found for this ad account.")
            return

        print(f"{'Page ID':<20} {'Page Name':<35} {'IG Username':<25} {'IG ID'}")
        print("-" * 105)
        for p in pages:
            ig = p.get("instagram_business_account", {})
            ig_user = ig.get("username", "---")
            ig_id = ig.get("id", "---")
            print(f"{p['id']:<20} {_truncate(p['name'], 33):<35} {ig_user:<25} {ig_id}")


# ---------------------------------------------------------------------------
# Command: campaigns
# ---------------------------------------------------------------------------

CAMPAIGN_FIELDS = "id,name,objective,status,effective_status,daily_budget,lifetime_budget,budget_remaining,bid_strategy,buying_type,created_time,start_time,stop_time,special_ad_categories"


def cmd_campaigns(args: argparse.Namespace) -> None:
    """List campaigns."""
    account_id = args.account_id or META_AD_ACCOUNT_ID
    params: dict = {"fields": CAMPAIGN_FIELDS, "limit": args.limit}

    if args.status:
        params["filtering"] = json.dumps([{
            "field": "effective_status",
            "operator": "IN",
            "value": [args.status.upper()],
        }])

    campaigns = _paginate(f"{account_id}/campaigns", params, max_items=args.limit)

    if args.json:
        _output_json(campaigns)
    else:
        if not campaigns:
            print("No campaigns found.")
            return

        print(f"{'ID':<20} {'Name':<40} {'Status':<12} {'Objective':<22} {'Daily Budget':<15} {'Lifetime Budget'}")
        print("-" * 130)
        for c in campaigns:
            daily = _format_budget(c.get("daily_budget")) if c.get("daily_budget") else "---"
            lifetime = _format_budget(c.get("lifetime_budget")) if c.get("lifetime_budget") else "---"
            print(f"{c['id']:<20} {_truncate(c.get('name', '---'), 38):<40} {c.get('effective_status', '---'):<12} {c.get('objective', '---'):<22} {daily:<15} {lifetime}")


# ---------------------------------------------------------------------------
# Command: campaign-detail
# ---------------------------------------------------------------------------

CAMPAIGN_DETAIL_FIELDS = "id,name,objective,status,effective_status,daily_budget,lifetime_budget,budget_remaining,bid_strategy,buying_type,created_time,start_time,stop_time,updated_time,special_ad_categories,spend_cap,promoted_object,source_campaign_id"


def cmd_campaign_detail(args: argparse.Namespace) -> None:
    """Show single campaign details."""
    data = _api_call("GET", str(args.campaign_id), {"fields": CAMPAIGN_DETAIL_FIELDS})

    if args.json:
        _output_json(data)
    else:
        print(f"Campaign: {data.get('name', '---')}")
        print(f"  ID:              {data.get('id')}")
        print(f"  Objective:       {data.get('objective', '---')}")
        print(f"  Status:          {data.get('status', '---')}")
        print(f"  Effective:       {data.get('effective_status', '---')}")
        print(f"  Bid strategy:    {data.get('bid_strategy', '---')}")
        print(f"  Buying type:     {data.get('buying_type', '---')}")
        if data.get("daily_budget"):
            print(f"  Daily budget:    {_format_budget(data['daily_budget'])}")
        if data.get("lifetime_budget"):
            print(f"  Lifetime budget: {_format_budget(data['lifetime_budget'])}")
        if data.get("budget_remaining"):
            print(f"  Budget remain:   {_format_budget(data['budget_remaining'])}")
        if data.get("spend_cap"):
            print(f"  Spend cap:       {_format_budget(data['spend_cap'])}")
        cats = data.get("special_ad_categories", [])
        print(f"  Special cats:    {', '.join(cats) if cats else 'none'}")
        print(f"  Created:         {data.get('created_time', '---')}")
        if data.get("start_time"):
            print(f"  Start:           {data['start_time']}")
        if data.get("stop_time"):
            print(f"  Stop:            {data['stop_time']}")
        if data.get("updated_time"):
            print(f"  Updated:         {data['updated_time']}")


# ---------------------------------------------------------------------------
# Command: adsets
# ---------------------------------------------------------------------------

ADSET_FIELDS = "id,name,campaign_id,status,effective_status,daily_budget,lifetime_budget,budget_remaining,optimization_goal,billing_event,bid_amount,bid_strategy,start_time,end_time,created_time,targeting"


def cmd_adsets(args: argparse.Namespace) -> None:
    """List ad sets."""
    account_id = args.account_id or META_AD_ACCOUNT_ID

    # Use campaign-level or account-level endpoint
    if args.campaign_id:
        base_endpoint = f"{args.campaign_id}/adsets"
    else:
        base_endpoint = f"{account_id}/adsets"

    params: dict = {"fields": ADSET_FIELDS, "limit": args.limit}

    if args.status:
        params["filtering"] = json.dumps([{
            "field": "effective_status",
            "operator": "IN",
            "value": [args.status.upper()],
        }])

    adsets = _paginate(base_endpoint, params, max_items=args.limit)

    if args.json:
        _output_json(adsets)
    else:
        if not adsets:
            print("No ad sets found.")
            return

        print(f"{'ID':<20} {'Name':<35} {'Status':<12} {'Opt Goal':<20} {'Daily':<12} {'Lifetime':<12} {'Campaign'}")
        print("-" * 130)
        for a in adsets:
            daily = _format_budget(a.get("daily_budget")) if a.get("daily_budget") else "---"
            lifetime = _format_budget(a.get("lifetime_budget")) if a.get("lifetime_budget") else "---"
            print(f"{a['id']:<20} {_truncate(a.get('name', '---'), 33):<35} {a.get('effective_status', '---'):<12} {_truncate(a.get('optimization_goal', '---'), 18):<20} {daily:<12} {lifetime:<12} {a.get('campaign_id', '---')}")


# ---------------------------------------------------------------------------
# Command: adset-detail
# ---------------------------------------------------------------------------

ADSET_DETAIL_FIELDS = "id,name,campaign_id,status,effective_status,daily_budget,lifetime_budget,budget_remaining,optimization_goal,billing_event,bid_amount,bid_strategy,targeting,promoted_object,start_time,end_time,created_time,updated_time,destination_type,attribution_spec"


def cmd_adset_detail(args: argparse.Namespace) -> None:
    """Show single ad set details."""
    data = _api_call("GET", str(args.adset_id), {"fields": ADSET_DETAIL_FIELDS})

    if args.json:
        _output_json(data)
    else:
        print(f"Ad Set: {data.get('name', '---')}")
        print(f"  ID:              {data.get('id')}")
        print(f"  Campaign ID:     {data.get('campaign_id', '---')}")
        print(f"  Status:          {data.get('status', '---')}")
        print(f"  Effective:       {data.get('effective_status', '---')}")
        print(f"  Opt goal:        {data.get('optimization_goal', '---')}")
        print(f"  Billing event:   {data.get('billing_event', '---')}")
        print(f"  Bid strategy:    {data.get('bid_strategy', '---')}")
        if data.get("bid_amount"):
            print(f"  Bid amount:      {_format_budget(data['bid_amount'])}")
        if data.get("daily_budget"):
            print(f"  Daily budget:    {_format_budget(data['daily_budget'])}")
        if data.get("lifetime_budget"):
            print(f"  Lifetime budget: {_format_budget(data['lifetime_budget'])}")
        if data.get("budget_remaining"):
            print(f"  Budget remain:   {_format_budget(data['budget_remaining'])}")
        if data.get("destination_type"):
            print(f"  Destination:     {data['destination_type']}")

        # Targeting summary
        targeting = data.get("targeting", {})
        if targeting:
            print(f"  Targeting:")
            geo = targeting.get("geo_locations", {})
            countries = geo.get("countries", [])
            if countries:
                print(f"    Countries:     {', '.join(countries)}")
            cities = geo.get("cities", [])
            if cities:
                city_names = [c.get("name", c.get("key", "?")) for c in cities]
                print(f"    Cities:        {', '.join(city_names)}")
            age_min = targeting.get("age_min")
            age_max = targeting.get("age_max")
            if age_min or age_max:
                print(f"    Age:           {age_min or '?'}-{age_max or '?'}")
            genders = targeting.get("genders", [])
            if genders:
                gender_map = {1: "Male", 2: "Female"}
                print(f"    Genders:       {', '.join(gender_map.get(g, str(g)) for g in genders)}")
            interests = targeting.get("flexible_spec", [])
            if interests:
                for spec in interests:
                    for key, vals in spec.items():
                        names = [v.get("name", "?") for v in vals] if isinstance(vals, list) else [str(vals)]
                        print(f"    {key}: {', '.join(names)}")
            custom_audiences = targeting.get("custom_audiences", [])
            if custom_audiences:
                print(f"    Custom audiences: {len(custom_audiences)}")
            excluded = targeting.get("excluded_custom_audiences", [])
            if excluded:
                print(f"    Excluded audiences: {len(excluded)}")

        # Promoted object
        promoted = data.get("promoted_object", {})
        if promoted:
            print(f"  Promoted object: {json.dumps(promoted)}")

        print(f"  Created:         {data.get('created_time', '---')}")
        if data.get("start_time"):
            print(f"  Start:           {data['start_time']}")
        if data.get("end_time"):
            print(f"  End:             {data['end_time']}")


# ---------------------------------------------------------------------------
# Command: ads
# ---------------------------------------------------------------------------

AD_LIST_FIELDS = "id,name,adset_id,campaign_id,status,effective_status,creative{id,name,thumbnail_url},created_time"


def cmd_ads(args: argparse.Namespace) -> None:
    """List ads."""
    account_id = args.account_id or META_AD_ACCOUNT_ID

    if args.adset_id:
        base_endpoint = f"{args.adset_id}/ads"
    elif args.campaign_id:
        base_endpoint = f"{args.campaign_id}/ads"
    else:
        base_endpoint = f"{account_id}/ads"

    params: dict = {"fields": AD_LIST_FIELDS, "limit": args.limit}

    if args.status:
        params["filtering"] = json.dumps([{
            "field": "effective_status",
            "operator": "IN",
            "value": [args.status.upper()],
        }])

    ads = _paginate(base_endpoint, params, max_items=args.limit)

    if args.json:
        _output_json(ads)
    else:
        if not ads:
            print("No ads found.")
            return

        print(f"{'ID':<20} {'Name':<35} {'Status':<15} {'Creative ID':<18} {'Ad Set':<20} {'Campaign'}")
        print("-" * 130)
        for a in ads:
            creative = a.get("creative", {})
            print(f"{a['id']:<20} {_truncate(a.get('name', '---'), 33):<35} {a.get('effective_status', '---'):<15} {creative.get('id', '---'):<18} {a.get('adset_id', '---'):<20} {a.get('campaign_id', '---')}")


# ---------------------------------------------------------------------------
# Command: ad-detail
# ---------------------------------------------------------------------------

AD_DETAIL_FIELDS = "id,name,adset_id,campaign_id,status,effective_status,creative{id,name,body,title,link_url,image_url,image_hash,thumbnail_url,object_story_spec,call_to_action_type,asset_feed_spec,url_tags},created_time,updated_time,tracking_specs,conversion_specs"


def cmd_ad_detail(args: argparse.Namespace) -> None:
    """Show single ad details with full creative info."""
    data = _api_call("GET", str(args.ad_id), {"fields": AD_DETAIL_FIELDS})

    if args.json:
        _output_json(data)
    else:
        print(f"Ad: {data.get('name', '---')}")
        print(f"  ID:              {data.get('id')}")
        print(f"  Ad Set ID:       {data.get('adset_id', '---')}")
        print(f"  Campaign ID:     {data.get('campaign_id', '---')}")
        print(f"  Status:          {data.get('status', '---')}")
        print(f"  Effective:       {data.get('effective_status', '---')}")

        creative = data.get("creative", {})
        if creative:
            print(f"  Creative:")
            print(f"    ID:            {creative.get('id', '---')}")
            print(f"    Name:          {creative.get('name', '---')}")
            if creative.get("body"):
                print(f"    Body:          {_truncate(creative['body'], 80)}")
            if creative.get("title"):
                print(f"    Title:         {creative['title']}")
            if creative.get("link_url"):
                print(f"    Link:          {creative['link_url']}")
            if creative.get("image_url"):
                print(f"    Image URL:     {_truncate(creative['image_url'], 80)}")
            if creative.get("image_hash"):
                print(f"    Image hash:    {creative['image_hash']}")
            if creative.get("call_to_action_type"):
                print(f"    CTA:           {creative['call_to_action_type']}")
            if creative.get("thumbnail_url"):
                print(f"    Thumbnail:     {_truncate(creative['thumbnail_url'], 80)}")

            oss = creative.get("object_story_spec")
            if oss:
                print(f"    Story spec:    {json.dumps(oss, ensure_ascii=False)[:200]}...")

            afs = creative.get("asset_feed_spec")
            if afs:
                bodies = afs.get("bodies", [])
                titles = afs.get("titles", [])
                descriptions = afs.get("descriptions", [])
                images = afs.get("images", [])
                videos = afs.get("videos", [])
                print(f"    Asset feed:")
                if bodies:
                    print(f"      Bodies ({len(bodies)}):")
                    for b in bodies:
                        print(f"        - {_truncate(b.get('text', ''), 70)}")
                if titles:
                    print(f"      Titles ({len(titles)}):")
                    for t in titles:
                        print(f"        - {_truncate(t.get('text', ''), 70)}")
                if descriptions:
                    print(f"      Descriptions ({len(descriptions)}):")
                    for d in descriptions:
                        print(f"        - {_truncate(d.get('text', ''), 70)}")
                if images:
                    print(f"      Images: {len(images)}")
                if videos:
                    print(f"      Videos: {len(videos)}")

        print(f"  Created:         {data.get('created_time', '---')}")
        if data.get("updated_time"):
            print(f"  Updated:         {data['updated_time']}")


# ---------------------------------------------------------------------------
# Command: creatives
# ---------------------------------------------------------------------------

CREATIVE_LIST_FIELDS = "id,name,status,thumbnail_url,title,body,link_url,image_url,call_to_action_type,object_type"


def cmd_creatives(args: argparse.Namespace) -> None:
    """List ad creatives."""
    account_id = args.account_id or META_AD_ACCOUNT_ID
    params: dict = {"fields": CREATIVE_LIST_FIELDS, "limit": args.limit}

    creatives = _paginate(f"{account_id}/adcreatives", params, max_items=args.limit)

    if args.json:
        _output_json(creatives)
    else:
        if not creatives:
            print("No creatives found.")
            return

        print(f"{'ID':<20} {'Name':<30} {'Status':<12} {'Type':<15} {'CTA':<15} {'Title'}")
        print("-" * 120)
        for c in creatives:
            print(f"{c['id']:<20} {_truncate(c.get('name', '---'), 28):<30} {c.get('status', '---'):<12} {c.get('object_type', '---'):<15} {_truncate(c.get('call_to_action_type', '---'), 13):<15} {_truncate(c.get('title', '---'), 30)}")


# ---------------------------------------------------------------------------
# Command: creative-detail
# ---------------------------------------------------------------------------

CREATIVE_DETAIL_FIELDS = "id,name,status,body,title,link_url,image_url,image_hash,thumbnail_url,object_story_spec,object_story_id,asset_feed_spec,call_to_action_type,object_type,url_tags,effective_object_story_id"


def cmd_creative_detail(args: argparse.Namespace) -> None:
    """Show creative details."""
    data = _api_call("GET", str(args.creative_id), {"fields": CREATIVE_DETAIL_FIELDS})

    if args.json:
        _output_json(data)
    else:
        print(f"Creative: {data.get('name', '---')}")
        print(f"  ID:              {data.get('id')}")
        print(f"  Status:          {data.get('status', '---')}")
        print(f"  Type:            {data.get('object_type', '---')}")
        if data.get("body"):
            print(f"  Body:            {data['body']}")
        if data.get("title"):
            print(f"  Title:           {data['title']}")
        if data.get("link_url"):
            print(f"  Link URL:        {data['link_url']}")
        if data.get("image_url"):
            print(f"  Image URL:       {_truncate(data['image_url'], 80)}")
        if data.get("image_hash"):
            print(f"  Image hash:      {data['image_hash']}")
        if data.get("call_to_action_type"):
            print(f"  CTA:             {data['call_to_action_type']}")
        if data.get("url_tags"):
            print(f"  URL tags:        {data['url_tags']}")
        if data.get("thumbnail_url"):
            print(f"  Thumbnail:       {_truncate(data['thumbnail_url'], 80)}")

        oss = data.get("object_story_spec")
        if oss:
            print(f"  Object story spec:")
            print(f"    {json.dumps(oss, indent=4, ensure_ascii=False)}")

        afs = data.get("asset_feed_spec")
        if afs:
            print(f"  Asset feed spec:")
            print(f"    {json.dumps(afs, indent=4, ensure_ascii=False)}")


# ---------------------------------------------------------------------------
# Command: campaign-create
# ---------------------------------------------------------------------------

OBJECTIVES = [
    "OUTCOME_AWARENESS", "OUTCOME_TRAFFIC", "OUTCOME_ENGAGEMENT",
    "OUTCOME_LEADS", "OUTCOME_SALES", "OUTCOME_APP_PROMOTION",
]

BID_STRATEGIES = [
    "LOWEST_COST_WITHOUT_CAP", "LOWEST_COST_WITH_BID_CAP",
    "COST_CAP", "MINIMUM_ROAS",
]


def cmd_campaign_create(args: argparse.Namespace) -> None:
    """Create a new campaign."""
    account_id = args.account_id or META_AD_ACCOUNT_ID

    params: dict = {
        "name": args.name,
        "objective": args.objective,
        "status": args.status,
        "special_ad_categories": json.dumps(json.loads(args.special_ad_categories) if args.special_ad_categories else []),
    }

    if args.daily_budget:
        params["daily_budget"] = _budget_to_cents(args.daily_budget)
    if args.lifetime_budget:
        params["lifetime_budget"] = _budget_to_cents(args.lifetime_budget)
    if args.bid_strategy:
        params["bid_strategy"] = args.bid_strategy
    if args.start_time:
        params["start_time"] = args.start_time
    if args.stop_time:
        params["stop_time"] = args.stop_time

    data = _api_call("POST", f"{account_id}/campaigns", params)

    if args.json:
        _output_json(data)
    else:
        print(f"Campaign created: ID {data.get('id')}")


# ---------------------------------------------------------------------------
# Command: campaign-update
# ---------------------------------------------------------------------------

def cmd_campaign_update(args: argparse.Namespace) -> None:
    """Update an existing campaign."""
    params: dict = {}

    if args.name:
        params["name"] = args.name
    if args.status:
        params["status"] = args.status
    if args.daily_budget is not None:
        params["daily_budget"] = _budget_to_cents(args.daily_budget)
    if args.lifetime_budget is not None:
        params["lifetime_budget"] = _budget_to_cents(args.lifetime_budget)
    if args.bid_strategy:
        params["bid_strategy"] = args.bid_strategy
    if args.stop_time:
        params["stop_time"] = args.stop_time

    if not params:
        print("ERROR: No fields to update. Provide at least one field.", file=sys.stderr)
        sys.exit(1)

    # Safety: confirm required for pausing/archiving
    if args.status in ("PAUSED", "ARCHIVED") and not args.confirm:
        print(f"ERROR: Changing status to {args.status} requires --confirm flag.", file=sys.stderr)
        sys.exit(1)

    data = _api_call("POST", str(args.campaign_id), params)

    if args.json:
        _output_json(data)
    else:
        if data.get("success"):
            print(f"Campaign {args.campaign_id} updated successfully.")
        else:
            print(f"Campaign update response: {data}")


# ---------------------------------------------------------------------------
# Command: campaign-duplicate
# ---------------------------------------------------------------------------

def cmd_campaign_duplicate(args: argparse.Namespace) -> None:
    """Duplicate a campaign."""
    params: dict = {
        "status_option": args.status_option,
    }

    if args.deep_copy:
        params["deep_copy"] = "true"
    if args.rename_suffix:
        params["rename_options"] = json.dumps({"rename_suffix": args.rename_suffix})

    data = _api_call("POST", f"{args.campaign_id}/copies", params)

    if args.json:
        _output_json(data)
    else:
        copied = data.get("copied_campaign_id") or data.get("id")
        print(f"Campaign duplicated: new ID {copied}")
        if data.get("ad_object_ids"):
            print(f"  Copied objects: {len(data['ad_object_ids'])}")


# ---------------------------------------------------------------------------
# Command: adset-create
# ---------------------------------------------------------------------------

OPTIMIZATION_GOALS = [
    "REACH", "IMPRESSIONS", "LINK_CLICKS", "LANDING_PAGE_VIEWS",
    "OFFSITE_CONVERSIONS", "CONVERSATIONS", "LEAD_GENERATION",
    "VALUE", "APP_INSTALLS", "QUALITY_LEAD", "ENGAGED_USERS",
]


def cmd_adset_create(args: argparse.Namespace) -> None:
    """Create a new ad set."""
    account_id = args.account_id or META_AD_ACCOUNT_ID

    if not args.daily_budget and not args.lifetime_budget:
        print("ERROR: Either --daily-budget or --lifetime-budget is required.", file=sys.stderr)
        sys.exit(1)

    params: dict = {
        "campaign_id": args.campaign_id,
        "name": args.name,
        "optimization_goal": args.optimization_goal,
        "billing_event": args.billing_event,
        "targeting": args.targeting,
        "status": args.status,
    }

    if args.daily_budget:
        params["daily_budget"] = _budget_to_cents(args.daily_budget)
    if args.lifetime_budget:
        params["lifetime_budget"] = _budget_to_cents(args.lifetime_budget)
    if args.bid_amount:
        params["bid_amount"] = _budget_to_cents(args.bid_amount)
    if args.start_time:
        params["start_time"] = args.start_time
    if args.end_time:
        params["end_time"] = args.end_time
    if args.promoted_object:
        params["promoted_object"] = args.promoted_object

    data = _api_call("POST", f"{account_id}/adsets", params)

    if args.json:
        _output_json(data)
    else:
        print(f"Ad set created: ID {data.get('id')}")


# ---------------------------------------------------------------------------
# Command: adset-update
# ---------------------------------------------------------------------------

def cmd_adset_update(args: argparse.Namespace) -> None:
    """Update an existing ad set."""
    params: dict = {}

    if args.name:
        params["name"] = args.name
    if args.status:
        params["status"] = args.status
    if args.daily_budget is not None:
        params["daily_budget"] = _budget_to_cents(args.daily_budget)
    if args.lifetime_budget is not None:
        params["lifetime_budget"] = _budget_to_cents(args.lifetime_budget)
    if args.bid_amount is not None:
        params["bid_amount"] = _budget_to_cents(args.bid_amount)
    if args.targeting:
        params["targeting"] = args.targeting
    if args.end_time:
        params["end_time"] = args.end_time

    if not params:
        print("ERROR: No fields to update.", file=sys.stderr)
        sys.exit(1)

    if args.status in ("PAUSED", "ARCHIVED") and not args.confirm:
        print(f"ERROR: Changing status to {args.status} requires --confirm flag.", file=sys.stderr)
        sys.exit(1)

    data = _api_call("POST", str(args.adset_id), params)

    if args.json:
        _output_json(data)
    else:
        if data.get("success"):
            print(f"Ad set {args.adset_id} updated successfully.")
        else:
            print(f"Ad set update response: {data}")


# ---------------------------------------------------------------------------
# Command: adset-duplicate
# ---------------------------------------------------------------------------

def cmd_adset_duplicate(args: argparse.Namespace) -> None:
    """Duplicate an ad set."""
    params: dict = {
        "status_option": args.status_option,
    }

    if args.campaign_id:
        params["campaign_id"] = args.campaign_id
    if args.deep_copy:
        params["deep_copy"] = "true"
    if args.rename_suffix:
        params["rename_options"] = json.dumps({"rename_suffix": args.rename_suffix})

    data = _api_call("POST", f"{args.adset_id}/copies", params)

    if args.json:
        _output_json(data)
    else:
        copied = data.get("copied_adset_id") or data.get("id")
        print(f"Ad set duplicated: new ID {copied}")


# ---------------------------------------------------------------------------
# Command: ad-create
# ---------------------------------------------------------------------------

def cmd_ad_create(args: argparse.Namespace) -> None:
    """Create a new ad."""
    account_id = args.account_id or META_AD_ACCOUNT_ID

    params: dict = {
        "adset_id": args.adset_id,
        "name": args.name,
        "status": args.status,
    }

    if args.creative_id:
        params["creative"] = json.dumps({"creative_id": args.creative_id})
    elif args.creative_json:
        params["creative"] = args.creative_json
    else:
        print("ERROR: Either --creative-id or --creative-json is required.", file=sys.stderr)
        sys.exit(1)

    if args.tracking_specs:
        params["tracking_specs"] = args.tracking_specs

    data = _api_call("POST", f"{account_id}/ads", params)

    if args.json:
        _output_json(data)
    else:
        print(f"Ad created: ID {data.get('id')}")


# ---------------------------------------------------------------------------
# Command: ad-update
# ---------------------------------------------------------------------------

def cmd_ad_update(args: argparse.Namespace) -> None:
    """Update an existing ad."""
    params: dict = {}

    if args.name:
        params["name"] = args.name
    if args.status:
        params["status"] = args.status
    if args.creative_id:
        params["creative"] = json.dumps({"creative_id": args.creative_id})

    if not params:
        print("ERROR: No fields to update.", file=sys.stderr)
        sys.exit(1)

    if args.status in ("PAUSED", "ARCHIVED") and not args.confirm:
        print(f"ERROR: Changing status to {args.status} requires --confirm flag.", file=sys.stderr)
        sys.exit(1)

    data = _api_call("POST", str(args.ad_id), params)

    if args.json:
        _output_json(data)
    else:
        if data.get("success"):
            print(f"Ad {args.ad_id} updated successfully.")
        else:
            print(f"Ad update response: {data}")


# ---------------------------------------------------------------------------
# Command: ad-duplicate
# ---------------------------------------------------------------------------

def cmd_ad_duplicate(args: argparse.Namespace) -> None:
    """Duplicate an ad."""
    params: dict = {
        "status_option": args.status_option,
    }

    if args.adset_id:
        params["adset_id"] = args.adset_id
    if args.rename_suffix:
        params["rename_options"] = json.dumps({"rename_suffix": args.rename_suffix})

    data = _api_call("POST", f"{args.ad_id}/copies", params)

    if args.json:
        _output_json(data)
    else:
        copied = data.get("copied_ad_id") or data.get("id")
        print(f"Ad duplicated: new ID {copied}")


# ---------------------------------------------------------------------------
# Command: image-upload
# ---------------------------------------------------------------------------

def cmd_image_upload(args: argparse.Namespace) -> None:
    """Upload image file, returns image hash."""
    account_id = args.account_id or META_AD_ACCOUNT_ID

    if not os.path.isfile(args.file):
        print(f"ERROR: File not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    with open(args.file, "rb") as f:
        file_bytes = base64.b64encode(f.read()).decode("ascii")

    filename = os.path.basename(args.file)
    data = _api_call("POST", f"{account_id}/adimages", {
        "bytes": file_bytes,
        "name": filename,
    })

    # Response format: {"images": {"filename": {"hash": "...", "url": "..."}}}
    images = data.get("images", {})
    img_data = next(iter(images.values()), {}) if images else {}

    if args.json:
        _output_json(img_data)
    else:
        print(f"Image uploaded: {filename}")
        print(f"  Hash:  {img_data.get('hash', '---')}")
        print(f"  URL:   {img_data.get('url', '---')}")


# ---------------------------------------------------------------------------
# Command: video-upload
# ---------------------------------------------------------------------------

def _wait_video_ready(video_id: str, timeout: int = 300, interval: int = 6) -> str:
    """Poll a video until processing completes. Returns final status string.

    Meta returns a video ID immediately after upload, but the video is still
    'processing'. Creating a creative that references a not-yet-ready video can
    fail, so callers that immediately build a creative should wait for 'ready'.
    """
    waited = 0
    status = "unknown"
    while waited < timeout:
        data = _api_call("GET", video_id, {"fields": "status"})
        status = (data.get("status") or {}).get("video_status", "unknown")
        print(f"  video {video_id} status={status} ({waited}s)", file=sys.stderr)
        if status == "ready":
            return status
        if status == "error":
            print(f"  VIDEO PROCESSING ERROR: {json.dumps(data.get('status'))}", file=sys.stderr)
            return status
        time.sleep(interval)
        waited += interval
    return status


def cmd_video_upload(args: argparse.Namespace) -> None:
    """Upload video file, returns video ID."""
    account_id = args.account_id or META_AD_ACCOUNT_ID

    if not os.path.isfile(args.file):
        print(f"ERROR: File not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    file_size = os.path.getsize(args.file)
    print(f"Uploading {os.path.basename(args.file)} ({file_size / 1024 / 1024:.1f} MB)...", file=sys.stderr)

    params: dict = {}
    if args.title:
        params["title"] = args.title

    with open(args.file, "rb") as f:
        data = _api_call(
            "POST",
            f"{account_id}/advideos",
            params,
            files={"source": (os.path.basename(args.file), f)},
            timeout=300,
        )

    video_id = data.get("id", "")
    final_status = None
    if getattr(args, "wait", False) and video_id:
        final_status = _wait_video_ready(video_id, timeout=args.wait_timeout)
        if isinstance(data, dict):
            data["video_status"] = final_status

    if args.json:
        _output_json(data)
    else:
        print(f"Video uploaded: ID {video_id or '---'}")
        if final_status is not None:
            print(f"  Processing status: {final_status}")


# ---------------------------------------------------------------------------
# Command: creative-create
# ---------------------------------------------------------------------------

def cmd_creative_create(args: argparse.Namespace) -> None:
    """Create a new ad creative."""
    account_id = args.account_id or META_AD_ACCOUNT_ID
    page_id = args.page_id or META_PAGE_ID

    if not page_id:
        print("ERROR: --page-id required (or set META_PAGE_ID in .env).", file=sys.stderr)
        sys.exit(1)

    params: dict = {"name": args.name}

    if args.url_tags:
        params["url_tags"] = args.url_tags

    creative_type = args.type

    if creative_type == "link":
        link_data: dict = {
            "link": args.link,
            "message": args.message or "",
        }
        if args.headline:
            link_data["name"] = args.headline
        if args.description:
            link_data["description"] = args.description
        if args.image_hash:
            link_data["image_hash"] = args.image_hash
        elif args.image_url:
            link_data["picture"] = args.image_url
        if args.call_to_action:
            link_data["call_to_action"] = {"type": args.call_to_action}

        params["object_story_spec"] = json.dumps({
            "page_id": page_id,
            "link_data": link_data,
        })

    elif creative_type == "video":
        if not args.video_id:
            print("ERROR: --video-id required for video creative.", file=sys.stderr)
            sys.exit(1)
        video_data: dict = {
            "video_id": args.video_id,
            "message": args.message or "",
        }
        if args.headline:
            video_data["title"] = args.headline
        if args.video_thumbnail:
            video_data["image_url"] = args.video_thumbnail
        if args.call_to_action:
            video_data["call_to_action"] = {"type": args.call_to_action, "value": {"link": args.link or ""}}

        params["object_story_spec"] = json.dumps({
            "page_id": page_id,
            "video_data": video_data,
        })

    elif creative_type == "photo":
        if not args.image_hash:
            print("ERROR: --image-hash required for photo creative.", file=sys.stderr)
            sys.exit(1)
        params["object_story_spec"] = json.dumps({
            "page_id": page_id,
            "photo_data": {
                "image_hash": args.image_hash,
                "message": args.message or "",
            },
        })

    elif creative_type == "carousel":
        if not args.child_attachments:
            print("ERROR: --child-attachments required for carousel creative.", file=sys.stderr)
            sys.exit(1)
        if not args.link:
            print("ERROR: --link required for carousel creative.", file=sys.stderr)
            sys.exit(1)
        link_data_c: dict = {
            "link": args.link,
            "message": args.message or "",
            "child_attachments": json.loads(args.child_attachments),
        }
        if args.call_to_action:
            link_data_c["call_to_action"] = {"type": args.call_to_action}

        params["object_story_spec"] = json.dumps({
            "page_id": page_id,
            "link_data": link_data_c,
        })
    else:
        print(f"ERROR: Unknown creative type: {creative_type}", file=sys.stderr)
        sys.exit(1)

    data = _api_call("POST", f"{account_id}/adcreatives", params)

    if args.json:
        _output_json(data)
    else:
        print(f"Creative created: ID {data.get('id')}")


# ---------------------------------------------------------------------------
# Command: creative-clone
# ---------------------------------------------------------------------------

# Deprecated degrees_of_freedom_spec fields: the API returns them on read but
# rejects them on create. Strip before recreating.
_DOF_DEPRECATED = [
    "standard_enhancements", "advantage_plus_creative", "cv_transformation",
    "image_animation", "replace_media_text", "show_destination_blurbs", "show_summary",
]


def cmd_creative_clone(args: argparse.Namespace) -> None:
    """Clone an existing creative, optionally swapping video/image/URL.

    Creative objects are immutable. This reads the source creative's full spec
    (object_story_spec + asset_feed_spec + degrees_of_freedom_spec), applies the
    requested swaps while preserving texts, adlabels and asset_customization_rules,
    creates a NEW creative, and optionally swaps it onto an ad (--swap-on-ad).

    Handles two Advantage+ gotchas automatically:
      - images[] must have UNIQUE hashes -> when --swap-image is given, all image
        slots collapse into a single entry carrying every original adlabel.
      - degrees_of_freedom_spec deprecated fields are stripped before create.
    """
    account_id = args.account_id or META_AD_ACCOUNT_ID

    orig = _api_call("GET", args.creative_id, {
        "fields": "object_story_spec,asset_feed_spec,degrees_of_freedom_spec",
    })
    afs = copy.deepcopy(orig.get("asset_feed_spec") or {})
    if not afs:
        print("ERROR: Source creative has no asset_feed_spec (not an Advantage+ creative). "
              "Use creative-create for simple creatives.", file=sys.stderr)
        sys.exit(1)

    # swap video(s)
    if args.swap_video:
        for v in afs.get("videos", []):
            v["video_id"] = args.swap_video
            if args.swap_thumbnail:
                v["thumbnail_hash"] = args.swap_thumbnail
                v.pop("thumbnail_url", None)

    # swap fallback image(s) -> collapse to one unique entry with all adlabels
    if args.swap_image:
        imgs = afs.get("images", [])
        if imgs:
            all_labels: list = []
            for img in imgs:
                all_labels.extend(img.get("adlabels", []))
            afs["images"] = [{"adlabels": all_labels, "hash": args.swap_image}]
        else:
            afs["images"] = [{"hash": args.swap_image}]

    # swap landing page URL(s)
    if args.new_url:
        for u in afs.get("link_urls", []):
            u["website_url"] = args.new_url

    # drop read-only/false response fields the API rejects on create
    for rk in ("reasons_to_shop", "shops_bundle"):
        if rk in afs and not afs[rk]:
            afs.pop(rk, None)

    dof = copy.deepcopy(orig.get("degrees_of_freedom_spec") or {})
    if dof:
        cfs = dof.get("creative_features_spec", {})
        for d in _DOF_DEPRECATED:
            cfs.pop(d, None)

    payload = {
        "name": args.name,
        "object_story_spec": json.dumps(orig.get("object_story_spec") or {}),
        "asset_feed_spec": json.dumps(afs),
    }
    if dof:
        payload["degrees_of_freedom_spec"] = json.dumps(dof)

    new_creative = _api_call("POST", f"{account_id}/adcreatives", payload)
    new_id = new_creative.get("id")

    result = {"new_creative_id": new_id}
    if args.swap_on_ad and new_id:
        swap = _api_call("POST", args.swap_on_ad, {"creative": json.dumps({"creative_id": new_id})})
        result["ad_swap"] = swap
        result["ad_id"] = args.swap_on_ad

    if args.json:
        _output_json(result)
    else:
        print(f"New creative: {new_id}")
        if args.swap_on_ad:
            print(f"Swapped onto ad {args.swap_on_ad}: {result.get('ad_swap')}")


# ---------------------------------------------------------------------------
# Command: insights
# ---------------------------------------------------------------------------

DEFAULT_INSIGHT_FIELDS = [
    "campaign_name", "adset_name", "ad_name",
    "impressions", "clicks", "ctr", "cpc", "cpm",
    "spend", "reach", "frequency",
    "actions", "cost_per_action_type",
]

DATE_PRESETS = [
    "today", "yesterday", "last_7d", "last_14d", "last_30d",
    "last_90d", "this_month", "last_month", "this_quarter",
    "last_quarter", "this_year", "last_year", "maximum",
]


def _format_insight_value(key: str, value: object) -> str:
    """Format insight metric value for human display."""
    if value is None:
        return "---"
    if key in ("ctr", "frequency"):
        return f"{float(value):.2f}"
    if key in ("cpc", "cpm", "spend"):
        return f"{float(value):.2f}"
    if key in ("impressions", "reach", "clicks"):
        return f"{int(value):,}"
    if key == "actions" and isinstance(value, list):
        parts = []
        for a in value:
            parts.append(f"{a.get('action_type', '?')}: {a.get('value', '?')}")
        return "; ".join(parts)
    if key == "cost_per_action_type" and isinstance(value, list):
        parts = []
        for a in value:
            parts.append(f"{a.get('action_type', '?')}: {float(a.get('value', 0)):.2f}")
        return "; ".join(parts)
    return str(value)


def cmd_insights(args: argparse.Namespace) -> None:
    """Get performance insights for any object (account/campaign/adset/ad)."""
    object_id = args.object_id
    if not object_id:
        object_id = args.account_id or META_AD_ACCOUNT_ID

    fields = args.fields.split(",") if args.fields else DEFAULT_INSIGHT_FIELDS

    params: dict = {
        "fields": ",".join(fields),
        "limit": args.limit,
    }

    # Date range
    if args.date_from and args.date_to:
        params["time_range"] = json.dumps({"since": args.date_from, "until": args.date_to})
    elif args.date_preset:
        params["date_preset"] = args.date_preset
    else:
        params["date_preset"] = "last_30d"

    # Level
    if args.level:
        params["level"] = args.level

    # Breakdowns
    if args.breakdowns:
        params["breakdowns"] = args.breakdowns

    # Time increment
    if args.time_increment:
        params["time_increment"] = args.time_increment

    data = _api_call("GET", f"{object_id}/insights", params)
    rows = data.get("data", [])

    if args.json:
        _output_json(rows)
    else:
        if not rows:
            print("No insights data for this period.")
            return

        for i, row in enumerate(rows):
            if i > 0:
                print()
            # Header with date range
            period = row.get("date_start", "?")
            period_end = row.get("date_stop", "?")
            name_parts = []
            if row.get("campaign_name"):
                name_parts.append(row["campaign_name"])
            if row.get("adset_name"):
                name_parts.append(row["adset_name"])
            if row.get("ad_name"):
                name_parts.append(row["ad_name"])
            header = " > ".join(name_parts) if name_parts else object_id
            print(f"--- {header} ({period} to {period_end}) ---")

            # Core metrics
            for key in fields:
                if key in ("campaign_name", "adset_name", "ad_name", "date_start", "date_stop"):
                    continue
                value = row.get(key)
                if value is not None:
                    formatted = _format_insight_value(key, value)
                    print(f"  {key:<25} {formatted}")

            # Breakdown values
            if args.breakdowns:
                for bd in args.breakdowns.split(","):
                    bd_val = row.get(bd)
                    if bd_val:
                        print(f"  {bd:<25} {bd_val}")


# ---------------------------------------------------------------------------
# Command: insights-report (async)
# ---------------------------------------------------------------------------

def cmd_insights_report(args: argparse.Namespace) -> None:
    """Generate async insights report for large queries."""
    object_id = args.object_id
    if not object_id:
        object_id = args.account_id or META_AD_ACCOUNT_ID

    fields = args.fields.split(",") if args.fields else DEFAULT_INSIGHT_FIELDS

    params: dict = {
        "fields": ",".join(fields),
        "level": args.level,
    }

    # Date range
    if args.date_from and args.date_to:
        params["time_range"] = json.dumps({"since": args.date_from, "until": args.date_to})
    elif args.date_preset:
        params["date_preset"] = args.date_preset
    else:
        params["date_preset"] = "last_30d"

    if args.breakdowns:
        params["breakdowns"] = args.breakdowns
    if args.time_increment:
        params["time_increment"] = args.time_increment

    # POST triggers async
    data = _api_call("POST", f"{object_id}/insights", params)
    report_run_id = data.get("report_run_id")

    if not report_run_id:
        print("ERROR: No report_run_id returned.", file=sys.stderr)
        sys.exit(1)

    print(f"Report queued (ID: {report_run_id}). Polling...", file=sys.stderr)

    # Poll for completion
    max_wait = 600  # 10 minutes
    elapsed = 0
    poll_interval = 5

    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval

        status_data = _api_call("GET", report_run_id, {
            "fields": "async_status,async_percent_completion",
        })
        status = status_data.get("async_status")
        pct = status_data.get("async_percent_completion", 0)
        print(f"  Status: {status} ({pct}%)", file=sys.stderr)

        if status == "Job Completed":
            break
        elif status in ("Job Failed", "Job Skipped"):
            print(f"ERROR: Report failed with status: {status}", file=sys.stderr)
            sys.exit(1)

        # Slow down polling after initial period
        if elapsed > 30:
            poll_interval = 15
    else:
        print("ERROR: Report timed out after 10 minutes.", file=sys.stderr)
        sys.exit(1)

    # Fetch results
    results = _paginate(f"{report_run_id}/insights", {"limit": 500}, max_items=5000)

    if args.json:
        _output_json(results)
    else:
        print(f"\nReport complete: {len(results)} rows", file=sys.stderr)
        for row in results:
            parts = []
            for key in fields:
                val = row.get(key)
                if val is not None:
                    parts.append(f"{key}={_format_insight_value(key, val)}")
            print(" | ".join(parts))


# ---------------------------------------------------------------------------
# Command: token-info
# ---------------------------------------------------------------------------

def cmd_token_info(args: argparse.Namespace) -> None:
    """Show current access token info (expiry, permissions)."""
    data = _api_call("GET", "debug_token", {"input_token": META_ACCESS_TOKEN})
    token_data = data.get("data", {})

    if args.json:
        _output_json(token_data)
    else:
        expires = token_data.get("expires_at", 0)
        if expires == 0:
            expiry_str = "Never (system user token)"
        else:
            expiry_dt = datetime.fromtimestamp(expires)
            days_left = (expiry_dt - datetime.now()).days
            expiry_str = f"{expiry_dt.strftime('%Y-%m-%d %H:%M')} ({days_left} days left)"

        print(f"Token Info:")
        print(f"  App:        {token_data.get('application', '---')}")
        print(f"  User ID:    {token_data.get('user_id', '---')}")
        print(f"  Type:       {token_data.get('type', '---')}")
        print(f"  Valid:      {token_data.get('is_valid', '---')}")
        print(f"  Expires:    {expiry_str}")
        scopes = token_data.get("scopes", [])
        if scopes:
            print(f"  Scopes:     {', '.join(scopes)}")


# ---------------------------------------------------------------------------
# Command: token-extend
# ---------------------------------------------------------------------------

def cmd_token_extend(args: argparse.Namespace) -> None:
    """Exchange current token for a long-lived one (60 days)."""
    if not META_APP_ID or not META_APP_SECRET:
        print("ERROR: META_APP_ID and META_APP_SECRET must be set in .env", file=sys.stderr)
        sys.exit(1)

    data = _api_call("GET", "oauth/access_token", {
        "grant_type": "fb_exchange_token",
        "client_id": META_APP_ID,
        "client_secret": META_APP_SECRET,
        "fb_exchange_token": META_ACCESS_TOKEN,
    })

    new_token = data.get("access_token")
    expires_in = data.get("expires_in", 0)

    if args.json:
        _output_json(data)
    else:
        days = expires_in // 86400
        print(f"New long-lived token generated ({days} days).")
        print(f"  Token: {new_token[:20]}...{new_token[-10:]}")
        print(f"\n  Update .env manually with the new token:")
        print(f"  META_ACCESS_TOKEN={new_token}")


# ---------------------------------------------------------------------------
# Main: argparse setup
# ---------------------------------------------------------------------------

def main() -> None:
    _check_config()

    parser = argparse.ArgumentParser(
        prog="meta_ads_cli",
        description="Meta Ads CLI — manage Facebook & Instagram ad campaigns.",
    )
    parser.add_argument(
        "--account-id",
        default=None,
        help=f"Override ad account ID (default: {META_AD_ACCOUNT_ID} from .env)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # -- account --
    p = subparsers.add_parser("account", help="Show ad account info")
    p.add_argument("--json", action="store_true")

    # -- pages --
    p = subparsers.add_parser("pages", help="List Facebook/Instagram pages for ad creatives")
    p.add_argument("--json", action="store_true")

    # -- campaign-create --
    p = subparsers.add_parser("campaign-create", help="Create a new campaign")
    p.add_argument("--name", required=True)
    p.add_argument("--objective", required=True, choices=OBJECTIVES)
    p.add_argument("--daily-budget", type=float, help="Daily budget in currency")
    p.add_argument("--lifetime-budget", type=float, help="Lifetime budget in currency")
    p.add_argument("--status", default="PAUSED", choices=["ACTIVE", "PAUSED"])
    p.add_argument("--special-ad-categories", help='JSON array, e.g. \'["HOUSING"]\'')
    p.add_argument("--bid-strategy", choices=BID_STRATEGIES)
    p.add_argument("--start-time", help="ISO 8601 datetime")
    p.add_argument("--stop-time", help="ISO 8601 datetime")
    p.add_argument("--json", action="store_true")

    # -- campaign-update --
    p = subparsers.add_parser("campaign-update", help="Update a campaign")
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--name")
    p.add_argument("--status", choices=["ACTIVE", "PAUSED", "ARCHIVED"])
    p.add_argument("--daily-budget", type=float)
    p.add_argument("--lifetime-budget", type=float)
    p.add_argument("--bid-strategy", choices=BID_STRATEGIES)
    p.add_argument("--stop-time")
    p.add_argument("--confirm", action="store_true", help="Required for PAUSED/ARCHIVED")
    p.add_argument("--json", action="store_true")

    # -- campaign-duplicate --
    p = subparsers.add_parser("campaign-duplicate", help="Duplicate a campaign")
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--deep-copy", action="store_true", help="Copy ad sets and ads too")
    p.add_argument("--status-option", default="PAUSED", choices=["ACTIVE", "PAUSED", "INHERITED_FROM_SOURCE"])
    p.add_argument("--rename-suffix", help="Suffix for copied name")
    p.add_argument("--json", action="store_true")

    # -- adset-create --
    p = subparsers.add_parser("adset-create", help="Create a new ad set")
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--optimization-goal", required=True, choices=OPTIMIZATION_GOALS)
    p.add_argument("--billing-event", default="IMPRESSIONS", choices=["IMPRESSIONS", "LINK_CLICKS"])
    p.add_argument("--daily-budget", type=float)
    p.add_argument("--lifetime-budget", type=float)
    p.add_argument("--targeting", required=True, help='JSON targeting spec, min: \'{"geo_locations":{"countries":["CZ"]}}\'')
    p.add_argument("--bid-amount", type=float)
    p.add_argument("--start-time")
    p.add_argument("--end-time")
    p.add_argument("--status", default="PAUSED", choices=["ACTIVE", "PAUSED"])
    p.add_argument("--promoted-object", help="JSON promoted object spec")
    p.add_argument("--json", action="store_true")

    # -- adset-update --
    p = subparsers.add_parser("adset-update", help="Update an ad set")
    p.add_argument("--adset-id", required=True)
    p.add_argument("--name")
    p.add_argument("--status", choices=["ACTIVE", "PAUSED", "ARCHIVED"])
    p.add_argument("--daily-budget", type=float)
    p.add_argument("--lifetime-budget", type=float)
    p.add_argument("--bid-amount", type=float)
    p.add_argument("--targeting", help="JSON targeting spec")
    p.add_argument("--end-time")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--json", action="store_true")

    # -- adset-duplicate --
    p = subparsers.add_parser("adset-duplicate", help="Duplicate an ad set")
    p.add_argument("--adset-id", required=True)
    p.add_argument("--campaign-id", help="Target campaign (optional)")
    p.add_argument("--deep-copy", action="store_true", help="Copy child ads too")
    p.add_argument("--status-option", default="PAUSED", choices=["ACTIVE", "PAUSED", "INHERITED_FROM_SOURCE"])
    p.add_argument("--rename-suffix")
    p.add_argument("--json", action="store_true")

    # -- ad-create --
    p = subparsers.add_parser("ad-create", help="Create a new ad")
    p.add_argument("--adset-id", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--creative-id", help="Existing creative ID")
    p.add_argument("--creative-json", help="Inline creative JSON spec")
    p.add_argument("--status", default="PAUSED", choices=["ACTIVE", "PAUSED"])
    p.add_argument("--tracking-specs", help="JSON tracking specs")
    p.add_argument("--json", action="store_true")

    # -- ad-update --
    p = subparsers.add_parser("ad-update", help="Update an ad")
    p.add_argument("--ad-id", required=True)
    p.add_argument("--name")
    p.add_argument("--status", choices=["ACTIVE", "PAUSED", "ARCHIVED"])
    p.add_argument("--creative-id", help="Swap to different creative")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--json", action="store_true")

    # -- ad-duplicate --
    p = subparsers.add_parser("ad-duplicate", help="Duplicate an ad")
    p.add_argument("--ad-id", required=True)
    p.add_argument("--adset-id", help="Target ad set (optional)")
    p.add_argument("--status-option", default="PAUSED", choices=["ACTIVE", "PAUSED", "INHERITED_FROM_SOURCE"])
    p.add_argument("--rename-suffix")
    p.add_argument("--json", action="store_true")

    # -- image-upload --
    p = subparsers.add_parser("image-upload", help="Upload image, returns hash")
    p.add_argument("--file", required=True, help="Path to image file")
    p.add_argument("--json", action="store_true")

    # -- video-upload --
    p = subparsers.add_parser("video-upload", help="Upload video, returns ID")
    p.add_argument("--file", required=True, help="Path to video file")
    p.add_argument("--title", help="Video title")
    p.add_argument("--wait", action="store_true", help="Poll until video processing is 'ready' (needed before using in a creative)")
    p.add_argument("--wait-timeout", type=int, default=300, help="Max seconds to wait when --wait (default 300)")
    p.add_argument("--json", action="store_true")

    # -- creative-create --
    p = subparsers.add_parser("creative-create", help="Create ad creative")
    p.add_argument("--name", required=True)
    p.add_argument("--type", required=True, choices=["link", "video", "photo", "carousel"])
    p.add_argument("--page-id", help=f"Facebook Page ID (default: {META_PAGE_ID} from .env)")
    p.add_argument("--message", help="Post text / primary text")
    p.add_argument("--link", help="Landing page URL")
    p.add_argument("--headline", help="Ad headline")
    p.add_argument("--description", help="Ad description")
    p.add_argument("--image-hash", help="Image hash from image-upload")
    p.add_argument("--image-url", help="Image URL (alternative to hash)")
    p.add_argument("--video-id", help="Video ID from video-upload")
    p.add_argument("--video-thumbnail", help="Video thumbnail URL")
    p.add_argument("--call-to-action", help="CTA type (LEARN_MORE, SHOP_NOW, SIGN_UP, ...)")
    p.add_argument("--child-attachments", help="JSON array for carousel")
    p.add_argument("--url-tags", help="UTM parameters")
    p.add_argument("--json", action="store_true")

    # -- creative-clone --
    p = subparsers.add_parser("creative-clone", help="Clone a creative, optionally swapping video/image/URL (Advantage+ asset_feed_spec)")
    p.add_argument("--creative-id", required=True, help="Source creative ID to clone")
    p.add_argument("--name", required=True, help="Name for the new creative")
    p.add_argument("--swap-video", help="New video ID (replaces video in videos[])")
    p.add_argument("--swap-thumbnail", help="New video thumbnail image hash (used with --swap-video)")
    p.add_argument("--swap-image", help="New fallback image hash (collapses images[] to one unique entry)")
    p.add_argument("--new-url", help="New landing page URL (replaces website_url in link_urls[])")
    p.add_argument("--swap-on-ad", help="Ad ID to immediately point at the new creative (triggers re-review)")
    p.add_argument("--json", action="store_true")

    # -- campaigns --
    p = subparsers.add_parser("campaigns", help="List campaigns")
    p.add_argument("--status", choices=["ACTIVE", "PAUSED", "ARCHIVED", "DELETED"], help="Filter by effective status")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--json", action="store_true")

    # -- campaign-detail --
    p = subparsers.add_parser("campaign-detail", help="Show campaign details")
    p.add_argument("--campaign-id", required=True, help="Campaign ID")
    p.add_argument("--json", action="store_true")

    # -- adsets --
    p = subparsers.add_parser("adsets", help="List ad sets")
    p.add_argument("--campaign-id", help="Filter by campaign")
    p.add_argument("--status", choices=["ACTIVE", "PAUSED", "ARCHIVED", "DELETED"], help="Filter by effective status")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--json", action="store_true")

    # -- adset-detail --
    p = subparsers.add_parser("adset-detail", help="Show ad set details")
    p.add_argument("--adset-id", required=True, help="Ad Set ID")
    p.add_argument("--json", action="store_true")

    # -- ads --
    p = subparsers.add_parser("ads", help="List ads")
    p.add_argument("--adset-id", help="Filter by ad set")
    p.add_argument("--campaign-id", help="Filter by campaign")
    p.add_argument("--status", choices=["ACTIVE", "PAUSED", "ARCHIVED", "DELETED"], help="Filter by effective status")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--json", action="store_true")

    # -- ad-detail --
    p = subparsers.add_parser("ad-detail", help="Show ad details with creative info")
    p.add_argument("--ad-id", required=True, help="Ad ID")
    p.add_argument("--json", action="store_true")

    # -- creatives --
    p = subparsers.add_parser("creatives", help="List ad creatives")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--json", action="store_true")

    # -- creative-detail --
    p = subparsers.add_parser("creative-detail", help="Show creative details")
    p.add_argument("--creative-id", required=True, help="Creative ID")
    p.add_argument("--json", action="store_true")

    # -- insights --
    p = subparsers.add_parser("insights", help="Get performance insights")
    p.add_argument("--object-id", help="Object ID (account/campaign/adset/ad). Default: ad account")
    p.add_argument("--level", choices=["account", "campaign", "adset", "ad"], help="Aggregation level")
    p.add_argument("--date-preset", choices=DATE_PRESETS, help="Predefined date range (default: last_30d)")
    p.add_argument("--date-from", help="Start date YYYY-MM-DD")
    p.add_argument("--date-to", help="End date YYYY-MM-DD")
    p.add_argument("--fields", help="Comma-separated metrics")
    p.add_argument("--breakdowns", help="Comma-separated breakdowns (age,gender,country,publisher_platform,platform_position,device_platform)")
    p.add_argument("--time-increment", help="Time granularity: 1,7,14,28,monthly,all_days")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--json", action="store_true")

    # -- insights-report --
    p = subparsers.add_parser("insights-report", help="Generate async insights report (large queries)")
    p.add_argument("--object-id", help="Object ID. Default: ad account")
    p.add_argument("--level", choices=["account", "campaign", "adset", "ad"], required=True)
    p.add_argument("--date-preset", choices=DATE_PRESETS)
    p.add_argument("--date-from", help="Start date YYYY-MM-DD")
    p.add_argument("--date-to", help="End date YYYY-MM-DD")
    p.add_argument("--fields", help="Comma-separated metrics")
    p.add_argument("--breakdowns", help="Comma-separated breakdowns")
    p.add_argument("--time-increment", help="Time granularity")
    p.add_argument("--json", action="store_true")

    # -- token-info --
    p = subparsers.add_parser("token-info", help="Show access token info and expiry")
    p.add_argument("--json", action="store_true")

    # -- token-extend --
    p = subparsers.add_parser("token-extend", help="Exchange token for long-lived (60 days)")
    p.add_argument("--json", action="store_true")

    # -------------------------------------------------------------------
    # Parse and dispatch
    # -------------------------------------------------------------------

    args = parser.parse_args()

    # Apply global account-id override
    if args.account_id:
        if not args.account_id.startswith("act_"):
            args.account_id = f"act_{args.account_id}"

    commands = {
        "account": cmd_account,
        "pages": cmd_pages,
        "campaigns": cmd_campaigns,
        "campaign-detail": cmd_campaign_detail,
        "campaign-create": cmd_campaign_create,
        "campaign-update": cmd_campaign_update,
        "campaign-duplicate": cmd_campaign_duplicate,
        "adsets": cmd_adsets,
        "adset-detail": cmd_adset_detail,
        "adset-create": cmd_adset_create,
        "adset-update": cmd_adset_update,
        "adset-duplicate": cmd_adset_duplicate,
        "ads": cmd_ads,
        "ad-detail": cmd_ad_detail,
        "ad-create": cmd_ad_create,
        "ad-update": cmd_ad_update,
        "ad-duplicate": cmd_ad_duplicate,
        "image-upload": cmd_image_upload,
        "video-upload": cmd_video_upload,
        "creative-create": cmd_creative_create,
        "creative-clone": cmd_creative_clone,
        "creatives": cmd_creatives,
        "creative-detail": cmd_creative_detail,
        "insights": cmd_insights,
        "insights-report": cmd_insights_report,
        "token-info": cmd_token_info,
        "token-extend": cmd_token_extend,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
