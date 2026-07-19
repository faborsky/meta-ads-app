"""Commands: ads, ad-detail/create/update/duplicate/delete, ad-review."""

from __future__ import annotations

import json

from metaads import api
from metaads.commands.common import (
    account_of,
    delete_with_brake,
    drop_deleted,
    status_filter_param,
)
from metaads.formatting import _die, _output_json, _truncate

AD_LIST_FIELDS = "id,name,adset_id,campaign_id,status,effective_status,creative{id,name,thumbnail_url},created_time"

AD_DETAIL_FIELDS = ("id,name,adset_id,campaign_id,status,effective_status,"
                    "creative{id,name,body,title,link_url,image_url,image_hash,thumbnail_url,"
                    "object_story_spec,call_to_action_type,asset_feed_spec,url_tags},"
                    "created_time,updated_time,tracking_specs,conversion_specs,"
                    "issues_info,ad_review_feedback{global,placement_specific}")

REVIEW_STATUSES = ["WITH_ISSUES", "DISAPPROVED", "PENDING_REVIEW", "IN_PROCESS"]


def cmd_ads(args) -> None:
    """List ads."""
    account_id = account_of(args)

    if args.adset_id:
        base_endpoint = f"{args.adset_id}/ads"
    elif args.campaign_id:
        base_endpoint = f"{args.campaign_id}/ads"
    else:
        base_endpoint = f"{account_id}/ads"

    params: dict = {"fields": AD_LIST_FIELDS, "limit": args.limit}
    params.update(status_filter_param(args.status))

    ads = api._paginate(base_endpoint, params, max_items=args.limit)
    ads = drop_deleted(ads, args.status)

    if args.json:
        _output_json(ads)
        return

    if not ads:
        print("No ads found.")
        return

    print(f"{'ID':<20} {'Name':<35} {'Status':<15} {'Creative ID':<18} {'Ad Set':<20} {'Campaign'}")
    print("-" * 130)
    for a in ads:
        creative = a.get("creative", {})
        print(f"{a['id']:<20} {_truncate(a.get('name', '---'), 33):<35} "
              f"{a.get('effective_status', '---'):<15} {creative.get('id', '---'):<18} "
              f"{a.get('adset_id', '---'):<20} {a.get('campaign_id', '---')}")


def cmd_ad_detail(args) -> None:
    """Show single ad details with full creative info and review feedback."""
    data = api._api_call("GET", str(args.ad_id), {"fields": AD_DETAIL_FIELDS})

    if args.json:
        _output_json(data)
        return

    print(f"Ad: {data.get('name', '---')}")
    print(f"  ID:              {data.get('id')}")
    print(f"  Ad Set ID:       {data.get('adset_id', '---')}")
    print(f"  Campaign ID:     {data.get('campaign_id', '---')}")
    print(f"  Status:          {data.get('status', '---')}")
    print(f"  Effective:       {data.get('effective_status', '---')}")

    creative = data.get("creative", {})
    if creative:
        print("  Creative:")
        print(f"    ID:            {creative.get('id', '---')}")
        print(f"    Name:          {creative.get('name', '---')}")
        if creative.get("body"):
            print(f"    Body:          {_truncate(creative['body'], 80)}")
        if creative.get("title"):
            print(f"    Title:         {creative['title']}")
        if creative.get("link_url"):
            print(f"    Link:          {creative['link_url']}")
        if creative.get("image_hash"):
            print(f"    Image hash:    {creative['image_hash']}")
        if creative.get("call_to_action_type"):
            print(f"    CTA:           {creative['call_to_action_type']}")

        afs = creative.get("asset_feed_spec")
        if afs:
            print("    Asset feed:")
            for key in ("bodies", "titles", "descriptions"):
                entries = afs.get(key, [])
                if entries:
                    print(f"      {key.capitalize()} ({len(entries)}):")
                    for e in entries:
                        print(f"        - {_truncate(e.get('text', ''), 70)}")
            if afs.get("images"):
                print(f"      Images: {len(afs['images'])}")
            if afs.get("videos"):
                print(f"      Videos: {len(afs['videos'])}")

    _print_review_info(data, indent="  ")

    print(f"  Created:         {data.get('created_time', '---')}")
    if data.get("updated_time"):
        print(f"  Updated:         {data['updated_time']}")


def _print_review_info(ad: dict, indent: str = "") -> None:
    issues = ad.get("issues_info") or []
    for issue in issues:
        print(f"{indent}⚠ Issue ({issue.get('level', '?')}): {issue.get('error_summary', '?')} — "
              f"{issue.get('error_message', '')}")
    feedback = ad.get("ad_review_feedback") or {}
    global_fb = feedback.get("global") or {}
    for reason, detail in global_fb.items():
        print(f"{indent}✖ Review (global): {reason}: {detail}")
    placement_fb = feedback.get("placement_specific") or {}
    for placement, reasons in placement_fb.items():
        if isinstance(reasons, dict):
            for reason, detail in reasons.items():
                print(f"{indent}✖ Review ({placement}): {reason}: {detail}")
        else:
            print(f"{indent}✖ Review ({placement}): {reasons}")


def cmd_ad_review(args) -> None:
    """Review check: single ad, or all ads in review/with issues across the account.

    Meta review is asynchronous (typically < 24 h) — run this after creating or
    swapping creatives.
    """
    if args.ad_id:
        data = api._api_call("GET", str(args.ad_id), {
            "fields": "id,name,status,effective_status,issues_info,"
                      "ad_review_feedback{global,placement_specific}",
        })
        if args.json:
            _output_json(data)
            return
        print(f"Ad {data.get('id')} \"{data.get('name', '---')}\": {data.get('effective_status', '?')}")
        _print_review_info(data, indent="  ")
        if not (data.get("issues_info") or data.get("ad_review_feedback")):
            print("  No issues or review feedback.")
        return

    account_id = account_of(args)
    statuses = [s.strip().upper() for s in args.statuses.split(",")] if args.statuses else REVIEW_STATUSES
    params = {
        "fields": "id,name,effective_status,adset_id,campaign_id,issues_info,"
                  "ad_review_feedback{global,placement_specific}",
        "effective_status": json.dumps(statuses),
        "limit": args.limit,
    }
    ads = api._paginate(f"{account_id}/ads", params, max_items=args.limit)

    if args.json:
        _output_json(ads)
        return

    if not ads:
        print(f"No ads with status in {statuses}. ✅")
        return

    by_status: dict[str, list] = {}
    for a in ads:
        by_status.setdefault(a.get("effective_status", "?"), []).append(a)

    print(f"Ads needing attention: {len(ads)}")
    for status, group in sorted(by_status.items()):
        print(f"\n{status} ({len(group)}):")
        for a in group:
            print(f"  {a['id']} \"{_truncate(a.get('name'), 50)}\" (campaign {a.get('campaign_id', '?')})")
            _print_review_info(a, indent="    ")


def cmd_ad_create(args) -> None:
    """Create a new ad (dry-run/validate by default)."""
    account_id = account_of(args)

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
        _die("ERROR: Either --creative-id or --creative-json is required.")

    if args.tracking_specs:
        params["tracking_specs"] = args.tracking_specs

    data, executed = api.mutate(f"{account_id}/ads", params, args.confirm)

    if args.json:
        _output_json({"executed": executed, "response": data})
        return
    api.print_mutation_result(data, executed, f"Ad created: ID {(data or {}).get('id')}")


def cmd_ad_update(args) -> None:
    """Update an existing ad (dry-run/validate by default)."""
    params: dict = {}

    if args.name:
        params["name"] = args.name
    if args.status:
        params["status"] = args.status
    if args.creative_id:
        params["creative"] = json.dumps({"creative_id": args.creative_id})

    if not params:
        _die("ERROR: No fields to update.")

    data, executed = api.mutate(str(args.ad_id), params, args.confirm)

    if args.json:
        _output_json({"executed": executed, "response": data})
        return
    done = f"Ad {args.ad_id} updated."
    if args.creative_id and executed:
        done += " (creative swap triggers re-review)"
    api.print_mutation_result(data, executed, done)


def cmd_ad_duplicate(args) -> None:
    """Duplicate an ad (PAUSED copy by default)."""
    params: dict = {"status_option": args.status_option}
    if args.adset_id:
        params["adset_id"] = args.adset_id
    if args.rename_suffix:
        params["rename_options"] = json.dumps({"rename_suffix": args.rename_suffix})

    data, executed = api.mutate(f"{args.ad_id}/copies", params, args.confirm,
                                validate_supported=False)

    if args.json:
        _output_json({"executed": executed, "response": data,
                      "plan": None if executed else params})
        return
    if executed:
        copied = data.get("copied_ad_id") or data.get("id")
        print(f"Ad duplicated: new ID {copied}")
    else:
        api.print_mutation_result(data, executed, "", plan={"copy_ad": args.ad_id, **params})


def cmd_ad_delete(args) -> None:
    """Delete an ad (permanent!). Only PAUSED/ARCHIVED unless --force."""
    delete_with_brake("ad", args.ad_id, args)
