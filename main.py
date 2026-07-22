"""
main.py — Laptop Monitoring System  v4
Run: python main.py

What runs:
  • Screenshot capture + OCR   (every second, change-detected, LOCAL only)
  • Bucket pipeline            (every 1 min — clean_log, primary, secondary)
  • Groq TEXT summary          (every 60 min — ONE API call, ~2k tokens)
  • Flask dashboard            (localhost:5000, live)

What does NOT run:
  ERROR No Vision AI on screenshots
  ERROR No image-to-API calls
  ERROR No screenshot buffer or AI worker thread
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')



import threading, signal

from config           import cfg
from capture          import ScreenCapture
from analyzer         import Analyzer
from storage          import Database
from dashboard        import Dashboard
from bucket_processor import BackgroundRefiner


def main():
    print("=" * 54)
    print("   LAPTOP MONITORING SYSTEM  v4")
    print("=" * 54)
    print("[*] Initializing...\n")

    db       = Database(cfg.db_path)
    analyzer = Analyzer()          # OCR only — no API key needed here
    capture  = ScreenCapture(
        save_dir         = cfg.screenshots_dir,
        db               = db,
        analyzer         = analyzer,
        interval         = cfg.capture_interval,
        change_threshold = cfg.change_threshold,
        max_screenshots  = cfg.max_screenshots,
        csv_path         = cfg.csv_path,
    )

    # Background refiner — bucket pipeline every 1 min (no API)
    #                     — Groq text summary every 60 min (1 call)
    refiner = BackgroundRefiner()
    refiner.start()

    print("[*] Starting capture thread (OCR only, zero API)...")
    threading.Thread(target=capture.start, daemon=True).start()

    print(f"[*] Dashboard    -> http://localhost:5000")
    print(f"[*] Activity log -> {cfg.csv_path}")
    print(f"[*] Buckets      -> every 1 min  (no API)")
    print(f"[*] AI Summary   -> every 60 min (text only, ~2k tokens/call)")
    print("[!] Press Ctrl+C to stop.\n")

    def shutdown(sig, frame):
        print("\n[*] Stopping...")
        capture.stop()
        refiner.stop()
        print("[*] Running final bucket pipeline + summary before exit...")
        from bucket_processor import run_full_pipeline
        from pathlib import Path
        run_full_pipeline(Path(cfg.csv_path))
        print("[*] Done. Check work_summary.txt for your session report.")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)

    Dashboard(db, analyzer=analyzer).run()


if __name__ == "__main__":
    main()