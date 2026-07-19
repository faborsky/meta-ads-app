# Meta Ads App

**Verze 2.1.0** · Python CLI pro správu Meta Ads (Facebook & Instagram) přes Marketing API v25.0 — stavěné pro orchestraci AI agentem (Claude Code) i pro vlastní automatizace.

Appka vznikla jako součást ekosystému kurzu [AI First](https://aifirst.cz) — praktická ukázka, jak si marketér může nechat AI postavit a řídit vlastní nástroje. Novinky sleduj přes **Watch → Custom → Releases** na GitHubu, changelog je v [CHANGELOG.md](CHANGELOG.md).

## 🆕 Co je nového (2.0.0)

- **Dry-run všech zápisů**: create/update/duplicate/delete se bez `--confirm` jen validují (`validate_only`), nic se nezapíše.
- **`pulse`** — přehled účtu v ~6 API callech: metriky s deltami vs. předchozí okno, top movery, reklamy v review, API limity, token.
- **Promoce organiky**: `creative-from-post` (FB), `creative-from-ig` (IG post/Reel), `ig-media`.
- **`ad-review`** (důvody zamítnutí), **`preview`** (HTML náhled před publikací), **`activities`** (kdo co změnil), **`api-limits`**, targeting search, Advantage+ stav, bid strategie, budget schedules, pixely a custom konverze.
- Preflight lint textů/obrázků, pojistka proti mazání (jen PAUSED entity), refaktor na balík `metaads/`.

Kompletní seznam změn: [CHANGELOG.md](CHANGELOG.md).

## Dva způsoby, jak appku používat

**A) Orchestrace přes Claude Code (doporučeno)** — appku řídí AI agent, ty zadáváš úkoly česky. Zkopíruj si tento prompt do Claude Code:

> Naklonuj si repo `git@github.com:faborsky/meta-ads-app.git` do `~/dev/meta-ads-app`, spusť `./setup.sh`, nainstaluj skill podle `skill/INSTALL.md` a proveď mě vyplněním `.env` (potřebuju Meta app, token a ad account ID — postup je v README v sekci „Získání přístupů"). Pak ověř funkčnost přes `./run.sh account`.

Skill `/meta-ads` pak umí scénáře create / optimize / review-check / creative refresh se zabudovanými bezpečnostními pravidly (plán → schválení → zápis, PAUSED starty, dry-run).

**B) Vlastní automatizace** — CLI má stabilní `--json` výstupy, dry-run default, rate-limit guard a retry logiku, takže jde bezpečně volat ze skriptů, cronů nebo vlastních agentů:

```bash
./run.sh campaigns --status ACTIVE --json | jq '.[].name'
./run.sh insights --date-preset last_7d --level campaign --json
./run.sh campaign-create --name "Test" --objective OUTCOME_TRAFFIC --daily-budget 200          # dry-run
./run.sh campaign-create --name "Test" --objective OUTCOME_TRAFFIC --daily-budget 200 --confirm # zápis
```

## Požadavky

- Python 3.9+
- Meta developerská aplikace s přístupem k Marketing API (postup níže)
- Reklamní účet, ke kterému máš roli inzerenta

## Instalace

```bash
git clone git@github.com:faborsky/meta-ads-app.git
cd meta-ads-app
./setup.sh          # vytvoří .venv, nainstaluje závislosti, založí .env
# vyplň .env (viz níže)
./run.sh account    # test funkčnosti
```

## Získání přístupů krok za krokem

Meta používá jiný model než Google OAuth: místo refresh tokenu máš **user access token s platností 60 dní**, který před vypršením vyměňuješ za nový (CLI to umí samo). Poctivé shrnutí: jednou za ~2 měsíce token obnovíš jedním příkazem; když ho necháš propadnout (nebo si změníš heslo na Facebooku), musíš vygenerovat nový ručně.

### 1) Meta aplikace

1. [developers.facebook.com](https://developers.facebook.com) → **My Apps → Create App** → typ **Business**.
2. V aplikaci přidej produkt **Marketing API**.
3. **App settings → Basic**: zkopíruj **App ID** a **App Secret** → `META_APP_ID`, `META_APP_SECRET`.
4. Aplikace musí být v **Live mode** (ne Development), jinak nejde vytvářet kreativy generující page posty.

### 2) Access token

1. [Graph API Explorer](https://developers.facebook.com/tools/explorer) → vyber svou aplikaci.
2. **Permissions**: `ads_management`, `ads_read`, `business_management`, `pages_show_list`, `pages_read_engagement`, `instagram_basic` (poslední dvě kvůli promoci organiky a `ig-media`).
3. **Generate Access Token** (přihlásíš se účtem, který má roli na reklamním účtu) → zkopíruj token → `META_ACCESS_TOKEN`. Pokud se dialog ptá, ke kterým stránkám dát přístup, **vyber všechny relevantní** — bez toho scopes stránek nestačí a čtení postů/IG přes stránku selhává (ads příkazy fungují i tak; CLI má fallback).
4. Krátkodobý token hned vyměň za 60denní: `./run.sh token-extend --write-env`.

### 3) Ad account a Page

1. ID reklamního účtu najdeš v [Ads Manageru](https://adsmanager.facebook.com) (URL parametr `act=`) → `META_AD_ACCOUNT_ID` ve formátu `act_XXXXXXXXX`.
2. Výchozí Facebook Page pro kreativy: `./run.sh pages` vypíše dostupné stránky → `META_PAGE_ID`.

### 4) Údržba tokenu

- `./run.sh token-info` — expiry a scopes; každý příkaz navíc varuje na stderr, když token expiruje za < 7 dní.
- `./run.sh token-extend --write-env` — obnova na dalších 60 dní (jde opakovat, dokud je token platný).
- Pro server-to-server automatizace zvaž **system user token** z Business Manageru (může být never-expiring) — CLI s ním funguje beze změn.

> **Bezpečnost:** všech 5 hodnot patří **výhradně do `.env`** (je v `.gitignore`). Nikdy je nedávej do kódu, gitu ani chatu.

## Použití — konvence

- **Dry-run default**: každý zápisový příkaz bez `--confirm` jen validuje (Meta `validate_only`), u endpointů bez validace vytiskne plán. Reálný zápis = přidej `--confirm`.
- **Vše nové vzniká PAUSED** — create/duplicate příkazy nic nespouštějí live.
- **DELETE je trvalé**: `*-delete` maže jen PAUSED/ARCHIVED entity (`--force` obejde). Preferuj `--status ARCHIVED`.
- **Částky v měně účtu** (CLI ↔ API centy převádí automaticky). Pozor: měna účtu nemusí být CZK.
- **`--json`** kdykoli výstup parsuje stroj.
- **`--account-id act_XXX`** před příkazem přepne účet (default z `.env`).

### Ochrana účtu (rate limity)

CLI parsuje limitové hlavičky po každém callu, persistuje usage do `.usage/`, varuje > 75 %, throttluje > 90 % a **odmítne další cally ≥ 95 %** (override `METAADS_IGNORE_USAGE_GUARD=1`). Rate-limited požadavky opakuje s backoffem 5/15/60 s (respektuje `estimated_time_to_regain_access`). Stav: `./run.sh api-limits`.

## Příkazy

### Účet & token

| Příkaz | Popis |
|---|---|
| `account` | Info o reklamním účtu (stav, měna, spend) |
| `pages` | FB/IG stránky použitelné v kreativách |
| `api-limits` | Aktuální API usage %, tier, referenční limity |
| `token-info` | Platnost a scopes tokenu |
| `token-extend` | Výměna za 60denní token (`--write-env` zapíše do .env) |

### Kampaně

| Příkaz | Popis |
|---|---|
| `campaigns` | Výpis kampaní (`--status`, DELETED skryté) |
| `campaign-detail` | Detail vč. Advantage+ stavu a issues |
| `campaign-create` | Nová kampaň (PAUSED; `--objective`, `--daily-budget` = CBO, `--bid-strategy`; bez budgetu = ABO + `--adset-budget-sharing`) |
| `campaign-update` | Změna názvu/statusu/rozpočtu/bid strategie |
| `campaign-duplicate` | Kopie kampaně (`--deep-copy` vč. sad a reklam) |
| `campaign-delete` | Trvalé smazání (jen PAUSED/ARCHIVED, `--force`) |
| `budget-schedule` | Naplánované navýšení rozpočtu na špičky (`--list` / create) |

### Ad sety

| Příkaz | Popis |
|---|---|
| `adsets` | Výpis ad setů (`--campaign-id`, `--status`) |
| `adset-detail` | Detail vč. cílení, DSA a issues |
| `adset-create` | Nový ad set (PAUSED; `--countries`, `--advantage-audience`, placementy, `--roas-floor`, `--dsa-payor/--dsa-beneficiary`, `--cbo`) |
| `adset-update` | Změny vč. merge cílení přes convenience flagy |
| `adset-duplicate` | Kopie ad setu (`--campaign-id` = cílová kampaň) |
| `adset-delete` | Trvalé smazání (jen PAUSED/ARCHIVED, `--force`) |

### Reklamy

| Příkaz | Popis |
|---|---|
| `ads` | Výpis reklam (`--adset-id`/`--campaign-id`/`--status`) |
| `ad-detail` | Detail vč. kreativy, issues a review feedbacku |
| `ad-review` | Reklamy v review / zamítnuté / s problémy + důvody |
| `ad-create` | Nová reklama (PAUSED; `--creative-id`) |
| `ad-update` | Změna názvu/statusu/kreativy (swap = re-review) |
| `ad-duplicate` | Kopie reklamy |
| `ad-delete` | Trvalé smazání (jen PAUSED/ARCHIVED, `--force`) |

### Kreativy & média

| Příkaz | Popis |
|---|---|
| `creatives` | Výpis kreativ |
| `creative-detail` | Detail vč. asset_feed_spec |
| `creative-create` | Jednoduchá kreativa (link/video/photo/carousel) |
| `creative-clone` | Klon Advantage+ kreativy se swapem videa/obrázku/URL |
| `creative-from-post` | Kreativa z existujícího FB postu (promoce organiky) |
| `creative-from-ig` | Kreativa z IG postu/Reelu (promoce organiky) |
| `ig-media` | Výpis IG médií page-connected účtu |
| `preview` | HTML náhled reklamy/kreativy (`--format`, `--out soubor.html`) |
| `creative-delete` | Trvalé smazání kreativy |
| `image-upload` | Upload obrázku → hash (s lint kontrolou rozměrů) |
| `video-upload` | Upload videa → ID (`--wait` počká na zpracování) |

### Insights & analýza

| Příkaz | Popis |
|---|---|
| `insights` | Výkonová data (breakdowny vč. asset, atribuce, filtering, sort) |
| `insights-report` | Async report pro velké dotazy |
| `pulse` | Digest účtu: delty, movery, review, limity, token (`--days`) |
| `activities` | Historie změn účtu (`--since`, `--category`, `--object-id`) |

### Konverze (read-only)

| Příkaz | Popis |
|---|---|
| `pixels` | Pixely/datasety účtu (last_fired_time) |
| `custom-conversions` | Custom konverze vč. policy příznaků |

### Targeting search

| Příkaz | Popis |
|---|---|
| `interest-search` | Hledání zájmů (`--q`) |
| `interest-suggest` | Návrhy příbuzných zájmů |
| `interest-validate` | Validace názvů zájmů |
| `geo-search` | Geo klíče (země/region/město/PSČ) |
| `locale-search` | Locale klíče pro jazykové cílení |

## Skill pro Claude Code (/meta-ads)

V `skill/meta-ads/` je operátorská skill se scénáři (create, optimize, review-check, promoce organiky, creative refresh) a bezpečnostními pravidly. Instalace: [skill/INSTALL.md](skill/INSTALL.md).

## Dokumentace

- [CHANGELOG.md](CHANGELOG.md) — historie verzí (sleduj přes Watch → Custom → Releases)
- [docs/api-notes.md](docs/api-notes.md) — reálné chování Marketing API vč. živě ověřených quirků
- [CLAUDE.md](CLAUDE.md) — orientace pro AI agenty (struktura kódu, safety, release checklist)
