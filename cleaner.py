"""
cleaner.py — Clean activity_log.csv → clean_log.csv

v3 improvements over v2:
  ─────────────────────────────────────────────────────────────
  WHAT CHANGED
  ─────────────────────────────────────────────────────────────
  • Segment-level filtering (NEW)
      OCR output is pipe-separated segments. v2 filtered at the
      full-row level, letting garbled browser-chrome fragments
      ("O Bi & re © Sana", "ZzQ0a@o -oomae") survive as long
      as some real words existed somewhere in the row.

      v3 splits on '|' first, scores every segment independently,
      and discards any that fail the quality bar. Only the
      surviving segments are joined back together.

  • Five independent segment signals (NEW)
      1. real_word_count   – need ≥ 2 words of 4+ letters
      2. clean_char_ratio  – alnum/space/punctuation ≥ 50 %
      3. word_coverage     – real-word chars / total chars ≥ 28 %
      4. noise_token_ratio – tokens containing Unicode symbols
                             or zero alpha chars ≤ 40 %
                             (only applied when real_words < 3)
      5. lone_caps count   – ≤ 2 lone uppercase letters
                             (browser icon labels like "O Bi &")
      6. short_token_ratio – ≤ 2-char tokens < 50 % of all tokens

  • Cross-row deduplication on clean_content (NEW)
      The same screen is often captured multiple times per minute.
      After cleaning, exact-duplicate clean_content values are
      dropped — only the first occurrence is kept.

  • Canonical noise sets still imported from analyzer.py
      UI_NOISE, CODE_NOISE, GARBAGE_PATTERNS — unchanged.

  ─────────────────────────────────────────────────────────────
  WHAT DID NOT CHANGE
  ─────────────────────────────────────────────────────────────
  • Output schema: timestamp, app, ai_summary, clean_content
  • App-name normalisation (_APP_REPLACEMENTS)
  • Placeholder detection (_PLACEHOLDERS)
  • Row-level skip when nothing salvageable remains
  • process() entry point and __main__ block
  • Import path for analyzer.py constants

Run:
    python cleaner.py
"""

import csv
import re
import os
from pathlib import Path

# Single source of truth for noise lists — imported from analyzer.py
from analyzer import UI_NOISE, CODE_NOISE, GARBAGE_PATTERNS, _COMPILED

BASE_DIR = Path(__file__).resolve().parent
INPUT    = BASE_DIR / "activity_log.csv"
OUTPUT   = BASE_DIR / "clean_log.csv"

# ═══════════════════════════════════════════════════════════════
# PRE-COMPILED REGEXES  (module-level — compiled once)
# ═══════════════════════════════════════════════════════════════

# Symbols that are reliable indicators of UI chrome / icon garbage
_SYMBOL_RE   = re.compile(r'[©®™°*->←↑↓@#\^*<>{}~`|\\]')
# Lone uppercase letters - e.g. "O Bi & re" from browser icon bars
_LONE_CAP_RE = re.compile(r'(?<![a-zA-Z])[A-Z](?![a-zA-Z])')
# Real words: sequences of 4+ letters
_WORD_RE     = re.compile(r'[a-zA-Z]{4,}')
# Characters considered "clean" for ratio check
_CLEAN_CHAR_RE = re.compile(r"[a-zA-Z0-9 .\-,()/'\":]")


# ═══════════════════════════════════════════════════════════════
# SEGMENT-LEVEL GARBAGE FILTER  (core v3 addition)
# ═══════════════════════════════════════════════════════════════

def _is_garbage_segment(seg: str) -> bool:
    """
    Return True when a pipe-delimited OCR segment is junk.

    A segment is kept only when ALL six checks pass:
      1. ≥ 2 real words (4+ alpha chars)
      2. clean char ratio ≥ 50 %
      3. real-word coverage ≥ 28 %
      4. noise token ratio ≤ 40 %  (only enforced when real_words < 3)
      5. lone uppercase letters < 3
      6. short-token ratio < 50 %
    """
    seg = seg.strip()
    if not seg or len(seg) < 8:
        return True

    # ── 1. Real word count ──────────────────────────────────
    real_words = _WORD_RE.findall(seg)
    if len(real_words) < 2:
        return True

    # ── 2. Clean char ratio ─────────────────────────────────
    clean_chars = sum(1 for c in seg if _CLEAN_CHAR_RE.match(c))
    if clean_chars / len(seg) < 0.50:
        return True

    # ── 3. Real-word coverage ───────────────────────────────
    word_chars = sum(len(w) for w in real_words)
    if word_chars / len(seg) < 0.28:
        return True

    # ── 4. Noise token ratio (strict only when few real words)
    tokens = [t for t in re.split(r'\s+', seg) if t]
    if tokens:
        noise = sum(
            1 for t in tokens
            if _SYMBOL_RE.search(t) or not re.search(r'[a-zA-Z]', t)
        )
        if len(real_words) < 3 and noise / len(tokens) > 0.40:
            return True

    # ── 5. Lone uppercase letters (icon/button label noise) ─
    lone_caps = len(_LONE_CAP_RE.findall(seg))
    if lone_caps >= 3:
        return True

    # ── 6. Short-token ratio ────────────────────────────────
    if tokens:
        short_toks = sum(1 for t in tokens if len(t) <= 2)
        if short_toks / len(tokens) > 0.50:
            return True

    return False


# ═══════════════════════════════════════════════════════════════
# ROW-LEVEL GARBAGE FILTER  (unchanged from v2 — last resort)
# ═══════════════════════════════════════════════════════════════

def _is_garbage_token(segment: str) -> bool:
    """
    Legacy row-level check (from v2).  Still used as a final guard
    on individual segments after the segment filter runs.
    """
    segment = segment.strip()
    if not segment or len(segment) < 5:
        return True
    for rx in _COMPILED:
        if rx.search(segment):
            return True
    printable = sum(1 for c in segment if c.isascii() and c.isprintable())
    if printable / len(segment) < 0.55:
        return True
    real_words = re.findall(r'[a-zA-Z]{3,}', segment)
    if not real_words:
        return True
    if {w.lower() for w in real_words}.issubset(UI_NOISE | CODE_NOISE):
        return True
    return False


# ═══════════════════════════════════════════════════════════════
# TEXT CLEANER  (v3 — segment-aware)
# ═══════════════════════════════════════════════════════════════

def clean_text(raw: str) -> str:
    """
    Clean a raw OCR string.

    Pipeline:
      1. Split on pipe or newline → individual segments
      2. Normalise whitespace & common quote artefacts
      3. Discard via _is_garbage_segment()  (v3 segment filter)
      4. Discard via _is_garbage_token()    (v2 legacy filter)
      5. Deduplicate within the row
      6. Rejoin with ' | '
    """
    if not raw or not raw.strip():
        return ""

    parts = re.split(r'\||\n', raw)
    kept  = []
    seen  = set()

    for part in parts:
        part = re.sub(r'\s+', ' ', part).strip()
        part = part.replace('""', '"').replace("''", "'")

        # v3 segment-level filter — the main new gate
        if _is_garbage_segment(part):
            continue

        # v2 legacy filter — additional pattern-based checks
        if _is_garbage_token(part):
            continue

        # Within-row dedup
        key = re.sub(r'\W+', '', part.lower())
        if key in seen or len(key) < 4:
            continue

        seen.add(key)
        kept.append(part)

    return " | ".join(kept)


# ═══════════════════════════════════════════════════════════════
# APP-NAME NORMALISER  (unchanged from v2)
# ═══════════════════════════════════════════════════════════════

_APP_REPLACEMENTS = {
    "Adobe Acrobat Reader (64-bit)" : "Acrobat Reader",
    "Visual Studio Code"            : "VS Code",
    "Google Chrome"                 : "Chrome",
    "Microsoft Edge"                : "Edge",
    "Windows PowerShell"            : "PowerShell",
}

def get_app_name(window: str) -> str:
    if not window or not window.strip():
        return "Unknown"
    parts = [p.strip() for p in re.split(r'\s[--]\s', window)]
    app   = parts[-1] if parts else window
    for long, short in _APP_REPLACEMENTS.items():
        app = app.replace(long, short)
    return app.strip() or "Unknown"


# ═══════════════════════════════════════════════════════════════
# PLACEHOLDER DETECTION  (unchanged from v2)
# ═══════════════════════════════════════════════════════════════

_PLACEHOLDERS = {
    "monitoring...", "screen captured.", "no readable content found.",
    "no readable content.", "", "n/a",
}

def _is_placeholder(text: str) -> bool:
    return text.strip().lower() in _PLACEHOLDERS


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def process(input_path: Path, output_path: Path):
    if not input_path.exists():
        print(f"ERROR Not found: {input_path}")
        return

    read = written = skipped_empty = skipped_dup = 0
    seen_content: set[str] = set()   # cross-row dedup (v3 addition)

    with open(input_path,  "r", encoding="utf-8") as fin, \
         open(output_path, "w", newline="", encoding="utf-8") as fout:

        reader = csv.DictReader(fin)
        writer = csv.DictWriter(fout, fieldnames=[
            "timestamp", "app", "ai_summary", "clean_content"
        ])
        writer.writeheader()

        for row in reader:
            read += 1

            timestamp = row.get("timestamp", "")[:19].replace("T", " ")
            window    = row.get("active_window", "")
            highlight = row.get("highlight", "").strip()
            raw_ocr   = row.get("ocr_text", "")

            app           = get_app_name(window)
            clean_content = clean_text(raw_ocr)
            ai_summary    = "" if _is_placeholder(highlight) else highlight

            # Drop rows with nothing useful
            if not clean_content and not ai_summary:
                skipped_empty += 1
                continue

            # Cross-row dedup: skip exact duplicate clean_content (v3)
            content_key = re.sub(r'\W+', '', clean_content.lower())
            if content_key and content_key in seen_content:
                skipped_dup += 1
                continue
            if content_key:
                seen_content.add(content_key)

            writer.writerow({
                "timestamp"    : timestamp,
                "app"          : app,
                "ai_summary"   : ai_summary,
                "clean_content": clean_content,
            })
            written += 1

    print(f"\nOK Done!")
    print(f"   Rows read         : {read}")
    print(f"   Rows saved        : {written}")
    print(f"   Skipped (empty)   : {skipped_empty}")
    print(f"   Skipped (duplicate): {skipped_dup}")
    print(f"   Total skipped     : {skipped_empty + skipped_dup}  "
          f"({(skipped_empty + skipped_dup)/read*100:.1f}% of input)")
    print(f"   Output            : {output_path.resolve()}")


if __name__ == "__main__":
    print("=" * 50)
    print("   ACTIVITY LOG CLEANER  v3")
    print("=" * 50)
    process(INPUT, OUTPUT)
