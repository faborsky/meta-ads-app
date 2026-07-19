"""Commands: account, pages, api-limits."""

from __future__ import annotations

import time

from metaads import api
from metaads.commands.common import account_of
from metaads.formatting import _format_budget, _output_json, _truncate

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


def cmd_account(args) -> None:
    """Show ad account info."""
    account_id = account_of(args)
    fields = "name,account_id,account_status,currency,timezone_name,balance,amount_spent,spend_cap,min_daily_budget"
    data = api._api_call("GET", account_id, {"fields": fields})

    if args.json:
        _output_json(data)
        return

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


def cmd_pages(args) -> None:
    """List Facebook pages available for ad creatives."""
    account_id = account_of(args)
    fields = "id,name,instagram_business_account{id,name,username}"

    pages = api._paginate(f"{account_id}/promote_pages", {"fields": fields}, max_items=200)

    if args.json:
        _output_json(pages)
        return

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


def cmd_api_limits(args) -> None:
    """Show current API usage per account (refreshed via a cheap GET)."""
    account_id = account_of(args)
    # Cheap call whose response headers refresh the persisted usage data
    api._api_call("GET", account_id, {"fields": "id"})

    usage = api._load_usage()
    token_days = api.token_days_left()

    if args.json:
        _output_json({
            "usage": usage,
            "access_tier": api._access_tier,
            "token_days_left": token_days,
            "hard_stop_threshold_pct": api.USAGE_HARD_STOP,
        })
        return

    print("API usage (from X-Business-Use-Case-Usage headers, hourly windows):")
    if not usage:
        print("  no data yet — run any command against the account first")
    for acc, entry in usage.items():
        age = int(time.time() - entry.get("ts", 0))
        tier = entry.get("tier") or "?"
        print(f"  {acc}: {entry.get('pct', 0):.0f}% of limit (tier {tier}, measured {age}s ago)")

    print(f"\nHard stop: CLI refuses calls above {api.USAGE_HARD_STOP}% recent usage "
          "(override: METAADS_IGNORE_USAGE_GUARD=1)")
    if token_days is not None:
        print(f"Token expires in: {token_days} day(s)")
    print("""
Limits (Marketing API, per app + ad account, hourly):
  development tier:  300 + 40 × active ads   calls/hour
  standard tier:     100 000 + 40 × active ads calls/hour
  mutations:         100 QPS (error 613/5044001)
Error codes: 17 user limit | 32 page limit | 613 custom/QPS | 80000/80004 BUC throttle
No proactive usage endpoint exists — usage comes from response headers only.""")
