# Laptop Monitor — Desktop App v4

Captures screenshots every second, extracts text via OCR, generates AI work summaries using Groq, stores everything in SQLite, and shows a live dashboard in your browser — all from a single GUI app. No terminal ever needed.

---

## Quick Start (New User)

1. Double-click **`setup_and_run.bat`**
   - Automatically installs Python, Tesseract OCR, and all packages
   - Opens the GUI when done (~2 minutes first time)

2. Paste your free Groq API key → click **Save Key**
   Get one at: https://console.groq.com (no credit card)

3. Click **▶ Start Monitoring**

That's it.

**Every day after:** double-click **`Laptop Monitor.bat`** to open the app.

---

## Project Structure

```
laptop_monitor/
├── app_launcher.pyw      ← GUI app — double-click to run
├── setup_and_run.bat     ← First-time auto setup + launch
├── Laptop Monitor.bat    ← Daily launcher (after setup)
├── SHARE_THIS_README.md  ← Simple instructions for sharing
│
├── main.py               ← Core monitor (launched by GUI)
├── config.py             ← All settings from .env
├── capture.py            ← Screenshot loop + change detection
├── analyzer.py           ← Tesseract OCR (local, no API)
├── storage.py            ← SQLite database
├── dashboard.py          ← Flask web dashboard (localhost:5000)
├── cleaner.py            ← Cleans activity_log.csv → clean_log.csv
├── bucket_processor.py   ← RAKE analysis + Groq work summary
├── svdd_model.pkl        ← Anomaly detection model
├── requirements.txt      ← Python package list
│
├── .env                  ← Your API key (auto-created by GUI)
├── activity_log.csv      ← Auto-created: raw real-time log
├── clean_log.csv         ← Created automatically every hour
├── primary_bucket.csv    ← Created automatically every hour
├── secondary_bucket.csv  ← Created automatically every hour
├── work_summary.txt      ← AI report, updated every hour
├── monitor.db            ← SQLite database (auto-created)
└── screenshots/          ← Latest 10 screenshots (auto-managed)
```

---

## The GUI

| Element | What it does |
|---|---|
| **Groq API Key** box | Paste once, click Save — stored in `.env` |
| **▶ Start Monitoring** | Starts everything: capture, OCR, buckets, dashboard |
| **■ Stop Monitoring** | Stops cleanly, runs final summary |
| **🌐 Open Dashboard** | Opens `http://localhost:5000` in your browser |
| **📄 Work Summary** | Opens `work_summary.txt` in a built-in viewer |
| **Status pills** | Capture / OCR / Buckets / AI Summary — green=running |
| **Log console** | Live output (replaces the old terminal window) |
| **Re-install Packages** | Fixes broken installs without reinstalling Python |

---

## What Runs Automatically

```
Every 1 second  → Screenshot + change detection + OCR (local, free)
Every 1 minute  → Bucket pipeline: clean_log, primary, secondary CSVs
Every 60 minutes → Groq AI text summary → work_summary.txt (1 API call)
Always          → Flask dashboard at localhost:5000
```

No Vision AI on screenshots. All image processing is local (Tesseract).
The only API call is one text-only summary per hour (~2k tokens).

---

## Output Files

### activity_log.csv — raw real-time log
| Column | Contents |
|---|---|
| `timestamp` | ISO timestamp |
| `active_window` | Full window title |
| `highlight` | OCR snippet |
| `ocr_text` | Full cleaned OCR text |

### clean_log.csv — noise removed (auto-updated hourly)
| Column | Contents |
|---|---|
| `timestamp` | Formatted timestamp |
| `app` | Normalised app name (e.g. "VS Code", "Chrome") |
| `ai_summary` | Summary if available |
| `clean_content` | OCR text with garbage stripped |

### primary_bucket.csv — what you were doing
| Column | Contents |
|---|---|
| `start_time` / `end_time` | Session window |
| `duration` | e.g. "14m 32s" |
| `app` | Most-used app in session |
| `task_type` | e.g. "Coding", "Learning & Development" |
| `core_phrases` | Short RAKE phrases — domain anchor |

### secondary_bucket.csv — what specifically
| Column | Contents |
|---|---|
| `specific_phrases` | Longer RAKE phrases — exact topic |
| `ai_context` | Best Groq summary for the session |

### work_summary.txt — AI-written report (updated hourly)
- Time breakdown by task type
- One-line per session
- Key topics and technologies
- Productivity insights

---

## Configuration

All settings live in `.env` (auto-created when you save your key in the GUI).
Edit in any text editor — never edit `.py` files.

| Setting | Default | What it does |
|---|---|---|
| `GROQ_API_KEY` | — | Your Groq key (required) |
| `CAPTURE_INTERVAL` | `1` | Seconds between screenshots |
| `CHANGE_THRESHOLD` | `5` | Pixel diff to trigger capture (lower = more) |
| `MAX_SCREENSHOTS` | `10` | Max images kept on disk |
| `DB_PATH` | `monitor.db` | SQLite file |
| `CSV_PATH` | `activity_log.csv` | Raw log file |
| `SCREENSHOTS_DIR` | `screenshots` | Screenshot folder |

---

## Troubleshooting

**App won't open after double-clicking `Laptop Monitor.bat`**
Run `setup_and_run.bat` again — it fixes itself.

**"Tesseract not found" in the log**
The setup script installs Tesseract automatically. If it failed, check your internet and run `setup_and_run.bat` again.

**Log console shows errors in red**
Check your Groq API key is correct and saved. Re-enter it in the key box and click Save Key.

**Dashboard won't load at localhost:5000**
Make sure monitoring is running (▶ Start pressed, status shows green). Then try opening http://localhost:5000 manually in your browser.

**work_summary.txt is empty**
The first summary is written after 60 minutes of monitoring. Keep the app running.

**clean_log.csv is mostly empty**
OCR is filtering too aggressively. Try lowering `CHANGE_THRESHOLD` to `3` in `.env`.

---

## Sharing with Someone

1. Delete `activity_log.csv`, `clean_log.csv`, `primary_bucket.csv`, `secondary_bucket.csv`, `work_summary.txt`, `monitor.db`, `screenshots/`, `.env` from your folder (these are your personal data)
2. Zip the remaining folder
3. Send the zip
4. Tell them: **"Unzip it and double-click `setup_and_run.bat` — it installs everything automatically"**

They need their own free Groq API key from https://console.groq.com
