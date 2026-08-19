# Meta Marketing API — poznámky k reálnému chování

Jak se API chová doopravdy. Položky ověřené proti oficiální dokumentaci (research 2026-07-19, aktualizace 2026-08-13) vs. položky označené **(živě)** = ověřeno na reálném účtu při vývoji.

## Verze & životní cyklus

- CLI je pinnuté na **v25.0** (release 2026-02-18). K 2026-08-13: **nejnovější je v26.0 (release 2026-07-29)**; Marketing API sunset v25.0 **není oznámen (TBD)** — podle ~1ročního vzoru čekej ~polovinu 2027. v24.0 sunset 2026-10-06, v23.0 mrtvá od 2026-06-09.
- **Auto-upgrade verzí (od 2026-07-29)**: cally na deprecated verzi Meta u endpointů nezasažených breaking changes automaticky povýší na další verzi (odpověď nese hlavičku `X-Ad-Api-Version-Warning`); endpointy se změnami dál failují. Jde vypnout v App Dashboardu.
- **Co si pohlídat při bumpu na v26.0**: Instagram **Explore placement** explicitně uvedený v `instagram_positions` → error; special ad categories (housing/employment/credit) nově **vyžadují explicitní** `targeting_automation.advantage_audience` 0/1 (CLI flag `--advantage-audience` existuje); Messenger Stories `story` se tiše zahazuje. `/copies`, `/budget_schedules`, `validate_only`, media a search endpointy beze změn.
- **Legacy Advantage+ Shopping/App kampaně** (`smart_promotion_type=AUTOMATED_SHOPPING_ADS`/`SMART_APP_PROMOTION`): create/duplicate/update **blokované na všech verzích API** od 2026-05-19; v září 2026 Meta zbývající legacy kampaně zapauzuje. Dnešní Advantage+ Sales = obyčejná `OUTCOME_SALES` kampaň + 3 automatizační páky (CBO rozpočet na kampani, `targeting_automation.advantage_audience=1`, žádná omezení placementů). Stav čti z read-only `advantage_state_info` (campaign-detail ho zobrazuje).

## Rate limity (BUC model — hodinová okna)

⚠️ Starý bodový model „60 bodů / 5 min (read=1, write=3), standard 9000" je **zastaralý** — neplatí.

- **Ads Management** (per app + ad account, klouzavá hodina):
  - development tier: `300 + 40 × počet aktivních reklam` callů/hod
  - standard tier: `100 000 + 40 × počet aktivních reklam` callů/hod
- **Mutace navíc**: 100 QPS (error 613, subcode 5044001).
- **Insights** mají vlastní throttling (header `x-fb-ads-insights-throttle`).
- Usage se čte **jen z response hlaviček** — žádný proaktivní endpoint neexistuje. Hlavička `X-Business-Use-Case-Usage`: `call_count`/`total_cputime`/`total_time` jsou **procenta** limitu, `estimated_time_to_regain_access` je v **minutách**, k tomu `ads_api_access_tier`.
- CLI: hlavičky parsuje po každém callu, persistuje do `.usage/usage.json`, varuje > 75 %, throttluje > 90 % a **hard-stopne ≥ 95 %** (čerstvá data < 10 min; override `METAADS_IGNORE_USAGE_GUARD=1`). `api-limits` ukáže aktuální stav.
- Error kódy: 17 (user request limit), 32 (page limit), 613 (custom/QPS/ad-creation cap dle spend limitu — subcode 1487225), 80000/80004 (BUC throttle; 80004 = ads_management), 4 (app limit / insights přetížení).
- **Přejmenování tierů (2026-05-04)**: „Ads Management Standard Access" → **„Marketing API Access Tier"** s úrovněmi **Limited access** (= starý development; stačí na vlastní/spravované účty, bez app review) a **Full access** (= starý standard; potřeba jen pro obsluhu cizích účtů — business verification + app review; auto-approval práh snížen na 500 callů / 15 dní s error rate < 15 %). Hlavička `ads_api_access_tier` zatím vrací staré hodnoty `development_access`/`standard_access`.

## Dry-run mutací (validate_only)

- `execution_options=["validate_only"]` podporují create/update endpointy **campaigns, adsets, ads i adcreatives** (u adcreatives jen `validate_only`, bez `include_recommendations`). Úspěšná validace vrací `{"success": true}`.
- Na `/ads` jde přidat `synchronous_ad_review` (spolu s validate_only) — provede navíc integrity checks textů/obrázků.
- CLI z toho dělá default: každý write bez `--confirm` = validace, nic se nezapíše.
- **`/copies` (duplicate) a `/budget_schedules` validate_only nemají** — CLI u nich bez `--confirm` neposílá žádný call a jen vytiskne plán.

## Review reklam & policy data

- Review je **asynchronní** (typicky < 24 h). Po vytvoření/swapu kreativy projde reklama stavy `PENDING_REVIEW`/`IN_PROCESS` → `ACTIVE`/`WITH_ISSUES`/`DISAPPROVED`. Pauznutá reklama zůstane po review pauznutá.
- Review spouští: změna kreativy (obrázek, text, link, video), cílení, optimalizačního cíle. Review NEspouští: bid, rozpočet, schedule.
- **`ad_review_feedback`** (na Ad): `global` + `placement_specific` mapy důvod → vysvětlení zamítnutí.
- **`issues_info`** (campaign/adset/ad): `error_code`, `error_summary`, `error_message`, `level` — proč objekt nedoručuje.
- Efektivní dotaz na problémové reklamy: edge `/act_X/ads` má přímo param `effective_status=["WITH_ISSUES","DISAPPROVED",...]` (list). CLI: `ad-review`.

## Kreativy

- **Kreativy jsou immutable** — „editace" = vytvořit NOVOU kreativu a swapnout na reklamu (`POST /{ad-id}` s `creative={"creative_id":...}`); reklama projde re-review (stejně jako edit v UI).
- **asset_feed_spec** (Advantage+ / dynamic creative): `images[]`, `videos[]`, `bodies[]` (max 5), `titles[]` (max 5), `descriptions[]`, `link_urls[]`, `call_to_action_types[]`, `ad_formats`, `asset_customization_rules` (placement pravidla; ≥ 2 když se použijí), `optimization_type` (`REGULAR` = dynamic creative, `PLACEMENT` = placement customization). Při přestavbě zachovat VŠECHNY `adlabels` (spojují assety s pravidly), customization rules a `degrees_of_freedom_spec`.
- **degrees_of_freedom_spec**: bundle `standard_enhancements` je mrtvý od v22 — jednotlivé enhancementy se řídí per-feature přes `creative_features_spec.{feature}.enroll_status = OPT_IN|OPT_OUT`. API při čtení vrací i deprecated pole (`standard_enhancements`, `advantage_plus_creative`, `cv_transformation`, `image_animation`, `replace_media_text`, `show_destination_blurbs`, `show_summary`), která create **odmítne** — před vytvořením nové kreativy je nutné je odstranit (`creative-clone` to dělá automaticky). **(živě, 2026-06)**
- **Nová kreativa bez `degrees_of_freedom_spec` = defaultní Advantage+ chování** (enhancementy typicky zapnuté). Vypnutí = poslat všech **14 user-facing featur** s `enroll_status: OPT_OUT`: `adapt_to_placement`, `add_text_overlay`, `description_automation`, `enhance_cta`, `image_background_gen`, `image_templates`, `image_touchups`, `image_uncrop`, `inline_comment`, `product_extensions`, `reveal_details_over_time`, `text_optimizations`, `text_translation`, `video_auto_crop`. Featury neaplikovatelné na daný typ kreativy server sám vyhodí, takže plný seznam je bezpečný vždy. Enhancement `music` se řídí jinde — `asset_feed_spec.audios` (prázdné pole = opt-out). CLI: `--no-enhancements` na `creative-create` / `creative-from-post` / `creative-from-ig` / `creative-clone`.
- **`url_tags` (UTM) je TOP-LEVEL pole kreativy** — není uvnitř `object_story_spec` ani `asset_feed_spec`, takže při přestavbě kreativy tiše zmizí, pokud se explicitně nepřečte a nepošle znovu. Kreativa bez `url_tags` je validní → dry-run ztrátu nechytí. (`creative-clone` je od 2.3.0 přenáší automaticky.)
- **Unikátní image hashe v asset_feed_spec**: každý entry v `images[]` musí mít UNIKÁTNÍ `hash`, jinak `error_subcode 1815629` „Duplicates of ad asset values are not allowed". Když je jeden nový obrázek pro dva sloty, sloučit do JEDNOHO entry se všemi původními `adlabels`. `thumbnail_hash` videa SMÍ být roven image hashi — unikátnost platí jen uvnitř `images[]`. (`creative-clone --swap-image` řeší.) **(živě, 2026-06)**
- **Read-only response pole**: čtení `asset_feed_spec` vrací `reasons_to_shop`/`shops_bundle` jako `false` — před re-create odstranit falsy hodnoty, jinak API může request odmítnout. **(živě, 2026-06)**
- **asset_feed_spec s IG placementy vyžaduje `instagram_user_id` v `object_story_spec`**, jinak `error_subcode 1772103` „Instagram account is missing" (jednoduché object_story_spec-only kreativy IG nevyžadují). ID: `GET /{page_id}?fields=instagram_business_account`. **(živě, 2026)** Výjimka: u FLEX kreativy (`asset_feed_spec` BEZ `asset_customization_rules`) s page-only identitou 1772103 zmizí, když payload obsahuje `degrees_of_freedom_spec` — Meta pak použije page-backed IG identitu (PBIA). Bez DOF padá hláška „Select an Instagram account or a Facebook Page". **(komunitně ověřeno živě, 2026-08, díky @HonzaKase)**
- **Video processing**: `advideos` vrátí ID okamžitě, ale video je `processing` — kreativa odkazující na ne-`ready` video umí selhat. `video-upload --wait` polluje do `ready`. **(živě, 2026-06)**
- **Velká videa: jeden multipart POST na `/advideos` vrací HTTP 413 s prázdným body** (pozorováno na 234 MB souboru; bez JSON error objektu, takže se příčina špatně diagnostikuje). Řešení = resumable **chunked upload**: `upload_phase=start` (s `file_size` → vrátí `upload_session_id`, `video_id` a offsety) → smyčka `upload_phase=transfer` (chunk `video_file_chunk` od `start_offset`; hranice chunků určuje server vrácenými offsety) → `upload_phase=finish` (+ `title`). Transfer chunky jsou offset-adresované, tedy idempotentní — transient retry je u nich bezpečný. CLI přepíná na chunked automaticky > 100 MB (`--chunked` vynutí). **(komunitně ověřeno živě, 2026-08, díky @HonzaKase)**
- Po vytvoření kreativy může být status `IN_PROCESS` než přejde na `ACTIVE`/`WITH_ISSUES`.

## Promoce organiky (existing post ads)

- **FB post**: kreativa s `object_story_id = "<PAGE_ID>_<POST_ID>"` místo `object_story_spec`.
- **IG post/Reel**: `POST /act_X/adcreatives` s `object_id=<PAGE_ID>` + `instagram_user_id` + `source_instagram_media_id=<IG_MEDIA_ID>`; volitelně `call_to_action`. Media ID: `GET /{ig-user}/media` (potřebuje `instagram_basic` scope + page-connected IG business účet). Podporované: feed foto/video, carousely, Reels; nepodporované: media s copyrighted hudbou, interaktivní filtry.
- Čtení zpět: `source_instagram_media_id` / `effective_instagram_media_id` na kreativě.

## Náhledy (previews)

- `GET /{ad_id}/previews` nebo `GET /act_X/generatepreviews?creative={"creative_id":...}` s `ad_format` (92 hodnot: `MOBILE_FEED_STANDARD`, `DESKTOP_FEED_STANDARD`, `INSTAGRAM_STANDARD`, `INSTAGRAM_STORY`, `INSTAGRAM_REELS`, `FACEBOOK_REELS_MOBILE`, `RIGHT_COLUMN_STANDARD`, …).
- Výstup = `<iframe>`; **URL platí ~24 hodin**. Vyžaduje user access token; account-scoped náhledy vidí jen lidé s rolí na účtu.

## Insights

- **Breakdowny** (80+): demografie (`age`, `gender`), geo (`country`, `region`, `dma`), platformy (`publisher_platform`, `platform_position`, `device_platform`, `impression_device`), hodinové, **asset breakdowny pro Advantage+** (`image_asset`, `video_asset`, `body_asset`, `title_asset`, `description_asset`, `link_url_asset`, `call_to_action_asset`, `ad_format_asset`). Kombinace jen ve vyjmenovaných permutacích; hourly nejde s reach/unique metrikami.
- **action_breakdowns**: `action_type`, `action_device`, `action_destination`, `action_video_type`, `action_carousel_card_id`, …
- **Atribuce**: `action_attribution_windows` — platná okna k 2026: `1d_click`, `7d_click`, `28d_click`, `1d_view`, `1d_ev` (engaged view), `dda`, `default`; default `["7d_click","1d_view"]`. **`7d_view` a `28d_view` Meta odstranila 2026-01-12** — requesty s nimi vracejí **tiché nuly, ne chybu** (CLI je proto odmítá lokálně). `use_unified_attribution_setting=true` = čísla shodná s Ads Managerem (CLI: `--unified-attribution`).
- **Retention limity insights (od 2026-01-12)**: agregovaná data 37 měsíců zpět, **unique-count metriky a hourly breakdowny jen 13 měsíců**, frequency breakdowny 6 měsíců. Starší dotazy vracejí prázdno/nuly bez chyby.
- **Async job**: `POST /{object}/insights` → `report_run_id` → poll `async_status` (`Job Completed`/`Failed`/`Skipped`) → `GET /{report_run_id}/insights`. Od v25.0 failed job vrací `error_code`, `error_message`, `error_subcode`, `error_user_title`, `error_user_msg`. report_run_id expiruje po 30 dnech.
- **Data freshness**: refresh à 15 min; metriky **zamrzají 28 dní** po reportovaném dni (do té doby se mohou měnit). Delta API neexistuje — bezpečný sync pattern = přetahovat posledních 28 dní, starší cachovat.
- `date_preset` nově i `last_3d`, `last_28d`, `this_week_mon_today`, `data_maximum`.

## Activity log

- `GET /act_X/activities` — `event_type`, `translated_event_type`, `event_time`, `actor_id/name`, `application_name`, `object_id/name/type`, `extra_data` (JSON string se starou/novou hodnotou). Default okno ~1 týden.
- Referenční stránka parametry nezmiňuje, ale oficiální SDK posílá `since`, `until`, `category` (ACCOUNT/AD/AD_SET/CAMPAIGN/BUDGET/TARGETING/…), `oid` (filtr na objekt), `uid`, `limit` — fungují.

## Budget schedules (špičky poptávky)

- `POST /{campaign_id|adset_id}/budget_schedules`: `time_start`/`time_end` (unix), `budget_value`, `budget_value_type` (`ABSOLUTE` v centech | `MULTIPLIER` v procentech základu). Jen kampaně/sady s daily budgetem; limity ~50 period, min 3 h, max 8× denní rozpočet (sekundární zdroje). Edge je create-only (update/delete přes UI).

## Mazání & lifecycle

- **DELETE je trvalé.** `ARCHIVED` = poloviční koš: objekt zůstává queryable přes edges (max 100K per typ), lze měnit jen `name` a status → DELETED.
- **DELETED objekty zůstávají ve výpisech edges** (s `effective_status: DELETED`) resp. dohledatelné přes přímé ID; statistiky se počítají ještě 28 dní po posledním delivery. CLI je ve výpisech defaultně skrývá.
- Doporučený úklid: archivovat, mazat až po 28 dnech bez delivery. CLI `*-delete` maže jen PAUSED/ARCHIVED (`--force` obejde).

## Batch requesty

- `POST graph.facebook.com` s `batch=[...]` — max **50 operací** na request, per-op chyby nezastaví ostatní. **Nešetří rate limity** („each call within the batch is counted separately") — šetří jen HTTP round-tripy. Starší poznámka „max 10 pro ad creation" není v aktuálních docs.

## Tokeny

- Long-lived user token = 60 dní; obnova `GET /oauth/access_token?grant_type=fb_exchange_token&client_id&client_secret&fb_exchange_token` (CLI: `token-extend`, s `--write-env` rovnou zapíše `.env`). **Prošlý/invalidovaný token vyměnit nejde** — nutný nový login (Graph API Explorer / Business Manager).
- Error 190 subcode 460 = session invalidated (změna hesla / bezpečnostní reset) — token je mrtvý okamžitě, bez ohledu na expiry. **(živě, 2026-07-19)**
- Alternativa pro server-to-server: **system user token** z Business Manageru (jde vygenerovat i never-expiring) — CLI ho podporuje (debug_token vrací `expires_at: 0` → „Never").
- CLI cachuje expiry v `.usage/token.json` (kontrola max 1× denně, klíčovaná hashem tokenu) a varuje na stderr < 7 dní. Kontrola je best-effort — její selhání (offline, captive portál) nikdy neshodí samotný příkaz.
- Graph API přijímá token i v hlavičce **`Authorization: Bearer <token>`** — CLI ji od 2.2.0 používá místo query parametru (token se tak nedostane do URL, proxy logů ani textu výjimek).

## Cílení

- `targeting` objekt: `geo_locations` (countries, regions, cities s radius 10–50 mi, custom_locations s radius 1–80 km, zips), `excluded_geo_locations`, `age_min`/`age_max` (strop 65), `genders`, `locales`, `flexible_spec` (AND mezi elementy, OR uvnitř) + `exclusions`, `custom_audiences`/`excluded_custom_audiences`, placementy (`publisher_platforms`, `facebook_positions`, `instagram_positions`, `device_platforms` — vynechání všech = Advantage+ placements), `targeting_automation.advantage_audience` (0/1).
- **EU DSA**: ad sety cílící na EU vyžadují `dsa_payor` + `dsa_beneficiary` (relevantní pro CZ/SK!).
- **Search endpointy** (`GET /search?type=...`): `adinterest` (q), `adinterestsuggestion`/`adinterestvalid` (interest_list), `adgeolocation` (q + location_types), `adlocale`, `adTargetingCategory` (class=behaviors/demographics/…). Plus account-scoped `/act_X/targetingsearch`, `/act_X/targetingsuggestions`, `/act_X/targetingvalidation`.

## Chyby (obecné)

- Error objekt: `code`, `error_subcode`, `message`, `error_user_title`, `error_user_msg`, `is_transient`, `fbtrace_id`; u code 100 navíc `error_data.blame_field_specs` (které pole je špatně). CLI vypisuje user_msg i blame fields.
- Známé subcody: 1815629 (duplicate asset values), 1772103 (missing IG account), 1487632 (budget change 4×/hod), 1487225 (ad creation cap), 5044001 (QPS), 460/463/467 (token invalid/expired/logged out).

## Živě ověřené quirky (sandbox roundtrip 2026-07-19)

PAUSED sandbox na reálném účtu: campaign → adset → image → creative → ad → preview → review → updates → duplicates → deletes. Vše prošlo; tyhle věci by bez živého testu nikdo neuhádl:

- **`/copies` ignoruje `execution_options=["validate_only"]` a kopii REÁLNĚ vytvoří** (vrátí `copied_campaign_id`, žádná chyba). Proto CLI u duplicate příkazů dry-run neposílá žádný call — „validace" duplikace přes API neexistuje a pokus o ni zakládá objekty.
- **ABO kampaň (bez campaign budgetu) vyžaduje `is_adset_budget_sharing_enabled`** — create bez toho pole padá na error 100 („You must specify True or False…"). Není v changelogu ani docs; CLI posílá default `false` (flag `--adset-budget-sharing`).
- **Ad set s `optimization_goal=LINK_CLICKS` chtěl `bid_amount`** („Bid amount required for bid strategy provided") — na testovaném účtu bez ohledu na ABO/CBO i bez explicitní bid strategie; s `--bid-amount` prošel. Pravděpodobně účetní default bid strategie; když na to narazíš, přidej `--bid-amount` nebo jiný optimalizační cíl.
- **EU DSA (`dsa_payor`/`dsa_beneficiary`) NENÍ vyžadováno** pro CZ cílení na CZ účtu (create prošel bez nich). Flagy existují pro účty/kombinace, kde to Meta vynucuje.
- **`budget-schedule` funguje i na PAUSED kampani** (create i list; `--list` čte edge normálně, přestože je dokumentovaný jako create-only).
- **Čtení stránek Explorer tokenem umí selhat i se správnými scopes** — pokud při generování tokenu nevybereš konkrétní stránky, `GET /{page_id}` i `/posts` padá na code 10/„Object does not exist" navzdory `pages_read_engagement` ve scopes. Ads edges (`promote_pages`, `act/connected_instagram_accounts`) fungují dál — CLI má pro IG resolution fallback přes `connected_instagram_accounts`.
- **IG boost blokuje nevyřízená EU „consent or pay" volba**: `creative-from-ig` validace vrátila „Advertising currently limited: log in to Instagram for @… and make a choice (subscribe or use free with ads)". Dokud se do IG účtu někdo nepřihlásí a volbu neodklikne, IG media boostovat nejdou.
- **`creative-from-post` s neexistujícím/nedostupným postem**: validace korektně vrátí „The post you've selected for your ad is not available" — `effective_object_story_id` staré kreativy nemusí odkazovat na živý post.
- **Čerstvě vytvořená reklama má `effective_status: IN_PROCESS`** i v PAUSED stavu (post-create processing) — mazání v tom stavu funguje (brake kouká na `status`, ne `effective_status`).
- **Validate_only funguje identicky jako ostrý create** i pro vynucování polí (ABO pole výše chytila už validace) — dry-run výsledkům se dá věřit.
- **`interest-validate` bere jen plné disambiguované názvy**: „Digital marketing (marketing)" je validní, samotné „Digital marketing" ne — názvy ber z `interest-search`, ne z hlavy.
- **BUC hlavičky vracejí i page/messaging use-casy** (klíč = page/IG ID) — pro guard jsou irelevantní, CLI filtruje jen `ads_*` typy.
- **Error 190 subcode 460** (session invalidated změnou hesla) zabije token okamžitě — `token-extend` nepomůže, nutný nový token z Exploreru.
- Netestováno individuálně: `adset-duplicate`/`ad-duplicate` (sdílejí ověřenou `/copies` mechaniku s `campaign-duplicate`), `creative-clone` a `video-upload` (živě ověřené v produkci 2026-06). `creative-from-post`/`creative-from-ig` ověřeny do úrovně validace (plný create blokovala dostupnost postu / IG consent stav, ne kód).
