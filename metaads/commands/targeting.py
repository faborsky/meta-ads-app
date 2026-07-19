"""Commands: targeting search — interests, geo, locales.

All hit GET /search with a type param; results feed --targeting specs.
"""

from __future__ import annotations

import json

from metaads import api
from metaads.formatting import _output_json, _truncate


def _audience_bounds(row: dict) -> str:
    low = row.get("audience_size_lower_bound")
    high = row.get("audience_size_upper_bound")
    if low is None:
        return "---"
    return f"{low:,}–{high:,}" if high else f"{low:,}"


def cmd_interest_search(args) -> None:
    """Search detailed-targeting interests by keyword."""
    data = api._api_call("GET", "search", {
        "type": "adinterest", "q": args.q, "limit": args.limit,
    })
    rows = data.get("data", [])

    if args.json:
        _output_json(rows)
        return
    if not rows:
        print("No interests found.")
        return
    print(f"{'ID':<20} {'Name':<40} {'Audience':<25} Path")
    print("-" * 120)
    for r in rows:
        path = " > ".join(r.get("path", []))
        print(f"{r['id']:<20} {_truncate(r.get('name'), 38):<40} {_audience_bounds(r):<25} {_truncate(path, 40)}")


def cmd_interest_suggest(args) -> None:
    """Suggest related interests for a list of interest names."""
    names = [n.strip() for n in args.interests.split(",")]
    data = api._api_call("GET", "search", {
        "type": "adinterestsuggestion",
        "interest_list": json.dumps(names),
        "limit": args.limit,
    })
    rows = data.get("data", [])

    if args.json:
        _output_json(rows)
        return
    if not rows:
        print("No suggestions.")
        return
    print(f"{'ID':<20} {'Name':<40} {'Audience'}")
    print("-" * 90)
    for r in rows:
        print(f"{r['id']:<20} {_truncate(r.get('name'), 38):<40} {_audience_bounds(r)}")


def cmd_interest_validate(args) -> None:
    """Validate interest names for use in targeting specs."""
    names = [n.strip() for n in args.interests.split(",")]
    data = api._api_call("GET", "search", {
        "type": "adinterestvalid",
        "interest_list": json.dumps(names),
    })
    rows = data.get("data", [])

    if args.json:
        _output_json(rows)
        return
    for r in rows:
        mark = "✅" if r.get("valid") else "✖"
        print(f"{mark} {r.get('name', '?')} (id {r.get('id', '---')}, audience {_audience_bounds(r)})")


def cmd_geo_search(args) -> None:
    """Search geo targeting keys (countries, regions, cities, zips)."""
    params: dict = {"type": "adgeolocation", "q": args.q, "limit": args.limit}
    if args.location_types:
        params["location_types"] = json.dumps([t.strip() for t in args.location_types.split(",")])
    if args.country_code:
        params["country_code"] = args.country_code
    data = api._api_call("GET", "search", params)
    rows = data.get("data", [])

    if args.json:
        _output_json(rows)
        return
    if not rows:
        print("No locations found.")
        return
    print(f"{'Key':<15} {'Name':<35} {'Type':<12} {'Country':<8} Region")
    print("-" * 100)
    for r in rows:
        print(f"{str(r.get('key', '?')):<15} {_truncate(r.get('name'), 33):<35} "
              f"{r.get('type', '?'):<12} {r.get('country_code', '---'):<8} {r.get('region', '---')}")


def cmd_locale_search(args) -> None:
    """Search locale keys for language targeting (targeting.locales)."""
    data = api._api_call("GET", "search", {
        "type": "adlocale", "q": args.q, "limit": args.limit,
    })
    rows = data.get("data", [])

    if args.json:
        _output_json(rows)
        return
    if not rows:
        print("No locales found.")
        return
    for r in rows:
        print(f"{r.get('key', '?'):<8} {r.get('name', '?')}")
