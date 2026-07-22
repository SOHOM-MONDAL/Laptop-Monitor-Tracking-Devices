"""
bucket_processor.py — Clean → Classify → Bucket → AI Story Summary

ARCHITECTURE:
  ┌──────────────────────────────────────────────────────────────┐
  │  Every 1 min  — ZERO API CALLS                               │
  │    Read activity_log.csv                                     │
  │    Clean OCR garbage (RAKE + TF-IDF + TextBlob + Bigrams)    │
  │    Classify rows → CODING / LEARNING / MEETING               │
  │    Group into sessions                                       │
  │    Build chronological activity timeline                     │
  │    Write clean_log.csv, primary_bucket.csv,                  │
  │         secondary_bucket.csv, activity_timeline.csv          │
  │                                                              │
  │  Every 60 min — ONE Groq API call (text only, ~2k tokens)    │
  │    Read clean_log.csv + primary_bucket.csv +                 │
  │         secondary_bucket.csv  (plain text — no images)       │
  │    Generate human-readable story summary                     │
  │    APPEND one block to work_summary.txt                      │
  └──────────────────────────────────────────────────────────────┘

PHRASE EXTRACTION — 4-algo pipeline (all optional, degrades gracefully):
  1. RAKE     (rake_nltk)  — rapid automatic keyword extraction
  2. TF-IDF   (sklearn)    — statistical importance vs session corpus
  3. TextBlob (textblob)   — noun-phrase chunking
  4. Bigrams  (built-in)   — raw co-occurrence counts, zero deps

3 CATEGORIES:
  [CODING] CODING    — LeetCode, VS Code, GitHub, terminals, etc.
  [LEARNING] LEARNING  — ChatGPT, Claude, YouTube, Google, PDFs, etc.
  [MEETING] MEETING   — Google Meet, Zoom, Teams, Discord calls
  Priority: CODING > MEETING > LEARNING (Chrome default = LEARNING)
"""

import csv, re, os, time, threading
from pathlib     import Path
from datetime    import datetime
from collections import Counter

from config import cfg

# ═══════════════════════════════════════════════════════════════
# PATHS
# ═══════════════════════════════════════════════════════════════
BASE_DIR      = Path(__file__).resolve().parent
INPUT_CSV     = BASE_DIR / "activity_log.csv"
CLEAN_CSV     = BASE_DIR / "clean_log.csv"
PRIMARY_OUT   = BASE_DIR / "primary_bucket.csv"
SECONDARY_OUT = BASE_DIR / "secondary_bucket.csv"
TIMELINE_OUT  = BASE_DIR / "activity_timeline.csv"
SUMMARY_OUT   = BASE_DIR / "work_summary.txt"

BUCKET_INTERVAL_MIN  = 1    # clean + classify — every minute, no API
SUMMARY_INTERVAL_MIN = 60   # one Groq text call per hour

SVDD_MODEL_PATH = BASE_DIR / "svdd_model.pkl"   # trained by svdd_trainer.py

# ═══════════════════════════════════════════════════════════════
# DEEP SVDD MODEL — loaded once at startup
#
# Replaces the old hardcoded PRIMARY_APPS keyword list.
# If svdd_model.pkl is not found, falls back to keyword matching
# so the system keeps running even before the model is trained.
# ═══════════════════════════════════════════════════════════════
_svdd_payload   = None
_svdd_loaded    = False

def _load_svdd():
    global _svdd_payload, _svdd_loaded
    if _svdd_loaded:
        return _svdd_payload
    _svdd_loaded = True
    if not SVDD_MODEL_PATH.exists():
        print(f"[SVDD] WARNING  svdd_model.pkl not found at {SVDD_MODEL_PATH}")
        print("[SVDD]     Running WITHOUT Deep SVDD - using keyword fallback.")
        print("[SVDD]     Train the model: python svdd_trainer.py")
        return None
    try:
        import joblib, torch
        payload = joblib.load(SVDD_MODEL_PATH)
        payload['model'].eval()
        _svdd_payload = payload
        ver = payload.get('version', '?')
        print(f"[SVDD] OK Deep SVDD model loaded (version {ver})")
        print(f"[SVDD]    Threshold : {payload['threshold']:.4f}")
        print(f"[SVDD]    Trained at: {payload.get('trained_at','unknown')}")
        return payload
    except Exception as e:
        print(f"[SVDD] ERROR Failed to load svdd_model.pkl: {e}")
        print("[SVDD]    Falling back to keyword matching.")
        return None


def svdd_bucket(app_name: str, category: str = "", content: str = "") -> str:
    """
    Classify a session as 'primary' (inside hypersphere = productive work/study)
    or 'secondary' (outlier = noise/distraction/system app).

    Input is enriched: "app_name category content_snippet" so the TF-IDF
    vectorizer has real signal instead of a near-empty 1-word sparse row.
    Falls back to keyword matching if model not loaded.
    """
    payload = _load_svdd()
    # Build enriched input string regardless — fallback also benefits
    enriched = f"{app_name} {category} {content[:200]}".strip()
    if payload is None:
        return _keyword_bucket(app_name)
    try:
        import torch, numpy as np
        net        = payload['model']
        scaler     = payload['scaler']
        vectorizer = payload['vectorizer']
        c          = payload['center']
        threshold  = payload['threshold']
        X_new = vectorizer.transform([enriched]).toarray().astype('float32')
        X_sc  = scaler.transform(X_new).astype('float32')
        with torch.no_grad():
            z = net(torch.from_numpy(X_sc))
        dist = torch.norm(z - c, dim=1).item()
        return 'primary' if dist <= threshold else 'secondary'
    except Exception as e:
        print(f"[SVDD] WARNING  Inference error ({e}) - using keyword fallback.")
        return _keyword_bucket(app_name)


# ── Keyword fallback (used when model not available) ──────────
_PRIMARY_APPS_FALLBACK = {
    "chrome", "edge", "firefox", "safari", "brave",
    "vs code", "vscode", "pycharm", "intellij", "sublime",
    "visual studio", "cursor", "windsurf",
    "powershell", "terminal", "cmd", "bash", "wsl",
    "claude", "chatgpt", "gemini", "copilot", "perplexity",
    "leetcode", "hackerrank", "codeforces", "kaggle", "coursera",
    "udemy", "youtube", "stackoverflow", "github",
    "google meet", "zoom", "slack", "teams", "discord", "whatsapp",
    "notion", "obsidian", "excel", "word", "powerpoint",
    "acrobat reader", "unknown", "explorer", "finder",
}

def _keyword_bucket(app_name: str) -> str:
    name = app_name.lower().strip()
    for p in _PRIMARY_APPS_FALLBACK:
        if p in name or name in p:
            return 'primary'
    return 'secondary'


# ═══════════════════════════════════════════════════════════════
# GROQ CLIENT SINGLETON
# ═══════════════════════════════════════════════════════════════
_groq_client = None
_groq_tried  = False

def _get_groq_client():
    global _groq_client, _groq_tried
    if _groq_tried:
        return _groq_client
    _groq_tried = True
    try:
        from groq import Groq
        _groq_client = Groq(api_key=cfg.groq_api_key)
        print("[Summary] OK Groq client ready (text-only, ~2k tokens/hour).")
    except Exception as e:
        print(f"[Summary] WARNING  Groq unavailable: {e}")
    return _groq_client

# ═══════════════════════════════════════════════════════════════
# 3-CATEGORY CLASSIFICATION
# ═══════════════════════════════════════════════════════════════
_CODING_WINDOW = {
    'leetcode','codechef','codeforces','hackerrank','atcoder',
    'codepen','replit','codesandbox','stackblitz','kaggle',
    'visual studio code','vs code','vscode','pycharm','intellij',
    'android studio','xcode','sublime','vim','neovim','emacs',
    'jupyter','powershell','windows terminal','cmd','bash','git bash','wsl',
    'github','gitlab','bitbucket','sourcetree','github desktop',
    'stackoverflow','developer.mozilla','docs.python',
}
_CODING_TEXT = {
    'leetcode','codechef','codeforces','hackerrank','atcoder',
    'algorithm','data structure','binary tree','dynamic programming',
    'graph bfs','graph dfs','dijkstra','shortest path','binary search',
    'time complexity','space complexity','big o',
    'compile error','runtime error','segmentation fault',
    'class solution','def solution','int main','public class',
    '#include','using namespace std','git commit','git push',
}
_MEETING_WINDOW = {
    'google meet','zoom','microsoft teams','teams','webex',
    'discord','skype','gotomeeting','whereby','jitsi',
    'meet.google.com','zoom.us','teams.microsoft.com',
}
_MEETING_TEXT = {
    'meeting','video call','conference call','webinar','on a call',
    'participants','presenter','screen share','muted','unmute',
    'join meeting','waiting room','breakout room','attendees',
}

CATEGORY_LABELS = {
    "CODING"  : "[CODING] Coding",
    "LEARNING": "[LEARNING] Learning & Development",
    "MEETING" : "[MEETING] Meeting / Call",
}

def classify_row(window: str, text: str) -> str:
    w = window.lower()
    t = (window + " " + text).lower()
    for sig in _CODING_WINDOW:
        if sig in w: return "CODING"
    if sum(1 for sig in _CODING_TEXT if sig in t) >= 2:
        return "CODING"
    for sig in _MEETING_WINDOW:
        if sig in w: return "MEETING"
    if sum(1 for sig in _MEETING_TEXT if sig in t) >= 2:
        return "MEETING"
    return "LEARNING"

# ═══════════════════════════════════════════════════════════════
# CONTENT EXTRACTION — dual-source pipeline (v3)
#
# Source 1 — Window title  (ALWAYS clean: no OCR noise ever)
#   Extract the page/document name from the window title string.
#   e.g. "(1) G-41. Bellman Ford Algorithm - YouTube - Google Chrome"
#        -> "G-41. Bellman Ford Algorithm"
#
# Source 2 — OCR segments  (STRICT gate: 3 hard rules)
#   Split raw OCR on '|', score every segment independently.
#   A segment is kept only when ALL five rules pass:
#     A. ≥ 3 real words (4+ alpha chars)     — blocks icon labels
#     B. real-word coverage ≥ 40 %            — blocks symbol soup
#     C. clean char ratio ≥ 60 %              — blocks garbled runs
#     D. lone uppercase letters < 2           - blocks "O Bi & re ©"
#     E. short-token ratio < 40 %             - blocks "X + © Ber O"
#
# Result: window_content | passing_ocr_segments (deduped)
# If OCR adds nothing, window_content alone is used.
# Rows where even the window title is uninformative are dropped.
# ═══════════════════════════════════════════════════════════════
from analyzer import UI_NOISE, CODE_NOISE, _COMPILED as _GARBAGE_RX

_NOISE = UI_NOISE | CODE_NOISE
_PLACEHOLDERS = {
    'monitoring...','screen captured.','no readable content found.',
    'no readable content.','','n/a',
}

# Pre-compiled — module level, built once
_LONE_CAP_RE = re.compile(r'(?<![a-zA-Z])[A-Z](?![a-zA-Z])')
_REAL_WORD_RE = re.compile(r'[a-zA-Z]{4,}')
_CLEAN_CHAR_RE = re.compile(r"[a-zA-Z0-9 .\-,()/'\":]")

# Window suffixes to strip (longest first so inner matches don't partial-strip)
_WIN_SUFFIXES = [
    ' - Google Chrome', ' - Microsoft Edge', ' - Mozilla Firefox',
    ' - Visual Studio Code', ' - Windows PowerShell',
    ' - YouTube', ' - Codeforces', ' - LeetCode',
]
# Window titles that carry no useful content
_SKIP_TITLES = {
    'new tab', 'program manager', 'quick settings', 'settings',
    'windows default lock screen', 'search', 'pair device',
    'file explorer', 'task manager', 'unknown',
}

def _extract_window_content(window: str) -> str:
    """
    Strip OS chrome from the window title and return the meaningful part.
    '(1) G-41. Bellman Ford Algorithm - YouTube - Google Chrome'
    -> 'G-41. Bellman Ford Algorithm'
    Returns '' for uninformative titles (New Tab, Quick settings, etc.)
    """
    if not window or not window.strip():
        return ''
    w = window.strip()
    # Remove notification badge e.g. "(165) "
    w = re.sub(r'^\(\d+\)\s*', '', w)
    # Strip known app suffixes
    for suffix in _WIN_SUFFIXES:
        w = w.replace(suffix, '')
    w = w.strip().strip('-').strip()
    # Skip uninformative titles
    if w.lower() in _SKIP_TITLES or len(w) < 4:
        return ''
    return w


def _ocr_segment_passes(seg: str) -> bool:
    """
    Return True only when a pipe-delimited OCR segment clears all 5 hard rules.
    This is intentionally strict — false negatives (dropping real content) are
    acceptable; false positives (letting garbage through) are not.
    """
    seg = seg.strip()
    if not seg or len(seg) < 10:
        return False
    # Rule A: ≥ 3 real words
    rw = _REAL_WORD_RE.findall(seg)
    if len(rw) < 3:
        return False
    # Rule B: real-word coverage ≥ 40 %
    if sum(len(w) for w in rw) / len(seg) < 0.40:
        return False
    # Rule C: clean char ratio ≥ 60 %
    if sum(1 for c in seg if _CLEAN_CHAR_RE.match(c)) / len(seg) < 0.60:
        return False
    # Rule D: lone uppercase letters < 2  (browser icon-bar noise)
    if len(_LONE_CAP_RE.findall(seg)) >= 2:
        return False
    # Rule E: short-token ratio < 40 %
    tokens = seg.split()
    if tokens and sum(1 for t in tokens if len(t) <= 2) / len(tokens) > 0.40:
        return False
    return True


def build_clean_content(window: str, raw_ocr: str) -> str:
    """
    Merge window-title content with OCR segments that pass the strict gate.
    Returns a pipe-joined string of unique, garbage-free segments.
    Returns '' when nothing meaningful survives (caller should drop the row).
    """
    parts: list[str] = []
    seen:  set[str]  = set()

    def _add(text: str):
        text = re.sub(r'\s+', ' ', text).strip()
        key  = re.sub(r'\W+', '', text.lower())
        if key and len(key) >= 4 and key not in seen:
            seen.add(key)
            parts.append(text)

    # Source 1: window title — always first, always clean
    win_content = _extract_window_content(window)
    if win_content:
        _add(win_content)

    # Source 2: OCR segments — strict gate
    if raw_ocr and str(raw_ocr).strip() and str(raw_ocr) != 'nan':
        for seg in str(raw_ocr).split('|'):
            if _ocr_segment_passes(seg):
                _add(seg)

    return ' | '.join(parts)


def get_app(window: str) -> str:
    if not window or not window.strip():
        return 'Unknown'
    parts = [p.strip() for p in re.split(r'\s[--]\s', window)]
    app = parts[-1] if parts else window
    for long, short in [
        ('Adobe Acrobat Reader (64-bit)', 'Acrobat Reader'),
        ('Visual Studio Code',            'VS Code'),
        ('Google Chrome',                 'Chrome'),
        ('Microsoft Edge',                'Edge'),
        ('Windows PowerShell',            'PowerShell'),
    ]:
        app = app.replace(long, short)
    return app.strip()[:50] or 'Unknown'

# ═══════════════════════════════════════════════════════════════
# PHRASE EXTRACTION — 4-algo pipeline
# ═══════════════════════════════════════════════════════════════
_rake        = None; _rake_tried   = False
_nlp         = None; _nlp_tried    = False
_tfidf_ready = False; _tfidf_tried = False
_blob_ready  = False; _blob_tried  = False

_EXTRA_STOP = _NOISE | {
    'capture','monitoring','screenshot','activity','highlight','ocr',
}

def _valid_phrase(p: str) -> bool:
    if not p or len(p) < 4: return False
    words = re.findall(r'[a-zA-Z]{3,}', p)
    if not words: return False
    if {w.lower() for w in words}.issubset(_EXTRA_STOP): return False
    if sum(c.isdigit() for c in p) / len(p) > 0.4: return False
    return True

# Algo 1: RAKE
def _get_rake():
    global _rake, _rake_tried
    if _rake_tried: return _rake
    _rake_tried = True
    try:
        from rake_nltk import Rake
        _rake = Rake(); print("[Processor] OK RAKE loaded.")
    except ImportError:
        print("[Processor] WARNING  rake_nltk missing - pip install rake-nltk")
    return _rake

def _get_nlp():
    global _nlp, _nlp_tried
    if _nlp_tried: return _nlp
    _nlp_tried = True
    try:
        import spacy
        _nlp = spacy.load("en_core_web_sm"); print("[Processor] OK spaCy loaded.")
    except Exception as e:
        print(f"[Processor] WARNING  spaCy unavailable ({e.__class__.__name__})")
    return _nlp

# Algo 2: TF-IDF
def _get_tfidf():
    global _tfidf_ready, _tfidf_tried
    if _tfidf_tried: return _tfidf_ready
    _tfidf_tried = True
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer  # noqa
        _tfidf_ready = True; print("[Processor] OK TF-IDF (sklearn) loaded.")
    except ImportError:
        print("[Processor] WARNING  sklearn missing - pip install scikit-learn")
    return _tfidf_ready

def _tfidf_phrases(texts: list, top_n: int = 12) -> list:
    if not _get_tfidf() or len(texts) < 2: return []
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        vec = TfidfVectorizer(ngram_range=(1,2), max_features=300,
                              stop_words='english', min_df=1, sublinear_tf=True)
        mat    = vec.fit_transform(texts)
        terms  = vec.get_feature_names_out()
        scores = mat[-1].toarray().flatten()
        top    = scores.argsort()[-top_n:][::-1]
        return [terms[i] for i in top if scores[i] > 0]
    except Exception as e:
        print(f"[Processor] WARNING  TF-IDF error: {e}"); return []

# Algo 3: TextBlob noun phrases
def _get_blob():
    global _blob_ready, _blob_tried
    if _blob_tried: return _blob_ready
    _blob_tried = True
    try:
        from textblob import TextBlob  # noqa
        _blob_ready = True; print("[Processor] OK TextBlob loaded.")
    except ImportError:
        print("[Processor] WARNING  textblob missing - pip install textblob")
    return _blob_ready

def _blob_phrases(text: str, top_n: int = 12) -> list:
    if not _get_blob() or not text: return []
    try:
        from textblob import TextBlob
        nps = list(dict.fromkeys(str(np).strip() for np in TextBlob(text).noun_phrases))
        return [p for p in nps if _valid_phrase(p)][:top_n]
    except Exception as e:
        print(f"[Processor] WARNING  TextBlob error: {e}"); return []

# Algo 4: Bigrams (built-in, always runs)
def _bigram_phrases(text: str, top_n: int = 12) -> list:
    if not text: return []
    words = [w for w in re.findall(r'[a-zA-Z]{3,}', text.lower())
             if w not in _EXTRA_STOP and not re.search(r'(.)\1{2,}', w)]
    if len(words) < 2: return []
    bg: dict = {}
    for a, b in zip(words, words[1:]):
        key = f"{a} {b}"; bg[key] = bg.get(key, 0) + 1
    ranked = sorted(bg, key=bg.__getitem__, reverse=True)
    return [p for p in ranked if _valid_phrase(p)][:top_n]

def extract_phrases(text: str, top_n: int = 12,
                    corpus_texts: list | None = None) -> list:
    """Merge results from all 4 algos, dedup, filter, return top_n."""
    if not text or not text.strip(): return []
    seen: set = set(); merged: list = []

    def _add(phrases):
        for p in phrases:
            key = re.sub(r'\W+', '', p.lower())
            if key and key not in seen:
                seen.add(key); merged.append(p)

    # Algo 1: RAKE
    rake = _get_rake()
    if rake:
        try:
            rake.extract_keywords_from_text(text)
            rp = [p for p in rake.get_ranked_phrases() if _valid_phrase(p)]
            nlp = _get_nlp()
            if nlp:
                rp = [p for p in rp
                      if any(t.pos_ in ('NOUN','VERB','ADJ','PROPN')
                             and not t.is_stop for t in nlp(p))]
            _add(rp)
        except Exception as e:
            print(f"[Processor] WARNING  RAKE error: {e}")

    # Algo 2: TF-IDF
    if corpus_texts:
        _add([p for p in _tfidf_phrases(corpus_texts + [text], top_n) if _valid_phrase(p)])

    # Algo 3: TextBlob
    _add(_blob_phrases(text, top_n))

    # Algo 4: Bigrams (always)
    _add(_bigram_phrases(text, top_n))

    # Fallback: top unigrams
    if not merged:
        words = re.findall(r'[a-zA-Z]{3,}', text.lower())
        filtered = [w for w in words if w not in _EXTRA_STOP
                    and not re.search(r'(.)\1{2,}', w)]
        merged = [w for w, _ in Counter(filtered).most_common(top_n)]

    return merged[:top_n]

# ═══════════════════════════════════════════════════════════════
# SESSION GROUPING
# ═══════════════════════════════════════════════════════════════
def group_sessions(rows: list, gap_sec: int = 60) -> list:
    if not rows: return []
    sessions, current = [], [rows[0]]
    for row in rows[1:]:
        try:
            t0  = datetime.fromisoformat(current[-1]['timestamp'])
            t1  = datetime.fromisoformat(row['timestamp'])
            gap = (t1 - t0).total_seconds()
        except Exception:
            gap = 0
        if gap <= gap_sec:
            current.append(row)
        else:
            sessions.append(current); current = [row]
    sessions.append(current)
    return sessions

def _dur_str(seconds: int) -> str:
    if seconds < 60: return f"{seconds}s"
    h, rem = divmod(seconds, 3600)
    m, s   = divmod(rem, 60)
    if h: return f"{h}h {m}m"
    return f"{m}m {s}s"

def _parse_dur_secs(dur: str) -> int:
    try:
        parts = dur.replace('h',' ').replace('m',' ').replace('s','').split()
        parts = [int(x) for x in parts]
        if len(parts) == 3: return parts[0]*3600 + parts[1]*60 + parts[2]
        if len(parts) == 2:
            return (parts[0]*3600+parts[1]*60) if 'h' in dur else (parts[0]*60+parts[1])
        return parts[0]
    except Exception:
        return 0

# ═══════════════════════════════════════════════════════════════
# ACTIVITY TIMELINE  (every 1 min, ZERO API)
#
# Replaces the coarse hourly-block view with a chronological
# sequence of app segments.  A new segment is opened whenever:
#   • the normalised app name changes, OR
#   • the category (CODING / LEARNING / MEETING) changes
#
# Each segment records:
#   start_time, end_time, duration_sec, duration,
#   app, category, context_switches, main_task
#
# context_switches counts how many distinct app titles appeared
# within the segment - a proxy for "was this focused work?"
#
# The timeline is written to activity_timeline.csv (overwritten
# every cycle — it is always the full picture, not incremental).
# ═══════════════════════════════════════════════════════════════

_MIN_SEGMENT_SEC = 5   # ignore sub-5-second blips (screen flash / unlock)

def build_activity_timeline(clean_rows: list) -> list:
    """
    Convert a list of clean_log rows (dicts with 'timestamp', 'app',
    'category', 'clean_content', 'ai_summary') into an ordered list of
    activity segments.

    A segment boundary is drawn whenever the (app, category) pair changes
    from the previous row — NOT based on a fixed time gap.  This means a
    15-minute Google Meet interruption inside a coding stretch is captured
    as its own segment, not buried inside an aggregated hour block.

    Returns a list of segment dicts, one per contiguous activity block.
    svdd_bucket is derived per-segment so it appears in the timeline CSV.
    """
    if not clean_rows:
        return []

    segments   : list[dict] = []
    # Seed with first row
    seg_app    : str  = clean_rows[0].get('app', 'Unknown')
    seg_cat    : str  = clean_rows[0].get('category', 'LEARNING')
    seg_start  : str  = clean_rows[0]['timestamp']
    seg_end    : str  = clean_rows[0]['timestamp']
    seg_tasks  : list = []
    seg_apps   : set  = set()   # all distinct window titles seen — for context_switches
    seg_content: list = []      # accumulate content for SVDD enriched scoring

    highlight = clean_rows[0].get('ai_summary', '') or clean_rows[0].get('clean_content', '')
    if highlight and highlight.lower() not in _PLACEHOLDERS:
        seg_tasks.append(highlight)
        seg_content.append(highlight)
    seg_apps.add(clean_rows[0].get('app', ''))

    def _close_segment(end_ts: str):
        try:
            t0  = datetime.fromisoformat(seg_start)
            t1  = datetime.fromisoformat(end_ts)
            dur = max(0, int((t1 - t0).total_seconds()))
        except Exception:
            dur = 0

        # Drop sub-threshold blips
        if dur < _MIN_SEGMENT_SEC:
            return None

        # Best task label: first non-empty ai_summary, else top OCR phrase
        task = next((t for t in seg_tasks if t), CATEGORY_LABELS.get(seg_cat, seg_cat))

        # SVDD scored with enriched input for this segment
        seg_svdd = svdd_bucket(
            seg_app,
            category=seg_cat,
            content=" ".join(seg_content)[:300],
        )

        return {
            'start_time'      : seg_start[:19],
            'end_time'        : end_ts[:19],
            'duration_sec'    : dur,
            'duration'        : _dur_str(dur),
            'app'             : seg_app,
            'category'        : CATEGORY_LABELS.get(seg_cat, seg_cat),
            'svdd_bucket'     : seg_svdd,
            'context_switches': max(0, len(seg_apps) - 1),
            'main_task'       : task[:120],
        }

    for row in clean_rows[1:]:
        row_app = row.get('app', 'Unknown')
        row_cat = row.get('category', 'LEARNING')
        row_ts  = row['timestamp']

        # Boundary condition: app OR category changed
        if row_app != seg_app or row_cat != seg_cat:
            seg = _close_segment(seg_end)
            if seg:
                segments.append(seg)
            # Open new segment
            seg_app     = row_app
            seg_cat     = row_cat
            seg_start   = row_ts
            seg_tasks   = []
            seg_apps    = set()
            seg_content = []

        seg_end = row_ts
        seg_apps.add(row_app)
        h = row.get('ai_summary', '') or row.get('clean_content', '')
        if h and h.lower() not in _PLACEHOLDERS:
            seg_tasks.append(h)
            seg_content.append(h)

    # Close the final open segment
    seg = _close_segment(seg_end)
    if seg:
        segments.append(seg)

    return segments


def write_timeline(segments: list, silent: bool = False) -> None:
    """Write activity_timeline.csv - always a full overwrite."""
    if not segments:
        return
    fieldnames = [
        'start_time', 'end_time', 'duration_sec', 'duration',
        'app', 'category', 'svdd_bucket', 'context_switches', 'main_task',
    ]
    try:
        with open(TIMELINE_OUT, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(segments)
        if not silent:
            print(f"[Output] OK activity_timeline.csv - {len(segments)} segments")
    except PermissionError as e:
        if not silent:
            print(f"[Processor] WARNING  Timeline write failed (locked): {e}")


def format_timeline_bar(segments: list, max_chars: int = 58) -> str:
    """
    Render a compact ASCII timeline bar for work_summary.txt.

    Example output:
      10:00 ──────────────── 10:40  [CODING] Coding (40m)  LeetCode
      10:40 ──────── 10:55   [MEETING] Meeting (15m)         Google Meet
      10:55 ─── 11:00        [CODING] Coding (5m)           VS Code

    Bar widths are proportional to duration.
    """
    if not segments:
        return ""

    total_sec = sum(s['duration_sec'] for s in segments)
    if total_sec == 0:
        return ""

    # Category → emoji
    _EMOJI = {
        "[CODING] Coding"                : "[CODING]",
        "[LEARNING] Learning & Development": "[LEARNING]",
        "[MEETING] Meeting / Call"        : "[MEETING]",
    }

    BAR_WIDTH = 20   # total dashes available across all segments
    lines = []
    for seg in segments:
        frac     = seg['duration_sec'] / total_sec
        dashes   = max(1, round(frac * BAR_WIDTH))
        is_secondary = seg.get('svdd_bucket', 'primary') == 'secondary'
        bar      = '·' * dashes if is_secondary else '─' * dashes
        emoji    = _EMOJI.get(seg['category'], '  ')
        t_start  = seg['start_time'][11:16]   # HH:MM
        t_end    = seg['end_time'][11:16]
        dur      = seg['duration']
        app      = seg['app'][:22]
        task     = seg['main_task'][:40] if seg['main_task'] else ''
        cs_note  = f" [{seg['context_switches']} switches]" if int(seg.get('context_switches', 0)) > 2 else ""
        sec_tag  = " [bg]" if is_secondary else ""
        lines.append(
            f"  {t_start} {bar} {t_end}  {emoji} {seg['category']} ({dur})"
            f"  {app}{cs_note}{sec_tag}"
        )
        if task and not is_secondary and task.lower() not in {app.lower(), seg['category'].lower()}:
            lines.append(f"         └─ {task}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# STEP 1 — BUCKET PIPELINE  (every 1 min, ZERO API)
# ═══════════════════════════════════════════════════════════════
def run_bucket_pipeline(input_path: Path = INPUT_CSV,
                        silent: bool = False) -> tuple:
    if not input_path.exists():
        if not silent: print(f"[Processor] ERROR Not found: {input_path}")
        return [], []

    # 1. Read
    raw_rows = []
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                ts = row.get('timestamp','').strip()
                if not ts: continue
                raw_rows.append({
                    'timestamp': ts,
                    'window'   : row.get('active_window', row.get('app','')),
                    'highlight': row.get('highlight', row.get('ai_summary','')).strip(),
                    'ocr_text' : row.get('ocr_text', row.get('clean_content','')),
                })
    except PermissionError:
        if not silent: print("[Processor] WARNING  CSV locked - skipping cycle.")
        return [], []

    if not silent: print(f"[Processor] >> {len(raw_rows)} rows read.")

    # 2. Load already-written clean_log timestamps to enable incremental append
    #    (never rewrite the whole file — only append rows not yet processed)
    already_written: set[str] = set()
    clean_log_exists = CLEAN_CSV.exists()
    if clean_log_exists:
        try:
            with open(CLEAN_CSV, 'r', encoding='utf-8') as f:
                for existing in csv.DictReader(f):
                    ts = existing.get('timestamp', '').strip()
                    if ts:
                        already_written.add(ts)
        except Exception:
            pass

    # 3. Clean + classify — dual-source pipeline (window title + strict OCR gate)
    #    Cross-session dedup: skip content seen in this run already
    clean_rows   = []   # rows to append to clean_log this cycle
    all_clean    = []   # full set (existing + new) for bucket building
    seen_content: set[str] = set()

    # Load existing clean_log content for session building & dedup context
    if clean_log_exists:
        try:
            with open(CLEAN_CSV, 'r', encoding='utf-8') as f:
                for existing in csv.DictReader(f):
                    cc = existing.get('clean_content', '')
                    ck = re.sub(r'\W+', '', cc.lower())
                    if ck:
                        seen_content.add(ck)
                    all_clean.append(existing)
        except Exception:
            pass

    new_count = skipped_dup = skipped_empty = 0
    for r in raw_rows:
        ts_clean = r['timestamp'][:19].replace('T', ' ')

        # Already in clean_log from a previous cycle — skip writing, still use for buckets
        if ts_clean in already_written:
            continue

        # Dual-source content extraction
        clean_content = build_clean_content(r['window'], r['ocr_text'])
        ai_summary    = ('' if r['highlight'].strip().lower() in _PLACEHOLDERS
                         else r['highlight'].strip())

        # Drop if nothing useful survived
        if not clean_content and not ai_summary:
            skipped_empty += 1
            continue

        # Cross-session dedup on clean_content
        ck = re.sub(r'\W+', '', clean_content.lower())
        if ck and ck in seen_content:
            skipped_dup += 1
            continue
        if ck:
            seen_content.add(ck)

        app      = get_app(r['window'])
        category = classify_row(r['window'], clean_content + ' ' + ai_summary)
        row_out  = {
            'timestamp'     : ts_clean,
            'app'           : app,
            'ai_summary'    : ai_summary,
            'clean_content' : clean_content,
            'category'      : category,
            'category_label': CATEGORY_LABELS[category],
        }
        clean_rows.append(row_out)
        all_clean.append(row_out)
        new_count += 1

    if not silent:
        print(f"[Processor] >> {new_count} new clean rows "
              f"(+{len(already_written)} existing | "
              f"skipped: {skipped_empty} empty, {skipped_dup} duplicate)")

    # 4. Append new rows to clean_log.csv (never overwrite)
    if clean_rows:
        write_header = not clean_log_exists or os.path.getsize(CLEAN_CSV) == 0
        try:
            with open(CLEAN_CSV, 'a', newline='', encoding='utf-8') as f:
                w = csv.DictWriter(f, fieldnames=[
                    'timestamp','app','category','category_label',
                    'ai_summary','clean_content'])
                if write_header:
                    w.writeheader()
                w.writerows(clean_rows)
            if not silent:
                print(f"[Output] OK clean_log.csv <- appended {len(clean_rows)} rows "
                      f"(total {len(all_clean)})")
        except PermissionError as e:
            if not silent: print(f"[Processor] WARNING  clean_log write failed (locked): {e}")

    # Use full set (existing + new) for session building
    clean_rows = all_clean
    if not silent: print(f"[Processor] >> {len(clean_rows)} total clean rows for buckets.")

    # 3. Group sessions
    sessions = group_sessions([
        {'timestamp': r['timestamp'], 'text': r['clean_content'],
         'highlight': r['ai_summary'], 'app': r['app'],
         'category' : r['category']}
        for r in clean_rows
    ])
    if not silent: print(f"[Processor] >> {len(sessions)} sessions grouped.")

    # 4. Build bucket rows
    all_session_texts = [
        " ".join(r.get('text','') + " " + r.get('highlight','') for r in s)
        for s in sessions
    ]

    primary_rows, secondary_rows = [], []
    for idx, session in enumerate(sessions):
        start_ts = session[0]['timestamp'][:19]
        end_ts   = session[-1]['timestamp'][:19]
        try:
            t0  = datetime.fromisoformat(session[0]['timestamp'])
            t1  = datetime.fromisoformat(session[-1]['timestamp'])
            dur = max(0, int((t1 - t0).total_seconds()))
        except Exception:
            dur = 0

        apps     = [r.get('app','') for r in session if r.get('app')]
        app_name = Counter(apps).most_common(1)[0][0] if apps else "Unknown"
        cats     = [r.get('category','LEARNING') for r in session]
        category = Counter(cats).most_common(1)[0][0]
        combined = " ".join(
            r.get('text','') + " " + r.get('highlight','') for r in session)
        ai_ctx   = next(
            (r.get('highlight','') for r in session
             if r.get('highlight','').lower() not in _PLACEHOLDERS), "")

        # ── Deep SVDD bucket assignment ────────────────────────
        # Score using enriched input: app + category + content snippet.
        # 'primary'   = inside hypersphere (work / study apps)
        # 'secondary' = outlier (gaming, media, system noise, etc.)
        svdd_label = svdd_bucket(app_name, category=category,
                                 content=combined[:300])

        other_texts = [t for i, t in enumerate(all_session_texts) if i != idx]
        phrases     = extract_phrases(combined, top_n=20, corpus_texts=other_texts)
        primary_p   = [p for p in phrases if len(p.split()) <= 2]
        secondary_p = [p for p in phrases if len(p.split()) >  2]
        if not primary_p and secondary_p:
            primary_p = secondary_p[:2]; secondary_p = secondary_p[2:]

        main_task = ai_ctx or (
            ", ".join(primary_p[:4]) if primary_p else CATEGORY_LABELS[category])

        # ── Route by SVDD label — this is the actual split ────────
        # primary  (inside hypersphere)  → productive work → primary_bucket.csv
        # secondary (outlier)            → noise/distraction → secondary_bucket.csv
        if svdd_label == 'primary':
            primary_rows.append({
                'start_time'  : start_ts,
                'end_time'    : end_ts,
                'duration'    : _dur_str(dur),
                'app'         : app_name,
                'svdd_bucket' : svdd_label,
                'category'    : CATEGORY_LABELS[category],
                'main_task'   : main_task,
                'core_phrases': ' | '.join(primary_p),
            })
        else:
            secondary_rows.append({
                'start_time'      : start_ts,
                'app'             : app_name,
                'svdd_bucket'     : svdd_label,
                'category'        : CATEGORY_LABELS[category],
                'specific_phrases': ' | '.join(secondary_p),
                'all_phrases'     : ' | '.join(phrases),
                'ai_context'      : ai_ctx,
            })

    if not primary_rows:
        if not silent: print("[Processor] WARNING  No sessions to write.")
        return [], []

    # 5. Write bucket CSVs — SVDD-routed: primary → PRIMARY_OUT, secondary → SECONDARY_OUT
    try:
        # primary_bucket.csv — productive work sessions only
        with open(PRIMARY_OUT, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=[
                'start_time','end_time','duration','app',
                'svdd_bucket','category','main_task','core_phrases'])
            w.writeheader()
            w.writerows(primary_rows)

        # secondary_bucket.csv — noise / distraction / background sessions only
        with open(SECONDARY_OUT, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=[
                'start_time','app','svdd_bucket','category',
                'specific_phrases','all_phrases','ai_context'])
            w.writeheader()
            w.writerows(secondary_rows)

        if not silent:
            print(f"[Output] OK primary_bucket.csv   - {len(primary_rows)} productive sessions")
            print(f"[Output] OK secondary_bucket.csv - {len(secondary_rows)} noise/bg sessions")
            if not primary_rows:
                print("[SVDD]  WARNING  All sessions classified as secondary — "
                      "model may need retraining (run svdd_trainer.py)")
    except PermissionError as e:
        if not silent: print(f"[Processor] WARNING  Write failed (file locked): {e}")

    # 6. Build + write activity timeline  (ZERO API — pure row scanning)
    #    Uses the full clean_rows list (already built above) so no re-read.
    timeline = build_activity_timeline(clean_rows)
    write_timeline(timeline, silent=silent)

    return primary_rows, secondary_rows


# ═══════════════════════════════════════════════════════════════
# STEP 2 — GROQ TEXT SUMMARY  (every 60 min, ONE API call, ~2k tokens)
#
# Reads clean_log.csv + primary_bucket.csv + secondary_bucket.csv
# as PLAIN TEXT — zero images, zero Vision API.
# ═══════════════════════════════════════════════════════════════

def _read_csv_as_text(path: Path, max_rows: int = 200) -> str:
    """Read a CSV and return it as a compact text block for the prompt."""
    if not path.exists():
        return ""
    try:
        lines = []
        with open(path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                if i >= max_rows:
                    break
                # Compact: join non-empty values with |
                parts = [v.strip() for v in row.values() if v and v.strip()]
                lines.append(" | ".join(parts))
        return "\n".join(lines)
    except Exception:
        return ""


def _build_prompt(primary_rows: list, secondary_rows: list,
                  timeline: list | None = None) -> str:
    """
    Build a compact text prompt.
    Now includes the chronological activity timeline so the model can see
    the SEQUENCE of activities (switches, interruptions) not just totals.
    Total prompt size stays well under 4k tokens.
    """
    # Time totals per category
    cat_dur: dict = {}
    for r in primary_rows:
        cat = r['category']
        cat_dur[cat] = cat_dur.get(cat, 0) + _parse_dur_secs(r['duration'])

    time_block = "\n".join(
        f"  {cat}: {_dur_str(secs)}"
        for cat, secs in sorted(cat_dur.items(), key=lambda x: -x[1]))

    # Chronological timeline block — PRIMARY segments only (SVDD-filtered)
    if timeline:
        primary_segs   = [s for s in timeline if s.get('svdd_bucket','primary') == 'primary']
        secondary_segs = [s for s in timeline if s.get('svdd_bucket','primary') == 'secondary']

        tl_lines = []
        for seg in primary_segs:
            t  = f"{seg['start_time'][11:16]}–{seg['end_time'][11:16]}"
            cs = f" [{seg['context_switches']}sw]" if int(seg.get('context_switches', 0)) > 2 else ""
            tl_lines.append(
                f"  {t}  {seg['category']}  {seg['app']} ({seg['duration']})"
                f"  {seg['main_task'][:70]}{cs}"
            )
        timeline_block = "\n".join(tl_lines) if tl_lines else "  (no productive sessions detected)"

        # Compact secondary note — just app names + durations, not full detail
        if secondary_segs:
            sec_note = "  " + ",  ".join(
                f"{s['app']} ({s['duration']})" for s in secondary_segs[:8]
            )
        else:
            sec_note = "  none"
    else:
        # Fallback: use primary_rows only (already SVDD-split)
        tl_lines = []
        for p in primary_rows:
            t  = f"{p['start_time'][11:16]}-{p['end_time'][11:16]}"
            ai = p['main_task'][:90] if p['main_task'] else p['core_phrases'][:60]
            tl_lines.append(
                f"  [{t}] {p['category']} | {p['app']} ({p['duration']}) | {ai}")
        timeline_block = "\n".join(tl_lines) if tl_lines else "  (no productive sessions)"

        sec_note = "  " + ",  ".join(
            f"{s['app']}" for s in secondary_rows[:8]
        ) if secondary_rows else "  none"

    # Extra phrase context from clean_log (last 40 rows only)
    clean_snippet = _read_csv_as_text(CLEAN_CSV, max_rows=40)

    tl_label = "PRODUCTIVE ACTIVITY TIMELINE (SVDD-filtered: noise/distraction removed)" \
               if timeline else "PRODUCTIVE SESSIONS (SVDD primary bucket)"

    return f"""Summarise this developer's work session. All data comes from screen OCR text - no images.

TIME BREAKDOWN (productive sessions only):
{time_block}

{tl_label}:
{timeline_block}

NON-PRODUCTIVE / BACKGROUND APPS (SVDD secondary — excluded from main analysis):
{sec_note}

RECENT ACTIVITY PHRASES (from clean_log.csv — last 40 rows):
{clean_snippet[:1400]}

Write a plain-text story (no markdown headers) covering:
1. What was actually worked on — be SPECIFIC (name problems, tools, topics)
2. How time split across Coding / Learning / Meetings
3. Comment on the SEQUENCE of activities — note any interruptions, context switches, or focused blocks
4. One honest observation about focus or productivity patterns
5. One actionable tip for the next session

Ignore the non-productive apps listed above — do NOT mention them in the summary.
Max 280 words. Sound like a mentor giving feedback, not a robot.
"""


def _load_timeline_csv() -> list:
    """
    Read activity_timeline.csv back into a list of dicts.
    Used by generate_and_append_summary when timeline is not passed directly.
    Returns [] if the file does not exist or cannot be read.
    """
    if not TIMELINE_OUT.exists():
        return []
    try:
        rows = []
        with open(TIMELINE_OUT, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                row["duration_sec"] = int(row.get("duration_sec", 0) or 0)
                row["context_switches"] = int(row.get("context_switches", 0) or 0)
                rows.append(row)
        return rows
    except Exception:
        return []


def generate_and_append_summary(primary_rows: list, secondary_rows: list,
                                timeline: list | None = None):
    """
    Called every 60 min by BackgroundRefiner.
    ONE Groq text call (~2k tokens). Appends a block to work_summary.txt.
    Now embeds the chronological ASCII timeline bar in the output block.
    """
    if not primary_rows:
        print("[Summary] WARNING  No sessions - skipping."); return

    # Load timeline from file if not passed in directly
    if timeline is None:
        timeline = _load_timeline_csv()

    client  = _get_groq_client()
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    first   = primary_rows[0]['start_time']
    last    = primary_rows[-1]['end_time']
    n       = len(primary_rows)

    cat_dur: dict = {}
    for r in primary_rows:
        cat = r['category']
        cat_dur[cat] = cat_dur.get(cat, 0) + _parse_dur_secs(r['duration'])
    cat_line = "  |  ".join(
        f"{cat}: {_dur_str(secs)}"
        for cat, secs in sorted(cat_dur.items(), key=lambda x: -x[1]))

    summary = None
    if client:
        prompt = _build_prompt(primary_rows, secondary_rows, timeline=timeline)
        try:
            print("[Summary] >> Calling Groq (text only, ~2k tokens)...")
            resp = client.chat.completions.create(
                model       = "llama-3.3-70b-versatile",
                messages    = [{"role": "user", "content": prompt}],
                max_tokens  = 450,
                temperature = 0.4,
            )
            summary = resp.choices[0].message.content.strip()
            print("[Summary] OK Done.")
        except Exception as e:
            print(f"[Summary] ERROR Groq error: {e}")

    if summary is None:
        summary = _local_story(primary_rows)

    # Render ASCII timeline bar (empty string if no timeline data)
    bar = format_timeline_bar(timeline) if timeline else ""

    is_new = not SUMMARY_OUT.exists()
    with open(SUMMARY_OUT, 'a', encoding='utf-8') as f:
        if is_new:
            f.write("╔" + "═"*58 + "╗\n")
            f.write("║       WORK SUMMARY - auto-updated every hour          ║\n")
            f.write("╚" + "═"*58 + "╝\n\n")
        f.write("─" * 60 + "\n")
        f.write(f"  🕐 {now_str}  |  {first[11:16]} -> {last[11:16]}  |  {n} sessions\n")
        f.write(f"  {cat_line}\n")
        if bar:
            f.write("\n  ACTIVITY TIMELINE\n")
            f.write(bar + "\n")
        f.write("─" * 60 + "\n")
        f.write(summary + "\n\n")

    print(f"[Summary] >> Block appended to work_summary.txt at {now_str}")


def _local_story(primary_rows: list) -> str:
    if not primary_rows: return "No activity recorded."
    cat_dur: dict = {}; cat_tasks: dict = {}
    for r in primary_rows:
        cat = r['category']
        cat_dur[cat] = cat_dur.get(cat, 0) + _parse_dur_secs(r['duration'])
        task = r.get('main_task','')
        if task and task.lower() not in _PLACEHOLDERS:
            cat_tasks.setdefault(cat, []).append(task[:80])
    lines = []
    for cat, secs in sorted(cat_dur.items(), key=lambda x: -x[1]):
        tasks    = list(dict.fromkeys(cat_tasks.get(cat, [])))
        task_str = "; ".join(tasks[:3])[:120] if tasks else "various activities"
        lines.append(f"{cat} ({_dur_str(secs)}): {task_str}")
    total = sum(cat_dur.values())
    return (f"Total tracked: {_dur_str(total)}\n\n"
            + "\n".join(lines)
            + "\n\n(Local summary - Groq not available.)")


# ═══════════════════════════════════════════════════════════════
# BACKGROUND REFINER — two independent timers, one daemon thread
#   Every  1 min → bucket pipeline (ZERO API)
#   Every 60 min → story summary   (ONE Groq text call)
# ═══════════════════════════════════════════════════════════════
class BackgroundRefiner(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="BackgroundRefiner")
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        BUCKET_SEC  = BUCKET_INTERVAL_MIN  * 60
        SUMMARY_SEC = SUMMARY_INTERVAL_MIN * 60

        now = time.time()
        next_bucket  = now + BUCKET_SEC
        next_summary = now + SUMMARY_SEC

        print(f"[Refiner] >> Buckets every {BUCKET_INTERVAL_MIN} min  "
              f"-> first at {self._fmt(next_bucket)}  (ZERO API)")
        print(f"[Refiner] >> Summary every {SUMMARY_INTERVAL_MIN} min "
              f"-> first at {self._fmt(next_summary)}  (~2k tokens / call)")

        _last_primary:   list = []
        _last_secondary: list = []
        _last_timeline:  list = []

        while not self._stop_event.is_set():
            time.sleep(1)
            now = time.time()

            # ── Bucket tick — every 1 min, zero API ──────────────
            if now >= next_bucket:
                next_bucket = now + BUCKET_SEC
                print(f"\n[Refiner] >> Bucket pipeline (no API)...")
                try:
                    p, s = run_bucket_pipeline(INPUT_CSV, silent=False)
                    if p:
                        _last_primary   = p
                        _last_secondary = s
                    # Read the fresh timeline that was just written to disk
                    _last_timeline = _load_timeline_csv()
                    print(f"[Refiner] OK Buckets done. "
                          f"Timeline: {len(_last_timeline)} segments. "
                          f"Next {self._fmt(next_bucket)}")
                except Exception as e:
                    print(f"[Refiner] ERROR Bucket error: {e}")

            # ── Summary tick — every 60 min, ONE text call ────────
            if now >= next_summary:
                next_summary = now + SUMMARY_SEC
                print(f"\n[Refiner] >> Summary pipeline (Groq text, ~2k tokens)...")
                try:
                    # Always re-run buckets first for freshest data
                    p, s = run_bucket_pipeline(INPUT_CSV, silent=True)
                    if p:
                        _last_primary   = p
                        _last_secondary = s
                    _last_timeline = _load_timeline_csv()
                    generate_and_append_summary(
                        _last_primary, _last_secondary, timeline=_last_timeline)
                    print(f"[Refiner] OK Summary done. Next {self._fmt(next_summary)}")
                except Exception as e:
                    print(f"[Refiner] ERROR Summary error: {e}")

    @staticmethod
    def _fmt(ts: float) -> str:
        return datetime.fromtimestamp(ts).strftime("%H:%M")


# ═══════════════════════════════════════════════════════════════
# Called by main.py shutdown for a final run
# ═══════════════════════════════════════════════════════════════
def run_full_pipeline(input_path: Path = INPUT_CSV, silent: bool = False):
    p, s = run_bucket_pipeline(input_path, silent=silent)
    if p:
        tl = _load_timeline_csv()
        generate_and_append_summary(p, s, timeline=tl)
    return p, s


if __name__ == "__main__":
    print("=" * 60)
    print("  BUCKET PROCESSOR - manual run")
    print("=" * 60)
    p, s = run_bucket_pipeline(INPUT_CSV)
    if p:
        tl = _load_timeline_csv()
        print(f"\n  activity_timeline.csv - {len(tl)} segments")
        ans = input("\nGenerate AI story summary now? [y/N] ").strip().lower()
        if ans == 'y':
            generate_and_append_summary(p, s, timeline=tl)
    print("\n  clean_log.csv, primary_bucket.csv, secondary_bucket.csv,")
    print("  activity_timeline.csv  written.")