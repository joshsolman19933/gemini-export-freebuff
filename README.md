# Gemini Chat Exporter

Teljes Gemini beszelgetes-exportalo eszkoz a `gemini_webapi` csomaggal,
kibovitve keresheto tudastarral, modern webes dashboarddal, AI elemzessel,
tobb AI provider tamogatassal, desktop alkalmazassal es cron-alapu utemezessel.

Az **osszes** beszelgetest exportalja a `gemini.google.com` feluletrol,
paginacioval, parhuzamos letoltessel, tobbszoros formatum tamogatassal.
Az exportalt beszelgeteseket SQLite adatbazisban indexeli, teljes szoveges
keresessel (FTS5), cimkekkel, projektkezelessel es AI altal generalt
osszefoglalokkal.

**Tobb platform import:** ChatGPT es Claude JSON exportok is betolthetok
a Gemini formatumba, igy egyseges tudastarban kereshetsz minden AI
beszelgetesed kozott.

---

## Funkciok

| Kategoria | Funkcio | Leiras |
|---|---|---|
| **Export** | Osszes chat exportalasa | Page-token alapu paginacio, atlepi a ~100-as API limitet |
| | Parhuzamos letoltes | `asyncio.gather` + semaphore, konfiguralhato konkurenciaval (alap: 3) |
| | Tobb formatum | JSON, Markdown, HTML, CSV, **PDF** (WeasyPrint) |
| | Kepek letoltese | Automatikus kep mentes `exports/media/`-ba |
| | Resume tamogatas | Mar exportalt chatek automatikus kihagyasa, manifest DB-vel |
| | Datum/kulcsszo szures | `--from` / `--to` / `--filter` |
| **Tudastar** | Teljes szoveges kereso | SQLite FTS5, hibrid keresessel (FTS5 + embedding) |
| | Cimkek es projektek | Cimkezes, projekt rendezes, kedvencek ⭐ |
| | Virtuális scroll | Fix 54px soros, RAF throttle, 2000+ chat kozott is gyors |
| | Chat kapcsolat graf | D3.js force-directed graf, embedding similarity alapu |
| | Export elozmenyek | Preset mentes/betoltes, elozmenyek lista |
| **AI** | AI elemzes | Osszefoglalok, teendok, cimkek — 7 AI provider |
| | RAG Q&A (🧠) | Kerdezz az archívumodbol — embedding alapu szemantikus kereses + LLM valasz |
| | Chat osszehasonlitas | Ket chat AI osszehasonlitasa 4 perspektivabol (SSE streaming) |
| | Egyedi prompt template-ek | Szerkesztheto AI promptok a dashboardon |
| | Tobb AI provider | OpenAI, Anthropic Claude, Google Gemini, Groq, Together.ai, DeepSeek, Ollama |
| | Lokalis embedding | `sentence-transformers` — teljesen offline RAG kereses |
| **Platform** | ChatGPT import | ChatGPT `conversations.json` → Gemini formatum |
| | Claude import | Claude `conversations.json` → Gemini formatum |
| | Egyseges tudastar | Minden platform egy kozos adatbazisban |
| **UX** | Tema valto | Vilagos/sotet (`localStorage` + rendszer tema kovetese) |
| | Billentyuparancsok | `j/k` nav, `Ctrl+F` keres, `Ctrl+Q` RAG, `?` help modal |
| | Kod masolas gomb | 📋 gomb minden `<pre><code>` blokkon |
| | PWA tamogatas | Telepitsd mobilon/asztalon — offline is mukodo UI |
| | Chat-en beluli kereses | `Ctrl+F` a readerben, highlight + ▲/▼ navigacio |
| **DevOps** | Docker support | `docker compose up -d` |
| | CI/CD pipeline | GitHub Actions: pytest + ruff 3 Python verzion |
| | PyPI publikacio | `pip install gemini-chat-exporter` → `gemini-export` CLI |
| | Telepito varazslo | `python setup.py` — interaktiv elso beallitas |
| | Desktop alkalmazas | `python desktop_app.py` — natív ablak (pywebview) + system tray (pystray) |
| | Export utemezes | Cron-alapu automatikus export (APScheduler) + desktop notification |

---

## Gyors kezdes

### A) PyPI-rol (leggyszerubb)

```bash
pip install gemini-chat-exporter
gemini-export --format all
```

Az elso futtatas elott allitsd be a cookie-kat a `.env`-ben (lasd `.env.example`).

### B) Telepito varazsloval (ajanlott elso hasznalatra)

```bash
pip install -r requirements.txt
python setup.py
```

A varazslo vegigvezet a cookie-k, konyvtarak es AI beallitasok megadasan.

### C) Dockerrel

```bash
cp .env.example .env
# Szerkeszd a .env fajlt: add meg a GEMINI_SECURE_1PSID ertekeket!
docker compose up -d
# Nyisd meg: http://localhost:5000
```

### D) Manualisan

```bash
pip install -r requirements.txt
cp .env.example .env
# Szerkeszd a .env fajlt a cookie ertekeiddel
python export.py --format all
```

### Opcionalis fuggo funkciok

```bash
# AI funkciok (OpenAI / Ollama / Claude):
pip install gemini-chat-exporter[ai]

# PDF export:
pip install gemini-chat-exporter[pdf]

# Desktop app (natív ablak + tray ikon):
pip install gemini-chat-exporter[desktop]

# Export ütemezés:
pip install gemini-chat-exporter[schedule]

# Minden funkcio:
pip install gemini-chat-exporter[full]
```

---

## CLI opciok

### Export parancsok

| Opcio | Alap | Leiras |
|---|---|---|
| `--format` | `both` | `json`, `markdown`, `html`, `csv`, `pdf`, `both`, `all` |
| `--output` | `./exports` | Kimeneti konyvtar |
| `--delay` | `0.5` | Kesleltetes mp-ben |
| `--concurrency`, `-c` | `3` | Parhuzamos letoltesek szama |
| `--max-chats` | `2000` | Maximum lekerheto beszelgetesek |
| `--from` | - | Datum szures kezdete (EEEE-HH-NN) |
| `--to` | - | Datum szures vege (EEEE-HH-NN) |
| `--filter` | - | Kulcsszo szures (case-insensitive) |
| `--template` | `dark` | HTML tema: `dark`, `light`, `minimal`, `academic` |
| `--list-chats` | - | Csak listazas, export nelkul |
| `--no-resume` | - | Teljes ujrakezdes |
| `--auto-cookies` | - | Cookie-k automatikus importalasa bongeszobol |
| `--ai-analyze` | - | Export utan AI elemzes (osszefoglalo, teendok, cimkek) |

### Tudastar parancsok (API kapcsolat nelkul is mukodnek):

| Opcio | Leiras |
|---|---|
| `--search "kereses"` | Hibrid kereses (FTS5 + embedding) |
| `--tag CHAT_ID "cimke1, cimke2"` | Cimkezes |
| `--list-tags` | Osszes cimke listazasa |
| `--browse` | Interaktiv chat bongeszo |
| `--reindex` | Korabbi exportok ujraindexelese |

### Import parancsok:

| Opcio | Leiras |
|---|---|
| `--import chatgpt FILE` | ChatGPT `conversations.json` importalasa |
| `--import claude FILE` | Claude `conversations.json` importalasa |
| `--import auto FILE` | Auto-detektalas (ChatGPT vs Claude) |
| `--source chatgpt` | Forras platform megadasa |

---

## Webes felulet

### Export felulet
```bash
python app.py
# → http://localhost:5000
```

### Tudastar dashboard
```bash
python app.py
# → http://localhost:5000/dashboard
```

A dashboard harom panelbol all:
- **Bal oldal**: Chat lista hibrid keresessel + cimke szurokkel + virtualis scroll + batch kijeloles
- **Kozepen**: Olvasonezet a beszelgetesekhez, AI osszefoglaloval, chat-en beluli keresessel (`Ctrl+F`)
- **Jobb oldal**: Metaadat szerkesztes (cimkek, projekt, jegyzet, kedvenc) + AI elemzes gomb + statisztikak

### Dashboard extrak:
- 🧠 **RAG Chat** (`Ctrl+Q`): Kerdezz az archívumodbol termeszetes nyelven
- 🔄 **Chat osszehasonlitas**: Ket chat AI osszehasonlitasa 4 perspektivabol
- 📊 **Analitika**: Chart.js diagramok — idosoros, cimke, hisztogram
- 🔗 **Kapcsolat graf**: D3.js force-directed — mely chat-ek kapcsolodnak egymashoz
- 🕐 **Export utemezes**: Cron-alapu automatikus export kezelese
- 📝 **AI prompt editor**: Szemelyre szabhato prompt template-ek
- 🌙 **Tema valto**: Vilagos/sotet tema valtogomb
- ? **Billentyuparancsok**: `?` gomb reszletes help modal
- 📋 **Kod masolas**: Automatikus masolas gomb minden kodblokkon
- 📲 **PWA**: Telepitsd asztali/mobil alkalmazaskent

---

## Desktop alkalmazas

```bash
python desktop_app.py
```

Natív ablakban futtatja a Flask app-ot:
- **Windows**: CEF engine, natív keret
- **macOS/Linux**: Natív WebView
- **System tray**: pystray ikon — Export, Dashboard, Kilepes menupontokkal
- **Fallback**: Ha pywebview/pystray nincs telepítve, automatikusan Flask-only szerver mod

---

## Export utemezes

Cron-alapu automatikus exportalas a dashboardrol vagy kornyezeti valtozobol:

```bash
# Kornyezeti valtozo: minden 12 oraban
EXPORT_SCHEDULE="0 */12 * * *"
```

A dashboard `🕐 Export ütemezés` modaljaban:
- Cron kifejezes megadasa (pl. `0 */6 * * *` = 6 orankent)
- Formatum, delay es egyeb opciok beallitasa
- Aktiv utemezesek listaja pause/resume/delete/run-now gombokkal
- Kovetkezo futtatas idejenek kijelzese
- Desktop notification az export befejezterol (Windows/macOS/Linux)

---

## AI Provider tamogatas

7 tamogatott AI szolgaltato, automatikus detektalassal:

| Provider | Valtozo/Base URL | Embedding |
|---|---|---|
| OpenAI | `OPENAI_API_KEY` | `text-embedding-3-small` |
| Anthropic Claude | `ANTHROPIC_API_KEY` | OpenAI (fallback) |
| Google Gemini | `GEMINI_API_KEY` | `text-embedding-004` |
| Groq | `OPENAI_API_KEY` (Groq base URL) | — |
| Together.ai | `OPENAI_API_KEY` (Together base URL) | — |
| DeepSeek | `OPENAI_API_KEY` (DeepSeek base URL) | — |
| **Ollama** (helyi) | `http://localhost:11434/v1` | `nomic-embed-text` |

```bash
# Provider kivalasztasa kornyezeti valtozoval:
AI_PROVIDER=ollama          # Helyi Ollama
AI_PROVIDER=anthropic       # Claude
AI_PROVIDER=openai          # OpenAI (alapertelmezett)
```

**Lokalis embedding:** `sentence-transformers` (`all-MiniLM-L6-v2`) — teljesen offline RAG kereses `EMBEDDING_PROVIDER=local` beallitassal.

---

## Kimeneti fajlstruktura

```
exports/
├── manifest.db                # SQLite: export allapot, FTS5 kereso, metaadatok, AI eredmenyek,
│                               #   export_schedules, export_history, export_presets
├── all_chats.json             # Osszes chat egy JSON-ban
├── all_chats.html             # Osszes chat egyetlen HTML-ben (navigacioval + keresovel)
├── all_chats.pdf              # Osszes chat egy PDF-ben (tartalomjegyzekkel)
├── json/
│   └── Beszelgetes_cime_abc12345.json
├── markdown/
│   ├── INDEX.md
│   └── Beszelgetes_cime_abc12345.md
├── html/
│   └── Beszelgetes_cime_abc12345.html
├── pdf/
│   └── Beszelgetes_cime_abc12345.pdf
├── csv/
│   └── chats.csv
└── media/
    └── abc123456789/
        ├── a1b2c3d4e5.png
        └── f6g7h8i9j0.jpg
```

---

## Kornyezeti valtozok

### Gemini hitelesites

| Valtozo | Leiras | Alapertelmezett |
|---|---|---|
| `GEMINI_SECURE_1PSID` | Gemini session cookie | - |
| `GEMINI_SECURE_1PSIDTS` | Gemini session cookie | - |
| `GEMINI_AUTO_COOKIES` | Auto-cookie (`1` = be) | `0` |

### Export beallitasok

| Valtozo | Leiras | Alapertelmezett |
|---|---|---|
| `EXPORT_OUTPUT_DIR` | Kimeneti konyvtar | `./exports` |
| `EXPORT_DELAY` | Kesleltetes mp-ben | `0.5` |
| `EXPORT_SCHEDULE` | Cron kifejezes automatikus exportra | - |

### AI szolgaltatok

| Valtozo | Leiras | Alapertelmezett |
|---|---|---|
| `AI_PROVIDER` | AI szolgaltato: `openai`, `anthropic`, `gemini`, `groq`, `together`, `deepseek`, `ollama` | auto-detektalas |
| `OPENAI_API_KEY` | OpenAI / kompatibilis API kulcs | - |
| `OPENAI_BASE_URL` | API base URL | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | Chat modell nev | `gpt-4o-mini` |
| `OPENAI_MAX_TOKENS` | Max valasz tokenek | `500` |
| `OPENAI_EMBEDDING_MODEL` | Embedding modell | `text-embedding-3-small` |
| `ANTHROPIC_API_KEY` | Anthropic Claude API kulcs | - |
| `GEMINI_API_KEY` | Google Gemini API kulcs | - |

### Embedding & RAG

| Valtozo | Leiras | Alapertelmezett |
|---|---|---|
| `EMBEDDING_PROVIDER` | Embedding forras: `auto`, `local`, `openai`, `ollama`, `gemini` | `auto` |
| `LOCAL_EMBEDDING_MODEL` | Lokalis embedding modell | `all-MiniLM-L6-v2` |

### Egyeb

| Valtozo | Leiras | Alapertelmezett |
|---|---|---|
| `DASHBOARD_ACCESS_TOKEN` | Dashboard auth token (ha ures, nyitott) | - |
| `LOG_LEVEL` | Log szint: `DEBUG`, `INFO`, `WARNING`, `ERROR` | `INFO` |
| `PROMPTS_DIR` | Egyedi prompt template-ek konyvtara | `prompts` |

---

## Docker

```bash
# Inditas
docker compose up -d

# Logok megtekintese
docker compose logs -f

# Leallitas
docker compose down

# Ujraepites frissites utan
docker compose up -d --build
```

A kontener az alabbi portokat es koteteket hasznalja:
- **Port**: `5000` (Flask webes felulet)
- **Kotet**: `./exports` → kontenerben is perzisztalva
- **Kornyezet**: `.env` fajlbol olvasva

---

## Architektura

```
gemini-export/
├── export.py                  # CLI eszkoz: backward compat re-exportok (60 sor)
├── app.py                     # Flask webes GUI (export felulet + dashboard API + scheduler)
├── ai_layer.py                # AI reteg: 7 provider, RAG, hybrid search, osszehasonlitas
├── setup.py                   # Telepito varazslo (interaktiv elso beallitas)
├── desktop_app.py             # Desktop alkalmazas: pywebview + pystray tray ikon
├── pyproject.toml             # PyPI csomagolas: build, dependenciak, CLI entry point
├── Dockerfile                 # Tobb lepcsos kontener epites
├── docker-compose.yml         # Docker szolgaltatas definicio
├── .github/workflows/         # CI/CD pipeline (test.yml, docker.yml, release.yml)
├── prompts/                   # Egyedi AI prompt template-ek
│   ├── summarize.txt, todos.txt, tags.txt, rag_query.txt, compare.txt
├── templates/
│   ├── index.html             # Export felulet (Flask GUI)
│   └── dashboard.html         # Tudastar dashboard (SPA, ~2000 sor)
├── gemini_export/             # Fo csomag (9+ modul)
│   ├── cli.py                 # CLI: parse_args() + main()
│   ├── exporter.py            # export_all_chats(), _export_single_chat()
│   ├── manifest.py            # SQLite manifest DB kezelo
│   ├── formatters.py          # JSON/HTML/Markdown/CSV formatumok
│   ├── pdf_formatter.py       # PDF export (WeasyPrint)
│   ├── template_engine.py     # Jinja2 template engine (4 beepitett tema)
│   ├── search.py              # FTS5 kereso + metaadat CRUD
│   ├── pagination.py          # API paginacio + retry
│   ├── image_utils.py         # Kep letoltes
│   ├── config.py              # Konfiguracio kezeles
│   ├── utils.py               # Segedfuggvenyek (9 utility)
│   ├── ai_providers.py        # Provider factory: OpenAI, Anthropic, Gemini, stb.
│   ├── local_embedding.py     # Lokalis embedding (sentence-transformers)
│   ├── prompt_templates.py    # Egyedi prompt template-ek kezelese
│   ├── scheduler.py           # Cron export utemezes (APScheduler)
│   ├── chat_import.py         # ChatGPT / Claude JSON import
│   ├── cookie_store.py        # Keyring cookie tarolas
│   ├── logging_config.py      # Strukturalt logging (dual handler)
│   └── __init__.py            # Re-exportok (35+ szimbolum)
├── tests/
│   ├── test_api.py            # API endpoint tesztek (11)
│   ├── test_helpers.py        # Segedfuggveny tesztek (17)
│   └── test_knowledge.py      # FTS5, metaadat CRUD tesztek (10)
├── .env.example               # Kornyezeti valtozo sablon
└── requirements.txt           # Python fuggosegek
```

---

## Mukodesi elv

1. **Hitelesites**: Gemini session cookie-kkal
2. **Chat lista**: `LIST_CHATS` RPC paginacioval (page token alapu)
3. **Szures**: Datum/kulcsszo szerint
4. **Export**: `asyncio.gather` parhuzamosan, valasztott formatumokba (JSON, MD, HTML, CSV, PDF)
5. **Kepek**: Letoltes `aiohttp`-val, `exports/media/`-ba
6. **Manifest**: SQLite adatbazis (`WAL` mod) — export allapot, FTS5 kereso, metaadatok, AI eredmenyek, utemezesek
7. **Retry**: 3x ujraprobalas exponencialis backoff-fal
8. **AI**: 7 provider (OpenAI, Claude, Gemini, Groq, Together, DeepSeek, Ollama) — factory pattern
9. **RAG**: Embedding alapu szemantikus kereses (OpenAI vagy helyi sentence-transformers) + LLM valasz
10. **Import**: ChatGPT/Claude JSON → Gemini formatum, egyseges manifest DB
11. **Utemezes**: APScheduler BackgroundScheduler + cron kifejezesek + desktop notification

---

## CI/CD & PyPI

### GitHub Actions

- **test.yml**: Python 3.10/3.11/3.12 matrix, pytest + ruff
- **docker.yml**: Docker build + push (buildx, GHA cache)
- **release.yml**: Git tag `v*` → auto PyPI publikacio

### PyPI telepites

```bash
pip install gemini-chat-exporter

# Majd:
gemini-export --format all
gemini-export --search "Python projekt"
gemini-export --import chatgpt conversations.json
```

---

## Rendszerkovetelmenyek

- Python 3.10+
- Windows, macOS, Linux
- Aktiv Google fiok Gemini elozmenyekkel
- Docker (opcionalis)
- OpenAI / Anthropic / Ollama (opcionalis, AI funkciokhoz)

---

## GYIK

### Sikertelen inicializalas

Cookie-k lejartak? Jelentkezz be ujra a gemini.google.com-on, frissitsd a `.env`-et, vagy hasznald az `--auto-cookies` kapcsolot.

### Csak 100 beszelgetes

Ellenorizd, hogy a kimenetben `paginacio: OK` szerepel-e. Ha `fallback mod`, a paginacio hibazott.

### Rate limit

```bash
python export.py --delay 2.0 --concurrency 1
```

### AI hiba: "API key not found"

Allitsd be az `OPENAI_API_KEY`-t a `.env`-ben, vagy hasznald az `setup.py` varazslot. Helyi Ollama-hoz: `AI_PROVIDER=ollama` + `ollama serve`.

### Hogyan importaljak ChatGPT/Claude beszelgeteseket?

```bash
# ChatGPT:
python export.py --import chatgpt /path/to/conversations.json

# Claude:
python export.py --import claude /path/to/conversations.json

# Auto-detektalas:
python export.py --import auto /path/to/conversations.json
```

Az importalt chatek automatikusan bekerulnek a manifest DB-be, kereshetok lesznek FTS5-tel, es ugyanugy cimkezhetok mint a Gemini chatek.

### Docker: port mar foglalt

Modositsd a `docker-compose.yml`-ben a portot, pl. `5000:5000` → `8080:5000`.

### Hogyan allitsak be automatikus exportot?

```bash
# Kornyezeti valtozoval:
EXPORT_SCHEDULE="0 */12 * * *" python app.py

# Vagy a dashboardon: 🕐 Export ütemezés modal
```

---

## Fontos megjegyzesek

- Reverse-engineered megoldas — a Google barmikor megvaltoztathatja az API-t
- Frissites: `pip install -U gemini-chat-exporter`
- Sertheti a Google ASZF-et — csak sajat felelossegre
- A cookie-k erzekeny adatok — a `.gitignore` vedi a `.env`-et

---

## Licensz

MIT
