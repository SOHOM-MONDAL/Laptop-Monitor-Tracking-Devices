"""
config.py — Central configuration loader.
Reads ALL secrets and settings from .env (never hardcoded).

Usage in any module:
    from config import cfg
    key = cfg.groq_api_key
"""

import os
from pathlib import Path

# ── Try to load .env file if python-dotenv is available ──────
try:
    from dotenv import load_dotenv
    # Walk up from this file's directory to find .env
    _here = Path(__file__).resolve().parent
    for _parent in [_here] + list(_here.parents):
        _env_file = _parent / ".env"
        if _env_file.exists():
            load_dotenv(_env_file)
            print(f"[Config] OK Loaded .env from: {_env_file}")
            break
except ImportError:
    print("[Config] WARNING  python-dotenv not installed. Run: pip install python-dotenv")
    print("[Config]     Falling back to system environment variables only.")


class _Config:
    """
    Central config object. All modules import `cfg` from here.
    Raises clear errors if required keys are missing.
    """

    # ── Required keys ─────────────────────────────────────────
    @property
    def groq_api_key(self) -> str:
        key = os.getenv("GROQ_API_KEY", "").strip()
        if not key:
            raise EnvironmentError(
                "\n[Config] ERROR GROQ_API_KEY is not set!\n"
                "  1. Copy .env.example -> .env\n"
                "  2. Paste your Groq key (https://console.groq.com)\n"
                "  3. Restart the program.\n"
            )
        return key

    # ── Optional / tunable settings ───────────────────────────
    @property
    def capture_interval(self) -> int:
        return int(os.getenv("CAPTURE_INTERVAL", "1"))

    @property
    def max_screenshots(self) -> int:
        return int(os.getenv("MAX_SCREENSHOTS", "10"))

    @property
    def change_threshold(self) -> float:
        return float(os.getenv("CHANGE_THRESHOLD", "5"))

    @property
    def db_path(self) -> str:
        return os.getenv("DB_PATH", "monitor.db")

    @property
    def csv_path(self) -> str:
        return os.getenv("CSV_PATH", "activity_log.csv")

    @property
    def screenshots_dir(self) -> str:
        return os.getenv("SCREENSHOTS_DIR", "screenshots")


# Singleton — import this everywhere
cfg = _Config()
