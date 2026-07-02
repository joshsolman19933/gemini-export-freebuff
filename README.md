# Gemini Chat Exporter

Teljes Gemini beszelgetes-exportalo eszkoz a `gemini_webapi` csomaggal.
Az **osszes** beszelgetest exportalja a `gemini.google.com` feluletrol,
paginacioval, parhuzamos letoltessel, tobbszoros formatum tamogatassal.

---

## Funkciok

| Funkcio | Leiras |
|---|---|
| **Osszes chat exportalasa** | Page-token alapu paginacio, atlepi a ~100-as API limitet |
| **Parhuzamos letoltes** | `asyncio.gather` + semaphore, konfiguralhato konkurenciaval (alap: 3) |
| **Tobb formatum** | JSON, Markdown, HTML (egyedi + osszesitett), CSV |
| **Resume tamogatas** | Mar exportalt chatek automatikus kihagyasa |
| **Datum szures** | `--from` / `--to` datum szerinti szures |
| **Kulcsszo szures** | `--filter` a chat cimeben kereses |
| **Lista mod** | `--list-chats` csak listazas, export nelkul |
| **Webes GUI** | Flask alapu modern webes felulet elo progress loggal |
| **Auto-cookie** | Cookie-k automatikus importalasa bongeszobol (`browser-cookie3`) |
| **Konfiguralhato kesleltetes** | Rate limiting az API leterhelesenek elkerulesere |

---

## Telepites

```bash
pip install -r requirements.txt
```

---

## Gyors kezdes

### 1. Cookie-k beszerzese

**A) Automatikusan:**
```bash
pip install browser-cookie3
python export.py --auto-cookies
```

**B) Manualisan (ajanlott):**
1. Nyisd meg: https://gemini.google.com es jelentkezz be
2. Nyomd meg az **F12**-t, majd **Application** ful, **Cookies**
3. Keresd ki a `__Secure-1PSID` es `__Secure-1PSIDTS` ertekeket
4. `cp .env.example .env` es szerkeszd a fajlt

### 2. Exportalas

```bash
python export.py --format all    # Minden formatum
python export.py                 # JSON + Markdown (alapertelmezett)
```

---

## CLI opciok

| Opcio | Alap | Leiras |
|---|---|---|
| `--format` | `both` | `json`, `markdown`, `html`, `csv`, `both`, `all` |
| `--output` | `./exports` | Kimeneti konyvtar |
| `--delay` | `0.5` | Kesleltetes mp-ben |
| `--concurrency`, `-c` | `3` | Parhuzamos letoltesek szama |
| `--max-chats` | `2000` | Maximum lekerheto beszelgetesek |
| `--from` | - | Datum szures kezdete (EEEE-HH-NN) |
| `--to` | - | Datum szures vege (EEEE-HH-NN) |
| `--filter` | - | Kulcsszo szures (case-insensitive) |
| `--list-chats` | - | Csak listazas, export nelkul |
| `--no-resume` | - | Teljes ujrakezdes |
| `--auto-cookies` | - | Cookie-k automatikus importalasa |

### Datum formatumok

- `2024-01-01` -- csak datum (`--to` eseten +23:59:59)
- `2024-01-01T12:00:00` -- datum es ido
- `2024-01-01 12:00:00` -- datum es ido (szokozzel)

A `--to` a nap vegeig szamol, kiveve ha pontos idopontot adtal meg.

### Peldak

```bash
python export.py --format html
python export.py --concurrency 5 --delay 0.2
python export.py --from 2024-01-01 --to 2024-12-31
python export.py --filter Python --format csv
python export.py --list-chats --filter API
python export.py --max-chats 500 --no-resume
```

---

## Webes GUI

```bash
pip install flask
python app.py
# Nyisd meg: http://localhost:5000
```

- Form alapu konfiguracio
- Elo progress log + progress bar
- Export utani statisztikak

---

## Kimeneti fajlstruktura

```
exports/
  all_chats.json            # Osszes chat egy JSON-ban
  all_chats.html            # Osszes chat egy HTML-ben (navigacioval + keresovel)
  json/                     # Egyedi JSON fajlok
    Beszelgetes_cime_abc12345.json
  markdown/                 # Egyedi Markdown fajlok
    INDEX.md
    Beszelgetes_cime_abc12345.md
  html/                     # Egyedi HTML fajlok
    Beszelgetes_cime_abc12345.html
  csv/
    chats.csv               # Osszes uzenet tablazatosan
```

### Formatumok

| Format | Fajlok | Leiras |
|---|---|---|
| JSON | `json/*.json` + `all_chats.json` | Teljes struktura |
| Markdown | `markdown/*.md` + `INDEX.md` | Ember altal olvashato |
| HTML | `html/*.html` + `all_chats.html` | Sotet tema, CSS animaciok |
| CSV | `csv/chats.csv` | Tablazatos (chat_id, title, role, text) |

### all_chats.html

- Oldalsavos navigacio + keresomezo
- Statisztika panel
- Reszponziv, egyetlen onallo fajl

---

## Kornyezeti valtozok

| Valtozo | Leiras | Alapertelmezett |
|---|---|---|
| `GEMINI_SECURE_1PSID` | Cookie ertek | - |
| `GEMINI_SECURE_1PSIDTS` | Cookie ertek | - |
| `GEMINI_AUTO_COOKIES` | Auto-cookie (`1` = be) | `0` |
| `EXPORT_OUTPUT_DIR` | Kimeneti konyvtar | `./exports` |
| `EXPORT_DELAY` | Kesleltetes mp-ben | `0.5` |

---

## Rendszerkovetelmenyek

- Python 3.10+
- Windows, macOS, Linux
- Aktiv Google fiok Gemini elozmenyekkel

---

## Gyakori problemak

### Sikertelen inicializalas

Cookie-k lejartak? Jelentkezz be ujra a gemini.google.com-on, frissitsd a `.env`-et, vagy hasznald az `--auto-cookies` kapcsolot.

### Csak 100 beszelgetes

Ellenorizd, hogy a kimenetben `paginacio: OK` szerepel-e. Ha `fallback mod`, a paginacio hibazott.

### Rate limit

```bash
python export.py --delay 2.0 --concurrency 1
```

---

## Architektura

```
export.py          # CLI eszkoz, fo export logika
app.py             # Flask webes GUI
templates/index.html
```

### Mukodesi elv

1. Hitelesites cookie-kkal a Gemini API-hoz
2. `LIST_CHATS` RPC paginacioval (page token a `part_body[1]`-ben)
3. Szures datum/kulcsszo szerint
4. Parhuzamos exportalas `asyncio.gather`-rel
5. Fajlba iras a valasztott formatumokban

---

## Fontos megjegyzesek

- Reverse-engineered megoldas -- a Google barmikor megvaltoztathatja az API-t
- Frissites: `pip install -U gemini_webapi`
- Sertheti a Google ASZF-et -- csak sajat felelossegre
- A cookie-k erzekeny adatok -- a `.gitignore` vedi a `.env`-et

---

## Licensz

MIT
