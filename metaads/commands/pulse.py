"""Command: pulse — account-wide digest in a handful of API calls.

Sections: account state, spend/results with deltas vs the previous window,
top campaign movers, ads needing review attention, API usage, token expiry.
~6 read calls total.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from metaads import api
from metaads.commands.common import account_of
from metaads.formatting import _fmt_delta, _output_json, _truncate

ACCOUNT_METRICS = "spend,impressions,clicks,ctr,cpc,cpm,reach,actions"
REVIEW_STATUSES = ["WITH_ISSUES", "DISAPPROVED", "PENDING_REVIEW"]
TOP_MOVERS = 5


def _window_insights(object_id: str, since: date, until: date, level: str | None = None,
                     fields: str = ACCOUNT_METRICS, limit: int = 500) -> list:
    params: dict = {
        "fields": fields,
        "time_range": json.dumps({"since": since.isoformat(), "until": until.isoformat()}),
        "limit": limit,
    }
    if level:
        params["level"] = level
    return api._api_call("GET", f"{object_id}/insights", params).get("data", [])


def _actions_map(row: dict) -> dict[str, float]:
    return {a.get("action_type", "?"): float(a.get("value", 0)) for a in row.get("actions", [])}


def cmd_pulse(args) -> None:
    """Account digest: deltas, movers, review issues, limits, token."""
    account_id = account_of(args)
    days = args.days

    today = date.today()
    cur_since, cur_until = today - timedelta(days=days), today - timedelta(days=1)
    prev_since, prev_until = today - timedelta(days=2 * days), today - timedelta(days=days + 1)

    acct = api._api_call("GET", account_id, {
        "fields": "name,currency,account_status,amount_spent,spend_cap",
    })
    currency = acct.get("currency", "")

    cur_rows = _window_insights(account_id, cur_since, cur_until)
    prev_rows = _window_insights(account_id, prev_since, prev_until)
    cur = cur_rows[0] if cur_rows else {}
    prev = prev_rows[0] if prev_rows else {}

    camp_fields = "campaign_id,campaign_name,spend"
    cur_camps = _window_insights(account_id, cur_since, cur_until, level="campaign", fields=camp_fields)
    prev_camps = _window_insights(account_id, prev_since, prev_until, level="campaign", fields=camp_fields)
    prev_by_id = {c.get("campaign_id"): c for c in prev_camps}

    movers = []
    seen_ids = set()
    for c in cur_camps:
        cid = c.get("campaign_id")
        seen_ids.add(cid)
        cur_spend = float(c.get("spend", 0))
        prev_spend = float(prev_by_id.get(cid, {}).get("spend", 0))
        movers.append({"id": cid, "name": c.get("campaign_name", "?"),
                       "spend": cur_spend, "prev_spend": prev_spend,
                       "delta": cur_spend - prev_spend})
    for c in prev_camps:  # campaigns that stopped spending entirely
        if c.get("campaign_id") not in seen_ids:
            prev_spend = float(c.get("spend", 0))
            movers.append({"id": c.get("campaign_id"), "name": c.get("campaign_name", "?"),
                           "spend": 0.0, "prev_spend": prev_spend, "delta": -prev_spend})
    movers.sort(key=lambda m: abs(m["delta"]), reverse=True)
    movers = movers[:TOP_MOVERS]

    review_ads = api._paginate(f"{account_id}/ads", {
        "fields": "id,name,effective_status,issues_info",
        "effective_status": json.dumps(REVIEW_STATUSES),
        "limit": 100,
    }, max_items=100)
    review_counts: dict[str, int] = {}
    for a in review_ads:
        review_counts[a.get("effective_status", "?")] = review_counts.get(a.get("effective_status", "?"), 0) + 1

    usage = api._load_usage()
    token_days = api.token_days_left()

    cur_actions = _actions_map(cur)
    prev_actions = _actions_map(prev)
    top_actions = sorted(cur_actions.items(), key=lambda kv: kv[1], reverse=True)[:5]

    if args.json:
        _output_json({
            "account": acct,
            "window_days": days,
            "current_window": {"since": cur_since.isoformat(), "until": cur_until.isoformat(), **cur},
            "previous_window": {"since": prev_since.isoformat(), "until": prev_until.isoformat(), **prev},
            "campaign_movers": movers,
            "review": {"counts": review_counts, "ads": review_ads},
            "api_usage": usage,
            "token_days_left": token_days,
        })
        return

    print(f"=== PULSE: {acct.get('name', account_id)} "
          f"({cur_since} → {cur_until} vs previous {days}d) — amounts in {currency} ===\n")

    print("Account totals:")
    for key, label in (("spend", "Spend"), ("impressions", "Impressions"), ("clicks", "Clicks"),
                       ("ctr", "CTR"), ("cpc", "CPC"), ("cpm", "CPM"), ("reach", "Reach")):
        cur_v = float(cur.get(key, 0) or 0)
        prev_v = float(prev.get(key, 0) or 0)
        if cur_v or prev_v:
            print(f"  {label:<13} {_fmt_delta(cur_v, prev_v)}")

    if top_actions:
        print("\nActions (top types):")
        for action_type, value in top_actions:
            print(f"  {_truncate(action_type, 40):<42} {_fmt_delta(value, prev_actions.get(action_type, 0))}")

    if movers:
        print(f"\nTop campaign movers (spend Δ, {currency}):")
        for m in movers:
            sign = "+" if m["delta"] >= 0 else ""
            print(f"  {sign}{m['delta']:.2f}  {_truncate(m['name'], 45)} "
                  f"({m['prev_spend']:.2f} → {m['spend']:.2f})")

    if review_counts:
        print("\n⚠ Ads needing attention:")
        for status, count in sorted(review_counts.items()):
            print(f"  {status}: {count}")
        print("  → detail: ad-review")
    else:
        print("\nReview: no ads with issues ✅")

    print("\nAPI usage:")
    if usage:
        for acc, entry in usage.items():
            print(f"  {acc}: {entry.get('pct', 0):.0f}% (tier {entry.get('tier') or '?'})")
    else:
        print("  no recent data")

    if token_days is not None:
        marker = " ⚠ RUN token-extend" if token_days < 7 else ""
        print(f"Token: expires in {token_days} day(s){marker}")
