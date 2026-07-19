# Instalace skillu /meta-ads do Claude Code

Skill je v repu v jediné kanonické verzi (`skill/meta-ads/`) — instalace = kopie + dosazení cesty k appce.

## 1. Zkopíruj skill

```bash
cp -r <cesta-k-repu>/skill/meta-ads ~/.claude/skills/meta-ads
```

## 2. Dosaď cestu k appce

Skill volá CLI přes placeholder `<META_APP_DIR>`. Nahraď ho skutečnou cestou:

```bash
APP_DIR="$HOME/dev/meta-ads-app"   # uprav na svou cestu
sed -i '' "s#<META_APP_DIR>#$APP_DIR#g" ~/.claude/skills/meta-ads/SKILL.md ~/.claude/skills/meta-ads/meta-creative-editing.md   # macOS
# Linux: sed -i "s#<META_APP_DIR>#$APP_DIR#g" ~/.claude/skills/meta-ads/SKILL.md ~/.claude/skills/meta-ads/meta-creative-editing.md
```

## 3. Ověř

V Claude Code spusť `/meta-ads` — skill by měl načíst účet přes `run.sh account`. Pokud hlásí, že `<META_APP_DIR>` je stále v souboru, krok 2 se nepovedl.

## Doplň si vlastní know-how (volitelné, doporučené)

Skill je záměrně generický — mechanika a bezpečnostní pravidla, žádná strategie. Vlastní playbook (cílové CPA/ROAS, kreativní strategii, zakázané postupy…) přidej jako další soubor do `~/.claude/skills/meta-ads/` (např. `my-strategy.md`) a připiš ho do sekce **Load Reference Documents** v SKILL.md. Při aktualizaci skillu z repa se tvůj soubor nepřepíše — přepisuj jen SKILL.md a meta-creative-editing.md.

## Aktualizace

Nová verze appky může přinést i novou verzi skillu — po `git pull` zopakuj kroky 1–2 (svoje vlastní reference soubory zachovej).
