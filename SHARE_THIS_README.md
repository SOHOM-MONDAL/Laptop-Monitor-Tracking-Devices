# Laptop Monitor — Desktop App

Share this folder with anyone. They only need Python installed — everything else installs automatically.

---

## First time (anyone getting this folder)

1. Make sure **Python 3.10+** is installed.
   Download from https://www.python.org/downloads/
   ⚠️  Tick **"Add Python to PATH"** during install.

2. Install Tesseract OCR (free, separate binary):
   https://github.com/UB-Mannheim/tesseract/wiki
   Use the default install path — the app already points there.

3. Double-click **`setup_and_run.bat`**
   - Installs all Python packages automatically
   - Opens the Laptop Monitor GUI

That's it. No terminal needed ever again.

---

## Every day after that

Double-click **`Laptop Monitor.bat`** — the GUI opens directly, no setup.

---

## Inside the GUI

| Element | What it does |
|---|---|
| **Groq API Key** box | Paste your key once, click Save — stored in `.env` |
| **▶ Start Monitoring** | Starts capture + OCR + buckets + dashboard |
| **■ Stop Monitoring** | Stops cleanly |
| **🌐 Open Dashboard** | Opens `http://localhost:5000` in your browser |
| **📄 Work Summary** | Shows today's AI-written report |
| Status pills | Green = running, Yellow = active, Red = problem |
| Log console | Real-time output (replaces the old terminal) |

Get a free Groq API key at: https://console.groq.com

---

## Files in this folder

```
app_launcher.pyw      ← The GUI app (this is what runs)
setup_and_run.bat     ← First-time setup + launch
Laptop Monitor.bat    ← Daily launcher (after setup)
main.py               ← Core monitor (launched by GUI)
config.py / capture.py / analyzer.py / ...
.env                  ← Your API key lives here (auto-created)
```

---

## Troubleshooting

**"Python not found"** — Reinstall Python, tick "Add to PATH".

**"Tesseract not found"** — Install from the link above, leave default path.

**Packages fail to install** — The GUI retries on startup. Or re-run `setup_and_run.bat`.

**Dashboard won't open** — Make sure monitoring is running (green status), then try http://localhost:5000 manually.
