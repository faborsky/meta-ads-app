"""Commands: insights, insights-report (async). Shared param builder + formatting."""

from __future__ import annotations

import json
import time

from metaads import api
from metaads.commands.common import account_of
from metaads.formatting import _die, _err, _output_json

DEFAULT_INSIGHT_FIELDS = [
    "campaign_name", "adset_name", "ad_name",
    "impressions", "clicks", "ctr", "cpc", "cpm",
    "spend", "reach", "frequency",
    "actions", "cost_per_action_type",
]

DATE_PRESETS = [
    "today", "yesterday", "last_3d", "last_7d", "last_14d", "last_28d", "last_30d",
    "last_90d", "this_month", "last_month", "this_quarter", "last_quarter",
    "this_week_mon_today", "last_week_mon_sun", "this_year", "last_year",
    "maximum",
]

# 7d_view/28d_view were removed by Meta on 2026-01-12 — the API silently
# returns zeros for them, so the CLI refuses them instead.
ATTRIBUTION_WINDOWS = [
    "1d_click", "7d_click", "28d_click", "1d_view", "1d_ev", "dda", "default",
]
_REMOVED_WINDOWS = {"7d_view", "28d_view"}

# insights-report fetches at most this many rows (paginated 500/page).
REPORT_ROW_CAP = 5000


def _format_insight_value(key: str, value: object) -> str:
    """Format insight metric value for human display (never crash on API shape)."""
    if value is None:
        return "---"
    try:
        if key in ("ctr", "frequency", "cpc", "cpm", "spend"):
            return f"{float(value):.2f}"
        if key in ("impressions", "reach", "clicks"):
            return f"{int(value):,}"
        if key in ("actions", "cost_per_action_type") and isinstance(value, list):
            parts = []
            for a in value:
                val = a.get("value", "?")
                if key == "cost_per_action_type":
                    try:
                        val = f"{float(val):.2f}"
                    except (TypeError, ValueError):
                        pass
                parts.append(f"{a.get('action_type', '?')}: {val}")
            return "; ".join(parts)
    except (TypeError, ValueError):
        pass
    return str(value)


def build_insight_params(args) -> dict:
    """Shared insights query params from CLI args."""
    fields = args.fields.split(",") if args.fields else DEFAULT_INSIGHT_FIELDS
    params: dict = {"fields": ",".join(fields)}

    if args.date_from and args.date_to:
        params["time_range"] = json.dumps({"since": args.date_from, "until": args.date_to})
    elif args.date_preset:
        params["date_preset"] = args.date_preset
    else:
        params["date_preset"] = "last_30d"

    if getattr(args, "level", None):
        params["level"] = args.level
    if args.breakdowns:
        params["breakdowns"] = args.breakdowns
    if getattr(args, "action_breakdowns", None):
        params["action_breakdowns"] = args.action_breakdowns
    if args.time_increment:
        params["time_increment"] = args.time_increment
    if getattr(args, "attribution_windows", None):
        windows = [w.strip() for w in args.attribution_windows.split(",")]
        for w in windows:
            if w in _REMOVED_WINDOWS:
                _die(f"ERROR: attribution window '{w}' was removed by Meta (2026-01-12) — "
                     f"the API returns silent zeros for it. Valid: {', '.join(ATTRIBUTION_WINDOWS)}")
            if w not in ATTRIBUTION_WINDOWS:
                _die(f"ERROR: unknown attribution window '{w}' (valid: {', '.join(ATTRIBUTION_WINDOWS)})")
        params["action_attribution_windows"] = json.dumps(windows)
    if getattr(args, "unified_attribution", False):
        params["use_unified_attribution_setting"] = "true"
    if getattr(args, "filtering", None):
        params["filtering"] = args.filtering
    if getattr(args, "sort", None):
        params["sort"] = args.sort

    return params


def cmd_insights(args) -> None:
    """Get performance insights for any object (account/campaign/adset/ad)."""
    object_id = args.object_id or account_of(args)

    fields = args.fields.split(",") if args.fields else DEFAULT_INSIGHT_FIELDS
    params = build_insight_params(args)
    params["limit"] = args.limit

    data = api._api_call("GET", f"{object_id}/insights", params)
    rows = data.get("data", [])

    # A paging.next link means more rows exist than --limit returned — say so
    # loudly (stderr, so --json stdout stays a clean row array).
    if (data.get("paging") or {}).get("next"):
        _err(f"⚠ Truncated: more rows exist beyond --limit {args.limit} (paging.next present). "
             "Raise --limit or use insights-report for large queries.")

    if args.json:
        _output_json(rows)
        return

    if not rows:
        print("No insights data for this period.")
        return

    breakdown_keys = args.breakdowns.split(",") if args.breakdowns else []
    for i, row in enumerate(rows):
        if i > 0:
            print()
        period = row.get("date_start", "?")
        period_end = row.get("date_stop", "?")
        name_parts = [row[k] for k in ("campaign_name", "adset_name", "ad_name") if row.get(k)]
        header = " > ".join(name_parts) if name_parts else object_id
        bd_parts = [f"{bd}={row[bd]}" for bd in breakdown_keys if row.get(bd)]
        if bd_parts:
            header += f" [{', '.join(bd_parts)}]"
        print(f"--- {header} ({period} to {period_end}) ---")

        for key in fields:
            if key in ("campaign_name", "adset_name", "ad_name", "date_start", "date_stop"):
                continue
            value = row.get(key)
            if value is not None:
                print(f"  {key:<25} {_format_insight_value(key, value)}")


def cmd_insights_report(args) -> None:
    """Generate async insights report for large queries."""
    object_id = args.object_id or account_of(args)

    fields = args.fields.split(",") if args.fields else DEFAULT_INSIGHT_FIELDS
    params = build_insight_params(args)
    params["level"] = args.level

    # POST triggers async
    data = api._api_call("POST", f"{object_id}/insights", params)
    report_run_id = data.get("report_run_id")

    if not report_run_id:
        _die("ERROR: No report_run_id returned.")

    _err(f"Report queued (ID: {report_run_id}). Polling...")

    max_wait = 600  # 10 minutes
    elapsed = 0
    poll_interval = 5

    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval

        status_data = api._api_call("GET", report_run_id, {
            "fields": "async_status,async_percent_completion",
        })
        status = status_data.get("async_status")
        pct = status_data.get("async_percent_completion", 0)
        _err(f"  Status: {status} ({pct}%)")

        if status == "Job Completed":
            break
        if status in ("Job Failed", "Job Skipped"):
            # Since v25.0 failed jobs carry full error fields
            err_data = api._api_call("GET", report_run_id, {
                "fields": "async_status,error_code,error_message,error_subcode,error_user_title,error_user_msg",
            })
            _die(f"ERROR: Report failed: {json.dumps(err_data, ensure_ascii=False)}")

        if elapsed > 30:
            poll_interval = 15
    else:
        _die("ERROR: Report timed out after 10 minutes.")

    results = api._paginate(f"{report_run_id}/insights", {"limit": 500}, max_items=REPORT_ROW_CAP)
    if len(results) >= REPORT_ROW_CAP:
        _err(f"⚠ Row cap {REPORT_ROW_CAP} reached — results may be truncated. "
             "Narrow the query (date range, level, filtering).")

    if args.json:
        _output_json(results)
        return

    _err(f"\nReport complete: {len(results)} rows")
    for row in results:
        parts = []
        for key in fields:
            val = row.get(key)
            if val is not None:
                parts.append(f"{key}={_format_insight_value(key, val)}")
        print(" | ".join(parts))
