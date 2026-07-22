"""
capture.py — Screenshot loop + change detection.

Every `interval` seconds:
  1. Takes a screenshot
  2. Compares pixel diff against the previous frame
  3. If change > threshold → runs OCR (local Tesseract, zero API cost)
  4. Saves row to SQLite + appends to activity_log.csv
  5. Keeps only the latest `max_screenshots` images on disk

No AI calls happen here.  All API budget goes to the hourly
text-only summary in bucket_processor.py.
"""

import csv
import os
import time
from datetime  import datetime
from pathlib   import Path

import numpy as np
from PIL import ImageGrab, Image

try:
    import pygetwindow as gw
    _GW_AVAILABLE = True
except ImportError:
    _GW_AVAILABLE = False


class ScreenCapture:

    def __init__(
        self,
        save_dir         : str   = "screenshots",
        db               = None,
        analyzer         = None,
        interval         : int   = 1,
        change_threshold : float = 5.0,
        max_screenshots  : int   = 10,
        csv_path         : str   = "activity_log.csv",
    ):
        self.save_dir         = Path(save_dir)
        self.db               = db
        self.analyzer         = analyzer
        self.interval         = interval
        self.change_threshold = change_threshold
        self.max_screenshots  = max_screenshots
        self.csv_path         = Path(csv_path)

        self._running    = False
        self._prev_array = None

        self._ensure_dirs()
        self._ensure_csv_header()

        print(f"[Capture]  Screenshots folder : {self.save_dir.resolve()}")
        print(f"[Capture]  Activity log (CSV) : {self.csv_path.resolve()}")
        print(f"[Capture]  Max screenshots kept on disk: {self.max_screenshots}")

    # ── Setup ─────────────────────────────────────────────────

    def _ensure_dirs(self):
        self.save_dir.mkdir(parents=True, exist_ok=True)

    def _ensure_csv_header(self):
        if not self.csv_path.exists():
            with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(
                    f,
                    fieldnames=["timestamp", "active_window", "highlight", "ocr_text"]
                ).writeheader()

    # ── Active window ─────────────────────────────────────────

    def _get_active_window(self) -> str:
        if not _GW_AVAILABLE:
            return "Unknown"
        try:
            wins = gw.getActiveWindow()
            return wins.title if wins else "Unknown"
        except Exception:
            return "Unknown"

    # ── Change detection ──────────────────────────────────────

    def _has_changed(self, img: Image.Image) -> bool:
        arr = np.array(img.convert("L").resize((320, 180)))
        if self._prev_array is None:
            self._prev_array = arr
            return True
        diff = np.mean(np.abs(arr.astype(int) - self._prev_array.astype(int)))
        self._prev_array = arr
        return diff > self.change_threshold

    # ── Disk management ───────────────────────────────────────

    def _save_screenshot(self, img: Image.Image, ts: str) -> str:
        safe_ts  = ts.replace(":", "-").replace(" ", "_")
        filename = self.save_dir / f"screen_{safe_ts}.jpg"
        img.save(filename, "JPEG", quality=70)

        shots = sorted(self.save_dir.glob("screen_*.jpg"))
        while len(shots) > self.max_screenshots:
            shots.pop(0).unlink(missing_ok=True)

        return str(filename)

    # ── CSV writer ────────────────────────────────────────────

    def _append_csv(self, timestamp: str, window: str,
                    highlight: str, ocr_text: str):
        row = {
            "timestamp"    : timestamp,
            "active_window": window,
            "highlight"    : highlight,
            "ocr_text"     : ocr_text,
        }
        for attempt in range(5):
            try:
                with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
                    csv.DictWriter(
                        f,
                        fieldnames=["timestamp","active_window","highlight","ocr_text"]
                    ).writerow(row)
                return
            except PermissionError:
                if attempt < 4:
                    time.sleep(0.2 * (attempt + 1))
                else:
                    print(f"[Capture]  WARNING  CSV locked - row dropped for {timestamp}")

    # ── Main loop ─────────────────────────────────────────────

    def start(self):
        self._running = True
        print("[Capture]  Monitoring started. OCR only - zero API calls here.")

        while self._running:
            loop_start = time.time()

            try:
                img = ImageGrab.grab()
            except Exception as e:
                print(f"[Capture]  ERROR Screenshot failed: {e}")
                time.sleep(self.interval)
                continue

            if not self._has_changed(img):
                time.sleep(max(0, self.interval - (time.time() - loop_start)))
                continue

            ts         = datetime.now().isoformat(timespec="seconds")
            window     = self._get_active_window()
            image_path = self._save_screenshot(img, ts)

            # OCR only — local Tesseract, no API call
            ocr_text, highlight = ("", "No readable content found.")
            if self.analyzer:
                ocr_text, highlight = self.analyzer.analyze(img)

            # Write to DB
            if self.db:
                self.db.insert(ts, window, highlight, ocr_text, image_path)

            # Write to CSV
            self._append_csv(ts, window, highlight, ocr_text)

            print(f"[Capture]  >> {ts} | {window[:40]} | {highlight[:50]}")

            elapsed = time.time() - loop_start
            time.sleep(max(0, self.interval - elapsed))

    def stop(self):
        self._running = False
        if self.analyzer:
            self.analyzer.shutdown()
        print("[Capture]  Monitoring stopped.")