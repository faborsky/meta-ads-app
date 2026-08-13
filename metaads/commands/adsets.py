"""Commands: adsets, adset-detail/create/update/duplicate/delete."""

from __future__ import annotations

import json

from metaads import api
from metaads.commands.common import (
    account_of,
    delete_with_brake,
    drop_deleted,
    parse_genders,
    parse_json_arg,
    status_filter_param,
)
from metaads.formatting import _budget_to_cents, _die, _format_budget, _output_json, _truncate

OPTIMIZATION_GOALS = [
    "REACH", "IMPRESSIONS", "LINK_CLICKS", "LANDING_PAGE_VIEWS",
    "OFFSITE_CONVERSIONS", "CONVERSATIONS", "LEAD_GENERATION",
    "VALUE", "APP_INSTALLS", "QUALITY_LEAD", "ENGAGED_USERS",
    "THRUPLAY", "POST_ENGAGEMENT", "PAGE_LIKES",
]

BID_STRATEGIES = [
    "LOWEST_COST_WITHOUT_CAP", "LOWEST_COST_WITH_BID_CAP",
    "COST_CAP", "LOWEST_COST_WITH_MIN_ROAS",
]

ADSET_FIELDS = ("id,name,campaign_id,status,effective_status,daily_budget,lifetime_budget,"
                "budget_remaining,optimization_goal,billing_event,bid_amount,bid_strategy,"
                "start_time,end_time,created_time,targeting")

ADSET_DETAIL_FIELDS = (ADSET_FIELDS +
                       ",promoted_object,updated_time,destination_type,attribution_spec,"
                       "issues_info,dsa_payor,dsa_beneficiary,is_dynamic_creative,bid_constraints")


def _split_csv(value: str | None) -> list[str]:
    return [v.strip() for v in value.split(",")] if value else []


def build_targeting(args, base: dict | None = None) -> dict:
    """Merge convenience targeting flags into a targeting spec (base = --targeting JSON)."""
    targeting: dict = dict(base) if base else {}

    if getattr(args, "countries", None):
        geo = targeting.setdefault("geo_locations", {})
        geo["countries"] = _split_csv(args.countries)
    if getattr(args, "age_min", None):
        targeting["age_min"] = args.age_min
    if getattr(args, "age_max", None):
        targeting["age_max"] = args.age_max
    if getattr(args, "genders", None):
        targeting["genders"] = parse_genders(args.genders)
    if getattr(args, "advantage_audience", None) is not None:
        ta = targeting.setdefault("targeting_automation", {})
        ta["advantage_audience"] = args.advantage_audience
    for flag, key in (
        ("publisher_platforms", "publisher_platforms"),
        ("facebook_positions", "facebook_positions"),
        ("instagram_positions", "instagram_positions"),
        ("device_platforms", "device_platforms"),
    ):
        val = getattr(args, flag, None)
        if val:
            targeting[key] = _split_csv(val)

    return targeting


def _print_targeting_summary(targeting: dict) -> None:
    geo = targeting.get("geo_locations", {})
    countries = geo.get("countries", [])
    if countries:
        print(f"    Countries:     {', '.join(countries)}")
    cities = geo.get("cities", [])
    if cities:
        city_names = [f"{c.get('name', c.get('key', '?'))}"
                      + (f" +{c.get('radius')}{c.get('distance_unit', 'mi')}" if c.get("radius") else "")
                      for c in cities]
        print(f"    Cities:        {', '.join(city_names)}")
    custom_locs = geo.get("custom_locations", [])
    if custom_locs:
        print(f"    Custom locs:   {len(custom_locs)} (radius targeting)")
    age_min = targeting.get("age_min")
    age_max = targeting.get("age_max")
    if age_min or age_max:
        print(f"    Age:           {age_min or '?'}-{age_max or '?'}")
    genders = targeting.get("genders", [])
    if genders:
        gender_map = {1: "Male", 2: "Female"}
        print(f"    Genders:       {', '.join(gender_map.get(g, str(g)) for g in genders)}")
    for spec in targeting.get("flexible_spec", []):
        for key, vals in spec.items():
            names = [v.get("name", "?") for v in vals] if isinstance(vals, list) else [str(vals)]
            print(f"    {key}: {', '.join(names)}")
    exclusions = targeting.get("exclusions", {})
    if exclusions:
        print(f"    Exclusions:    {json.dumps(exclusions, ensure_ascii=False)[:100]}")
    custom_audiences = targeting.get("custom_audiences", [])
    if custom_audiences:
        print(f"    Custom audiences: {len(custom_audiences)}")
    excluded = targeting.get("excluded_custom_audiences", [])
    if excluded:
        print(f"    Excluded audiences: {len(excluded)}")
    ta = targeting.get("targeting_automation", {})
    if ta:
        print(f"    Advantage+ audience: {ta.get('advantage_audience', '---')}")
    platforms = targeting.get("publisher_platforms")
    if platforms:
        print(f"    Platforms:     {', '.join(platforms)} (manual placements)")
        for key in ("facebook_positions", "instagram_positions", "device_platforms"):
            if targeting.get(key):
                print(f"    {key}: {', '.join(targeting[key])}")
    else:
        print("    Placements:    Advantage+ (automatic)")


def cmd_adsets(args) -> None:
    """List ad sets."""
    account_id = account_of(args)
    base_endpoint = f"{args.campaign_id}/adsets" if args.campaign_id else f"{account_id}/adsets"

    params: dict = {"fields": ADSET_FIELDS, "limit": args.limit}
    params.update(status_filter_param(args.status))

    adsets = api._paginate(base_endpoint, params, max_items=args.limit)
    adsets = drop_deleted(adsets, args.status)

    if args.json:
        _output_json(adsets)
        return

    if not adsets:
        print("No ad sets found.")
        return

    print(f"{'ID':<20} {'Name':<35} {'Status':<12} {'Opt Goal':<20} {'Daily':<12} {'Lifetime':<12} {'Campaign'}")
    print("-" * 130)
    for a in adsets:
        daily = _format_budget(a.get("daily_budget")) if a.get("daily_budget") else "---"
        lifetime = _format_budget(a.get("lifetime_budget")) if a.get("lifetime_budget") else "---"
        print(f"{a['id']:<20} {_truncate(a.get('name', '---'), 33):<35} "
              f"{a.get('effective_status', '---'):<12} {_truncate(a.get('optimization_goal', '---'), 18):<20} "
              f"{daily:<12} {lifetime:<12} {a.get('campaign_id', '---')}")


def cmd_adset_detail(args) -> None:
    """Show single ad set details."""
    data = api._api_call("GET", str(args.adset_id), {"fields": ADSET_DETAIL_FIELDS})

    if args.json:
        _output_json(data)
        return

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
    if data.get("bid_constraints"):
        print(f"  Bid constraints: {json.dumps(data['bid_constraints'])}")
    if data.get("daily_budget"):
        print(f"  Daily budget:    {_format_budget(data['daily_budget'])}")
    if data.get("lifetime_budget"):
        print(f"  Lifetime budget: {_format_budget(data['lifetime_budget'])}")
    if data.get("budget_remaining"):
        print(f"  Budget remain:   {_format_budget(data['budget_remaining'])}")
    if data.get("destination_type"):
        print(f"  Destination:     {data['destination_type']}")
    if data.get("is_dynamic_creative"):
        print(f"  Dynamic creative: {data['is_dynamic_creative']}")
    if data.get("dsa_payor") or data.get("dsa_beneficiary"):
        print(f"  DSA payor/beneficiary: {data.get('dsa_payor', '---')} / {data.get('dsa_beneficiary', '---')}")

    targeting = data.get("targeting", {})
    if targeting:
        print("  Targeting:")
        _print_targeting_summary(targeting)

    promoted = data.get("promoted_object", {})
    if promoted:
        print(f"  Promoted object: {json.dumps(promoted)}")

    issues = data.get("issues_info") or []
    for issue in issues:
        print(f"  ⚠ Issue ({issue.get('level', '?')}): {issue.get('error_summary', '?')} — "
              f"{issue.get('error_message', '')}")

    print(f"  Created:         {data.get('created_time', '---')}")
    if data.get("start_time"):
        print(f"  Start:           {data['start_time']}")
    if data.get("end_time"):
        print(f"  End:             {data['end_time']}")


def cmd_adset_create(args) -> None:
    """Create a new ad set (dry-run/validate by default)."""
    account_id = account_of(args)

    if not args.daily_budget and not args.lifetime_budget:
        # CBO campaigns hold the budget at campaign level — allow no budget
        # only when explicitly flagged.
        if not args.cbo:
            _die("ERROR: --daily-budget or --lifetime-budget required "
                 "(or --cbo when the campaign holds the budget).")

    base = parse_json_arg(args.targeting, "--targeting") or {}
    targeting = build_targeting(args, base)
    if not targeting.get("geo_locations"):
        _die('ERROR: targeting must include geo_locations (use --countries CZ or --targeting JSON).')

    params: dict = {
        "campaign_id": args.campaign_id,
        "name": args.name,
        "optimization_goal": args.optimization_goal,
        "billing_event": args.billing_event,
        "targeting": json.dumps(targeting),
        "status": args.status,
    }

    if args.daily_budget or args.lifetime_budget or args.bid_amount:
        api.budget_currency_guard(account_id)
    if args.daily_budget:
        params["daily_budget"] = _budget_to_cents(args.daily_budget)
    if args.lifetime_budget:
        params["lifetime_budget"] = _budget_to_cents(args.lifetime_budget)
    if args.bid_amount:
        params["bid_amount"] = _budget_to_cents(args.bid_amount)
    if args.bid_strategy:
        params["bid_strategy"] = args.bid_strategy
    if args.roas_floor:
        params["bid_constraints"] = json.dumps({"roas_average_floor": int(args.roas_floor * 10000)})
    if args.start_time:
        params["start_time"] = args.start_time
    if args.end_time:
        params["end_time"] = args.end_time
    if args.promoted_object:
        params["promoted_object"] = args.promoted_object
    if args.dsa_payor:
        params["dsa_payor"] = args.dsa_payor
    if args.dsa_beneficiary:
        params["dsa_beneficiary"] = args.dsa_beneficiary

    data, executed = api.mutate(f"{account_id}/adsets", params, args.confirm)

    if args.json:
        _output_json({"executed": executed, "response": data, "targeting": targeting})
        return
    api.print_mutation_result(data, executed, f"Ad set created: ID {(data or {}).get('id')}")


def cmd_adset_update(args) -> None:
    """Update an existing ad set (dry-run/validate by default)."""
    params: dict = {}

    if any(v is not None for v in (args.daily_budget, args.lifetime_budget, args.bid_amount)):
        api.budget_currency_guard(account_of(args))
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
    if args.bid_strategy:
        params["bid_strategy"] = args.bid_strategy
    if args.roas_floor:
        params["bid_constraints"] = json.dumps({"roas_average_floor": int(args.roas_floor * 10000)})
    if args.end_time:
        params["end_time"] = args.end_time
    if args.dsa_payor:
        params["dsa_payor"] = args.dsa_payor
    if args.dsa_beneficiary:
        params["dsa_beneficiary"] = args.dsa_beneficiary

    # Targeting: --targeting replaces the whole spec; convenience flags without
    # --targeting do read-merge-write on the current spec.
    has_targeting_flags = any(
        getattr(args, f, None) not in (None, "")
        for f in ("countries", "age_min", "age_max", "genders", "advantage_audience",
                  "publisher_platforms", "facebook_positions", "instagram_positions",
                  "device_platforms")
    )
    if args.targeting:
        params["targeting"] = json.dumps(build_targeting(args, parse_json_arg(args.targeting, "--targeting")))
    elif has_targeting_flags:
        current = api._api_call("GET", str(args.adset_id), {"fields": "targeting"}).get("targeting", {})
        params["targeting"] = json.dumps(build_targeting(args, current))

    if not params:
        _die("ERROR: No fields to update.")

    data, executed = api.mutate(str(args.adset_id), params, args.confirm)

    if args.json:
        _output_json({"executed": executed, "response": data})
        return
    api.print_mutation_result(data, executed, f"Ad set {args.adset_id} updated.")


def cmd_adset_duplicate(args) -> None:
    """Duplicate an ad set (PAUSED copy by default)."""
    params: dict = {"status_option": args.status_option}
    if args.campaign_id:
        params["campaign_id"] = args.campaign_id
    if args.deep_copy:
        params["deep_copy"] = "true"
    if args.rename_suffix:
        params["rename_options"] = json.dumps({"rename_suffix": args.rename_suffix})

    data, executed = api.mutate(f"{args.adset_id}/copies", params, args.confirm,
                                validate_supported=False)

    if args.json:
        _output_json({"executed": executed, "response": data,
                      "plan": None if executed else params})
        return
    if executed:
        copied = data.get("copied_adset_id") or data.get("id")
        print(f"Ad set duplicated: new ID {copied}")
    else:
        api.print_mutation_result(data, executed, "", plan={"copy_adset": args.adset_id, **params})


def cmd_adset_delete(args) -> None:
    """Delete an ad set (permanent!). Only PAUSED/ARCHIVED unless --force."""
    delete_with_brake("adset", args.adset_id, args)
