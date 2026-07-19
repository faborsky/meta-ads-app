"""Commands: campaigns, campaign-detail/create/update/duplicate/delete, budget-schedule."""

from __future__ import annotations

import json

from metaads import api
from metaads.commands.common import (
    account_of,
    delete_with_brake,
    drop_deleted,
    parse_time_to_unix,
    status_filter_param,
)
from metaads.formatting import _budget_to_cents, _die, _format_budget, _output_json, _truncate

OBJECTIVES = [
    "OUTCOME_AWARENESS", "OUTCOME_TRAFFIC", "OUTCOME_ENGAGEMENT",
    "OUTCOME_LEADS", "OUTCOME_SALES", "OUTCOME_APP_PROMOTION",
]

BID_STRATEGIES = [
    "LOWEST_COST_WITHOUT_CAP", "LOWEST_COST_WITH_BID_CAP",
    "COST_CAP", "LOWEST_COST_WITH_MIN_ROAS",
]

CAMPAIGN_FIELDS = ("id,name,objective,status,effective_status,daily_budget,lifetime_budget,"
                   "budget_remaining,bid_strategy,buying_type,created_time,start_time,stop_time,"
                   "special_ad_categories")

CAMPAIGN_DETAIL_FIELDS = (CAMPAIGN_FIELDS +
                          ",updated_time,spend_cap,promoted_object,source_campaign_id,"
                          "issues_info,advantage_state_info,smart_promotion_type")


def cmd_campaigns(args) -> None:
    """List campaigns."""
    account_id = account_of(args)
    params: dict = {"fields": CAMPAIGN_FIELDS, "limit": args.limit}
    params.update(status_filter_param(args.status))

    campaigns = api._paginate(f"{account_id}/campaigns", params, max_items=args.limit)
    campaigns = drop_deleted(campaigns, args.status)

    if args.json:
        _output_json(campaigns)
        return

    if not campaigns:
        print("No campaigns found.")
        return

    print(f"{'ID':<20} {'Name':<40} {'Status':<12} {'Objective':<22} {'Daily Budget':<15} {'Lifetime Budget'}")
    print("-" * 130)
    for c in campaigns:
        daily = _format_budget(c.get("daily_budget")) if c.get("daily_budget") else "---"
        lifetime = _format_budget(c.get("lifetime_budget")) if c.get("lifetime_budget") else "---"
        print(f"{c['id']:<20} {_truncate(c.get('name', '---'), 38):<40} "
              f"{c.get('effective_status', '---'):<12} {c.get('objective', '---'):<22} "
              f"{daily:<15} {lifetime}")


def cmd_campaign_detail(args) -> None:
    """Show single campaign details."""
    data = api._api_call("GET", str(args.campaign_id), {"fields": CAMPAIGN_DETAIL_FIELDS})

    if args.json:
        _output_json(data)
        return

    print(f"Campaign: {data.get('name', '---')}")
    print(f"  ID:              {data.get('id')}")
    print(f"  Objective:       {data.get('objective', '---')}")
    print(f"  Status:          {data.get('status', '---')}")
    print(f"  Effective:       {data.get('effective_status', '---')}")
    print(f"  Bid strategy:    {data.get('bid_strategy', '---')}")
    print(f"  Buying type:     {data.get('buying_type', '---')}")
    if data.get("daily_budget"):
        print(f"  Daily budget:    {_format_budget(data['daily_budget'])}  (campaign-level = CBO)")
    if data.get("lifetime_budget"):
        print(f"  Lifetime budget: {_format_budget(data['lifetime_budget'])}  (campaign-level = CBO)")
    if data.get("budget_remaining"):
        print(f"  Budget remain:   {_format_budget(data['budget_remaining'])}")
    if data.get("spend_cap"):
        print(f"  Spend cap:       {_format_budget(data['spend_cap'])}")
    cats = data.get("special_ad_categories", [])
    print(f"  Special cats:    {', '.join(cats) if cats else 'none'}")

    adv = data.get("advantage_state_info")
    if adv:
        print(f"  Advantage+:      {adv.get('advantage_state', '---')}")
        for key in ("advantage_audience_state", "advantage_budget_state", "advantage_placement_state"):
            if adv.get(key):
                print(f"    {key.replace('advantage_', '').replace('_state', ''):<12} {adv[key]}")
    if data.get("smart_promotion_type"):
        print(f"  Smart promo:     {data['smart_promotion_type']}")

    issues = data.get("issues_info") or []
    for issue in issues:
        print(f"  ⚠ Issue ({issue.get('level', '?')}): {issue.get('error_summary', '?')} — "
              f"{issue.get('error_message', '')}")

    print(f"  Created:         {data.get('created_time', '---')}")
    if data.get("start_time"):
        print(f"  Start:           {data['start_time']}")
    if data.get("stop_time"):
        print(f"  Stop:            {data['stop_time']}")
    if data.get("updated_time"):
        print(f"  Updated:         {data['updated_time']}")


def cmd_campaign_create(args) -> None:
    """Create a new campaign (dry-run/validate by default; --confirm to write)."""
    account_id = account_of(args)

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
    else:
        # ABO campaigns (no campaign-level budget) must declare whether ad sets
        # may share 20 % of their budgets — the API rejects the create otherwise.
        if not args.daily_budget:
            params["is_adset_budget_sharing_enabled"] = args.adset_budget_sharing or "false"
    if args.bid_strategy:
        params["bid_strategy"] = args.bid_strategy
    if args.start_time:
        params["start_time"] = args.start_time
    if args.stop_time:
        params["stop_time"] = args.stop_time

    data, executed = api.mutate(f"{account_id}/campaigns", params, args.confirm)

    if args.json:
        _output_json({"executed": executed, "response": data})
        return
    api.print_mutation_result(data, executed, f"Campaign created: ID {(data or {}).get('id')}")


def cmd_campaign_update(args) -> None:
    """Update an existing campaign (dry-run/validate by default)."""
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
        _die("ERROR: No fields to update. Provide at least one field.")

    data, executed = api.mutate(str(args.campaign_id), params, args.confirm)

    if args.json:
        _output_json({"executed": executed, "response": data})
        return
    api.print_mutation_result(data, executed, f"Campaign {args.campaign_id} updated.")


def cmd_campaign_duplicate(args) -> None:
    """Duplicate a campaign (PAUSED copy by default)."""
    params: dict = {"status_option": args.status_option}
    if args.deep_copy:
        params["deep_copy"] = "true"
    if args.rename_suffix:
        params["rename_options"] = json.dumps({"rename_suffix": args.rename_suffix})

    data, executed = api.mutate(f"{args.campaign_id}/copies", params, args.confirm,
                                validate_supported=False)

    if args.json:
        _output_json({"executed": executed, "response": data,
                      "plan": None if executed else params})
        return
    if executed:
        copied = data.get("copied_campaign_id") or data.get("id")
        print(f"Campaign duplicated: new ID {copied}")
        if data.get("ad_object_ids"):
            print(f"  Copied objects: {len(data['ad_object_ids'])}")
    else:
        api.print_mutation_result(data, executed, "", plan={"copy_campaign": args.campaign_id, **params})


def cmd_campaign_delete(args) -> None:
    """Delete a campaign (permanent!). Only PAUSED/ARCHIVED unless --force."""
    delete_with_brake("campaign", args.campaign_id, args)


def cmd_budget_schedule(args) -> None:
    """List or create budget schedules (high-demand periods) on a campaign/ad set."""
    object_id = args.campaign_id or args.adset_id
    if not object_id:
        _die("ERROR: --campaign-id or --adset-id is required.")

    if args.list:
        data = api._api_call("GET", f"{object_id}/budget_schedules",
                             {"fields": "id,time_start,time_end,budget_value,budget_value_type,recurrence_type"})
        rows = data.get("data", [])
        if args.json:
            _output_json(rows)
            return
        if not rows:
            print("No budget schedules.")
            return
        for r in rows:
            print(f"{r.get('id')}: {r.get('time_start')} → {r.get('time_end')}  "
                  f"value={r.get('budget_value')} ({r.get('budget_value_type')})")
        return

    if not (args.start and args.end and args.value is not None):
        _die("ERROR: --start, --end and --value are required to create a schedule (or use --list).")

    value = args.value
    if args.value_type == "ABSOLUTE":
        value = _budget_to_cents(value)
    else:
        value = int(value)  # MULTIPLIER: percentage of base budget (e.g. 200 = 2×)

    params = {
        "time_start": parse_time_to_unix(args.start),
        "time_end": parse_time_to_unix(args.end),
        "budget_value": value,
        "budget_value_type": args.value_type,
    }

    data, executed = api.mutate(f"{object_id}/budget_schedules", params, args.confirm,
                                validate_supported=False)

    if args.json:
        _output_json({"executed": executed, "response": data,
                      "plan": None if executed else params})
        return
    if executed:
        print(f"Budget schedule created: ID {(data or {}).get('id')}")
    else:
        api.print_mutation_result(data, executed, "", plan=params)
