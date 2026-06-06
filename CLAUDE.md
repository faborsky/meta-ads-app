# Meta Ads CLI — Facebook & Instagram Ad Management

Python CLI for managing Meta Ads campaigns via the Marketing API v25.0.

## Setup

```bash
source <APP_DIR>/venv/bin/activate && python <APP_DIR>/meta_ads_cli.py <command> [flags]
```

## Authentication

- Long-lived token in `.env` as `META_ACCESS_TOKEN` (60-day expiry)
- Ad account ID in `.env` as `META_AD_ACCOUNT_ID` (format: `act_XXXXXXXXX`)
- Default Facebook Page ID in `.env` as `META_PAGE_ID`
- Override account per-command: `--account-id act_XXXXX`
- Token status: `token-info` shows expiry, `token-extend` renews for 60 days
- App credentials in `.env`: `META_APP_ID`, `META_APP_SECRET` (needed for token-extend)

## Budget Convention

- **CLI accepts/displays amounts in the account currency**
- API uses cents internally (100 cents = 1 unit)
- CLI converts automatically in both directions

## Commands Reference

### Account & Token
| Command | Key Flags |
|---------|-----------|
| `account` | `--json` |
| `pages` | `--json` |
| `token-info` | `--json` |
| `token-extend` | `--json` |

### Campaigns
| Command | Key Flags |
|---------|-----------|
| `campaigns` | `--status ACTIVE/PAUSED/ARCHIVED`, `--limit`, `--json` |
| `campaign-detail` | `--campaign-id`, `--json` |
| `campaign-create` | `--name`, `--objective`, `--daily-budget`, `--status PAUSED`, `--json` |
| `campaign-update` | `--campaign-id`, `--name`, `--status`, `--daily-budget`, `--confirm`, `--json` |
| `campaign-duplicate` | `--campaign-id`, `--deep-copy`, `--status-option PAUSED`, `--json` |

### Ad Sets
| Command | Key Flags |
|---------|-----------|
| `adsets` | `--campaign-id`, `--status`, `--limit`, `--json` |
| `adset-detail` | `--adset-id`, `--json` |
| `adset-create` | `--campaign-id`, `--name`, `--optimization-goal`, `--targeting JSON`, `--daily-budget`, `--json` |
| `adset-update` | `--adset-id`, `--name`, `--status`, `--daily-budget`, `--targeting`, `--confirm`, `--json` |
| `adset-duplicate` | `--adset-id`, `--campaign-id` (target), `--deep-copy`, `--json` |

### Ads
| Command | Key Flags |
|---------|-----------|
| `ads` | `--adset-id`, `--campaign-id`, `--status`, `--limit`, `--json` |
| `ad-detail` | `--ad-id`, `--json` |
| `ad-create` | `--adset-id`, `--name`, `--creative-id`, `--status PAUSED`, `--json` |
| `ad-update` | `--ad-id`, `--name`, `--status`, `--creative-id`, `--confirm`, `--json` |
| `ad-duplicate` | `--ad-id`, `--adset-id` (target), `--json` |

### Media & Creatives
| Command | Key Flags |
|---------|-----------|
| `image-upload` | `--file`, `--json` |
| `video-upload` | `--file`, `--title`, `--wait`, `--wait-timeout`, `--json` |
| `creative-create` | `--name`, `--type link/video/photo/carousel`, `--page-id`, `--message`, `--link`, `--headline`, `--image-hash`, `--video-id`, `--call-to-action`, `--json` |
| `creative-clone` | `--creative-id` (source), `--name`, `--swap-video`, `--swap-thumbnail`, `--swap-image`, `--new-url`, `--swap-on-ad`, `--json` |
| `creatives` | `--limit`, `--json` |
| `creative-detail` | `--creative-id`, `--json` |

**`video-upload --wait`**: Upload returns the video ID immediately, but the video is still `processing`. Building a creative that references a not-yet-`ready` video can fail. Use `--wait` to poll until ready before creating a creative.

**`creative-clone`**: Clones an existing Advantage+ creative (immutable) into a new one, swapping media/URL while preserving texts, adlabels and `asset_customization_rules`. Handles the duplicate-image-hash and deprecated-`degrees_of_freedom_spec` gotchas automatically. Add `--swap-on-ad <AD_ID>` to point an ad at the new creative in one call (triggers re-review). Example — replace the video on a large-scale ad:
```bash
VID=$(python meta_ads_cli.py video-upload --file new.mp4 --wait --json | jq -r .id)
HASH=$(python meta_ads_cli.py image-upload --file thumb.jpg --json | jq -r .hash)
python meta_ads_cli.py creative-clone --creative-id <OLD_CREATIVE> --name "Video V2" \
  --swap-video "$VID" --swap-thumbnail "$HASH" --swap-image "$HASH" \
  --new-url "https://example.com/lp" --swap-on-ad <AD_ID>
```

### Insights
| Command | Key Flags |
|---------|-----------|
| `insights` | `--object-id`, `--level`, `--date-preset`, `--date-from`, `--date-to`, `--breakdowns`, `--json` |
| `insights-report` | `--object-id`, `--level` (required), `--date-preset`, `--breakdowns`, `--json` |

## Safety Rules

- All create commands default to `--status PAUSED` — nothing goes live automatically
- `campaign-update`, `adset-update`, `ad-update` with `--status PAUSED/ARCHIVED` require `--confirm`
- Duplicate commands always create PAUSED copies by default
- Always use `--json` flag when parsing output programmatically

## API Gotchas

- **Creatives are immutable**: Creative objects (`/adcreatives`) cannot be modified after creation. To "edit" text/images, create a NEW creative and swap it on the ad via `POST /{ad-id}` with `creative={"creative_id":"<NEW_ID>"}`. The ad goes through re-review (same as UI edit).
- **asset_feed_spec**: Advantage+ / Dynamic Creative ads use `asset_feed_spec` with multiple bodies/titles/descriptions/images/videos + `asset_customization_rules` (placement targeting) + `adlabels` (linking assets to rules). When recreating, preserve ALL adlabels, customization rules, and `degrees_of_freedom_spec`.
- **degrees_of_freedom_spec cleanup**: When reading an existing creative, the API returns deprecated fields (`standard_enhancements`, `advantage_plus_creative`, `cv_transformation`, `image_animation`, `replace_media_text`, `show_destination_blurbs`, `show_summary`). These MUST be removed before creating a new creative, or the API rejects the request. (`creative-clone` does this automatically.)
- **Unique image hashes in asset_feed_spec**: Every entry in `images[]` must have a UNIQUE `hash`. Putting the same hash in two slots fails with `error_subcode 1815629` "Duplicates of ad asset values are not allowed". When you only have one new image but two image slots, collapse them into ONE entry carrying all the original `adlabels` (so `asset_customization_rules` still resolve). A video's `thumbnail_hash` MAY equal an image hash — the uniqueness constraint is within `images[]` only. (`creative-clone --swap-image` handles this.)
- **Read-only response fields**: `asset_feed_spec` read returns `reasons_to_shop` / `shops_bundle` as `false` — strip falsy values before re-creating, or the API may reject them.
- **CLI vs direct API**: The `creative-create` CLI command creates simple creatives (single image/video/carousel). For Advantage+ creatives with `asset_feed_spec`, use direct API calls via Python script importing `meta_ads_cli._api_call()`.
- **Budget in cents**: API uses cents, CLI handles conversion automatically
- **Cursor pagination**: Meta uses cursor-based pagination (not offset). The CLI handles this transparently
- **Async insights**: For large queries (long date ranges, many breakdowns), use `insights-report` which runs async
- **Targeting JSON**: Must be valid JSON string. Minimum: `{"geo_locations":{"countries":["CZ"]}}`
- **object_story_spec**: Creative creation requires a Facebook Page (`--page-id`). Use different page IDs for different projects
- **Rate limits (Dev tier)**: 60 points per 5 min window (read=1pt, write=3pt). CLI handles throttling automatically. Standard tier: 9000 points.
- **Token expiry**: Long-lived tokens last 60 days. Use `token-info` to check, `token-extend` to renew
- **Ad review**: Changing creative triggers review. Budget/bid/schedule changes do NOT. Paused ads stay paused after review.
- **Post-processing**: After creative creation, status may be `IN_PROCESS` before `ACTIVE` or `WITH_ISSUES`. Poll status if needed.
- **Batch requests**: Up to 50 requests per batch via `POST https://graph.facebook.com` with `batch` param. Max 10 for ad creation. Useful for bulk creative swaps.
- **ARCHIVED vs DELETED**: Archived objects are queryable (max 100K per type). Deleted objects only by direct ID. Stats tracked 28 days after last delivery.
- **App must be in Live mode**: Creating ad creatives that generate page posts requires the app to be published (not in development mode).

## Workflow: Creating an Ad from Scratch

```bash
# 1. Create campaign
python meta_ads_cli.py campaign-create --name "My Campaign" --objective OUTCOME_TRAFFIC --daily-budget 10 --json

# 2. Create ad set with targeting
python meta_ads_cli.py adset-create --campaign-id <CAMPAIGN_ID> --name "CZ Broad" \
  --optimization-goal LINK_CLICKS --daily-budget 10 \
  --targeting '{"geo_locations":{"countries":["CZ"]},"age_min":25,"age_max":55}' --json

# 3. Upload image
python meta_ads_cli.py image-upload --file creative.jpg --json

# 4. Create creative
python meta_ads_cli.py creative-create --name "Ad Creative" --type link \
  --page-id <PAGE_ID> --link "https://example.com" \
  --image-hash <HASH> --headline "Check this out" \
  --message "Great offer" --call-to-action LEARN_MORE --json

# 5. Create ad
python meta_ads_cli.py ad-create --adset-id <ADSET_ID> --name "My Ad" \
  --creative-id <CREATIVE_ID> --json
```

## Objectives (ODAX)

| Objective | Description |
|-----------|-------------|
| OUTCOME_AWARENESS | Brand awareness and reach |
| OUTCOME_TRAFFIC | Drive traffic to website |
| OUTCOME_ENGAGEMENT | Post engagement, video views |
| OUTCOME_LEADS | Lead generation forms |
| OUTCOME_SALES | Conversions, catalog sales |
| OUTCOME_APP_PROMOTION | App installs |
