"""
app_launcher.pyw  —  Laptop Monitor  |  Desktop GUI Launcher
──────────────────────────────────────────────────────────────
Double-click this file (or the .exe wrapper) to start.

What this does:
  1. First-run: installs all pip packages automatically
  2. Lets you enter / save your Groq API key (stored in .env)
  3. Shows a live log console (replaces the terminal)
  4. One-click  ▶ START  /  ■ STOP
  5. Opens the dashboard in your browser automatically
  6. Shows live status: Capture, OCR, Buckets, Summary
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, font as tkfont
import subprocess, sys, os, threading, time, webbrowser, re
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────
APP_DIR     = Path(__file__).resolve().parent
ENV_FILE    = APP_DIR / ".env"
REQ_FILE    = APP_DIR / "requirements.txt"
MAIN_PY     = APP_DIR / "main.py"
SUMMARY_TXT = APP_DIR / "work_summary.txt"

PYTHON = sys.executable   # same interpreter that launched this script

# ─── Colours / theme ─────────────────────────────────────────
BG       = "#0d0f18"
BG2      = "#13162b"
BG3      = "#1a1e35"
ACCENT   = "#7c83fd"
ACCENT2  = "#a78bfa"
GREEN    = "#34d399"
RED      = "#f87171"
YELLOW   = "#fbbf24"
MUTED    = "#4b5563"
TEXT     = "#e2e8f0"
TEXT2    = "#94a3b8"
FONT_UI  = ("Segoe UI", 10)
FONT_MONO= ("Cascadia Code", 9) if sys.platform == "win32" else ("Courier New", 9)

PACKAGES = [
    "Pillow>=10.0.0",
    "numpy>=1.24.0",
    "flask>=3.0.0",
    "pytesseract>=0.3.10",
    "pygetwindow>=0.0.9",
    "groq>=0.9.0",
    "python-dotenv>=1.0.0",
    "rake-nltk>=1.0.6",
    "nltk>=3.8.0",
    "opencv-python>=4.8.0",
]


# ══════════════════════════════════════════════════════════════
class LauncherApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Laptop Monitor")
        self.geometry("860x640")
        self.minsize(720, 520)
        self.configure(bg=BG)
        self.resizable(True, True)

        self._process      = None   # main.py subprocess
        self._log_thread   = None
        self._setup_done   = self._check_packages()
        self._running      = False

        self._build_ui()
        self._load_api_key()

        if not self._setup_done:
            self.after(200, self._run_setup)
        else:
            self._log("✅  All packages already installed.", "ok")
            self._log("📝  Enter your Groq API key and press  ▶ Start.", "info")

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ─── Check packages ──────────────────────────────────────
    def _check_packages(self) -> bool:
        import importlib
        must_have = ["PIL", "flask", "pytesseract", "groq", "dotenv",
                     "numpy", "cv2", "pygetwindow"]
        for mod in must_have:
            try:
                importlib.import_module(mod)
            except ImportError:
                return False
        return True

    # ─── UI build ────────────────────────────────────────────
    def _build_ui(self):
        # ── Header bar ──
        hdr = tk.Frame(self, bg=BG2, height=56)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        tk.Label(hdr, text="📊  LAPTOP MONITOR", bg=BG2,
                 fg=ACCENT, font=("Segoe UI", 14, "bold")).pack(side="left", padx=20, pady=14)

        self._status_lbl = tk.Label(hdr, text="● Idle", bg=BG2,
                                    fg=MUTED, font=FONT_UI)
        self._status_lbl.pack(side="right", padx=20)

        # ── Key row ──
        key_row = tk.Frame(self, bg=BG3, pady=10)
        key_row.pack(fill="x")

        tk.Label(key_row, text="Groq API Key:", bg=BG3,
                 fg=TEXT2, font=FONT_UI).pack(side="left", padx=(16, 6))

        self._key_var = tk.StringVar()
        key_entry = tk.Entry(key_row, textvariable=self._key_var,
                             show="•", width=48,
                             bg="#0d0f18", fg=TEXT,
                             insertbackground=ACCENT,
                             relief="flat", font=FONT_MONO,
                             highlightthickness=1,
                             highlightbackground=MUTED,
                             highlightcolor=ACCENT)
        key_entry.pack(side="left", ipady=5)

        self._show_key = False
        self._eye_btn = tk.Button(key_row, text="👁", bg=BG3, fg=TEXT2,
                                  relief="flat", cursor="hand2",
                                  command=lambda: self._toggle_key(key_entry))
        self._eye_btn.pack(side="left", padx=4)

        tk.Button(key_row, text="Save Key", bg=BG3, fg=ACCENT,
                  relief="flat", cursor="hand2",
                  font=FONT_UI, command=self._save_api_key).pack(side="left", padx=8)

        tk.Label(key_row, text="Get a free key →", bg=BG3,
                 fg=TEXT2, font=FONT_UI).pack(side="left", padx=(12, 2))
        lnk = tk.Label(key_row, text="console.groq.com", bg=BG3,
                       fg=ACCENT2, font=FONT_UI, cursor="hand2")
        lnk.pack(side="left")
        lnk.bind("<Button-1>", lambda e: webbrowser.open("https://console.groq.com"))

        # ── Status pills ──
        pills = tk.Frame(self, bg=BG, pady=8)
        pills.pack(fill="x", padx=16)

        self._pill_vars = {}
        for label in ["Capture", "OCR", "Buckets", "AI Summary", "Dashboard"]:
            col = tk.Frame(pills, bg=BG)
            col.pack(side="left", padx=6)
            dot = tk.Label(col, text="●", fg=MUTED, bg=BG, font=("Segoe UI", 9))
            dot.pack(side="left")
            tk.Label(col, text=label, fg=TEXT2, bg=BG, font=("Segoe UI", 9)).pack(side="left", padx=(2,0))
            self._pill_vars[label] = dot

        # ── Log console ──
        console_frame = tk.Frame(self, bg=BG, padx=12, pady=4)
        console_frame.pack(fill="both", expand=True)

        self._console = scrolledtext.ScrolledText(
            console_frame,
            bg="#080a12", fg="#c8d3f0",
            font=FONT_MONO,
            relief="flat",
            state="disabled",
            wrap="word",
            insertbackground=ACCENT,
        )
        self._console.pack(fill="both", expand=True)
        self._console.tag_config("ok",    foreground=GREEN)
        self._console.tag_config("err",   foreground=RED)
        self._console.tag_config("warn",  foreground=YELLOW)
        self._console.tag_config("info",  foreground=ACCENT)
        self._console.tag_config("dim",   foreground=TEXT2)

        # ── Bottom bar ──
        bar = tk.Frame(self, bg=BG2, pady=10)
        bar.pack(fill="x")

        self._start_btn = tk.Button(
            bar, text="▶  Start Monitoring",
            bg=ACCENT, fg="#fff",
            activebackground=ACCENT2,
            relief="flat", cursor="hand2",
            font=("Segoe UI", 11, "bold"),
            padx=22, pady=6,
            command=self._toggle_monitor,
        )
        self._start_btn.pack(side="left", padx=16)

        tk.Button(bar, text="🌐  Open Dashboard",
                  bg=BG3, fg=ACCENT,
                  relief="flat", cursor="hand2",
                  font=FONT_UI, padx=12, pady=6,
                  command=lambda: webbrowser.open("http://localhost:5000")
                  ).pack(side="left", padx=4)

        tk.Button(bar, text="📄  Work Summary",
                  bg=BG3, fg=TEXT2,
                  relief="flat", cursor="hand2",
                  font=FONT_UI, padx=12, pady=6,
                  command=self._open_summary,
                  ).pack(side="left", padx=4)

        self._setup_btn = tk.Button(
            bar, text="⚙  Re-install Packages",
            bg=BG3, fg=TEXT2,
            relief="flat", cursor="hand2",
            font=FONT_UI, padx=12, pady=6,
            command=lambda: threading.Thread(
                target=self._run_setup, daemon=True).start(),
        )
        self._setup_btn.pack(side="right", padx=16)

    # ─── API key helpers ─────────────────────────────────────
    def _load_api_key(self):
        if ENV_FILE.exists():
            for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
                if line.startswith("GROQ_API_KEY="):
                    self._key_var.set(line.split("=", 1)[1].strip())
                    return

    def _save_api_key(self):
        key = self._key_var.get().strip()
        if not key:
            messagebox.showwarning("Missing Key", "Please paste your Groq API key first.")
            return
        lines = []
        found = False
        if ENV_FILE.exists():
            for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
                if line.startswith("GROQ_API_KEY="):
                    lines.append(f"GROQ_API_KEY={key}")
                    found = True
                else:
                    lines.append(line)
        if not found:
            lines.insert(0, f"GROQ_API_KEY={key}")
        ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._log("✅  API key saved to .env", "ok")

    def _toggle_key(self, entry):
        self._show_key = not self._show_key
        entry.config(show="" if self._show_key else "•")

    # ─── Package installer ───────────────────────────────────
    def _run_setup(self):
        self._log("⚙  Installing packages — please wait…", "info")
        self._set_status("Installing…", YELLOW)

        for pkg in PACKAGES:
            self._log(f"   pip install {pkg}", "dim")
            result = subprocess.run(
                [PYTHON, "-m", "pip", "install", pkg, "--quiet"],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                self._log(f"   ⚠  {result.stderr.strip()[:120]}", "warn")

        # NLTK stopwords
        self._log("   Downloading NLTK stopwords…", "dim")
        subprocess.run(
            [PYTHON, "-c",
             "import nltk; nltk.download('stopwords', quiet=True); nltk.download('punkt', quiet=True)"],
            capture_output=True
        )

        self._setup_done = True
        self._log("✅  Setup complete.  Press  ▶ Start  to begin.", "ok")
        self._set_status("Ready", GREEN)

    # ─── Monitor toggle ──────────────────────────────────────
    def _toggle_monitor(self):
        if self._running:
            self._stop_monitor()
        else:
            self._start_monitor()

    def _start_monitor(self):
        if not self._setup_done:
            messagebox.showinfo("Setup Required",
                                "Packages are still being installed. Please wait.")
            return
        key = self._key_var.get().strip()
        if not key:
            messagebox.showwarning("API Key Missing",
                                   "Enter your Groq API key and click Save Key first.")
            return
        self._save_api_key()

        if not MAIN_PY.exists():
            messagebox.showerror("Missing File",
                                 f"Cannot find main.py in:\n{APP_DIR}")
            return

        env = os.environ.copy()
        env["GROQ_API_KEY"] = key

        self._log("\n▶  Starting Laptop Monitor…", "ok")
        try:
            self._process = subprocess.Popen(
                [PYTHON, str(MAIN_PY)],
                cwd=str(APP_DIR),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except Exception as e:
            self._log(f"❌  Failed to start: {e}", "err")
            return

        self._running = True
        self._start_btn.config(text="■  Stop Monitoring", bg="#ef4444")
        self._set_status("● Running", GREEN)

        self._log_thread = threading.Thread(
            target=self._stream_logs, daemon=True)
        self._log_thread.start()

        # Open dashboard after 3 s
        self.after(3000, lambda: webbrowser.open("http://localhost:5000"))

    def _stop_monitor(self):
        self._running = False
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=8)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

        self._start_btn.config(text="▶  Start Monitoring", bg=ACCENT)
        self._set_status("● Stopped", RED)
        self._log("\n■  Monitoring stopped.", "warn")
        self._reset_pills()

    def _stream_logs(self):
        """Read stdout from main.py and forward to the console widget."""
        pill_map = {
            "capture": "Capture",
            "ocr":     "OCR",
            "refiner": "Buckets",
            "summary": "AI Summary",
            "dashboard": "Dashboard",
        }
        try:
            for line in self._process.stdout:
                line = line.rstrip()
                if not line:
                    continue

                # Colour-code
                tag = "dim"
                l = line.lower()
                if "✅" in line or "ready" in l or "done" in l:
                    tag = "ok"
                elif "❌" in line or "error" in l or "failed" in l:
                    tag = "err"
                elif "⚠" in line or "warn" in l:
                    tag = "warn"
                elif any(k in l for k in ["starting", "monitoring", "▶", "📸", "🤖", "📝"]):
                    tag = "info"

                self._log(line, tag)

                # Update pills
                for key, pill_name in pill_map.items():
                    if key in l and "✅" in line:
                        self._set_pill(pill_name, GREEN)
                    elif key in l and "❌" in line:
                        self._set_pill(pill_name, RED)
                    elif key in l:
                        self._set_pill(pill_name, YELLOW)
        except Exception:
            pass

        if self._running:
            self._log("⚠  Monitor process ended unexpectedly.", "warn")
            self._running = False
            self.after(0, lambda: (
                self._start_btn.config(text="▶  Start Monitoring", bg=ACCENT),
                self._set_status("● Crashed", RED),
            ))

    # ─── Console helpers ─────────────────────────────────────
    def _log(self, msg: str, tag: str = ""):
        def _do():
            self._console.config(state="normal")
            self._console.insert("end", msg + "\n", tag)
            self._console.see("end")
            self._console.config(state="disabled")
        self.after(0, _do)

    def _set_status(self, text: str, colour: str):
        self.after(0, lambda: self._status_lbl.config(text=text, fg=colour))

    def _set_pill(self, name: str, colour: str):
        def _do():
            if name in self._pill_vars:
                self._pill_vars[name].config(fg=colour)
        self.after(0, _do)

    def _reset_pills(self):
        for dot in self._pill_vars.values():
            self.after(0, lambda d=dot: d.config(fg=MUTED))

    # ─── Work summary viewer ─────────────────────────────────
    def _open_summary(self):
        if not SUMMARY_TXT.exists():
            messagebox.showinfo("No Summary Yet",
                                "work_summary.txt hasn't been created yet.\n"
                                "It's written after the first hour of monitoring.")
            return
        win = tk.Toplevel(self)
        win.title("Work Summary")
        win.geometry("720x540")
        win.configure(bg=BG)
        txt = scrolledtext.ScrolledText(win, bg="#080a12", fg=TEXT,
                                        font=FONT_MONO, relief="flat")
        txt.pack(fill="both", expand=True, padx=10, pady=10)
        txt.insert("1.0", SUMMARY_TXT.read_text(encoding="utf-8", errors="replace"))
        txt.config(state="disabled")

    # ─── Close handler ───────────────────────────────────────
    def _on_close(self):
        if self._running:
            if messagebox.askyesno("Stop Monitoring?",
                                   "Monitoring is running. Stop it and exit?"):
                self._stop_monitor()
                self.after(1000, self.destroy)
        else:
            self.destroy()


# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app = LauncherApp()
    app.mainloop()
