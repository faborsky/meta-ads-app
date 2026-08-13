"""Helpers shared across command modules."""

from __future__ import annotations

import json

from metaads import api
from metaads.formatting import _die, _err, _output_json

# Deleting is permanent on Meta (ARCHIVED is the half-way trash can that stays
# queryable). The brake: only PAUSED/ARCHIVED entities may be deleted.
DELETABLE_STATUSES = ("PAUSED", "ARCHIVED")


def account_of(args) -> str:
    return args.account_id or api.META_AD_ACCOUNT_ID


def parse_json_arg(value: str | None, flag: str):
    """json.loads a CLI flag value with a clean error instead of a traceback."""
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError as e:
        _die(f"ERROR: {flag} is not valid JSON ({e}).\n  Got: {value[:120]}")


def parse_genders(value: str) -> list[int]:
    """Parse --genders '1,2' with a clean error for '--genders male'."""
    result = []
    for g in value.split(","):
        g = g.strip()
        if g not in ("1", "2"):
            _die(f"ERROR: --genders takes 1 (male) and/or 2 (female), comma-separated "
                 f"— e.g. --genders 1,2 (got '{g}').")
        result.append(int(g))
    return result


def status_filter_param(status: str | None) -> dict:
    """Build the `filtering` param for an effective_status filter."""
    if not status:
        return {}
    return {
        "filtering": json.dumps([
            {"field": "effective_status", "operator": "IN", "value": [status.upper()]}
        ])
    }


def drop_deleted(rows: list, status_arg: str | None) -> list:
    """Hide DELETED rows from listings unless explicitly requested.

    Meta keeps deleted entities in edge listings; showing them by default is
    noise (and confused past sessions).
    """
    if status_arg and status_arg.upper() == "DELETED":
        return rows
    return [r for r in rows if r.get("effective_status") != "DELETED" and r.get("status") != "DELETED"]


def delete_with_brake(kind: str, object_id: str, args) -> None:
    """Shared delete flow: plan without --confirm; refuse non-PAUSED without --force.

    DELETE has no validate_only — the dry-run is a local plan (one status read,
    no mutation).
    """
    data = api._api_call("GET", str(object_id), {"fields": "name,status,effective_status"})
    name = data.get("name", "---")
    status = data.get("status", "?")
    effective = data.get("effective_status", "?")

    if not args.confirm:
        if args.json:
            _output_json({
                "executed": False,
                "would_delete": {"kind": kind, "id": str(object_id), "name": name,
                                 "status": status, "effective_status": effective},
                "note": "DELETE is permanent; consider ARCHIVED. Add --confirm to execute.",
            })
            return
        print(f"DRY-RUN: would DELETE {kind} {object_id} \"{name}\" (status {status}, effective {effective}).")
        print("⚠ DELETE is permanent. Consider ARCHIVED instead "
              f"({kind}-update --status ARCHIVED --confirm) — archived objects stay queryable.")
        print("Add --confirm to execute.")
        return

    if status not in DELETABLE_STATUSES and not args.force:
        _die(
            f"ERROR: {kind} {object_id} \"{name}\" has status {status} — refusing to delete a "
            f"non-PAUSED entity. Pause it first, or use --force."
        )

    api._api_call("DELETE", str(object_id), {})
    result = {"executed": True, "deleted": str(object_id), "kind": kind, "name": name}
    if args.json:
        _output_json(result)
    else:
        print(f"{kind} {object_id} \"{name}\" deleted (permanent).")
        _err("Note: Meta keeps deleted objects readable by direct ID for stats (28 days after last delivery).")


def parse_time_to_unix(value: str) -> int:
    """Accept unix seconds or ISO 8601 (YYYY-MM-DDTHH:MM:SS±HHMM) and return unix."""
    from datetime import datetime

    if value.isdigit():
        return int(value)
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value, fmt)
            return int(dt.timestamp())
        except ValueError:
            continue
    _die(f"ERROR: cannot parse time '{value}' (use unix seconds, YYYY-MM-DD or ISO 8601)")
    return 0  # unreachable
