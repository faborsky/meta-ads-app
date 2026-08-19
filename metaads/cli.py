"""Argparse wiring + dispatch. Every subcommand is registered by exactly one
_cmd() call that both adds the parser and binds the handler — parser/dispatch
parity by construction."""

from __future__ import annotations

import argparse
import os
import signal
import sys

from metaads import __version__, api
from metaads.commands.account import cmd_account, cmd_api_limits, cmd_pages
from metaads.commands.activity import CATEGORIES, cmd_activities
from metaads.commands.ads import (
    cmd_ad_create,
    cmd_ad_delete,
    cmd_ad_detail,
    cmd_ad_duplicate,
    cmd_ad_review,
    cmd_ad_update,
    cmd_ads,
)
from metaads.commands.adsets import (
    BID_STRATEGIES,
    OPTIMIZATION_GOALS,
    cmd_adset_create,
    cmd_adset_delete,
    cmd_adset_detail,
    cmd_adset_duplicate,
    cmd_adset_update,
    cmd_adsets,
)
from metaads.commands.auth import cmd_token_extend, cmd_token_info
from metaads.commands.campaigns import (
    OBJECTIVES,
    cmd_budget_schedule,
    cmd_campaign_create,
    cmd_campaign_delete,
    cmd_campaign_detail,
    cmd_campaign_duplicate,
    cmd_campaign_update,
    cmd_campaigns,
)
from metaads.commands.conversions import cmd_custom_conversions, cmd_pixels
from metaads.commands.creatives import (
    cmd_creative_clone,
    cmd_creative_create,
    cmd_creative_delete,
    cmd_creative_detail,
    cmd_creative_from_ig,
    cmd_creative_from_post,
    cmd_creatives,
    cmd_ig_media,
    cmd_preview,
)
from metaads.commands.insights import DATE_PRESETS, cmd_insights, cmd_insights_report
from metaads.commands.media import cmd_image_upload, cmd_video_upload
from metaads.commands.pulse import cmd_pulse
from metaads.commands.targeting import (
    cmd_geo_search,
    cmd_interest_search,
    cmd_interest_suggest,
    cmd_interest_validate,
    cmd_locale_search,
)

LIST_STATUSES = ["ACTIVE", "PAUSED", "ARCHIVED", "DELETED"]
STATUS_CHOICES = ["ACTIVE", "PAUSED"]
UPDATE_STATUS_CHOICES = ["ACTIVE", "PAUSED", "ARCHIVED"]
COPY_STATUS_CHOICES = ["ACTIVE", "PAUSED", "INHERITED_FROM_SOURCE"]


# ---------------------------------------------------------------------------
# Visual signature — humans only. Printed ONLY when stdout is a TTY and the
# run isn't --json, so pipes, scripts and agent tool-calls always get clean,
# token-free output.
# ---------------------------------------------------------------------------

_ART = [
    "███╗   ███╗ ███████╗ ████████╗  █████╗ ",
    "████╗ ████║ ██╔════╝ ╚══██╔══╝ ██╔══██╗",
    "██╔████╔██║ █████╗      ██║    ███████║",
    "██║╚██╔╝██║ ██╔══╝      ██║    ██╔══██║",
    "██║ ╚═╝ ██║ ███████╗    ██║    ██║  ██║",
    "╚═╝     ╚═╝ ╚══════╝    ╚═╝    ╚═╝  ╚═╝",
]


def _print_banner() -> None:
    if not sys.stdout.isatty() or "--json" in sys.argv:
        return
    use_color = "NO_COLOR" not in os.environ
    blue = "\033[38;5;33m" if use_color else ""   # Meta blue
    dim = "\033[2m" if use_color else ""
    bold = "\033[1m" if use_color else ""
    reset = "\033[0m" if use_color else ""
    info = [
        "",
        f"{bold}Meta Ads CLI{reset} v{__version__}",
        f"{dim}Facebook & Instagram kampaně — insights · kreativy · dry-run zápisy{reset}",
        f"{dim}./run.sh <příkaz> --help{reset}",
        f"{dim}by Jindřich Fáborský · AIFirst.cz{reset}",
        "",
    ]
    for art_line, info_line in zip(_ART, info):
        print(f"{blue}{art_line}{reset}   {info_line}")
    print()


def _cmd(sub, name: str, func, help_: str, *, write: bool = False):
    """Add a subcommand; writes get --confirm (default = dry-run/validate)."""
    sp = sub.add_parser(name, help=help_ + (" [write]" if write else ""))
    if write:
        sp.add_argument("--confirm", action="store_true",
                        help="Actually write (default: dry-run / validate_only)")
    sp.add_argument("--json", action="store_true", help="JSON output")
    sp.set_defaults(func=func)
    return sp


def _add_insight_args(sp, level_required: bool = False) -> None:
    sp.add_argument("--object-id", help="Object ID (account/campaign/adset/ad). Default: ad account")
    sp.add_argument("--level", choices=["account", "campaign", "adset", "ad"],
                    required=level_required, help="Aggregation level")
    sp.add_argument("--date-preset", choices=DATE_PRESETS, help="Predefined date range (default: last_30d)")
    sp.add_argument("--date-from", help="Start date YYYY-MM-DD")
    sp.add_argument("--date-to", help="End date YYYY-MM-DD")
    sp.add_argument("--fields", help="Comma-separated metrics")
    sp.add_argument("--breakdowns",
                    help="Comma-separated (age,gender,country,publisher_platform,platform_position,"
                         "device_platform,image_asset,video_asset,title_asset,body_asset,...)")
    sp.add_argument("--action-breakdowns", help="Comma-separated (action_type,action_device,...)")
    sp.add_argument("--attribution-windows", help="Comma-separated (1d_click,7d_click,1d_view,...)")
    sp.add_argument("--unified-attribution", action="store_true",
                    help="use_unified_attribution_setting=true (matches Ads Manager numbers)")
    sp.add_argument("--filtering", help='Raw filtering JSON, e.g. \'[{"field":"spend","operator":"GREATER_THAN","value":0}]\'')
    sp.add_argument("--sort", help="Sort, e.g. spend_descending")
    sp.add_argument("--time-increment", help="Time granularity: 1..90, monthly, all_days")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meta_ads_cli",
        description="Meta Ads CLI — manage Facebook & Instagram ad campaigns "
                    "(writes default to dry-run; add --confirm to execute).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--account-id", default=None,
                        help="Override ad account ID (default: META_AD_ACCOUNT_ID from .env)")
    sub = parser.add_subparsers(dest="command", required=True)

    # ----- Account & token ------------------------------------------------
    _cmd(sub, "account", cmd_account, "Show ad account info")
    _cmd(sub, "pages", cmd_pages, "List Facebook/Instagram pages for ad creatives")
    _cmd(sub, "api-limits", cmd_api_limits, "Show API usage percent, tier and rate-limit reference")
    _cmd(sub, "token-info", cmd_token_info, "Show access token info and expiry")
    sp = _cmd(sub, "token-extend", cmd_token_extend, "Exchange token for long-lived (60 days)")
    sp.add_argument("--write-env", action="store_true", help="Write the new token into .env (backup in .env.bak)")

    # ----- Campaigns ------------------------------------------------------
    sp = _cmd(sub, "campaigns", cmd_campaigns, "List campaigns (DELETED hidden by default)")
    sp.add_argument("--status", choices=LIST_STATUSES, help="Filter by effective status")
    sp.add_argument("--limit", type=int, default=100)

    sp = _cmd(sub, "campaign-detail", cmd_campaign_detail, "Campaign detail incl. Advantage+ state & issues")
    sp.add_argument("--campaign-id", required=True)

    sp = _cmd(sub, "campaign-create", cmd_campaign_create, "Create a campaign (PAUSED)", write=True)
    sp.add_argument("--name", required=True)
    sp.add_argument("--objective", required=True, choices=OBJECTIVES)
    sp.add_argument("--daily-budget", type=float, help="Daily budget in account currency (campaign-level = CBO)")
    sp.add_argument("--lifetime-budget", type=float, help="Lifetime budget in account currency")
    sp.add_argument("--status", default="PAUSED", choices=STATUS_CHOICES)
    sp.add_argument("--special-ad-categories", help='JSON array, e.g. \'["HOUSING"]\'')
    sp.add_argument("--bid-strategy", choices=BID_STRATEGIES)
    sp.add_argument("--adset-budget-sharing", choices=["true", "false"],
                    help="ABO only: let ad sets share 20 percent of budgets (default false)")
    sp.add_argument("--start-time", help="ISO 8601 datetime")
    sp.add_argument("--stop-time", help="ISO 8601 datetime")

    sp = _cmd(sub, "campaign-update", cmd_campaign_update, "Update a campaign", write=True)
    sp.add_argument("--campaign-id", required=True)
    sp.add_argument("--name")
    sp.add_argument("--status", choices=UPDATE_STATUS_CHOICES)
    sp.add_argument("--daily-budget", type=float)
    sp.add_argument("--lifetime-budget", type=float)
    sp.add_argument("--bid-strategy", choices=BID_STRATEGIES)
    sp.add_argument("--stop-time")

    sp = _cmd(sub, "campaign-duplicate", cmd_campaign_duplicate, "Duplicate a campaign (PAUSED copy)", write=True)
    sp.add_argument("--campaign-id", required=True)
    sp.add_argument("--deep-copy", action="store_true", help="Copy ad sets and ads too")
    sp.add_argument("--status-option", default="PAUSED", choices=COPY_STATUS_CHOICES)
    sp.add_argument("--rename-suffix", help="Suffix for copied name")

    sp = _cmd(sub, "campaign-delete", cmd_campaign_delete, "DELETE a campaign (permanent!)", write=True)
    sp.add_argument("--campaign-id", required=True)
    sp.add_argument("--force", action="store_true", help="Delete even when not PAUSED/ARCHIVED")

    sp = _cmd(sub, "budget-schedule", cmd_budget_schedule, "List/create budget schedules (demand spikes)", write=True)
    sp.add_argument("--campaign-id")
    sp.add_argument("--adset-id")
    sp.add_argument("--list", action="store_true", help="List existing schedules")
    sp.add_argument("--start", help="Unix seconds / YYYY-MM-DD / ISO 8601")
    sp.add_argument("--end", help="Unix seconds / YYYY-MM-DD / ISO 8601")
    sp.add_argument("--value", type=float, help="Budget value (currency for ABSOLUTE, percent for MULTIPLIER)")
    sp.add_argument("--value-type", default="ABSOLUTE", choices=["ABSOLUTE", "MULTIPLIER"])

    # ----- Ad sets --------------------------------------------------------
    sp = _cmd(sub, "adsets", cmd_adsets, "List ad sets (DELETED hidden by default)")
    sp.add_argument("--campaign-id", help="Filter by campaign")
    sp.add_argument("--status", choices=LIST_STATUSES)
    sp.add_argument("--limit", type=int, default=100)

    sp = _cmd(sub, "adset-detail", cmd_adset_detail, "Ad set detail incl. targeting & issues")
    sp.add_argument("--adset-id", required=True)

    sp = _cmd(sub, "adset-create", cmd_adset_create, "Create an ad set (PAUSED)", write=True)
    sp.add_argument("--campaign-id", required=True)
    sp.add_argument("--name", required=True)
    sp.add_argument("--optimization-goal", required=True, choices=OPTIMIZATION_GOALS)
    sp.add_argument("--billing-event", default="IMPRESSIONS", choices=["IMPRESSIONS", "LINK_CLICKS", "THRUPLAY"])
    sp.add_argument("--daily-budget", type=float)
    sp.add_argument("--lifetime-budget", type=float)
    sp.add_argument("--cbo", action="store_true", help="No ad set budget (campaign holds it — CBO)")
    sp.add_argument("--targeting", help='JSON targeting spec, e.g. \'{"geo_locations":{"countries":["CZ"]}}\'')
    sp.add_argument("--countries", help="Shortcut: comma-separated country codes (merged into targeting)")
    sp.add_argument("--age-min", type=int)
    sp.add_argument("--age-max", type=int)
    sp.add_argument("--genders", help="1=male,2=female (comma-separated)")
    sp.add_argument("--advantage-audience", type=int, choices=[0, 1],
                    help="targeting_automation.advantage_audience (Advantage+ audience)")
    sp.add_argument("--publisher-platforms", help="Comma: facebook,instagram,audience_network,messenger (manual placements)")
    sp.add_argument("--facebook-positions", help="Comma: feed,facebook_reels,story,marketplace,search,...")
    sp.add_argument("--instagram-positions", help="Comma: stream,story,reels,...")
    sp.add_argument("--device-platforms", help="Comma: mobile,desktop")
    sp.add_argument("--bid-amount", type=float, help="Bid/cost cap in account currency")
    sp.add_argument("--bid-strategy", choices=BID_STRATEGIES)
    sp.add_argument("--roas-floor", type=float, help="Min ROAS for LOWEST_COST_WITH_MIN_ROAS (e.g. 2.5)")
    sp.add_argument("--start-time")
    sp.add_argument("--end-time")
    sp.add_argument("--status", default="PAUSED", choices=STATUS_CHOICES)
    sp.add_argument("--promoted-object", help="JSON promoted object spec (pixel_id + custom_event_type,...)")
    sp.add_argument("--dsa-payor", help="EU DSA: who pays for the ad")
    sp.add_argument("--dsa-beneficiary", help="EU DSA: who benefits from the ad")

    sp = _cmd(sub, "adset-update", cmd_adset_update, "Update an ad set", write=True)
    sp.add_argument("--adset-id", required=True)
    sp.add_argument("--name")
    sp.add_argument("--status", choices=UPDATE_STATUS_CHOICES)
    sp.add_argument("--daily-budget", type=float)
    sp.add_argument("--lifetime-budget", type=float)
    sp.add_argument("--bid-amount", type=float)
    sp.add_argument("--bid-strategy", choices=BID_STRATEGIES)
    sp.add_argument("--roas-floor", type=float)
    sp.add_argument("--targeting", help="JSON targeting spec (replaces the whole spec)")
    sp.add_argument("--countries", help="Merged into current targeting when --targeting absent")
    sp.add_argument("--age-min", type=int)
    sp.add_argument("--age-max", type=int)
    sp.add_argument("--genders")
    sp.add_argument("--advantage-audience", type=int, choices=[0, 1])
    sp.add_argument("--publisher-platforms")
    sp.add_argument("--facebook-positions")
    sp.add_argument("--instagram-positions")
    sp.add_argument("--device-platforms")
    sp.add_argument("--end-time")
    sp.add_argument("--dsa-payor")
    sp.add_argument("--dsa-beneficiary")

    sp = _cmd(sub, "adset-duplicate", cmd_adset_duplicate, "Duplicate an ad set (PAUSED copy)", write=True)
    sp.add_argument("--adset-id", required=True)
    sp.add_argument("--campaign-id", help="Target campaign (optional)")
    sp.add_argument("--deep-copy", action="store_true", help="Copy child ads too")
    sp.add_argument("--status-option", default="PAUSED", choices=COPY_STATUS_CHOICES)
    sp.add_argument("--rename-suffix")

    sp = _cmd(sub, "adset-delete", cmd_adset_delete, "DELETE an ad set (permanent!)", write=True)
    sp.add_argument("--adset-id", required=True)
    sp.add_argument("--force", action="store_true")

    # ----- Ads ------------------------------------------------------------
    sp = _cmd(sub, "ads", cmd_ads, "List ads (DELETED hidden by default)")
    sp.add_argument("--adset-id", help="Filter by ad set")
    sp.add_argument("--campaign-id", help="Filter by campaign")
    sp.add_argument("--status", choices=LIST_STATUSES)
    sp.add_argument("--limit", type=int, default=100)

    sp = _cmd(sub, "ad-detail", cmd_ad_detail, "Ad detail incl. creative, issues & review feedback")
    sp.add_argument("--ad-id", required=True)

    sp = _cmd(sub, "ad-review", cmd_ad_review, "Review check: ads in review / with issues + reasons")
    sp.add_argument("--ad-id", help="Single ad (default: scan the whole account)")
    sp.add_argument("--statuses", help="Comma list (default: WITH_ISSUES,DISAPPROVED,PENDING_REVIEW,IN_PROCESS)")
    sp.add_argument("--limit", type=int, default=100)

    sp = _cmd(sub, "ad-create", cmd_ad_create, "Create an ad (PAUSED)", write=True)
    sp.add_argument("--adset-id", required=True)
    sp.add_argument("--name", required=True)
    sp.add_argument("--creative-id", help="Existing creative ID")
    sp.add_argument("--creative-json", help="Inline creative JSON spec")
    sp.add_argument("--status", default="PAUSED", choices=STATUS_CHOICES)
    sp.add_argument("--tracking-specs", help="JSON tracking specs")

    sp = _cmd(sub, "ad-update", cmd_ad_update, "Update an ad (creative swap triggers re-review)", write=True)
    sp.add_argument("--ad-id", required=True)
    sp.add_argument("--name")
    sp.add_argument("--status", choices=UPDATE_STATUS_CHOICES)
    sp.add_argument("--creative-id", help="Swap to different creative")

    sp = _cmd(sub, "ad-duplicate", cmd_ad_duplicate, "Duplicate an ad (PAUSED copy)", write=True)
    sp.add_argument("--ad-id", required=True)
    sp.add_argument("--adset-id", help="Target ad set (optional)")
    sp.add_argument("--status-option", default="PAUSED", choices=COPY_STATUS_CHOICES)
    sp.add_argument("--rename-suffix")

    sp = _cmd(sub, "ad-delete", cmd_ad_delete, "DELETE an ad (permanent!)", write=True)
    sp.add_argument("--ad-id", required=True)
    sp.add_argument("--force", action="store_true")

    # ----- Creatives & media ----------------------------------------------
    sp = _cmd(sub, "creatives", cmd_creatives, "List ad creatives")
    sp.add_argument("--limit", type=int, default=50)

    sp = _cmd(sub, "creative-detail", cmd_creative_detail, "Creative detail incl. asset_feed_spec")
    sp.add_argument("--creative-id", required=True)

    sp = _cmd(sub, "creative-create", cmd_creative_create,
              "Create a creative (link/video/photo/carousel/flex)", write=True)
    sp.add_argument("--name", required=True)
    sp.add_argument("--type", required=True, choices=["link", "video", "photo", "carousel", "flex"])
    sp.add_argument("--page-id", help="Facebook Page ID (default: META_PAGE_ID from .env)")
    sp.add_argument("--message", action="append", help="Primary text (repeat for --type flex, max 5)")
    sp.add_argument("--link", help="Landing page URL")
    sp.add_argument("--headline", action="append", help="Ad headline (repeat for --type flex, max 5)")
    sp.add_argument("--description", action="append", help="Ad description (repeat for --type flex, max 5)")
    sp.add_argument("--image-hash", action="append", help="Image hash from image-upload (repeat for --type flex)")
    sp.add_argument("--image-url", help="Image URL (alternative to hash)")
    sp.add_argument("--video-id", action="append", help="Video ID from video-upload (repeat for --type flex)")
    sp.add_argument("--video-thumbnail", help="Video thumbnail URL")
    sp.add_argument("--call-to-action", help="CTA type (LEARN_MORE, SHOP_NOW, SIGN_UP, ...)")
    sp.add_argument("--child-attachments", help="JSON array for carousel")
    sp.add_argument("--url-tags", help="UTM parameters")
    sp.add_argument("--lead-gen-form-id",
                    help="Lead form ID → CTA value (link/video; requires --link, http://fb.me/ works)")
    sp.add_argument("--ig-user-id",
                    help="IG identity for flex (or use --no-enhancements → page-backed identity, PBIA)")
    sp.add_argument("--no-enhancements", action="store_true",
                    help="Opt out of all Advantage+ enhancements (14 features, enroll_status=OPT_OUT)")

    sp = _cmd(sub, "creative-clone", cmd_creative_clone,
              "Clone an Advantage+ creative, swapping video/image/URL", write=True)
    sp.add_argument("--creative-id", required=True, help="Source creative ID")
    sp.add_argument("--name", required=True, help="Name for the new creative")
    sp.add_argument("--swap-video", help="New video ID (replaces video in videos[])")
    sp.add_argument("--swap-thumbnail", help="New video thumbnail image hash (with --swap-video)")
    sp.add_argument("--swap-image", help="New fallback image hash (collapses images[] to one entry)")
    sp.add_argument("--new-url", help="New landing page URL (replaces website_url in link_urls[])")
    sp.add_argument("--url-tags", help="Override URL tags (default: carried over from the source creative)")
    sp.add_argument("--no-enhancements", action="store_true",
                    help="Replace source enhancement spec with full opt-out (14 features OPT_OUT)")
    sp.add_argument("--swap-on-ad", help="Ad ID to point at the new creative (triggers re-review)")

    sp = _cmd(sub, "creative-from-post", cmd_creative_from_post,
              "Creative from an existing FB Page post (boost organic)", write=True)
    sp.add_argument("--post-id", required=True, help="PAGEID_POSTID (or bare post ID with --page-id)")
    sp.add_argument("--page-id", help="Page ID when --post-id is bare")
    sp.add_argument("--name", required=True)
    sp.add_argument("--call-to-action", help="Optional CTA type")
    sp.add_argument("--link", help="CTA landing URL")
    sp.add_argument("--no-enhancements", action="store_true",
                    help="Opt out of all Advantage+ enhancements (14 features, enroll_status=OPT_OUT)")

    sp = _cmd(sub, "creative-from-ig", cmd_creative_from_ig,
              "Creative from an existing IG post/Reel (boost organic)", write=True)
    sp.add_argument("--media-id", required=True, help="IG media ID (see ig-media)")
    sp.add_argument("--page-id", help="Page connected to the IG account (default META_PAGE_ID)")
    sp.add_argument("--ig-user-id", help="IG business account ID (auto-resolved from page)")
    sp.add_argument("--name", required=True)
    sp.add_argument("--call-to-action", help="Optional CTA type")
    sp.add_argument("--link", help="CTA landing URL")
    sp.add_argument("--no-enhancements", action="store_true",
                    help="Opt out of all Advantage+ enhancements (14 features, enroll_status=OPT_OUT)")

    sp = _cmd(sub, "ig-media", cmd_ig_media, "List IG media of the page-connected account")
    sp.add_argument("--page-id", help="Page ID (default META_PAGE_ID)")
    sp.add_argument("--limit", type=int, default=25)

    sp = _cmd(sub, "preview", cmd_preview, "HTML preview of an ad/creative (iframe valid ~24 h)")
    sp.add_argument("--ad-id", help="Preview an existing ad")
    sp.add_argument("--creative-id", help="Preview an existing creative")
    sp.add_argument("--creative-spec", help="Preview an inline creative spec JSON")
    sp.add_argument("--format", default="MOBILE_FEED_STANDARD",
                    help="Ad format (MOBILE_FEED_STANDARD, DESKTOP_FEED_STANDARD, INSTAGRAM_STANDARD, "
                         "INSTAGRAM_STORY, INSTAGRAM_REELS, FACEBOOK_STORY_MOBILE, RIGHT_COLUMN_STANDARD, ...)")
    sp.add_argument("--height", type=int)
    sp.add_argument("--width", type=int)
    sp.add_argument("--out", help="Write preview HTML to this file")

    sp = _cmd(sub, "creative-delete", cmd_creative_delete, "DELETE a creative (permanent!)", write=True)
    sp.add_argument("--creative-id", required=True)

    sp = _cmd(sub, "image-upload", cmd_image_upload, "Upload image, returns hash (direct write)")
    sp.add_argument("--file", required=True, help="Path to image file")

    sp = _cmd(sub, "video-upload", cmd_video_upload, "Upload video, returns ID (direct write)")
    sp.add_argument("--file", required=True, help="Path to video file")
    sp.add_argument("--title", help="Video title")
    sp.add_argument("--chunked", action="store_true",
                    help="Force resumable chunked upload (automatic for files > 100 MB)")
    sp.add_argument("--wait", action="store_true",
                    help="Poll until processing is 'ready' (needed before using in a creative)")
    sp.add_argument("--wait-timeout", type=int, default=300, help="Max seconds to wait (default 300)")

    # ----- Insights & analysis --------------------------------------------
    sp = _cmd(sub, "insights", cmd_insights, "Performance insights (sync)")
    _add_insight_args(sp)
    sp.add_argument("--limit", type=int, default=100)

    sp = _cmd(sub, "insights-report", cmd_insights_report, "Async insights report (large queries)")
    _add_insight_args(sp, level_required=True)

    sp = _cmd(sub, "pulse", cmd_pulse, "Account digest: deltas, movers, review issues, limits, token")
    sp.add_argument("--days", type=int, default=7, help="Window length in days (default 7)")

    sp = _cmd(sub, "activities", cmd_activities, "Account change history (who changed what)")
    sp.add_argument("--since", help="YYYY-MM-DD (default: API's ~1 week window)")
    sp.add_argument("--until", help="YYYY-MM-DD")
    sp.add_argument("--category", choices=CATEGORIES)
    sp.add_argument("--object-id", help="Filter to one object (oid)")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--verbose", action="store_true", help="Show extra_data (old/new values)")

    # ----- Conversions (read-only) ----------------------------------------
    _cmd(sub, "pixels", cmd_pixels, "List account pixels/datasets (read-only)")
    sp = _cmd(sub, "custom-conversions", cmd_custom_conversions, "List custom conversions (read-only)")
    sp.add_argument("--limit", type=int, default=100)

    # ----- Targeting search -----------------------------------------------
    sp = _cmd(sub, "interest-search", cmd_interest_search, "Search detailed-targeting interests")
    sp.add_argument("--q", required=True, help="Keyword")
    sp.add_argument("--limit", type=int, default=25)

    sp = _cmd(sub, "interest-suggest", cmd_interest_suggest, "Suggest related interests")
    sp.add_argument("--interests", required=True, help="Comma-separated interest names")
    sp.add_argument("--limit", type=int, default=25)

    sp = _cmd(sub, "interest-validate", cmd_interest_validate, "Validate interest names")
    sp.add_argument("--interests", required=True, help="Comma-separated interest names")

    sp = _cmd(sub, "geo-search", cmd_geo_search, "Search geo targeting keys")
    sp.add_argument("--q", required=True, help="Location name")
    sp.add_argument("--location-types", help="Comma: country,region,city,zip,geo_market")
    sp.add_argument("--country-code", help="Restrict to country, e.g. CZ")
    sp.add_argument("--limit", type=int, default=25)

    sp = _cmd(sub, "locale-search", cmd_locale_search, "Search locale keys for language targeting")
    sp.add_argument("--q", required=True, help="Language name, e.g. czech")
    sp.add_argument("--limit", type=int, default=25)

    return parser


def main() -> None:
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    _print_banner()
    # parse_args first: --help/--version must work before .env is filled in
    # (argparse handles both during parsing and exits on its own).
    parser = build_parser()
    args = parser.parse_args()
    api.check_config()

    if args.account_id and not args.account_id.startswith("act_"):
        args.account_id = f"act_{args.account_id}"

    # Daily-cached token expiry warning on every command except token ops
    if args.command not in ("token-info", "token-extend"):
        api.maybe_warn_token_expiry()

    args.func(args)


if __name__ == "__main__":
    main()
