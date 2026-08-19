# Meta Ads App

**Verze 2.3.0** · Python CLI pro správu Meta Ads (Facebook & Instagram) přes Marketing API v25.0 — stavěné pro orchestraci AI agentem (Claude Code) i pro vlastní automatizace.

Appka vznikla jako součást ekosystému kurzu [AI First](https://aifirst.cz) — praktická ukázka, jak si marketér může nechat AI postavit a řídit vlastní nástroje. Novinky sleduj přes **Watch → Custom → Releases** na GitHubu, changelog je v [CHANGELOG.md](CHANGELOG.md).

## 🆕 Co je nového (2.3.0)

Verze z komunitní zpětné vazby — díky **Honzovi Kašemu** za tři skvěle zdokumentovaná GitHub issues (#1, #2, #3):

- **`creative-clone` už neztrácí `url_tags`** (UTM parametry vč. dynamických maker) — klon je přenáší ze zdroje automaticky, `--url-tags` je přepíše. Dřív klon tiše vznikl bez měření a dry-run to nechytil.
- **`--no-enhancements`** na `creative-create` / `creative-from-post` / `creative-from-ig` / `creative-clone` — vypne všech 14 Advantage+ enhancementů (`enroll_status: OPT_OUT`). Bez toho nové kreativy vznikají s defaultními automatickými úpravami Mety, což u klientských účtů nikdo neschválil.
- **Chunked upload velkých videí**: `video-upload` nad 100 MB automaticky přepne na resumable upload po částech (`--chunked` vynutí) — jeden multipart POST na velkém souboru vracel HTTP 413 s prázdnou odpovědí. Chyba 413 má teď i srozumitelnou hlášku.
- **Insights nahlas hlásí ořez**: `insights` varuje na stderr, když existují další řádky nad `--limit` (dřív tiše neúplná čísla u breakdownů na větších účtech), `insights-report` varuje při dosažení stropu 5000 řádků.
- **Testy**: suite rozšířena na 64 testů; nové poznatky o API (413/chunked, PBIA u error 1772103, top-level `url_tags`) zapsány v [docs/api-notes.md](docs/api-notes.md).

Kompletní seznam změn: [CHANGELOG.md](CHANGELOG.md).

## Dva způsoby, jak appku používat

**A) Orchestrace přes Claude Code (doporučeno)** — appku řídí AI agent, ty zadáváš úkoly česky. Zkopíruj si tento prompt do Claude Code:

> Naklonuj si repo `https://github.com/faborsky/meta-ads-app.git` do `~/dev/meta-ads-app`, spusť `./setup.sh`, nainstaluj skill podle `skill/INSTALL.md` a proveď mě vyplněním `.env` (potřebuju Meta app, token a ad account ID — postup je v README v sekci „Získání přístupů"). Pak ověř funkčnost přes `./run.sh account`.

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
git clone https://github.com/faborsky/meta-ads-app.git
cd meta-ads-app
./setup.sh          # vytvoří .venv, nainstaluje závislosti, založí .env
# vyplň .env (viz níže)
./run.sh account    # test funkčnosti
```

### Windows

Skripty `setup.sh`/`run.sh` jsou bashové — na Windows použij **Git Bash** (součást [Git for Windows](https://git-scm.com/download/win)) nebo **WSL** a postup výše funguje beze změny. Alternativně čistý PowerShell:

```powershell
git clone https://github.com/faborsky/meta-ads-app.git; cd meta-ads-app
python -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # vyplň credentials
python meta_ads_cli.py account
```

## Získání přístupů krok za krokem

Meta používá jiný model než Google OAuth: místo refresh tokenu máš **user access token s platností 60 dní**, který před vypršením vyměňuješ za nový (CLI to umí samo). Poctivé shrnutí: jednou za ~2 měsíce token obnovíš jedním příkazem; když ho necháš propadnout (nebo si změníš heslo na Facebooku), musíš vygenerovat nový ručně.

Dobrá zpráva pro začátek: **pro práci s vlastním reklamním účtem nepotřebuješ žádné schvalování od Mety** (app review). Výchozí přístupová úroveň Marketing API („Limited access", dřív „development") stačí na všechno, co CLI umí — limituje jen počet API callů za hodinu, což pro jednoho člověka bohatě stačí.

### 0) Registrace jako Meta developer (jednorázově)

Pokud jsi na [developers.facebook.com](https://developers.facebook.com) ještě nikdy nic nedělal/a:

1. Přihlas se svým **běžným facebookovým účtem** (tím, který má roli na reklamním účtu — žádný nový účet se nezakládá).
2. Klikni na **Get Started** (pravý horní roh) a projdi registraci developera: odsouhlasení podmínek, **ověření e-mailu a telefonního čísla**, výběr role (klidně „Other").
3. Hotovo — účet se tím nijak nemění, jen smí vytvářet aplikace.

### 1) Meta aplikace

1. [developers.facebook.com](https://developers.facebook.com) → **My Apps → Create App** → use case / typ **Business** (název appky je libovolný, např. „Moje Ads CLI"; appka je jen tvoje a nikdo jiný ji neuvidí).
2. V aplikaci přidej produkt **Marketing API** (Dashboard → Add product).
3. **App settings → Basic**: zkopíruj **App ID** a **App Secret** → `META_APP_ID`, `META_APP_SECRET`.
4. Aplikace musí být v **Live mode** (ne Development), jinak nejde vytvářet kreativy generující page posty. Přepínač je nahoře na dashboardu appky; Live mode může chtít doplnit Privacy Policy URL v Basic settings — pro osobní nástroj stačí odkaz na libovolnou existující stránku s policy.

### 2) Access token

1. [Graph API Explorer](https://developers.facebook.com/tools/explorer) → vpravo v **Meta App** vyber svou aplikaci z kroku 1.
2. **Permissions** (pole „Add a permission"): `ads_management`, `ads_read`, `business_management`, `pages_show_list`, `pages_read_engagement`, `instagram_basic` (poslední dvě kvůli promoci organiky a `ig-media`).
3. **Generate Access Token** (přihlásíš se účtem, který má roli na reklamním účtu) → zkopíruj token → `META_ACCESS_TOKEN`. Pokud se dialog ptá, ke kterým stránkám dát přístup, **vyber všechny relevantní** — bez toho scopes stránek nestačí a čtení postů/IG přes stránku selhává (ads příkazy fungují i tak; CLI má fallback).
4. Token z Exploreru platí jen ~1–2 hodiny — **hned ho vyměň za 60denní**: `./run.sh token-extend --write-env` (zapíše nový token do `.env`, starý zálohuje do `.env.bak`).

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
- **Vše nové vzniká PAUSED** — create/duplicate příkazy nic nespouštějí live. **Jediná cesta, jak přes CLI spustit útratu, je explicitní `--status ACTIVE` (resp. `--status-option ACTIVE`) spolu s `--confirm`** — nic se nespustí omylem.
- **DELETE je trvalé**: `campaign/adset/ad-delete` mažou jen PAUSED/ARCHIVED entity (`--force` obejde). Preferuj `--status ARCHIVED`. (`creative-delete` brzdu nemá — kreativy PAUSED stav neznají; použitou kreativu odmítne smazat Meta sama.)
- **Částky v měně účtu** (CLI ↔ API centy převádí automaticky). Pozor: měna účtu nemusí být CZK; u měn bez haléřů (JPY, HUF, …) CLI nastavování rozpočtů odmítne.
- **`--json`** kdykoli výstup parsuje stroj.
- **`--account-id act_XXX`** před příkazem přepne účet (default z `.env`).
- `--version` vypíše verzi; barevný banner se tiskne jen člověku v terminálu (respektuje `NO_COLOR`, s `--json` nikdy).

### Ochrana účtu (rate limity)

CLI parsuje limitové hlavičky po každém callu, persistuje usage do `.usage/`, varuje > 75 %, throttluje > 90 % a **odmítne další cally ≥ 95 %** na daném účtu. Rate-limited požadavky opakuje s backoffem 5/15/60 s (respektuje `estimated_time_to_regain_access`; při odhadu > 5 min neblokuje terminál a poradí počkat). Stav: `./run.sh api-limits`. Override `METAADS_IGNORE_USAGE_GUARD=1` používej jen, když víš, že hodinové okno už se resetovalo — guard je tu od toho, aby tě chránil před zablokováním účtu.

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
| `creative-create` | Jednoduchá kreativa (link/video/photo/carousel; `--no-enhancements` vypne Advantage+ úpravy) |
| `creative-clone` | Klon Advantage+ kreativy se swapem videa/obrázku/URL (přenáší `url_tags`; `--url-tags` přepíše, `--no-enhancements`) |
| `creative-from-post` | Kreativa z existujícího FB postu (promoce organiky; `--no-enhancements`) |
| `creative-from-ig` | Kreativa z IG postu/Reelu (promoce organiky; `--no-enhancements`) |
| `ig-media` | Výpis IG médií page-connected účtu |
| `preview` | HTML náhled reklamy/kreativy (`--format`, `--out soubor.html`) |
| `creative-delete` | Trvalé smazání kreativy (bez PAUSED brzdy — kreativy status nemají; použitou odmítne Meta) |
| `image-upload` | Upload obrázku → hash (s lint kontrolou rozměrů; **zapisuje rovnou**, bez dry-runu — plní jen knihovnu médií) |
| `video-upload` | Upload videa → ID (`--wait` počká na zpracování; > 100 MB automaticky chunked, `--chunked` vynutí; **zapisuje rovnou** — plní jen knihovnu médií) |

### Insights & analýza

| Příkaz | Popis |
|---|---|
| `insights` | Výkonová data (breakdowny vč. asset, atribuce `1d_click`/`7d_click`/`28d_click`/`1d_view`/`1d_ev`, filtering, sort; varuje při ořezu nad `--limit`) |
| `insights-report` | Async report pro velké dotazy (varuje při dosažení stropu 5000 řádků) |
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

## Testy

```bash
pip install -r requirements-dev.txt
python -m pytest tests/
```

Suite pokrývá bezpečnostní mechanismy (redakce tokenů, rate-limit guard, dry-run mutací, retry politiku, atomický zápis `.env`) — běží kompletně offline, bez credentials.

## Dokumentace

- [CHANGELOG.md](CHANGELOG.md) — historie verzí (sleduj přes Watch → Custom → Releases)
- [docs/api-notes.md](docs/api-notes.md) — reálné chování Marketing API vč. živě ověřených quirků
- [CLAUDE.md](CLAUDE.md) — orientace pro AI agenty (struktura kódu, safety, release checklist)

## Chyby a náměty

Něco nefunguje nebo chybí? Založ **GitHub Issue**. Pull requesty vítány — repo je primárně výukové, drž se stylu okolního kódu a přilož test.

## Licence

[MIT](LICENSE) © 2026 Jindřich Fáborský
