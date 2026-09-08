"""Commands: audiences, audience-create-website, audience-create-customerlist.

Custom Audiences live on POST /act_<id>/customaudiences. Two flavours here:

* WEBSITE — rule-based on pixel events (e.g. InitiateCheckout minus Purchase).
* CUSTOM  — a customer list uploaded from CSV.

Safety note for the customer list: unlike the Ads Manager UI (which hashes on
Meta's side after upload), the API expects data already hashed. Every email and
phone is normalised and SHA256-hashed *locally* in build_users_payload() — raw
personal data never reaches the network. See tests/test_audiences.py.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import random

from metaads import api
from metaads.commands.common import account_of
from metaads.formatting import _die, _err, _output_json, _truncate

AUDIENCE_FIELDS = ("id,name,description,subtype,approximate_count_lower_bound,"
                   "approximate_count_upper_bound,delivery_status,operation_status,"
                   "retention_days,customer_file_source,time_created,time_updated")

# Meta accepts up to 10 000 records per /users call.
USERS_BATCH_SIZE = 10000

# Retention bounds from the audience-rules docs.
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 365

CUSTOMER_FILE_SOURCES = ("USER_PROVIDED_ONLY", "PARTNER_PROVIDED_ONLY",
                         "BOTH_USER_AND_PARTNER_PROVIDED")

# CSV header aliases -> Meta schema key. Lowercased, stripped before lookup.
COLUMN_ALIASES = {
    "EMAIL": ("email", "e-mail", "e_mail", "mail", "email_address", "emailaddress"),
    "PHONE": ("phone", "phone_number", "phonenumber", "telephone", "tel",
              "mobile", "msisdn"),
}


# ---------------------------------------------------------------------------
# Normalisation + hashing (the security-critical part)
# ---------------------------------------------------------------------------

def _sha256(value: str) -> str:
    """Hex SHA256 — the only hash Meta accepts for customer-list data."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_email(raw: str | None) -> str | None:
    """Meta rule: trim whitespace, lowercase. None = unusable, skip the row."""
    email = (raw or "").strip().lower()
    if not email or "@" not in email or email.startswith("@") or email.endswith("@"):
        return None
    return email


def normalize_phone(raw: str | None, default_country_code: str | None = None) -> str | None:
    """Meta rule: digits only, no leading zeroes, country code included.

    A '+' or '00' prefix means the number already carries its country code.
    Otherwise the national leading zero is dropped and --country-code (if given)
    is prepended — unless the number already starts with that code.
    """
    raw = (raw or "").strip()
    if not raw:
        return None

    international = raw.startswith("+") or raw.startswith("00")
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None

    if international:
        if digits.startswith("00"):
            digits = digits[2:]
    else:
        digits = digits.lstrip("0")
        if default_country_code and not digits.startswith(default_country_code):
            digits = f"{default_country_code}{digits}"

    # Shortest plausible E.164 subscriber+country number; below this it is junk.
    if len(digits) < 8:
        return None
    return digits


def build_users_payload(records: list[dict], schema: list[str]) -> dict:
    """Build the /users payload with every value SHA256-hashed.

    records hold *normalised but still raw* identifiers; hashing happens here
    and nowhere else, so this is the single choke point a test can guard.
    Missing values become "" (Meta's placeholder for an absent key).
    """
    data = []
    for rec in records:
        row = []
        for key in schema:
            value = rec.get(key)
            row.append(_sha256(value) if value else "")
        data.append(row)
    return {"schema": list(schema), "data": data}


def _mask(value: str) -> str:
    """Mask a normalised identifier for on-screen preview — never print full PII."""
    if "@" in value:
        local, _, domain = value.partition("@")
        return f"{local[:1]}{'*' * max(len(local) - 1, 1)}@{domain}"
    if len(value) > 6:
        return f"{value[:3]}{'*' * (len(value) - 5)}{value[-2:]}"
    return "*" * len(value)


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------

def load_customer_csv(path: str, default_country_code: str | None = None) -> tuple[list[dict], list[str], dict]:
    """Read a CSV of emails/phones into normalised records.

    Returns (records, schema, stats). Records are normalised, NOT hashed —
    build_users_payload() does that.
    """
    if not os.path.isfile(path):
        _die(f"ERROR: CSV not found: {path}")

    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames:
                _die(f"ERROR: {path} has no header row (expected an 'email' and/or 'phone' column).")
            headers = {(h or "").strip().lower(): h for h in reader.fieldnames}
            column_for = {}
            for key, aliases in COLUMN_ALIASES.items():
                for alias in aliases:
                    if alias in headers:
                        column_for[key] = headers[alias]
                        break
            if not column_for:
                _die(f"ERROR: no email/phone column in {path}.\n"
                     f"  Found columns: {', '.join(reader.fieldnames)}\n"
                     f"  Expected one of: {', '.join(COLUMN_ALIASES['EMAIL'])} "
                     f"or {', '.join(COLUMN_ALIASES['PHONE'])}")
            rows = list(reader)
    except UnicodeDecodeError:
        _die(f"ERROR: {path} is not UTF-8 — re-export the CSV as UTF-8.")

    schema = [k for k in ("EMAIL", "PHONE") if k in column_for]
    records: list[dict] = []
    stats = {"rows": len(rows), "skipped": 0, "email_ok": 0, "phone_ok": 0}

    for row in rows:
        rec = {}
        if "EMAIL" in column_for:
            email = normalize_email(row.get(column_for["EMAIL"]))
            if email:
                rec["EMAIL"] = email
                stats["email_ok"] += 1
        if "PHONE" in column_for:
            phone = normalize_phone(row.get(column_for["PHONE"]), default_country_code)
            if phone:
                rec["PHONE"] = phone
                stats["phone_ok"] += 1
        if rec:
            records.append(rec)
        else:
            stats["skipped"] += 1

    stats["uploadable"] = len(records)
    return records, schema, stats


# ---------------------------------------------------------------------------
# Website rule building
# ---------------------------------------------------------------------------

def build_website_rule(pixel_id: str, retention_seconds: int, event: str | None = None,
                       url_contains: str | None = None, exclude_event: str | None = None) -> dict:
    """Flexible rule spec for a WEBSITE custom audience."""
    source = [{"id": str(pixel_id), "type": "pixel"}]

    filters = []
    if event:
        filters.append({"field": "event", "operator": "eq", "value": event})
    if url_contains:
        filters.append({"field": "url", "operator": "i_contains", "value": url_contains})

    rule = {
        "inclusions": {
            "operator": "or",
            "rules": [{
                "event_sources": source,
                "retention_seconds": retention_seconds,
                "filter": {"operator": "and", "filters": filters},
            }],
        }
    }

    if exclude_event:
        rule["exclusions"] = {
            "operator": "or",
            "rules": [{
                "event_sources": source,
                "retention_seconds": retention_seconds,
                "filter": {"operator": "and", "filters": [
                    {"field": "event", "operator": "eq", "value": exclude_event}
                ]},
            }],
        }
    return rule


def _validate_days(days: int) -> None:
    if not (MIN_RETENTION_DAYS <= days <= MAX_RETENTION_DAYS):
        _die(f"ERROR: --days must be between {MIN_RETENTION_DAYS} and {MAX_RETENTION_DAYS} (got {days}).")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_audiences(args) -> None:
    """List custom audiences (read-only)."""
    account_id = account_of(args)
    rows = api._paginate(f"{account_id}/customaudiences",
                         {"fields": AUDIENCE_FIELDS, "limit": args.limit},
                         max_items=args.limit)

    if args.json:
        _output_json(rows)
        return

    if not rows:
        print("No custom audiences found.")
        return

    print(f"{'ID':<20} {'Name':<38} {'Subtype':<14} {'Size':<18} {'Days':<6} {'Status'}")
    print("-" * 120)
    for r in rows:
        low = r.get("approximate_count_lower_bound")
        high = r.get("approximate_count_upper_bound")
        # Meta returns -1 both while an audience builds AND for audiences whose
        # size it does not expose (lookalikes) — the two are indistinguishable,
        # so stay neutral and let the status column carry the real state.
        if low is None or int(low) < 0:
            size = "n/a"
        elif high is not None and int(high) != int(low):
            size = f"{int(low):,}–{int(high):,}"
        else:
            size = f"{int(low):,}"
        status = (r.get("delivery_status") or {}).get("description") \
            or (r.get("operation_status") or {}).get("description") or "---"
        print(f"{r['id']:<20} {_truncate(r.get('name'), 36):<38} "
              f"{_truncate(r.get('subtype'), 12):<14} {size:<18} "
              f"{str(r.get('retention_days') or '---'):<6} {_truncate(status, 30)}")


def cmd_audience_create_website(args) -> None:
    """Create a rule-based Website Custom Audience from pixel events [write]."""
    account_id = account_of(args)
    _validate_days(args.days)

    if not args.event and not args.url_contains:
        _die("ERROR: give at least --event (e.g. --event InitiateCheckout) "
             "or --url-contains.")

    pixel_id = args.pixel_id
    if not pixel_id:
        pixels = api._paginate(f"{account_id}/adspixels", {"fields": "id,name"}, max_items=10)
        if not pixels:
            _die(f"ERROR: no pixel on {account_id} — pass --pixel-id explicitly.")
        if len(pixels) > 1:
            names = ", ".join(f"{p['id']} ({p.get('name', '?')})" for p in pixels)
            _die(f"ERROR: {len(pixels)} pixels on this account — pass --pixel-id.\n  {names}")
        pixel_id = pixels[0]["id"]
        _err(f"Note: using the account's only pixel {pixel_id} ({pixels[0].get('name', '?')}).")

    retention_seconds = args.days * 86400
    rule = build_website_rule(
        pixel_id=pixel_id,
        retention_seconds=retention_seconds,
        event=args.event,
        url_contains=args.url_contains,
        exclude_event=args.exclude_event,
    )

    # No `subtype` here: Meta dropped it for rule-based audiences ("The parameter
    # 'subtype' is not supported in the current API version") — the rule itself
    # defines the audience type. Customer lists still require subtype=CUSTOM.
    params = {
        "name": args.name,
        "retention_days": args.days,
        "prefill": "true" if args.prefill else "false",
        "rule": json.dumps(rule),
    }
    if args.description:
        params["description"] = args.description

    plan = {"account_id": account_id, "name": args.name, "type": "website (rule-based)",
            "pixel_id": str(pixel_id), "retention_days": args.days,
            "prefill": bool(args.prefill), "rule": rule}

    if not args.confirm:
        if args.json:
            _output_json({"executed": False, "would_create": plan,
                          "note": "Add --confirm to execute."})
            return
        print(f"DRY-RUN: would create WEBSITE audience \"{args.name}\" on {account_id}.")
        print(f"  Pixel:     {pixel_id}")
        print(f"  Include:   {args.event or '(any event)'}"
              + (f" + url contains \"{args.url_contains}\"" if args.url_contains else ""))
        print(f"  Exclude:   {args.exclude_event or '---'}")
        print(f"  Retention: {args.days} days | prefill: {bool(args.prefill)}")
        print("  Rule: " + json.dumps(rule, ensure_ascii=False))
        print("Add --confirm to execute.")
        return

    data = api._api_call("POST", f"{account_id}/customaudiences", params)
    result = {"executed": True, "audience_id": data.get("id"), "name": args.name,
              "subtype": "WEBSITE"}
    if args.json:
        _output_json(result)
    else:
        print(f"Audience created: {data.get('id')} \"{args.name}\" (WEBSITE, {args.days} d).")
        _err("Note: a fresh audience needs time to populate before it is usable for targeting.")


def cmd_audience_create_customerlist(args) -> None:
    """Create a Customer List audience from CSV; hashes SHA256 locally [write]."""
    account_id = account_of(args)

    records, schema, stats = load_customer_csv(args.csv, args.country_code)
    if not records:
        _die(f"ERROR: no usable email/phone rows in {args.csv} "
             f"({stats['rows']} rows read, all skipped).")

    payload = build_users_payload(records, schema)
    batches = [payload["data"][i:i + USERS_BATCH_SIZE]
               for i in range(0, len(payload["data"]), USERS_BATCH_SIZE)]

    plan = {
        "account_id": account_id, "name": args.name, "subtype": "CUSTOM",
        "customer_file_source": args.customer_file_source, "schema": schema,
        "csv": args.csv, "rows_read": stats["rows"], "uploadable": stats["uploadable"],
        "skipped": stats["skipped"], "email_values": stats["email_ok"],
        "phone_values": stats["phone_ok"], "batches": len(batches),
    }

    if not args.confirm:
        # Preview a handful of records: masked source + hash prefix, so the
        # operator can eyeball normalisation without dumping the whole list.
        preview = []
        for rec in records[:args.preview]:
            item = {}
            for key in schema:
                value = rec.get(key)
                item[key] = {"normalized_masked": _mask(value),
                             "sha256": _sha256(value)[:16] + "…"} if value else None
            preview.append(item)

        if args.json:
            _output_json({"executed": False, "would_create": plan, "preview": preview,
                          "note": "Values are SHA256-hashed locally before upload. "
                                  "Add --confirm to execute."})
            return

        print(f"DRY-RUN: would create CUSTOM audience \"{args.name}\" on {account_id} "
              f"and upload {stats['uploadable']} records.")
        print(f"  Source CSV:  {args.csv}")
        print(f"  Rows read:   {stats['rows']}  |  uploadable: {stats['uploadable']}  "
              f"|  skipped (unusable): {stats['skipped']}")
        print(f"  Schema:      {schema}  (EMAIL values: {stats['email_ok']}, "
              f"PHONE values: {stats['phone_ok']})")
        print(f"  Batches:     {len(batches)} × up to {USERS_BATCH_SIZE}")
        print(f"  File source: {args.customer_file_source}")
        if args.country_code:
            print(f"  Country code applied to local numbers: +{args.country_code}")
        print(f"\n  Preview (first {min(args.preview, len(records))} of "
              f"{len(records)}, hashed locally — raw data never leaves this machine):")
        for i, item in enumerate(preview, 1):
            parts = [f"{k}={v['normalized_masked']} → {v['sha256']}"
                     for k, v in item.items() if v]
            print(f"    {i}. " + "  ".join(parts))
        print("\nAdd --confirm to create the audience and upload.")
        return

    created = api._api_call("POST", f"{account_id}/customaudiences", {
        "name": args.name,
        "subtype": "CUSTOM",
        "customer_file_source": args.customer_file_source,
        **({"description": args.description} if args.description else {}),
    })
    audience_id = created.get("id")
    if not audience_id:
        _die(f"ERROR: audience creation returned no id: {created}")
    print(f"Audience created: {audience_id} \"{args.name}\" (CUSTOM).")

    session_id = random.randint(1, 2**31 - 1)
    uploaded = 0
    for seq, batch in enumerate(batches, start=1):
        api._api_call("POST", f"{audience_id}/users", {
            "session": json.dumps({
                "session_id": session_id,
                "batch_seq": seq,
                "last_batch_flag": seq == len(batches),
                "estimated_num_total": len(payload["data"]),
            }),
            "payload": json.dumps({"schema": schema, "data": batch}),
        })
        uploaded += len(batch)
        print(f"  batch {seq}/{len(batches)}: {len(batch)} records uploaded "
              f"({uploaded}/{len(payload['data'])}).")

    result = {"executed": True, "audience_id": audience_id, "name": args.name,
              "uploaded": uploaded, "batches": len(batches), "schema": schema}
    if args.json:
        _output_json(result)
    else:
        print(f"Done: {uploaded} records uploaded to {audience_id}.")
        _err("Note: Meta reports the final match rate only after processing "
             "(check with `audiences`). Audiences under 1000 matches will not deliver.")
