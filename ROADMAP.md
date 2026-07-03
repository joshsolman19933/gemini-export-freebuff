# 🗺️ Gemini Chat Exporter — Fejlesztési Roadmap

> **Állapot:** 2026. július — A projekt magja kiforrott, a FEJLESZTESI_OTLETEK.md fázisainak nagy része implementálva.
> Ez a dokumentum a következő fejlesztési ciklusok tervét tartalmazza, priorizálva és mérföldkövekre bontva.

---

## 📊 Jelenlegi Állapot Összegzése

| Terület | Érettség | Megjegyzés |
|---|---|---|
| Export logika | ⭐⭐⭐⭐⭐ | Pagináció, retry, párhuzamos letöltés, képek — kiforrott |
| Tudástár (manifest DB) | ⭐⭐⭐⭐ | SQLite + FTS5 + metaadat CRUD — stabil |
| Webes dashboard | ⭐⭐⭐⭐ | SPA, keresés, RAG chat, analitika, batch AI — impozáns |
| AI réteg | ⭐⭐⭐⭐⭐ | 7 provider (OpenAI/Anthropic/Gemini/Groq/Together/DeepSeek/Ollama), RAG, hybrid search |
| Tesztek | ⭐⭐⭐ | 38 teszt (mind zöld), főként unit — jó alap |
| Kódminőség | ⭐⭐⭐⭐⭐ | `export.py` 60 sor (1830-ról), 9 modul a `gemini_export/` csomagban |

**Teszt státusz:** 38/38 ✅ — `tests/test_api.py` (11), `tests/test_helpers.py` (17), `tests/test_knowledge.py` (10)

**M1.1 haladás:** `gemini_export/` csomag **✅ KÉSZ** — 9 modul: `utils.py`, `config.py`, `manifest.py`, `formatters.py`, `search.py`, `pagination.py`, `image_utils.py`, `exporter.py`, `cli.py`. `export.py`: 1830 → 60 sor (vékony wrapper).

---

## 🎯 Mérföldkövek

```
┌─────────────────────────────────────────────────────────────────────────┐
│  M1: Alapozás        │  M2: Biztonság    │  M3: Funkciók    │  M4: AI  │
│  export.py refaktor  │  Dashboard auth   │  PDF export      │  Több    │
│  Logging framework   │  API rate limit   │  Tömeges műv.   │  provider│
│  Kódstílus fixek     │  Cookie keyring   │  Virtuális scroll│  Lokális │
│                      │                   │  Kapcsolat gráf  │  embed.  │
│  ~2-3 hét            │  ~1-2 hét         │  ~3-4 hét        │  ~2-3 hét│
└─────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│  M5: UX Polish      │  M6: DevOps        │  M7: Termék       │
│  Téma váltó         │  CI/CD pipeline    │  Desktop app      │
│  Billentyű help     │  .env.example fix  │  Import más       │
│  Kód másolás gomb   │  PyPI publikálás   │  platformokról    │
│  PWA támogatás      │                    │  Export ütemezés  │
│                      │                    │                   │
│  ~1-2 hét            │  ~1 hét            │  ~3-4 hét         │
└──────────────────────────────────────────────────────────────────┘
```

---

## 🔨 M1: Alapozó Refaktorálás (KRITIKUS — Ezzel Kezdd)

A legnagyobb hatású, legalacsonyabb kockázatú változtatások. Ezek nélkül minden további fejlesztés exponenciálisan lassulni fog.

### 1.1 `export.py` feldarabolása modulokra

**Probléma:** 1830 sor, 40 függvény egy fájlban — nehezen tesztelhető, nehezen bővíthető.

**Aktuális struktúra** (✅ M1.1 kész):
```
gemini_export/              # ✅ Létrehozva
├── __init__.py             # ✅ Re-exportokkal (35+ szimbólum)
├── utils.py                # ✅ sanitize_filename(), format_timestamp(), parse_date(), filter_chats(), stb.
├── config.py               # ✅ load_config()
├── manifest.py             # ✅ _init_manifest(), _manifest_mark_*(), _manifest_get_stats()
├── formatters.py           # ✅ export_chat_to_*(), generate_all_chats_html(), _build_html_chat_content()
├── search.py               # ✅ FTS5 kereső + metaadat CRUD + _browse_chats()
├── pagination.py           # ✅ _fetch_chats_paginated() + _retry_read_chat()
├── image_utils.py          # ✅ _download_turn_images()
├── exporter.py             # ✅ export_all_chats(), _export_single_chat()
└── cli.py                  # ✅ parse_args() + main()

export.py                   # ✅ Vékony wrapper (~60 sor) — backward compat re-exportok
```

**Migrációs terv:**
- [x] Létrehozni a `gemini_export/` csomagot `__init__.py`-vel
- [x] `utils.py` kiszervezése (alacsony függőség) — 9 utility függvény
- [x] `config.py` kiszervezése — `load_config()`
- [x] `manifest.py` kiszervezése — 5 manifest DB kezelő függvény
- [x] `formatters.py` kiszervezése — 8 formázó függvény + `_HIGHLIGHT_JS_CDN`
- [x] `search.py` kiszervezése — FTS5 + metaadat CRUD + `_browse_chats()`
- [x] `pagination.py` kiszervezése — `_fetch_chats_paginated()` + `_retry_read_chat()`
- [x] `image_utils.py` kiszervezése — `_download_turn_images()`
- [x] `exporter.py` kiszervezése — `export_all_chats()`, `_export_single_chat()`
- [x] `cli.py` kiszervezése — `parse_args()` + `main()` + backward-compat `export.py` wrapper
- [x] `app.py` importjainak frissítése az új csomagra
- [x] Minden tesztnek továbbra is zöldnek kell lennie (38/38 ✅)

**Eredmény:** modulonként 80-300 sor, egyértelmű felelősségi körök.

### 1.2 `ai_layer.py` kódstílus javítások

**Probléma:** Az `import math` és `from typing import Generator` a fájl közepén van (a RAG szekció előtt), ami nem követi a PEP8-at.

- [x] Minden import a fájl tetejére mozgatva (`re`, `math`, `Generator` a top importokba)
- [x] `batch_analyze_all` függvény teljességének ellenőrzése — nincs csonkolás
- [x] Ruff lint futtatva + fixálva (B905 `zip strict`, B007 unused vars, E501 long lines)

### 1.3 Proper logging framework

**Probléma:** A projekt jelenleg `print()` hívásokkal logol — nincs szint, nincs formátum, nincs fájlba írás.

- [x] Python `logging` modul bevezetése
- [x] Konfigurálható log szintek (DEBUG, INFO, WARNING, ERROR) környezeti változóból
- [x] Egyidejű konzol + fájl handler
- [x] Strukturált formátum: `[IDŐ] [SZINT] [MODUL] üzenet`
- [x] Az összes `print()` csere `logger.info()`-ra, `logger.warning()`-ra, stb.
- [x] `export.py` → `logger`
- [x] `app.py` → `logger`
- [x] `ai_layer.py` → `logger`

**Példa:**
```python
import logging
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("gemini-export.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)
```

---

## 🔒 M2: Biztonság & Stabilitás

### 2.1 Dashboard hitelesítés

**Probléma:** A `/dashboard` és az API endpointok jelenleg authentikáció nélkül elérhetők a hálózatról.

- [x] Token-alapú auth: `ACCESS_TOKEN` környezeti változó bevezetése
- [x] Flask middleware: minden `/api/*` és `/dashboard` kérés ellenőrzése
- [x] Login oldal (`/login`) — ha nincs token beállítva, skip
- [x] Session kezelés Flask session-nel
- [x] Rate limiting a login endpointra

**Minimál implementáció:**
```python
# Környezeti változó: DASHBOARD_ACCESS_TOKEN=my-secret-token
# Ha nincs beállítva, a dashboard nyitott marad (backward compat)
# Ha be van állítva: Authorization: Bearer <token> header vagy ?token=<token> query param
```

### 2.2 API Rate Limiting

- [x] `flask-limiter` telepítése
- [x] Limit: 30 req/perc `/api/chat/*/analyze`-ra (költséges AI hívás)
- [x] Limit: 10 req/perc `/api/rag/query`-re
- [x] Limit: 5 req/perc `/api/ai/batch-analyze`-re
- [x] Limit: 120 req/perc az összes többi endpointra
- [x] Rate limit header-ek válaszban: `X-RateLimit-*`

### 2.3 Cookie-k biztonságos tárolása

- [x] `keyring` könyvtár integrálása (`gemini_export/cookie_store.py`)
- [x] Windows: Credential Manager, macOS: Keychain, Linux: Secret Service
- [x] `setup.py` varázslóban opció: "Mentsem a cookie-kat a rendszer kulcstartóba?"
- [x] Fallback: `.env` fájl ha a keyring nem elérhető (`get_cookies()`: keyring → .env)
- [x] Figyelmeztetés indításkor ha a cookie-k plaintext `.env`-ben vannak
- [x] CLI flag-ek: `--store-cookies`, `--clear-cookies`, `--list-cookies`

---

## 🚀 M3: Új Funkciók

### 3.1 PDF Export

- [x] `weasyprint` integrálása (`gemini_export/pdf_formatter.py`)
- [x] Új formátum: `--format pdf`
- [x] Egyedi chat PDF (`export_chat_to_pdf`) + teljes archívum PDF (`generate_all_chats_pdf`)
- [x] Tartalomjegyzék, oldalszámozás, oldaltörések
- [x] Képek beágyazása (`file://` abszolút elérési utakkal)
- [x] API endpoint: `/api/chat/<cid>/export/pdf`
- [x] Print-optimalizált CSS `@media print` világos témával
- [x] Lazy WeasyPrint import (GTK hiánya nem akadályozza a többi funkciót)

### 3.2 Tömeges Műveletek a Dashboard-on

- [x] Chat lista elemeken checkbox
- [x] "Összes kijelölése" / "Kijelölés törlése" gombok
- [x] Batch action toolbar:
  - [x] Címke hozzáadása a kijelöltekhez
  - [x] Projekt beállítása a kijelöltekre
  - [x] Törlés (megerősítő dialog-gal)
  - [x] AI elemzés indítása a kijelöltekre
- [x] Shift+click tartomány kijelölés
- [x] Kijelöltek számlálója
- [x] Batch API: POST /api/chat/batch/tags, /delete, /ai-analyze (SSE)
- [x] Ctrl+A = Összes, Space = toggle, Esc = mégsem

### 3.3 Virtuális Scroll a Chat Listában

**Probléma:** 2000+ chat esetén a DOM mérete problémás lehet.

- [x] Saját lightweight virtual scroll implementáció (fix 54px sor, RAF throttle)
- [x] Csak a látható + buffer (overscan 8) elemek renderelése
- [x] Scroll pozíció visszaállítása navigációnál (`scrollTop` mentés/visszaállítás)
- [x] Keresési eredményeknél is működik (`displayedChats` alapú)
- [x] `contain:layout style` a teljesítményért, `scrollHeight` megőrzésével

### 3.4 Chat Kapcsolatok / Tudásgráf

- [x] Embedding similarity alapú "kapcsolódó chat-ek" panel (`find_related_chats()` koszinusz hasonlósággal)
- [x] Force-directed graph vizualizáció D3.js v7-tel (`forceSimulation`, glow filter, hover animáció, kattintás)
- [x] Kattintható node-ok → navigáció a chat-re + kapcsolódó chat-ek listája hasonlósági %-kal
- [x] Másodlagos élek: kapcsolódók egymás közti hasonlósága (0.45 küszöb)
- [x] API endpointok: `/api/chat/<cid>/related` + `/api/chat/<cid>/related/graph`
- [x] CSS-változó alapú színezés (`getComputedStyle`), téma váltáskor automatikusan frissül
- [x] `typeof d3` guard a CDN hiba ellen

### 3.5 Kimeneti Template Rendszer

- [x] Jinja2 template engine a HTML/Markdown generáláshoz (`gemini_export/template_engine.py`)
- [x] Beépített témák: `dark` (alap), `light`, `minimal`, `academic` (4× chat.j2 + all_chats.j2 CSS változat)
- [x] Egyedi CSS betöltése: `--template` CLI flag + `custom_css` paraméter
- [x] Template választás a webes felületen (`GET /api/templates` endpoint, `--template` a `/start` subprocess-ben)
- [x] Backward compat: a régi string-formázás megtartása automatikus fallback-ként
- [x] Kódblokk kezelés: `render_text` egyedi Jinja2 filter — ```...``` → `<pre><code>` wrap
- [x] Lazy init: Jinja2/template-ek hiánya nem akadályozza a többi funkciót

---

## 🤖 M4: AI Réteg Továbbfejlesztése

### 4.1 Több AI Provider Támogatása

- [x] Provider absztrakciós réteg (factory pattern) — `gemini_export/ai_providers.py`
- [x] OpenAI-kompatibilis wrapper: OpenAI, Groq, Together.ai, DeepSeek, Ollama (közös `openai` SDK)
- [x] Anthropic Claude API integráció (`_AnthropicResponse` adapter `.choices[0].message.content`-hez)
- [x] Google Gemini API integráció (`_GeminiResponse` adapter, `system_instruction` konverzió)
- [x] Provider választás: `AI_PROVIDER=openai|anthropic|gemini|groq|together|deepseek|ollama` + auto-detektálás base_url-ből
- [x] Minden providerhez tartozó embedding modell mapping (7 provider config, `get_embedding_model()`)
- [x] Thread-safe singleton factory (`threading.Lock`, double-check locking)
- [x] `ai_layer.py` refaktor: `_get_openai_client()` → `provider.chat_completion()`, `provider.embedding()`, `provider.chat_completion_stream()`

### 4.2 Lokális Embedding Modellek

**Probléma:** Jelenleg az embedding generáláshoz API hívás kell (OpenAI/Ollama).

- [x] `sentence-transformers` integrálása — `gemini_export/local_embedding.py`, lazy import
- [x] `all-MiniLM-L6-v2` mint alapértelmezett lokális embedding modell (384 dimenzió, ~80 MB)
- [x] Automatikus letöltés első használatkor (`SentenceTransformer` Hugging Face Hub-ból)
- [x] Thread-safe singleton (double-check locking, `_initialized` csak sikeres betöltés után)
- [x] Teljesen offline RAG keresés lehetősége (`EMBEDDING_PROVIDER=local`)
- [x] Környezeti változók: `EMBEDDING_PROVIDER=auto|local|openai|ollama|gemini`, `LOCAL_EMBEDDING_MODEL`
- [x] Auto-routing: lokálisat próbál először, API-ra fallback (`is_local_embedding_available()` check)

### 4.3 Chat Összehasonlítás AI-val

- [x] Két chat kiválasztása → AI összehasonlító elemzés (`compare_chats()` SSE streaming)
- [x] 4 perspektíva: "Általános", "Különbségek", "Hasonlóságok", "Részletesség"
- [x] `prompts/compare.txt` — egyedi compare template `{{title_a}}`, `{{content_a}}`, `{{title_b}}`, `{{content_b}}`, `{{perspective}}`, `{{lang}}` változókkal
- [x] API endpoint: `POST /api/ai/compare` (10/perc rate limit, SSE streaming válasz)
- [x] Dashboard UI: modal két chat keresővel/szelektálással, perspektíva gombok, markdown renderelt eredmény, pre-fill batch selection-ből
- [x] `ai_layer.py` integrálva: `_format_chat_for_ai()` 2×6000 char, `render_prompt()` import-fallback, `provider.chat_completion_stream()` min. 800 token

### 4.4 Személyre Szabott AI Prompt Template-ek

- [x] `prompts/` könyvtár a projektben (4 template fájl)
- [x] `prompts/summarize.txt` — egyedi összefoglaló prompt (`{{lang}}` változóval)
- [x] `prompts/todos.txt` — egyedi teendő kinyerő prompt (`{{lang}}` változóval)
- [x] `prompts/tags.txt` — egyedi címke javaslat prompt
- [x] `prompts/rag_query.txt` — RAG Q&A prompt (`{{lang}}`, `{{context}}` változókkal)
- [x] Változók támogatása: `{{title}}`, `{{lang}}`, `{{context}}` + `_substitute_variables()`
- [x] `gemini_export/prompt_templates.py` — loader: `get_prompt()`, `render_prompt()`, `save_prompt()`, `reset_prompt()`, `list_prompts()`, in-memory cache
- [x] UI a template-ek szerkesztéséhez (modal 4 tab-bal, textarea, mentés/visszaállítás, Esc+overlay close)
- [x] API endpointok: `GET /api/prompts` + `GET/POST/DELETE /api/prompts/<name>`
- [x] `ai_layer.py` integrálva: `generate_summary`, `extract_todos`, `suggest_tags`, `rag_query_stream` → `render_prompt()` import-fallback védettséggel

---

## ✨ M5: UX Polish

### 5.1 Téma Váltó (Világos/Sötét)

- [x] CSS változók alapján `[data-theme="light"]` és `[data-theme="dark"]` (12 alpha-csatornás változó)
- [x] Toggle gomb a topbar-ban (🌙/☀️), hover forgatás animációval
- [x] Választás mentése `localStorage`-ba (`gemini-theme` kulcs)
- [x] Rendszer téma követése (`prefers-color-scheme`) + automatikus váltás rendszer téma változáskor
- [x] Highlight.js CDN csere (`atom-one-dark` ↔ `atom-one-light`)
- [x] Chart.js `Chart.defaults.color` + `borderColor` frissítés + analitika chart refresh
- [x] `body` transition (`.3s`) a sima téma váltáshoz
- [x] Világos paletta: `#f8f9fa` háttér, `#4f46e5` (indigó) accent, `#fff` surface

### 5.2 Billentyűparancsok Segítség Modal

- [x] `?` gomb a topbar-ban → modal (kerek gomb, accent hover)
- [x] `?` / `Shift+?` billentyű a modal megnyitásához/bezárásához (input-ban is működik)
- [x] 5 szekciós shortcuts grid `.kbd` stílusú billentyűkkel:
  - Navigáció: `j/k` / `↓/↑`
  - Keresés: `/`, `Ctrl+K`, `Ctrl+F`, `Enter`, `Shift+Enter`, `Esc`
  - Chat műveletek: `f`, `t`, `Space`, `Ctrl+A`, `Shift+kattintás`
  - AI & Panel: `Ctrl+Q`
  - Egyéb: `?` — ez a súgó
- [x] `Esc` prioritási lánc: help modal → chat-en belüli keresés → globális keresés törlése
- [x] Overlay click-to-close + modal dialog `Bezárás` gomb

### 5.3 Kódblokk Másolás Gomb

- [x] Minden `<pre><code>` blokk jobb felső sarkában "📋 Másolás" gomb
- [x] Kattintásra a kód vágólapra másolása `navigator.clipboard.writeText()` API-val
- [x] Visszajelzés: "✅ Másolva!" 2 mp-ig + `.copied` zöld állapot
- [x] `execCommand('copy')` fallback rejtett textarea-val régebbi böngészőkhöz
- [x] `pre:focus-within` támogatás érintőképernyős eszközökhöz
- [x] `type="button"`, `title` + `aria-label` akadálymentesség

### 5.4 PWA Támogatás

- [x] `manifest.json` létrehozása (app név, ikonok, theme color, `display: standalone`, `window-controls-overlay`)
- [x] Service worker a statikus asset-ek cache-elésére (cache-first + CDN cache + network-first HTML offline fallback-kel)
- [x] Offline dashboard (cache-elt HTML/JS/CSS, API hívások nélkül nem működik, de a UI betölt)
- [x] "Telepítés" prompt mobilon + asztali gépen (`beforeinstallprompt`, 📲 gomb a topbar-ban)
- [x] App ikonok generálása több méretben (SVG kristály ikon + 192×192 és 512×512 PNG, raw bytes generator)
- [x] SW update toast (🔄 Frissítés elérhető! — `skipWaiting` + reload)
- [x] Apple iOS támogatás (`apple-mobile-web-app-capable`, `apple-touch-icon`, `status-bar-style`)

### 5.5 Chat-en Belüli Keresés (Ctrl+F a Readerben)

- [x] Kereső input a reader fejlécében (sticky keresősáv, `position:sticky; top:0`)
- [x] Találatok kiemelése (highlight) a szövegben (`TreeWalker` + `<mark>` wrap, case-insensitive)
- [x] Előző/Következő találat gombok (▲/▼ navigáció + ciklikus léptetés + `scrollIntoView`)
- [x] Találatok számlálója ("3/15", automatikus frissítés)
- [x] `Ctrl+F`/`Cmd+F` megnyitás, `Esc` bezárás, `Enter`/`Shift+Enter` navigáció
- [x] Keresősáv a reader-en kívül (nem semmisül meg chat váltáskor), `closeFindInChat()` hívás `renderChat()`-ban

### 5.6 Export Előzmények / Korábbi Exportok Listája

- [x] `export_history` + `export_presets` táblák a manifest DB-ben
- [x] `GET /api/exports/history` — utolsó 50 session (dátum, formátum, output, statisztikák ✅/⏭️/❌)
- [x] `GET/POST /api/exports/presets` + `DELETE /api/exports/presets/<id>`
- [x] Export beállítások mentése/betöltése preset-ként (prompt név megadás, select, delete)
- [x] Előzmények kártya a webes felületen (⏳ futó exportok, statisztikák összegzése)
- [x] Automatikus rögzítés: `_record_export_start()` / `_record_export_end()` a `/start` route-ban

---

## 🔧 M6: DevOps & CI/CD

### 6.1 CI/CD Pipeline (GitHub Actions)

- [x] `.github/workflows/test.yml`:
  - Python 3.10, 3.11, 3.12 mátrix (`fail-fast: false`)
  - `actions/setup-python@v5` pip cache-el
  - `pip install -r requirements.txt`
  - `pytest tests/ -v --tb=short`
  - `ruff check .` (pyproject.toml szerint, `--select` nélkül)
- [x] `.github/workflows/docker.yml`:
  - Docker image build + push Docker Hub-ra (buildx, metadata-action@v5, GHA cache)
- [x] `.github/workflows/release.yml`:
  - Git tag `v*` → PyPI publikálás (`build` + `twine upload --skip-existing`, pre-publish pytest ellenőrzés)

### 6.2 `.env.example` Ellenőrzése

- [x] `.env.example` fájl meglétének ellenőrzése — létezik, 18 környezeti változóval
- [x] Tartalom frissítése az új környezeti változókkal: `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `PROMPTS_DIR`
- [x] Meglévő változók kommentjeinek frissítése (AI_PROVIDER: "jövőbeli M4.1" → 7 provider felsorolva, EMBEDDING_PROVIDER: pontosított leírás, OPENAI_API_KEY: kiegészítve a kompatibilis API-kkal)
- [x] Mind a 18 aktív `os.getenv()` hívás lefedve a kódbázisból

### 6.3 PyPI Publikálás Előkészítése

- [x] `pyproject.toml` bővítése: `[build-system]`, `[project]` metaadatok (név, verzió 1.0.0, leírás, MIT licenc, 19 classifier, 6 kulcsszó), `[project.urls]`, `[project.scripts]`
- [x] Core + opcionális függőségek 7 csoportban (`ai`, `rate-limit`, `pdf`, `desktop`, `schedule`, `full`, `dev`)
- [x] `pip install gemini-chat-exporter` lehetőség — `gemini-export` CLI parancs (`export:main`), `py_modules` root szintű modulokhoz

---

## 🏁 M7: Termék Érettség

### 7.1 Desktop Alkalmazás

- [x] `pywebview` integrálása — a Flask app natív ablakban (`desktop_app.py`)
- [x] System tray ikon (`pystray`):
  - Export felület megnyitása böngészőben
  - Dashboard megnyitása böngészőben
  - Kilépés (graceful shutdown: SIGINT → Flask leáll)
- [x] PIL fallback ikon generálás (64×64 kék négyzet "G" betűvel)
- [x] Graceful degradation: pywebview/pystray/Pillow hiányában Flask-only szerver mód
- [x] CEF engine Windows-on (`gui="cef"`), natív webview macOS/Linux-on

### 7.2 Más Platformok Importálása

- [x] ChatGPT export JSON → Gemini formátum konvertáló (`_parse_chatgpt_conversation`: mapping fa bejárás parent láncon, multimodal parts, cycle detection)
- [x] Claude export JSON → Gemini formátum konvertáló (`_parse_claude_conversation`: chat_messages, human/assistant sender)
- [x] Univerzális `chat_import.py` tool (`gemini_export/chat_import.py`, auto-detektálás, CLI `--source` flag)
- [x] Importált chatek automatikus indexelése a manifest DB-be (`_manifest_mark_exported`, `_index_chat_for_search`, `_ensure_metadata_row`)
- [x] Forrás platform jelölése: `source: "chatgpt" | "claude" | "gemini"` (project mezőben + source_platform metaadat)

### 7.3 Export Ütemezés

- [x] `APScheduler` integrálása — `gemini_export/scheduler.py`: `BackgroundScheduler` + SQLite persistencia (`export_schedules` tábla)
- [x] Cron-szerű konfiguráció: `0 */6 * * *` (6 óránként) — `CronTrigger.from_crontab()` validálás
- [x] Háttérszál a Flask app-ban — `init_scheduler()` az `app.py __main__` blokkjában
- [x] UI az ütemezések kezelésére — modal kron inputtal, job lista pause/resume/delete/run-now gombokkal, next-run kijelzés
- [x] Desktop notification az eredményről — `_send_desktop_notification()`: PowerShell (Windows), osascript (macOS), notify-send (Linux)
- [x] Környezeti változó: `EXPORT_SCHEDULE=0 */12 * * *` — auto-schedule indításkor, ENV badge a UI-ban

---

## 📋 Teljes Checklist — Prioritással és Becsléssel

| # | Feladat | Prioritás | Munkaigény | Függőség |
|---|---|---|---|---|
| **M1: Alapozás** | | | | |
| 1.1 | `gemini_export/` csomag + `export.py` wrapper | 🔴 KRITIKUS | 🔴 4-6 óra | ✅ **KÉSZ** (9 modul + 60 soros wrapper) |
| 1.2 | `ai_layer.py` importok rendezése | 🟡 Magas | 🟢 15 perc | ✅ **KÉSZ** (importok topra, ruff clean, `_msg_count` bugfix) |
| 1.3 | Logging framework | 🟡 Magas | 🟡 2-3 óra | ✅ **KÉSZ** (logging_config.py, dual handler, LOG_LEVEL env) |
| **M2: Biztonság** | | | | |
| 2.1 | Dashboard auth | 🟡 Magas | 🟡 2 óra | ✅ **KÉSZ** (DASHBOARD_ACCESS_TOKEN, session, rate-limited login) |
| 2.2 | API rate limiting | 🟡 Magas | 🟢 1 óra | ✅ **KÉSZ** (flask-limiter, analyze 30/min, rag 10/min, default 120/min) |
| 2.3 | Cookie keyring | 🟢 Közepes | 🟡 2 óra | ✅ **KÉSZ** (cookie_store.py, keyring→env fallback, 3 CLI flag) |
| **M3: Funkciók** | | | | |
| 3.1 | PDF export | 🟢 Közepes | 🟡 3-4 óra | ✅ **KÉSZ** (pdf_formatter.py, WeasyPrint, egyedi+összesített, TOC) |
| 3.2 | Tömeges műveletek | 🟡 Magas | 🟡 3-4 óra | ✅ **KÉSZ** (checkbox, toolbar, Shift+click, modal, batch API) |
| 3.3 | Virtuális scroll | 🟢 Közepes | 🟡 3-4 óra | ✅ **KÉSZ** (fix 54px sor, RAF throttle, overscan 8, scrollTop save) |
| 3.4 | Chat kapcsolat gráf | 🟢 Közepes | 🔴 5-6 óra | ✅ **KÉSZ** (D3.js force-directed, find_related_chats, primary+secondary edges, glow+hover, click nav) |
| 3.5 | Template rendszer | 🟢 Alacsony | 🟡 3 óra | ✅ **KÉSZ** (Jinja2 engine, 4 téma, render_text filter, --template CLI, /api/templates, auto-fallback) |
| **M4: AI** | | | | |
| 4.1 | Több AI provider | 🟢 Közepes | 🔴 4-5 óra | ✅ **KÉSZ** (7 provider factory, ABC, OpenAICompat+Anthropic+Gemini wrappers, thread-safe singleton) |
| 4.2 | Lokális embedding | 🟢 Közepes | 🟡 3 óra | ✅ **KÉSZ** (sentence-transformers, all-MiniLM-L6-v2, auto-routing, thread-safe singleton) |
| 4.3 | Chat összehasonlítás | 🟢 Alacsony | 🟡 2-3 óra | ✅ **KÉSZ** (4 perspektíva, compare template, POST /api/ai/compare, modal UI) |
| 4.4 | Egyedi prompt template-ek | 🟢 Alacsony | 🟡 2 óra | ✅ **KÉSZ** (4 template, loader, modal editor, 3 API, ai_layer integráció) |
| **M5: UX** | | | | |
| 5.1 | Téma váltó | 🟢 Közepes | 🟢 1 óra | ✅ **KÉSZ** (data-theme, ☀️/🌙 toggle, localStorage, prefers-color-scheme, hljs+Chart swap) |
| 5.2 | Billentyű help modal | 🟢 Közepes | 🟢 30 perc | ✅ **KÉSZ** (5 szekció, ? gomb, Esc lánc, kbd stílus, overlay click-to-close) |
| 5.3 | Kód másolás gomb | 🟢 Közepes | 🟢 1 óra | ✅ **KÉSZ** (📋 btn, clipboard API + execCommand fallback, focus-within, aria-label) |
| 5.4 | PWA támogatás | 🟢 Alacsony | 🟡 2-3 óra | ✅ **KÉSZ** (manifest.json, SW cache-first+CDN, install prompt, SVG+PNG ikonok, iOS meta tagek) |
| 5.5 | Chat-en belüli keresés | 🟢 Alacsony | 🟡 2 óra | ✅ **KÉSZ** (TreeWalker find, mark highlights, ▲/▼ nav, counter, Ctrl+F/Esc/Enter keys) |
| 5.6 | Export előzmények | 🟢 Alacsony | 🟡 2 óra | ✅ **KÉSZ** (history+presets DB, 4 API, UI kártyák, statisztikák) |
| **M6: DevOps** | | | | |
| 6.1 | CI/CD pipeline | 🟢 Közepes | 🟡 2 óra | ✅ **KÉSZ** (test.yml 3×Python mátrix, docker.yml, release.yml pre-publish teszttel) |
| 6.2 | `.env.example` frissítés | 🟢 Közepes | 🟢 15 perc | ✅ **KÉSZ** (18 env var, +ANTHROPIC_API_KEY, +GEMINI_API_KEY, +PROMPTS_DIR) |
| 6.3 | PyPI publikálás | 🟢 Alacsony | 🟡 1 óra | ✅ **KÉSZ** (pyproject.toml: build-system, 19 classifier, 7 optional-deps, CLI entry point) |
| **M7: Termék** | | | | |
| 7.1 | Desktop alkalmazás | 🟢 Alacsony | 🔴 5-6 óra | ✅ **KÉSZ** (pywebview ablak, pystray tray, graceful degradation) |
| 7.2 | Platform import | 🟢 Alacsony | 🔴 4-5 óra | ✅ **KÉSZ** (chat_import.py, ChatGPT+Claude parser, manifest integráció) |
| 7.3 | Export ütemezés | 🟢 Alacsony | 🟡 3 óra | ✅ **KÉSZ** (scheduler.py, cron CRUD API, modal UI, desktop notification) |

---

## 🎯 Ajánlott Kezdési Sorrend

```
1. hét:  M1.1 (export.py refaktor) — a legnagyobb blocker
2. hét:  M1.2 + M1.3 (kódstílus + logging)
3. hét:  M2.1 + M2.2 (auth + rate limit) 
4. hét:  M3.2 + M5.1 (tömeges műveletek + téma váltó)
5. hét:  M2.3 + M5.2 + M5.3 (keyring + billentyű help + kód másolás)
6. hét:  M3.1 (PDF export)
7. hét:  M4.1 + M4.2 (több provider + lokális embedding)
8. hét:  M3.3 + M5.4 (virtuális scroll + PWA)
```

**Első 4 hét fókusza:** A projekt alapjainak megerősítése + a legnagyobb UX hiányosságok pótlása.  
**Második 4 hét:** Új funkciók és AI képességek bővítése.

---

## 🏆 "Quick Wins" — Apró, Nagy Hatású Változtatások

Ezeket bármikor be lehet illeszteni, akár egy-egy délután alatt:

| Feladat | Idő | Hatás |
|---|---|---|
| `.env.example` létrehozása/frissítése | 15 perc | Új fejlesztők onboardingja |
| `ai_layer.py` import rendezés + Ruff fix | 15 perc | ✅ KÉSZ |
| Billentyűparancsok help modal | 30 perc | ✅ KÉSZ |
| Kódblokk másolás gomb | 1 óra | ✅ KÉSZ |
| Téma váltó (világos/sötét) | 1 óra | ✅ KÉSZ |
| Chat-en belüli keresés | 2 óra | Funkcionalitás |
| API rate limiting | 1 óra | ✅ KÉSZ |
| Dashboard auth token | 2 óra | ✅ KÉSZ |

---

## 📝 Megjegyzések

- **Backward Compatibility:** Minden változtatásnál alapelv, hogy a meglévő API, CLI interface és fájlstruktúra ne törjön. A `export.py` refaktorálás után is működnie kell a `python export.py --format all` parancsnak.
- **Tesztelés:** Minden mérföldkő után futtatni kell a teljes teszt suite-ot (`pytest tests/ -v`). A 38 meglévő tesztnek mindig zöldnek kell maradnia.
- **Dokumentáció:** A `README.md` frissítése minden jelentősebb változás után.
- **Git:** Minden mérföldkő külön branch-en (`feat/m1-refactor`, `feat/m2-security`, stb.)

---

*Utolsó frissítés: 2026. július 3. — M1 ✅, M2 ✅ (Biztonság), M3.1–M3.5 ✅ (Funkciók), M4.1–M4.4 ✅ (AI réteg), M5.1–M5.6 ✅ (UX Polish), M6.1–M6.3 ✅ (DevOps), M7.1–M7.3 ✅ (Termék). 38/38 teszt zöld.*
