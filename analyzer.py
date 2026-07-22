"""
analyzer.py — OCR extraction ONLY.  Zero API calls.  Zero screenshot buffer.

What this file does:
  • Runs Tesseract OCR on every screenshot (local, free, instant)
  • Cleans OCR garbage via UI_NOISE / CODE_NOISE / GARBAGE_PATTERNS
  • Returns (ocr_text, simple_highlight) to capture.py

What this file NO LONGER does:
  ERROR No Groq Vision API calls
  ERROR No screenshot buffer / ScreenshotBuffer class
  ERROR No _ScheduledAIWorker thread
  ERROR No _RateLimiter
  ERROR No base64 image encoding

API budget is 100% reserved for the hourly text-only summary
in bucket_processor.py, which reads clean_log.csv +
primary_bucket.csv + secondary_bucket.csv (~2k tokens total,
NOT ~150k tokens of image data).
"""

import os
import re

import pytesseract
pytesseract.pytesseract.tesseract_cmd = os.getenv(
    "TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)

# ═══════════════════════════════════════════════════════════════
# OCR NOISE FILTERS — single source of truth.
# cleaner.py and bucket_processor.py import these directly so
# the lists never drift out of sync.
# ═══════════════════════════════════════════════════════════════
UI_NOISE = {
    'file','edit','view','go','run','selection','help','tools','window',
    'settings','undo','redo','saved','timeline','welcome','requirements',
    'spaces','utf','python','prettier','lf','col','ln','capture',
    'deleted','screenshot','terminal','debug','explorer','search',
    'extensions','source','control','localhost','powershell','bash','cmd',
    'mainpy','capturepy','analyzerpy','storagepy','dashboardpy',
    'cleanerpy','configpy','bucketprocessorpy',
    'alltools','compress','acrobat','reader','adobe',
    'free','pro','premium','desktop','mobile','chats','customize',
    'sonnet','claude','monitering','monitoring',
}
CODE_NOISE = {
    'import','from','def','return','self','print','class','elif','else',
    'true','false','none','except','finally','raise','yield','lambda',
    'pass','break','continue','global','assert','async','await','with','try',
}
GARBAGE_PATTERNS = [
    r'^[^a-zA-Z0-9]{1,5}$', r'^[a-zA-Z0-9]{1,2}$', r'^[\s\W]+$',
    r'^\d+\s*\d*$', r'^[_=\-]{2,}$', r'.*\\screen.*\.jpg.*',
    r'.*Spaces.*UTF.*', r'.*\bFle\b.*\bSelection\b.*',
    r'.*\bYQ\b.*\bFle\b.*', r'.*Selection\s+View\s+Go.*',
    r'.*Fle\s+E[ai]t\s+Selection.*',
    r'[A-Za-z0-9+/]{20,}={0,2}', r'[^\w\s]{4,}',
    r'.*[ZBQoe]{4,}.*', r'.*[@#$%^&*()]{3,}.*', r'(.)\1{3,}',
    r'.*ndtet.*',   r'.*@oAo.*',    r'.*ZBQ.*',
    r'.*Cw\).*',    r'.*Q0EBC.*',   r'.*aoae.*',
    r'.*[^\x00-\x7F]{3,}.*',        r'.*MITP.*',
    r'.*@colne.*',  r'.*Ptaptap.*', r'.*ocogw.*',
    r'.*ndtetrts.*',r'.*Gamay.*',   r'.*crotcesfo.*',
    r'.*aturaresponge.*',           r'.*atura\s*[""]\s*Seraen.*',
    r'.*ef\s+sine\s+highlight.*',   r'.*seonetorn.*',
    r'.*eonomae.*', r'.*enatyze.*', r'.*oases\s*F.*',
    r'.*comae.*',   r'.*Comat.*',   r'.*eomomat.*',
    r'.*poms.*orton.*',             r'.*Bona.*Bosh.*',
    r'.*AureREwoRs.*',              r'.*Q\s+6\s+©.*',
    r'.*Grog\s+Manish.*',          r'.*acraur.*',
    r'.*rainpy.*',  r'.*maingy.*',  r'.*ananzespy.*',
    r'.*copurepy.*',r'.*ceanecpy.*',r'.*datbowdoy.*',
    r'.*storagepy.*', r'[^\x00-\x7F]{2,}',
]
_COMPILED = [re.compile(p, re.IGNORECASE) for p in GARBAGE_PATTERNS]


def _is_garbage(line: str) -> bool:
    line = line.strip()
    if not line or len(line) < 5:
        return True
    for rx in _COMPILED:
        if rx.search(line):
            return True
    printable = sum(1 for c in line if c.isascii() and c.isprintable())
    if printable / len(line) < 0.55:
        return True
    real_words = re.findall(r'[a-zA-Z]{3,}', line)
    if not real_words:
        return True
    if {w.lower() for w in real_words}.issubset(UI_NOISE | CODE_NOISE):
        return True
    return False


def clean_ocr(raw: str) -> str:
    if not raw or not raw.strip():
        return ""
    kept, seen = [], set()
    for line in raw.splitlines():
        line = re.sub(r'\s+', ' ', line).strip()
        if _is_garbage(line):
            continue
        key = re.sub(r'\W+', '', line.lower())
        if key in seen or len(key) < 4:
            continue
        seen.add(key)
        kept.append(line)
    return " | ".join(kept)


# ═══════════════════════════════════════════════════════════════
# PUBLIC ANALYZER  — OCR only, no AI, no buffer, no threads
# ═══════════════════════════════════════════════════════════════
class Analyzer:
    """
    Thin wrapper around Tesseract OCR.
    analyze(img) → (ocr_text, simple_highlight)
    Both values are local strings — no API call ever happens here.
    """

    class _NullBuffer:
        """
        Stub so dashboard.py's  len(analyzer.buffer)  never crashes.
        Always returns 0 — there is no screenshot queue anymore.
        """
        def __len__(self):       return 0
        def add(self, *a, **kw): pass

    def __init__(self):
        self.use_ai = False           # Vision AI is permanently OFF
        self.buffer = self._NullBuffer()
        self._tesseract_ok = self._check_tesseract()

    def _check_tesseract(self) -> bool:
        try:
            pytesseract.get_tesseract_version()
            print("[Analyzer] OK Tesseract OCR ready. API calls = 0 (text-summary only)")
            return True
        except Exception:
            print("[Analyzer] ERROR Tesseract not found - OCR disabled.")
            return False

    def extract_text(self, img) -> str:
        if not self._tesseract_ok:
            return ""
        try:
            small = img.resize((img.width // 2, img.height // 2))
            return clean_ocr(pytesseract.image_to_string(small))
        except Exception as e:
            print(f"[Analyzer] OCR error: {e}")
            return ""

    def analyze(self, img) -> tuple[str, str]:
        """
        Called by capture.py on every changed screenshot.
        Returns (ocr_text, highlight) — both local, zero API cost.
        """
        ocr_text = self.extract_text(img)
        return ocr_text, self.simple_highlight(ocr_text)

    def simple_highlight(self, text: str) -> str:
        return text[:150] if text else "No readable content found."

    def shutdown(self):
        """Called by capture.py on Ctrl+C - nothing to tear down."""
        pass

    def get_highlight(self, timestamp: float):
        """
        Called by capture.py async back-fill loop.
        Always returns None — no AI results to back-fill.
        """
        return None