"""Command: activities — account change history (who changed what)."""

from __future__ import annotations

from metaads import api
from metaads.commands.common import account_of
from metaads.formatting import _output_json, _truncate

CATEGORIES = ["ACCOUNT", "AD", "AD_SET", "AUDIENCE", "BID", "BUDGET",
              "CAMPAIGN", "CREDIT", "PAYMENT_METHOD", "TARGETING"]

ACTIVITY_FIELDS = ("event_type,translated_event_type,event_time,actor_id,actor_name,"
                   "application_name,object_id,object_name,object_type,extra_data")


def cmd_activities(args) -> None:
    """List account activity log entries (default window: last 7 days)."""
    account_id = account_of(args)

    params: dict = {"fields": ACTIVITY_FIELDS, "limit": args.limit}
    if args.since:
        params["since"] = args.since
    if args.until:
        params["until"] = args.until
    if args.category:
        params["category"] = args.category
    if args.object_id:
        params["oid"] = args.object_id

    rows = api._paginate(f"{account_id}/activities", params, max_items=args.limit)

    if args.json:
        _output_json(rows)
        return

    if not rows:
        print("No activity entries (default window is ~1 week; use --since).")
        return

    print(f"{'Time':<20} {'Actor':<20} {'Event':<35} {'Object'}")
    print("-" * 120)
    for r in rows:
        event = r.get("translated_event_type") or r.get("event_type", "?")
        obj = r.get("object_name") or r.get("object_id", "---")
        print(f"{(r.get('event_time') or '')[:19]:<20} {_truncate(r.get('actor_name'), 18):<20} "
              f"{_truncate(event, 33):<35} {_truncate(obj, 40)}")
        extra = r.get("extra_data")
        if args.verbose and extra:
            print(f"{'':<20} {_truncate(extra, 95)}")
