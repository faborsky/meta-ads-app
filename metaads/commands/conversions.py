"""Commands: pixels, custom-conversions (read-only)."""

from __future__ import annotations

from metaads import api
from metaads.commands.common import account_of
from metaads.formatting import _output_json, _truncate

PIXEL_FIELDS = "id,name,creation_time,last_fired_time,data_use_setting"
CONVERSION_FIELDS = ("id,name,custom_event_type,rule,pixel{id,name},is_archived,"
                     "is_unavailable,default_conversion_value,creation_time,last_fired_time")


def cmd_pixels(args) -> None:
    """List ad account pixels (datasets)."""
    account_id = account_of(args)
    pixels = api._paginate(f"{account_id}/adspixels", {"fields": PIXEL_FIELDS}, max_items=100)

    if args.json:
        _output_json(pixels)
        return

    if not pixels:
        print("No pixels found.")
        return

    print(f"{'Pixel ID':<20} {'Name':<35} {'Last fired':<22} {'Data use'}")
    print("-" * 100)
    for p in pixels:
        print(f"{p['id']:<20} {_truncate(p.get('name'), 33):<35} "
              f"{(p.get('last_fired_time') or '---')[:19]:<22} {p.get('data_use_setting', '---')}")


def cmd_custom_conversions(args) -> None:
    """List custom conversions (max 100 per account)."""
    account_id = account_of(args)
    convs = api._paginate(f"{account_id}/customconversions",
                          {"fields": CONVERSION_FIELDS, "limit": args.limit},
                          max_items=args.limit)

    if args.json:
        _output_json(convs)
        return

    if not convs:
        print("No custom conversions found.")
        return

    print(f"{'ID':<20} {'Name':<32} {'Event':<15} {'Pixel':<20} {'Last fired':<22} {'Flags'}")
    print("-" * 130)
    for c in convs:
        flags = []
        if c.get("is_archived"):
            flags.append("archived")
        if c.get("is_unavailable"):
            flags.append("UNAVAILABLE(policy)")
        pixel = (c.get("pixel") or {}).get("id", "---")
        print(f"{c['id']:<20} {_truncate(c.get('name'), 30):<32} "
              f"{_truncate(c.get('custom_event_type'), 13):<15} {pixel:<20} "
              f"{(c.get('last_fired_time') or '---')[:19]:<22} {','.join(flags) or '---'}")
