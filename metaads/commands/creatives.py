"""Commands: creatives, creative-detail/create/clone/delete,
creative-from-post, creative-from-ig, ig-media, preview."""

from __future__ import annotations

import copy
import json

from metaads import api, lint
from metaads.commands.common import account_of, drop_deleted, parse_json_arg
from metaads.formatting import _die, _err, _output_json, _truncate

CREATIVE_LIST_FIELDS = "id,name,status,thumbnail_url,title,body,link_url,image_url,call_to_action_type,object_type"

CREATIVE_DETAIL_FIELDS = ("id,name,status,body,title,link_url,image_url,image_hash,thumbnail_url,"
                          "object_story_spec,object_story_id,asset_feed_spec,call_to_action_type,"
                          "object_type,url_tags,effective_object_story_id,"
                          "source_instagram_media_id,effective_instagram_media_id")


def cmd_creatives(args) -> None:
    """List ad creatives."""
    account_id = account_of(args)
    params: dict = {"fields": CREATIVE_LIST_FIELDS, "limit": args.limit}

    creatives = api._paginate(f"{account_id}/adcreatives", params, max_items=args.limit)
    creatives = drop_deleted(creatives, None)

    if args.json:
        _output_json(creatives)
        return

    if not creatives:
        print("No creatives found.")
        return

    print(f"{'ID':<20} {'Name':<30} {'Status':<12} {'Type':<15} {'CTA':<15} {'Title'}")
    print("-" * 120)
    for c in creatives:
        print(f"{c['id']:<20} {_truncate(c.get('name', '---'), 28):<30} {c.get('status', '---'):<12} "
              f"{c.get('object_type', '---'):<15} {_truncate(c.get('call_to_action_type', '---'), 13):<15} "
              f"{_truncate(c.get('title', '---'), 30)}")


def cmd_creative_detail(args) -> None:
    """Show creative details."""
    data = api._api_call("GET", str(args.creative_id), {"fields": CREATIVE_DETAIL_FIELDS})

    if args.json:
        _output_json(data)
        return

    print(f"Creative: {data.get('name', '---')}")
    print(f"  ID:              {data.get('id')}")
    print(f"  Status:          {data.get('status', '---')}")
    print(f"  Type:            {data.get('object_type', '---')}")
    for key, label in (("body", "Body"), ("title", "Title"), ("link_url", "Link URL"),
                       ("image_hash", "Image hash"), ("call_to_action_type", "CTA"),
                       ("url_tags", "URL tags"), ("object_story_id", "Story ID"),
                       ("source_instagram_media_id", "IG media"),
                       ("effective_instagram_media_id", "IG media (eff.)")):
        if data.get(key):
            print(f"  {label + ':':<17}{data[key]}")
    if data.get("image_url"):
        print(f"  Image URL:       {_truncate(data['image_url'], 80)}")
    if data.get("thumbnail_url"):
        print(f"  Thumbnail:       {_truncate(data['thumbnail_url'], 80)}")

    oss = data.get("object_story_spec")
    if oss:
        print("  Object story spec:")
        print(f"    {json.dumps(oss, indent=4, ensure_ascii=False)}")

    afs = data.get("asset_feed_spec")
    if afs:
        print("  Asset feed spec:")
        print(f"    {json.dumps(afs, indent=4, ensure_ascii=False)}")


def cmd_creative_create(args) -> None:
    """Create a new ad creative (dry-run/validate by default)."""
    account_id = account_of(args)
    page_id = args.page_id or api.META_PAGE_ID

    if not page_id:
        _die("ERROR: --page-id required (or set META_PAGE_ID in .env).")

    lint.lint_texts(args.message, args.headline, args.description)
    lint.lint_url(args.link)
    lint.lint_cta(args.call_to_action)

    params: dict = {"name": args.name}
    if args.url_tags:
        params["url_tags"] = args.url_tags

    creative_type = args.type

    if creative_type == "link":
        link_data: dict = {"link": args.link, "message": args.message or ""}
        if args.headline:
            link_data["name"] = args.headline
        if args.description:
            link_data["description"] = args.description
        if args.image_hash:
            link_data["image_hash"] = args.image_hash
        elif args.image_url:
            link_data["picture"] = args.image_url
        if args.call_to_action:
            link_data["call_to_action"] = {"type": args.call_to_action}
        params["object_story_spec"] = json.dumps({"page_id": page_id, "link_data": link_data})

    elif creative_type == "video":
        if not args.video_id:
            _die("ERROR: --video-id required for video creative.")
        video_data: dict = {"video_id": args.video_id, "message": args.message or ""}
        if args.headline:
            video_data["title"] = args.headline
        if args.video_thumbnail:
            video_data["image_url"] = args.video_thumbnail
        if args.call_to_action:
            video_data["call_to_action"] = {"type": args.call_to_action, "value": {"link": args.link or ""}}
        params["object_story_spec"] = json.dumps({"page_id": page_id, "video_data": video_data})

    elif creative_type == "photo":
        if not args.image_hash:
            _die("ERROR: --image-hash required for photo creative.")
        params["object_story_spec"] = json.dumps({
            "page_id": page_id,
            "photo_data": {"image_hash": args.image_hash, "message": args.message or ""},
        })

    elif creative_type == "carousel":
        if not args.child_attachments:
            _die("ERROR: --child-attachments required for carousel creative.")
        if not args.link:
            _die("ERROR: --link required for carousel creative.")
        link_data_c: dict = {
            "link": args.link,
            "message": args.message or "",
            "child_attachments": parse_json_arg(args.child_attachments, "--child-attachments"),
        }
        if args.call_to_action:
            link_data_c["call_to_action"] = {"type": args.call_to_action}
        params["object_story_spec"] = json.dumps({"page_id": page_id, "link_data": link_data_c})
    else:
        _die(f"ERROR: Unknown creative type: {creative_type}")

    data, executed = api.mutate(f"{account_id}/adcreatives", params, args.confirm)

    if args.json:
        _output_json({"executed": executed, "response": data})
        return
    api.print_mutation_result(data, executed, f"Creative created: ID {(data or {}).get('id')}")


# Deprecated degrees_of_freedom_spec fields: the API returns them on read but
# rejects them on create (standard_enhancements bundle is dead since v22 —
# individual creative_features_spec features are the current mechanism).
_DOF_DEPRECATED = [
    "standard_enhancements", "advantage_plus_creative", "cv_transformation",
    "image_animation", "replace_media_text", "show_destination_blurbs", "show_summary",
]


def cmd_creative_clone(args) -> None:
    """Clone an existing Advantage+ creative, optionally swapping video/image/URL.

    Creative objects are immutable. Reads the source spec, applies swaps while
    preserving texts, adlabels and asset_customization_rules, creates a NEW
    creative, and optionally swaps it onto an ad (--swap-on-ad, needs --confirm).
    """
    account_id = account_of(args)
    lint.lint_url(args.new_url)

    orig = api._api_call("GET", args.creative_id, {
        "fields": "object_story_spec,asset_feed_spec,degrees_of_freedom_spec",
    })
    afs = copy.deepcopy(orig.get("asset_feed_spec") or {})
    if not afs:
        _die("ERROR: Source creative has no asset_feed_spec (not an Advantage+ creative). "
             "Use creative-create for simple creatives.")

    if args.swap_video:
        for v in afs.get("videos", []):
            v["video_id"] = args.swap_video
            if args.swap_thumbnail:
                v["thumbnail_hash"] = args.swap_thumbnail
                v.pop("thumbnail_url", None)

    # swap fallback image(s) -> collapse to one unique entry with all adlabels
    # (duplicate hashes in images[] fail with error_subcode 1815629)
    if args.swap_image:
        imgs = afs.get("images", [])
        if imgs:
            all_labels: list = []
            for img in imgs:
                all_labels.extend(img.get("adlabels", []))
            afs["images"] = [{"adlabels": all_labels, "hash": args.swap_image}]
        else:
            afs["images"] = [{"hash": args.swap_image}]

    if args.new_url:
        for u in afs.get("link_urls", []):
            u["website_url"] = args.new_url

    # drop read-only/false response fields the API rejects on create
    for rk in ("reasons_to_shop", "shops_bundle"):
        if rk in afs and not afs[rk]:
            afs.pop(rk, None)

    dof = copy.deepcopy(orig.get("degrees_of_freedom_spec") or {})
    if dof:
        cfs = dof.get("creative_features_spec", {})
        for d in _DOF_DEPRECATED:
            cfs.pop(d, None)

    payload = {
        "name": args.name,
        "object_story_spec": json.dumps(orig.get("object_story_spec") or {}),
        "asset_feed_spec": json.dumps(afs),
    }
    if dof:
        payload["degrees_of_freedom_spec"] = json.dumps(dof)

    data, executed = api.mutate(f"{account_id}/adcreatives", payload, args.confirm)
    new_id = (data or {}).get("id") if executed else None

    result: dict = {"executed": executed, "new_creative_id": new_id}
    if args.swap_on_ad:
        if executed and new_id:
            swap = api._api_call("POST", args.swap_on_ad, {"creative": json.dumps({"creative_id": new_id})})
            result["ad_swap"] = swap
            result["ad_id"] = args.swap_on_ad
        elif not executed:
            result["ad_swap_plan"] = f"would swap new creative onto ad {args.swap_on_ad} (re-review)"

    if args.json:
        _output_json(result)
        return
    if executed:
        print(f"New creative: {new_id}")
        if args.swap_on_ad:
            print(f"Swapped onto ad {args.swap_on_ad}: {result.get('ad_swap')} (triggers re-review)")
    else:
        api.print_mutation_result(data, executed, "")
        if args.swap_on_ad:
            print(f"Plan: swap new creative onto ad {args.swap_on_ad} (triggers re-review).")


def cmd_creative_from_post(args) -> None:
    """Create a creative from an existing Facebook Page post (boost organic content)."""
    account_id = account_of(args)

    post_id = args.post_id
    if "_" not in post_id:
        page_id = args.page_id or api.META_PAGE_ID
        if not page_id:
            _die("ERROR: --post-id without PAGEID_ prefix needs --page-id (or META_PAGE_ID in .env).")
        post_id = f"{page_id}_{post_id}"

    params: dict = {"name": args.name, "object_story_id": post_id}
    if args.call_to_action:
        lint.lint_cta(args.call_to_action)
        lint.lint_url(args.link)
        cta: dict = {"type": args.call_to_action}
        if args.link:
            cta["value"] = {"link": args.link}
        params["call_to_action"] = json.dumps(cta)

    data, executed = api.mutate(f"{account_id}/adcreatives", params, args.confirm)

    if args.json:
        _output_json({"executed": executed, "response": data, "object_story_id": post_id})
        return
    api.print_mutation_result(
        data, executed,
        f"Creative from post {post_id}: ID {(data or {}).get('id')}",
    )


def _resolve_ig_user(page_id: str, account_id: str | None = None) -> dict:
    """Page → connected Instagram business account (id, username).

    Reading the page node needs the page granted to the token; when that fails
    (common with Explorer tokens where no pages were selected), fall back to
    the ad account's connected IG accounts (ads permission only).
    """
    try:
        pdata = api._api_call("GET", str(page_id), {"fields": "instagram_business_account{id,username}"})
        ig = pdata.get("instagram_business_account")
        if ig:
            return ig
        _err(f"Note: page {page_id} has no connected IG business account — trying the ad account.")
    except SystemExit:
        _err("Note: page read failed (page not granted to this token) — "
             "falling back to the ad account's connected IG accounts.")

    if account_id:
        data = api._api_call("GET", f"{account_id}/connected_instagram_accounts",
                             {"fields": "id,username"})
        rows = data.get("data", [])
        if rows:
            if len(rows) > 1:
                _err("Multiple connected IG accounts: "
                     + ", ".join(f"@{r.get('username', '?')} ({r['id']})" for r in rows)
                     + " — using the first; pass --ig-user-id to pick another.")
            return rows[0]

    _die(f"ERROR: No Instagram business account reachable for page {page_id}.\n"
         "  Grant the page to the token, or create a page-backed IG account (PBIA).")
    return {}  # unreachable


def cmd_creative_from_ig(args) -> None:
    """Create a creative from an existing Instagram post/Reel (boost organic IG content)."""
    account_id = account_of(args)
    page_id = args.page_id or api.META_PAGE_ID
    if not page_id:
        _die("ERROR: --page-id required (or META_PAGE_ID in .env).")

    ig_user_id = args.ig_user_id
    if not ig_user_id:
        ig_user_id = _resolve_ig_user(page_id, account_id)["id"]

    params: dict = {
        "name": args.name,
        "object_id": page_id,
        "instagram_user_id": ig_user_id,
        "source_instagram_media_id": args.media_id,
    }
    if args.call_to_action:
        lint.lint_cta(args.call_to_action)
        lint.lint_url(args.link)
        cta: dict = {"type": args.call_to_action}
        if args.link:
            cta["value"] = {"link": args.link}
        params["call_to_action"] = json.dumps(cta)

    data, executed = api.mutate(f"{account_id}/adcreatives", params, args.confirm)

    if args.json:
        _output_json({"executed": executed, "response": data, "instagram_user_id": ig_user_id})
        return
    api.print_mutation_result(
        data, executed,
        f"Creative from IG media {args.media_id}: ID {(data or {}).get('id')}",
    )


def cmd_ig_media(args) -> None:
    """List Instagram media of the page-connected IG account (for creative-from-ig)."""
    page_id = args.page_id or api.META_PAGE_ID
    if not page_id:
        _die("ERROR: --page-id required (or META_PAGE_ID in .env).")

    ig = _resolve_ig_user(page_id, account_of(args))
    fields = "id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count"
    media = api._paginate(f"{ig['id']}/media", {"fields": fields, "limit": args.limit},
                          max_items=args.limit)

    if args.json:
        _output_json({"instagram_user": ig, "media": media})
        return

    print(f"IG account: @{ig.get('username', '?')} (ID {ig['id']})")
    if not media:
        print("No media found.")
        return
    print(f"{'Media ID':<20} {'Type':<10} {'Product':<8} {'Date':<12} {'Likes':<7} {'Cmts':<6} Caption")
    print("-" * 110)
    for m in media:
        ts = (m.get("timestamp") or "")[:10]
        print(f"{m['id']:<20} {m.get('media_type', '?'):<10} {m.get('media_product_type', '?'):<8} "
              f"{ts:<12} {m.get('like_count', 0):<7} {m.get('comments_count', 0):<6} "
              f"{_truncate(m.get('caption'), 40)}")


def cmd_preview(args) -> None:
    """Generate an HTML preview of an ad / creative / creative spec."""
    account_id = account_of(args)
    params: dict = {"ad_format": args.format}

    if args.ad_id:
        endpoint = f"{args.ad_id}/previews"
    elif args.creative_id:
        endpoint = f"{account_id}/generatepreviews"
        params["creative"] = json.dumps({"creative_id": args.creative_id})
    elif args.creative_spec:
        endpoint = f"{account_id}/generatepreviews"
        params["creative"] = args.creative_spec
    else:
        _die("ERROR: One of --ad-id, --creative-id or --creative-spec is required.")

    if args.height:
        params["height"] = args.height
    if args.width:
        params["width"] = args.width

    data = api._api_call("GET", endpoint, params)
    previews = data.get("data", [])

    if args.json:
        _output_json(previews)
        return

    if not previews:
        print("No preview returned.")
        return

    body = previews[0].get("body", "")
    if args.out:
        html = ("<!doctype html><html><head><meta charset='utf-8'>"
                f"<title>Meta ad preview — {args.format}</title></head>"
                f"<body style='margin:2em;background:#f0f2f5'>{body}</body></html>")
        with open(args.out, "w") as f:
            f.write(html)
        print(f"Preview written to {args.out} (open in a browser; iframe URL is valid ~24 h).")
    else:
        print(body)
        _err("Tip: --out preview.html writes an openable file (iframe URL valid ~24 h).")


def cmd_creative_delete(args) -> None:
    """Delete a creative (permanent!). Fails on Meta side if the creative is in use.

    Note: unlike campaign/adset/ad-delete there is no PAUSED brake — creatives
    have no PAUSED status. The server-side protection is Meta refusing to
    delete a creative that is used by an ad.
    """
    data = api._api_call("GET", str(args.creative_id), {"fields": "name,status"})
    name = data.get("name", "---")

    if not args.confirm:
        if args.json:
            _output_json({
                "executed": False,
                "would_delete": {"kind": "creative", "id": str(args.creative_id),
                                 "name": name, "status": data.get("status", "?")},
                "note": "DELETE is permanent. Add --confirm to execute.",
            })
            return
        print(f"DRY-RUN: would DELETE creative {args.creative_id} \"{name}\" "
              f"(status {data.get('status', '?')}).")
        print("⚠ DELETE is permanent. Add --confirm to execute.")
        return

    api._api_call("DELETE", str(args.creative_id), {})
    if args.json:
        _output_json({"executed": True, "deleted": str(args.creative_id), "name": name})
    else:
        print(f"Creative {args.creative_id} \"{name}\" deleted.")
