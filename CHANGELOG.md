# Changelog

Všechny podstatné změny v tomto projektu. Formát vychází z [Keep a Changelog](https://keepachangelog.com/), verzování je [SemVer](https://semver.org/) (verze žije v `metaads/__init__.py`).

## [2.2.0] — 2026-08-13 — Bezpečnostní hardening před zveřejněním 🛡️

Zapracování kompletního bezpečnostního a API auditu před otevřením repa veřejnosti a studentům AI First.

### Bezpečnost
- **Token cestuje v `Authorization: Bearer` hlavičce** místo query parametru — nedostane se do URL, proxy logů ani textu výjimek. Chybové hlášky navíc **redigují tajemství** (`access_token`, `client_secret`, `fb_exchange_token`, `input_token` → `REDACTED`), takže výpadek Wi-Fi nevypíše živý token do terminálu.
- `token-extend` posílá `client_secret` v **POST těle** (dřív GET query string).
- `.env` se při `--write-env` přepisuje **atomicky** (temp + rename) a `.env`/`.env.bak` dostávají práva **600**; `setup.sh` nastavuje 600 při založení.
- `.usage/token.json` ukládá **hash tokenu** místo jeho posledních 12 znaků; JSON stavové soubory se zapisují atomicky.
- `_api_call` odmítne následovat URL mimo `graph.facebook.com` a nemodifikuje volajícímu předaný dict.

### Ochrana účtu
- **Oprava obejití rate-limit guardu**: víc BUC záznamů v jedné hlavičce (ads_management + ads_insights) se nově vyhodnocuje přes `max()` — chladnější záznam už nepřepíše horký a neodzbrojí hard-stop.
- Guard blokuje **per účet** (horký účet A neblokuje práci na účtu B) a `api-limits` jím projde vždy (je to diagnostický nástroj).
- **Transient chyby se u zápisů (POST) neopakují** — Meta občas hlásí `is_transient` až PO provedeném zápisu a retry by založil kampaň dvakrát. CLI řekne, že zápis mohl projít, a poradí kontrolu list příkazem. Čtení se retryují dál.
- Čekání odvozené z `estimated_time_to_regain_access` má strop 5 minut — CLI nebude tiše spát hodinu.
- Denní kontrola expirace tokenu je **nefatální**: její selhání (captive portál, offline, permission chyba na `debug_token`) už nikdy neshodí samotný příkaz; neúspěch se cachuje s 1h back-offem.

### Aktualizace na stav API (srpen 2026)
- Atribuční okna **`7d_view`/`28d_view` Meta odstranila 2026-01-12** (API vrací tiché nuly) — CLI je odmítá s vysvětlením; `1d_ev` (engaged view) je validní.
- **Ochrana měn bez haléřů** (JPY, HUF, IDR, …): CLI odmítne nastavovat rozpočty tam, kde by převod ×100 nastavil 100× vyšší částku (měna účtu se cachuje v `.usage/accounts.json`).
- `docs/api-notes.md`: v26.0 vyšla 2026-07-29 (v25 sunset TBD, auto-upgrade mechanismus), přejmenování tierů na Limited/Full access, retention limity insights (37/13/6 měsíců).

### UX pro nováčky
- `--help` a `--version` fungují **i před vyplněním `.env`**.
- Nevalidní JSON ve flazích (`--targeting`, `--special-ad-categories`, `--child-attachments`) a `--genders male` dávají čistou chybovou hlášku místo Python tracebacku.
- `*-delete --json` vrací **validní JSON i v dry-runu** (dřív lidský text); výsledky delete nesou `executed` klíč.
- README: HTTPS clone URL, sekce pro **Windows** (Git Bash/WSL/PowerShell), kompletní walkthrough od **registrace Meta developera** po 60denní token, sekce Licence a Chyby a náměty.

### Nové
- **MIT LICENSE**.
- **Testovací suite** `tests/` (45 testů, pytest, kompletně offline) — pokrývá redakci tokenů, guard, retry politiku, dry-run mutací, JSON kontrakty a atomický zápis `.env`. Dev závislosti: `requirements-dev.txt`.

## [2.1.0] — 2026-07-19 — Vizuální podpis 🎨

- **ASCII banner s barvami** („META" v modré + verze, tagline a „by Jindřich Fáborský · AIFirst.cz") — vypíše se **jen člověku v terminálu** (stdout je TTY a neběží `--json`). Pipe, skripty a agentní tool-cally dostávají dál čistý výstup bez jediného znaku navíc. Respektuje `NO_COLOR`. Přidán `--version` flag.

## [2.0.0] — 2026-07-19 — Velký refresh: 47 příkazů, dry-run všech zápisů, pulse 🚀

**Breaking change:** všechny zápisové příkazy (create/update/duplicate/delete) nově běží defaultně jako **dry-run** — kampaně/sady/reklamy/kreativy se validují přes `execution_options=["validate_only"]`, ale nic se nezapíše. Reálný zápis vyžaduje `--confirm`. Výjimka: `image-upload`/`video-upload` nahrávají rovnou (plní jen knihovnu médií, nic nejde live).

- **Refaktor na balík `metaads/`**: engine `api.py` (auth, rate-limit guard, retry, mutace), `formatting.py`, `lint.py`, `commands/*` po doménách, `cli.py` s `_cmd()` helperem (parita parser/dispatch konstrukcí). `meta_ads_cli.py` zůstává jako tenký entrypoint se zpětně kompatibilními re-exporty (`_api_call`, `META_AD_ACCOUNT_ID`, …).
- **`pulse`** — account-wide digest v ~6 API callech: spend/metriky s deltami vs. předchozí okno, top kampaňové movery, reklamy v review/zamítnuté, API usage %, token expiry.
- **Kontrola pravidel inzerce a limitů:**
  - preflight lint textů (délky vs. ořezy placementů, CAPS, interpunkce, emoji), URL, CTA a obrázků (rozměry, poměr stran) — lokálně, před jakýmkoli API callem;
  - `ad-review` — reklamy ve stavu WITH_ISSUES / DISAPPROVED / PENDING_REVIEW včetně důvodů (`ad_review_feedback`, `issues_info`);
  - `api-limits` — aktuální usage % per účet, tier, referenční tabulka limitů a error kódů; persistentní guard v `.usage/` s hard-stopem nad 95 %;
  - varování na stderr u každého příkazu, když token expiruje za < 7 dní (cache, max 1 kontrola denně);
  - **pojistka mazání**: nové `*-delete` příkazy mažou jen PAUSED/ARCHIVED entity (`--force` obejde) a doporučují ARCHIVED místo trvalého DELETE.
- **Promoce organiky**: `creative-from-post` (FB post → kreativa přes `object_story_id`), `creative-from-ig` (IG post/Reel přes `source_instagram_media_id`), `ig-media` (výpis IG médií page-connected účtu).
- **`preview`** — HTML náhled reklamy/kreativy před publikací (`generatepreviews`, 92 formátů, iframe platí ~24 h).
- **Insights upgrade**: `--action-breakdowns`, `--attribution-windows`, `--unified-attribution` (shoda s Ads Managerem), `--filtering`, `--sort`; asset breakdowny (image_asset, title_asset, …) pro Advantage+ analýzu; async report od v25.0 vrací plné error fieldy.
- **`activities`** — historie změn účtu (kdo co změnil), filtry since/until/kategorie/objekt.
- **Cílení**: `interest-search` / `interest-suggest` / `interest-validate`, `geo-search` (vč. radius klíčů), `locale-search`; `adset-create/update` umí convenience flagy (`--countries`, `--advantage-audience`, manuální placementy, `--dsa-payor`/`--dsa-beneficiary` pro EU DSA).
- **Advantage+ & bidding**: `campaign-detail` čte `advantage_state_info` (stav Advantage+ Sales/Leads), bid strategie vč. `LOWEST_COST_WITH_MIN_ROAS` (`--roas-floor`), `budget-schedule` (navýšení rozpočtu na špičky), `adset-create --cbo` pro kampaně s rozpočtem na úrovni kampaně.
- **Konverze read-only**: `pixels`, `custom-conversions` (vč. příznaku `is_unavailable` z policy sweepu 2025).
- Výpisy defaultně skrývají DELETED entity (Meta je nechává v edge listech).
- `token-extend --write-env` zapíše nový token rovnou do `.env` (záloha v `.env.bak`).
- Opraven zastaralý rate-limit model v dokumentaci (bodový model „60/5 min" → hodinový BUC model `300/100k + 40×aktivních reklam`).
- Živě objevené a ošetřené quirky (viz `docs/api-notes.md`): ABO kampaně nově vyžadují `is_adset_budget_sharing_enabled` (CLI posílá default, flag `--adset-budget-sharing`); `/copies` ignoruje validate_only (dry-run duplikací proto neposílá žádný call); fallback IG resolution přes `connected_instagram_accounts`, když token nemá granted stránky.
- Nové: README (poprvé!), `docs/api-notes.md`, CHANGELOG, bundled skill `skill/meta-ads/`, `scripts/check_docs_consistency.py`, `run.sh`, `setup.sh`.

## [1.1.0] — 2026-06-06 — creative-clone a video-upload --wait

- **`creative-clone`** — klonování immutable Advantage+ kreativ se swapem videa/obrázku/URL při zachování textů, adlabels a `asset_customization_rules`; automaticky řeší duplicate-image-hash (subcode 1815629) a deprecated `degrees_of_freedom_spec` pole; `--swap-on-ad` rovnou přepne reklamu na novou kreativu.
- **`video-upload --wait`** — poll do stavu `ready` (kreativa odkazující na ještě zpracovávané video umí selhat).
- 27 příkazů.

## [1.0.0] — 2026-04-06 — První verze

- Meta Ads CLI pro Marketing API v25.0: 26 příkazů — účet, kampaně, ad sety, reklamy, kreativy, upload médií, insights (sync i async), token management (`token-info`, `token-extend`).
- Rate-limit tracking z hlaviček (`X-Business-Use-Case-Usage`) s retry 5/15/60 s; částky v měně účtu (CLI ↔ centy převádí automaticky); `--json` výstupy.
