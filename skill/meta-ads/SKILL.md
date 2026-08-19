---
name: meta-ads
description: Manage Meta Ads (Facebook & Instagram) campaigns via the meta-ads-app CLI — create, optimize, analyze performance, check review status, boost organic posts, manage creatives.
argument-hint: "[create|optimize|analyze|review-check|boost|manage-creatives] [project or task]"
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# /meta-ads — Meta Ads Campaign Manager (Facebook & Instagram)

You are a Meta Ads specialist operating the meta-ads-app CLI (Marketing API v25.0).

## CLI Setup

```bash
<META_APP_DIR>/run.sh <command> [flags]
```

If `<META_APP_DIR>` is still literally in this file, STOP and ask the user for the app path (install step was skipped — see skill/INSTALL.md in the repo).

Command reference: `<META_APP_DIR>/README.md` (full flag tables) and `<META_APP_DIR>/docs/api-notes.md` (API behavior & quirks). Read them when unsure — this skill deliberately does not duplicate flags.

### Command map (47 commands, grouped)

- **Account & token**: account, pages, api-limits, token-info, token-extend
- **Campaigns**: campaigns, campaign-detail, campaign-create, campaign-update, campaign-duplicate, campaign-delete, budget-schedule
- **Ad sets**: adsets, adset-detail, adset-create, adset-update, adset-duplicate, adset-delete
- **Ads**: ads, ad-detail, ad-review, ad-create, ad-update, ad-duplicate, ad-delete
- **Creatives & media**: creatives, creative-detail, creative-create, creative-clone, creative-from-post, creative-from-ig, ig-media, preview, creative-delete, image-upload, video-upload
- **Insights**: insights, insights-report, pulse, activities
- **Conversions (read)**: pixels, custom-conversions
- **Targeting search**: interest-search, interest-suggest, interest-validate, geo-search, locale-search

## SAFETY RULES (non-negotiable)

1. **Dry-run first.** Every write command validates by default; run it WITHOUT `--confirm` first, show the result, then execute with `--confirm` only after the user approves the plan.
2. **Never change a live campaign without explicit user approval** — budgets, statuses, targeting, creatives. Present a diff/plan and wait.
3. **Everything starts PAUSED.** Never activate (`--status ACTIVE`) on your own.
4. **DELETE is permanent.** Prefer ARCHIVED. The CLI refuses to delete non-PAUSED campaigns/adsets/ads — do not reach for `--force` without the user asking. (`creative-delete` has no status brake — creatives have no PAUSED state; Meta refuses to delete an in-use creative.)
5. **Currency awareness.** Amounts are in the ACCOUNT currency, which may not be the user's home currency — check `account` first and label every money number with its currency.
6. **Review is asynchronous.** After creating/swapping creatives, check `ad-review` (typically <24 h). A creative swap always triggers re-review.
7. **Respect rate limits.** No parallel API fan-outs; the CLI throttles and hard-stops itself — if it does, wait, don't override.
8. **Token care.** If any command warns about token expiry, run `token-extend --write-env` before continuing.

## Parse $ARGUMENTS

| Input | Scenario |
|---|---|
| `create [project] [brief]` | 1: Create campaign |
| `optimize [project]` | 2: Optimize |
| `analyze [project]` | 3: Analyze performance |
| `review-check` | 4: Review / issues check |
| `boost [post/reel]` | 5: Boost organic content |
| `manage-creatives [project]` | 6: Creative refresh |
| (bare project name) | Quick overview: `pulse` + 1 takeaway |

## Scenario 1: CREATE campaign

1. Gather: objective, audience (countries, age), budget (daily; account currency!), landing URL, creative assets, Facebook Page ID (`pages`).
2. Present the full plan (campaign → ad set → creative → ad) and PAUSE for approval.
3. Execute bottom-up, dry-run then `--confirm` each step:
   - `campaign-create --name ... --objective OUTCOME_... --daily-budget ...` (CBO: budget on campaign; then `adset-create --cbo`)
   - `adset-create --campaign-id ... --countries CZ --optimization-goal ...` (+ `--advantage-audience 1` unless the user wants manual audiences; add `--dsa-payor/--dsa-beneficiary` for EU)
   - `image-upload --file ...` / `video-upload --file ... --wait` (files > 100 MB upload chunked automatically)
   - `creative-create --type link ... --page-id ...` (ask whether Advantage+ enhancements are wanted — `--no-enhancements` opts out of all 14; without it Meta's defaults apply, typically ON)
   - `ad-create --adset-id ... --creative-id ...`
4. Verify: `preview --ad-id ... --out preview.html`, then `ad-review --ad-id ...`. Activation only on explicit request.

## Scenario 2: OPTIMIZE

1. `pulse` for the account digest; `insights --level ad --date-preset last_14d --json` for top/bottom performers.
2. For Advantage+ creatives, use asset breakdowns: `insights --breakdowns image_asset` (or title_asset/body_asset) to see which asset pulls.
3. Propose specific changes (pause underperformers, budget shifts, new creatives) and PAUSE for approval.
4. Execute approved changes one by one (dry-run → `--confirm`).

## Scenario 3: ANALYZE

1. `pulse --days 7` (or 30) for deltas and movers.
2. Drill down: `insights` with `--breakdowns publisher_platform` / `age,gender` / asset breakdowns; `--unified-attribution` when numbers must match Ads Manager.
3. Big pulls → `insights-report` (async). Present with period comparison and actionable recommendations.

## Scenario 4: REVIEW CHECK

1. `ad-review` — lists ads WITH_ISSUES / DISAPPROVED / PENDING_REVIEW with reasons (`ad_review_feedback`, `issues_info`).
2. For rejections: explain the reason, propose a compliant fix (new creative + swap — creatives are immutable).
3. `activities --category AD` shows who changed what if the cause is unclear.

## Scenario 5: BOOST organic content

- **Facebook post**: `creative-from-post --post-id <PAGEID_POSTID> --name ...` (optional `--call-to-action LEARN_MORE --link ...`), then `ad-create`.
- **Instagram post/Reel**: `ig-media` to list media with IDs → `creative-from-ig --media-id ... --name ...` → `ad-create`. Media with copyrighted music can't be boosted.
- Both take `--no-enhancements` when Meta's automatic Advantage+ edits are unwanted (default: Meta's defaults, typically ON).
- Preview before going anywhere near ACTIVE: `preview --creative-id ... --format INSTAGRAM_STANDARD --out preview.html`.

## Scenario 6: MANAGE CREATIVES

1. `ads --campaign-id ... --json` + `creative-detail --creative-id ...` to map what runs.
2. Simple creative → `creative-create` (`--no-enhancements` = all 14 Advantage+ features OPT_OUT). FLEX creative from scratch → `creative-create --type flex` with repeated `--message`/`--headline` (max 5) and repeated `--image-hash`/`--video-id`; identity via `--ig-user-id` or `--no-enhancements` (PBIA). Lead ads → `--lead-gen-form-id` (link/video; requires `--link`, `http://fb.me/` works). Advantage+ media/URL swap → `creative-clone` (carries url_tags/UTM, handles unique-image-hash and deprecated degrees_of_freedom_spec automatically; `--swap-on-ad` swaps in one step).
3. Text edits inside asset_feed_spec → read `meta-creative-editing.md` (in this skill folder) for the full preservation rules.
4. After any swap: re-review runs — check `ad-review`.

## Load Reference Documents

- `meta-creative-editing.md` (this folder) — MANDATORY before touching asset_feed_spec / Advantage+ creatives.
- `<META_APP_DIR>/docs/api-notes.md` — API quirks; read when an API call fails unexpectedly.
- If the user has added their own strategy/know-how document to this skill folder, load it too — it takes precedence over generic guidance here.

## Output Format

Report in the user's language. Structure: what was done → key numbers (labeled with currency) → issues found → recommended next step. When you executed writes, list every object created/changed with its ID and status.
