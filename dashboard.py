"""
dashboard.py — Flask live dashboard at http://localhost:5000

Features:
  - Live feed of recent captures (auto-refreshes every 5 s)
  - Full-text search across window titles, highlights, OCR text
  - Screenshot thumbnails with click-to-zoom
  - Buffer status: shows how many screenshots are queued for AI
"""

from flask import Flask, render_template_string, request, jsonify


_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Laptop Monitor</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Segoe UI', sans-serif; background: #0f1117; color: #e0e0e0; }
    header {
      background: #1a1d27; padding: 16px 24px;
      display: flex; align-items: center; justify-content: space-between;
      border-bottom: 1px solid #2a2d3a; position: sticky; top: 0; z-index: 10;
    }
    header h1 { font-size: 1.2rem; color: #7c83fd; letter-spacing: 1px; }
    #status { font-size: 0.78rem; color: #888; }
    #status span { color: #7c83fd; font-weight: 600; }
    .search-bar {
      padding: 10px 24px; background: #13151f;
      border-bottom: 1px solid #1e2030;
    }
    .search-bar input {
      width: 100%; max-width: 480px; padding: 8px 14px;
      background: #1e2130; border: 1px solid #2e3150;
      border-radius: 6px; color: #e0e0e0; font-size: 0.9rem; outline: none;
    }
    .search-bar input:focus { border-color: #7c83fd; }
    #buffer-bar {
      padding: 6px 24px; background: #12141e;
      font-size: 0.78rem; color: #666; border-bottom: 1px solid #1a1c2a;
    }
    #buffer-bar span { color: #f0a500; font-weight: 600; }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
      gap: 16px; padding: 20px 24px;
    }
    .card {
      background: #1a1d27; border: 1px solid #242740;
      border-radius: 10px; overflow: hidden;
      transition: border-color 0.2s;
    }
    .card:hover { border-color: #7c83fd; }
    .card img {
      width: 100%; height: 180px; object-fit: cover;
      background: #12141e; cursor: zoom-in; display: block;
    }
    .card .no-img {
      width: 100%; height: 180px; background: #12141e;
      display: flex; align-items: center; justify-content: center;
      color: #333; font-size: 0.8rem;
    }
    .card-body { padding: 12px 14px; }
    .card-ts   { font-size: 0.72rem; color: #555; margin-bottom: 4px; }
    .card-win  { font-size: 0.8rem; color: #9098c8; margin-bottom: 6px;
                 white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .card-hi   { font-size: 0.85rem; color: #d0d4f0; line-height: 1.4; }
    .card-ocr  { font-size: 0.72rem; color: #555; margin-top: 6px;
                 max-height: 40px; overflow: hidden; }
    #modal {
      display: none; position: fixed; inset: 0;
      background: rgba(0,0,0,0.85); z-index: 100;
      align-items: center; justify-content: center;
    }
    #modal.open { display: flex; }
    #modal img { max-width: 90vw; max-height: 90vh; border-radius: 8px; }
    #modal-close {
      position: absolute; top: 20px; right: 28px;
      color: #fff; font-size: 2rem; cursor: pointer; user-select: none;
    }
    #empty { text-align: center; padding: 80px 20px; color: #444; }
  </style>
</head>
<body>

<header>
  <h1>📊 LAPTOP MONITOR</h1>
  <div id="status">Auto-refresh in <span id="countdown">5</span>s &nbsp;|&nbsp;
    Total captures: <span id="total">–</span></div>
</header>

<div class="search-bar">
  <input id="q" type="text" placeholder="Search window titles, highlights, OCR text…"
         oninput="doSearch(this.value)">
</div>
<div id="buffer-bar">AI buffer: <span id="buf-count">0</span> screenshots queued for batch processing</div>

<div class="grid" id="grid"></div>
<div id="empty" style="display:none">No results found.</div>

<div id="modal" onclick="closeModal()">
  <span id="modal-close" onclick="closeModal()">✕</span>
  <img id="modal-img" src="" alt="screenshot">
</div>

<script>
  let searchTimer = null;
  let countdownVal = 5;
  let autoRefreshTimer = null;

  function renderCards(rows) {
    const grid  = document.getElementById('grid');
    const empty = document.getElementById('empty');
    grid.innerHTML = '';
    if (!rows.length) { empty.style.display='block'; return; }
    empty.style.display = 'none';
    rows.forEach(r => {
      const card = document.createElement('div');
      card.className = 'card';
      const imgHtml = r.image_path
        ? `<img src="/thumb?path=${encodeURIComponent(r.image_path)}"
               onclick="openModal(this.src)" loading="lazy">`
        : `<div class="no-img">No image saved</div>`;
      card.innerHTML = `
        ${imgHtml}
        <div class="card-body">
          <div class="card-ts">${r.timestamp}</div>
          <div class="card-win" title="${r.active_window}">${r.active_window || '-'}</div>
          <div class="card-hi">${r.highlight || 'No highlight'}</div>
          <div class="card-ocr">${(r.ocr_text||'').slice(0,120)}</div>
        </div>`;
      grid.appendChild(card);
    });
  }

  function fetchRecent() {
    fetch('/api/recent')
      .then(r => r.json())
      .then(data => {
        renderCards(data.rows);
        document.getElementById('total').textContent = data.stats.total ?? '–';
        document.getElementById('buf-count').textContent = data.buffer_size ?? 0;
      })
      .catch(console.error);
  }

  function doSearch(q) {
    clearTimeout(searchTimer);
    if (!q.trim()) { fetchRecent(); return; }
    searchTimer = setTimeout(() => {
      fetch('/api/search?q=' + encodeURIComponent(q))
        .then(r => r.json())
        .then(data => renderCards(data.rows))
        .catch(console.error);
    }, 300);
  }

  function startCountdown() {
    clearInterval(autoRefreshTimer);
    countdownVal = 5;
    autoRefreshTimer = setInterval(() => {
      countdownVal--;
      document.getElementById('countdown').textContent = countdownVal;
      if (countdownVal <= 0) {
        countdownVal = 5;
        if (!document.getElementById('q').value.trim()) fetchRecent();
      }
    }, 1000);
  }

  function openModal(src) {
    document.getElementById('modal-img').src = src;
    document.getElementById('modal').classList.add('open');
  }
  function closeModal() {
    document.getElementById('modal').classList.remove('open');
  }
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

  fetchRecent();
  startCountdown();
</script>
</body>
</html>
"""


class Dashboard:

    def __init__(self, db, analyzer=None):
        self._db       = db
        self._analyzer = analyzer
        self._app      = Flask(__name__)
        self._register_routes()

    def _register_routes(self):
        app = self._app

        @app.route("/")
        def index():
            return render_template_string(_HTML)

        @app.route("/api/recent")
        def api_recent():
            rows  = self._db.get_recent(limit=50)
            stats = self._db.get_stats()
            buf   = len(self._analyzer.buffer) if self._analyzer else 0
            return jsonify({"rows": rows, "stats": stats, "buffer_size": buf})

        @app.route("/api/search")
        def api_search():
            q    = request.args.get("q", "").strip()
            rows = self._db.search(q, limit=100) if q else self._db.get_recent(50)
            return jsonify({"rows": rows})

        @app.route("/thumb")
        def thumb():
            """Serve screenshot images directly from disk.
            Path traversal guard: resolved path must be inside screenshots_dir.
            """
            import os
            from flask import send_file, abort
            from config import cfg as _cfg
            path = request.args.get("path", "")
            if not path:
                abort(404)
            # Resolve to absolute path — neutralises ../ traversal attempts
            abs_path      = os.path.realpath(path)
            allowed_root  = os.path.realpath(_cfg.screenshots_dir)
            # Reject anything that escapes the screenshots folder
            if not abs_path.startswith(allowed_root + os.sep):
                abort(403)
            if not os.path.isfile(abs_path):
                abort(404)
            return send_file(abs_path, mimetype="image/jpeg")

    def run(self, host: str = "0.0.0.0", port: int = 5000):
        print(f"[Dashboard] >> Running at http://localhost:{port}")
        self._app.run(host=host, port=port, debug=False, use_reloader=False)