# Editing Meta Ad Creatives — mechanics reference

Meta creative objects (`/adcreatives`) are **immutable**. The Ads Manager UI appears to edit them, but internally creates a new creative and swaps the reference. Via API you do this explicitly: create a NEW creative, swap it onto the ad (`ad-update --creative-id` or `creative-clone --swap-on-ad`). The ad then goes through re-review.

Load this file whenever you touch `asset_feed_spec` (Advantage+ / Dynamic Creative), do a placement-customized creative, or a bulk text update.

## Shortcut: `creative-clone` (media / URL swaps)

For the common case — swapping **video / image / landing URL** on an Advantage+ creative while preserving all texts, `url_tags` (UTM), adlabels and `asset_customization_rules` — use the built-in command instead of manual Python. It automatically handles unique-image-hash collapsing and deprecated `degrees_of_freedom_spec` cleanup. `--url-tags` overrides the carried-over UTM parameters; `--no-enhancements` replaces the source enhancement spec with a full 14-feature OPT_OUT:

```bash
VID=$(<META_APP_DIR>/run.sh video-upload --file new.mp4 --wait --json | jq -r .id)
HASH=$(<META_APP_DIR>/run.sh image-upload --file thumb.jpg --json | jq -r .hash)
<META_APP_DIR>/run.sh creative-clone --creative-id <OLD> --name "V2" \
  --swap-video "$VID" --swap-thumbnail "$HASH" --swap-image "$HASH" \
  --new-url "https://..." --swap-on-ad <AD_ID> --confirm
```

Upload videos with `--wait` — a creative referencing a still-`processing` video can fail.

## Text-only edit of an asset_feed_spec creative

Creating a NEW asset_feed_spec creative from scratch needs no manual Python — use `creative-create --type flex` (repeatable `--message`/`--headline`/`--description`, multiple `--image-hash`/`--video-id`). The recipe below is for EDITING an existing one.

Manual Python (the CLI has no text-edit command by design — too many variants). Import the engine and preserve everything except the texts:

```python
import json, sys, copy
sys.path.insert(0, '<META_APP_DIR>')
import meta_ads_cli as cli

original = cli._api_call('GET', '<CREATIVE_ID>', {
    'fields': 'id,name,object_story_spec,asset_feed_spec,degrees_of_freedom_spec,url_tags,status'
})

afs = copy.deepcopy(original['asset_feed_spec'])
for body in afs.get('bodies', []):
    body['text'] = body['text'].replace('old text', 'new text')
for title in afs.get('titles', []):
    title['text'] = title['text'].replace('old text', 'new text')
for desc in afs.get('descriptions', []):
    desc['text'] = desc['text'].replace('old text', 'new text')

# API returns deprecated fields on read but rejects them on create:
dof = copy.deepcopy(original.get('degrees_of_freedom_spec') or {})
cfs = dof.get('creative_features_spec', {})
for deprecated in ['standard_enhancements', 'advantage_plus_creative', 'cv_transformation',
                   'image_animation', 'replace_media_text', 'show_destination_blurbs', 'show_summary']:
    cfs.pop(deprecated, None)

# Strip falsy read-only response fields:
for rk in ('reasons_to_shop', 'shops_bundle'):
    if rk in afs and not afs[rk]:
        afs.pop(rk, None)

payload = {
    'name': 'Updated: <descriptive name>',
    'object_story_spec': json.dumps(original['object_story_spec']),
    'asset_feed_spec': json.dumps(afs),
}
if original.get('url_tags'):          # top-level field — dropping it loses UTM tracking
    payload['url_tags'] = original['url_tags']
if dof:
    payload['degrees_of_freedom_spec'] = json.dumps(dof)
new_creative = cli._api_call('POST', f'{cli.META_AD_ACCOUNT_ID}/adcreatives', payload)

# Swap on ad (re-review):
cli._api_call('POST', '<AD_ID>', {'creative': json.dumps({'creative_id': new_creative['id']})})
```

## Preservation rules (when recreating any asset_feed_spec creative)

- Keep ALL `adlabels` on images/videos/bodies/titles/link_urls — they connect assets to `asset_customization_rules`.
- Keep `asset_customization_rules`, `ad_formats`, `optimization_type`, `call_to_action_types` unchanged.
- Keep `object_story_spec` (page_id, instagram_user_id).
- Keep **`url_tags`** — it is a TOP-LEVEL creative field (not inside the specs), so it silently disappears unless read and re-sent explicitly. A creative without `url_tags` is valid, so no dry-run catches the loss — the clone just ships without UTM.
- Strip deprecated `degrees_of_freedom_spec` features and falsy read-only fields (see above).
- `images[]` hashes must be UNIQUE (error_subcode 1815629) — one new image for several slots ⇒ collapse into ONE entry carrying all original adlabels. A video's `thumbnail_hash` MAY equal an image hash (uniqueness applies within `images[]` only).

## Recipe: placement-customized creative (video everywhere + static square in right-hand column)

One creative = video for most placements + 1:1 image for `right_hand_column` (uncropped). Structure:

```python
afs = {
  'videos': [{'video_id': VID, 'adlabels': [{'name': 'lbl_video'}]}],
  'images': [{'hash': SQUARE_HASH, 'adlabels': [{'name': 'lbl_rhc'}]}],
  'bodies': [{'text': '...', 'adlabels': [{'name': 'lbl_body'}]}],
  'titles': [{'text': '...', 'adlabels': [{'name': 'lbl_title'}]}],
  'descriptions': [{'text': '...'}],
  'link_urls': [{'website_url': URL, 'adlabels': [{'name': 'lbl_link'}]}],
  'call_to_action_types': ['SIGN_UP'],
  'ad_formats': ['AUTOMATIC_FORMAT'],
  'optimization_type': 'PLACEMENT',
  'asset_customization_rules': [
    # priority 1 (specific): IMAGE in right-hand column
    {'customization_spec': {'age_min': 18, 'age_max': 65,
                            'publisher_platforms': ['facebook'],
                            'facebook_positions': ['right_hand_column']},
     'image_label': {'name': 'lbl_rhc'}, 'body_label': {'name': 'lbl_body'},
     'title_label': {'name': 'lbl_title'}, 'link_url_label': {'name': 'lbl_link'},
     'priority': 1},
    # priority 2 (catch-all, NO platforms/positions): VIDEO everywhere else
    {'customization_spec': {'age_min': 18, 'age_max': 65},
     'video_label': {'name': 'lbl_video'}, 'body_label': {'name': 'lbl_body'},
     'title_label': {'name': 'lbl_title'}, 'link_url_label': {'name': 'lbl_link'},
     'priority': 2},
  ],
}
```

**Gotchas (all hit in production):**
- ⚠️ `object_story_spec` MUST include `instagram_user_id` when the catch-all rule covers IG placements, or create/swap fails with error_subcode 1772103 "Instagram account is missing". Get it: `GET /{page_id}?fields=instagram_business_account`. Exception: a FLEX creative (`asset_feed_spec` WITHOUT `asset_customization_rules`) with page-only identity passes without an IG account when the payload includes a `degrees_of_freedom_spec` — Meta then uses the page-backed IG identity (PBIA).
- Rule ordering: specific rule = priority 1, catch-all = priority 2 with an EMPTY customization_spec (age only).
- When debugging a failing create, the real cause is in `error_user_msg` — the CLI prints it; in raw Python check `r.json()['error']['error_user_msg']`.

## Bulk text update workflow

1. **Audit** — read all creatives, scan for the outdated pattern.
2. **Plan** — show the user exact old → new text per creative.
3. **PAUSE** — wait for approval.
4. **Execute** per ad: read full creative → apply replacements → create new → swap → verify.
5. **Report** — summary of all changes (creative IDs old → new, ads touched, re-review note).
