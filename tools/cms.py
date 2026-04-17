#!/usr/bin/env python3
"""
Getawayguide CMS Dashboard

Run with:
    python tools/cms.py

Then open http://localhost:5001 in your browser.

Features:
- Thumbnail preview of every article
- Draft / Published toggle (draft = hidden from the live site)
- Per-article notes (never published, local only)
"""

import json
import re
from pathlib import Path
from flask import Flask, jsonify, request, send_file

PROJECT = Path(__file__).resolve().parent.parent
ARTICLES_FILE = PROJECT / "_content" / "articles.json"

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_articles():
    with open(ARTICLES_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_articles(data):
    with open(ARTICLES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def set_card_visibility(article_id: str, visible: bool):
    """
    Comment-out or restore article cards across index.html, posts.html,
    and the country index page (el-salvador/index.html, etc.).

    Cards are identified by their onclick href containing the article path.
    """
    data = load_articles()
    article = next((a for a in data["articles"] if a["id"] == article_id), None)
    if not article:
        return

    # Derive the filename to search for in onclick/href attributes
    filename = Path(article["path"]).name  # e.g. santa-ana.html
    country  = article["country"]          # e.g. el-salvador

    listing_pages = [
        PROJECT / "index.html",
        PROJECT / "posts.html",
        PROJECT / f"{country}" / "index.html",
    ]

    for page_path in listing_pages:
        if not page_path.exists():
            continue

        text = page_path.read_text(encoding="utf-8")

        if visible:
            # Restore: uncomment blocks that contain this article's filename
            text = re.sub(
                r'<!--DRAFT:' + re.escape(article_id) + r'(.*?)DRAFT-->',
                lambda m: m.group(1).strip(),
                text, flags=re.DOTALL
            )
        else:
            # Draft: comment out the <article> or <div> card block that links to this article
            # Match post-card articles
            text = re.sub(
                r'(<article class="post-card"[^>]*onclick="location\.href=\'[^\']*'
                + re.escape(filename) + r'\'[^>]*>.*?</article>)',
                r'<!--DRAFT:' + article_id + r'\n\1\nDRAFT-->',
                text, flags=re.DOTALL
            )
            # Match country article card divs
            text = re.sub(
                r'(<(?:div|a)[^>]*class="country-article-card[^"]*"[^>]*'
                + re.escape(filename) + r'[^>]*>.*?</(?:div|a)>)',
                r'<!--DRAFT:' + article_id + r'\n\1\nDRAFT-->',
                text, flags=re.DOTALL
            )

        page_path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def dashboard():
    return DASHBOARD_HTML


@app.route("/api/articles")
def get_articles():
    return jsonify(load_articles())


@app.route("/api/articles/<article_id>", methods=["PUT"])
def update_article(article_id):
    payload = request.get_json()
    data = load_articles()

    article = next((a for a in data["articles"] if a["id"] == article_id), None)
    if not article:
        return jsonify({"error": "not found"}), 404

    changed_status = False
    old_status = article.get("status", "published")

    if "notes" in payload:
        article["notes"] = payload["notes"]
    if "status" in payload:
        article["status"] = payload["status"]
        changed_status = (old_status != payload["status"])

    save_articles(data)

    if changed_status:
        set_card_visibility(article_id, article["status"] == "published")

    return jsonify({"ok": True, "article": article})


@app.route("/image/<path:img_path>")
def serve_image(img_path):
    """Serve images from the project directory for thumbnails."""
    full = PROJECT / img_path
    if full.exists():
        return send_file(full)
    return "", 404


# ---------------------------------------------------------------------------
# Dashboard HTML (self-contained, no external deps)
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Getawayguide CMS</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f4f4f0; color: #1a2a22; min-height: 100vh; }

  /* Header */
  header { background: #1a3a28; color: #edf0ec; padding: 1.25rem 2rem;
           display: flex; align-items: center; justify-content: space-between; }
  header h1 { font-size: 1.1rem; font-weight: 500; letter-spacing: .05em; }
  header span { font-size: .8rem; opacity: .5; }

  /* Toolbar */
  .toolbar { padding: 1rem 2rem; display: flex; gap: .75rem; align-items: center;
             border-bottom: 1px solid #ddd; background: #fff; }
  .toolbar select { padding: .4rem .75rem; border: 1px solid #ccc; border-radius: 4px;
                    font-size: .85rem; background: #fff; }

  /* Grid */
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
          gap: 1.5rem; padding: 2rem; }

  /* Card */
  .card { background: #fff; border-radius: 8px; overflow: hidden;
          box-shadow: 0 1px 4px rgba(0,0,0,.08); display: flex; flex-direction: column; }
  .card.draft { opacity: .6; }
  .card.draft .thumb::after { content: 'DRAFT';
    position: absolute; top: .6rem; left: .6rem;
    background: #c0392b; color: #fff; font-size: .62rem; font-weight: 700;
    letter-spacing: .1em; padding: .25rem .6rem; border-radius: 3px; }

  /* Thumbnail */
  .thumb { position: relative; aspect-ratio: 4/3; overflow: hidden; background: #1a3a28; }
  .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }

  /* Status badge */
  .badge { display: inline-block; font-size: .65rem; font-weight: 600;
           letter-spacing: .08em; text-transform: uppercase; padding: .2rem .55rem;
           border-radius: 3px; }
  .badge.published { background: #d4edda; color: #155724; }
  .badge.draft { background: #f8d7da; color: #721c24; }

  /* Card body */
  .card-body { padding: 1rem; display: flex; flex-direction: column; gap: .75rem; flex: 1; }
  .card-tag { font-size: .7rem; text-transform: uppercase; letter-spacing: .12em;
              color: #2d6b50; font-weight: 600; }
  .card-title { font-size: .95rem; font-weight: 500; line-height: 1.35; }
  .card-date { font-size: .75rem; color: #888; }

  /* Toggle button */
  .btn-toggle { margin-top: auto; padding: .55rem 1rem; border: none; border-radius: 5px;
                font-size: .8rem; font-weight: 600; cursor: pointer; transition: background .2s; }
  .btn-toggle.publish { background: #1a3a28; color: #fff; }
  .btn-toggle.publish:hover { background: #2d6b50; }
  .btn-toggle.draft { background: #f8d7da; color: #721c24; }
  .btn-toggle.draft:hover { background: #f1aeb5; }

  /* Notes */
  .notes-label { font-size: .72rem; text-transform: uppercase; letter-spacing: .1em;
                 color: #888; margin-top: .25rem; }
  .notes-input { width: 100%; border: 1px solid #ddd; border-radius: 4px; padding: .5rem;
                 font-size: .82rem; font-family: inherit; resize: vertical; min-height: 64px;
                 line-height: 1.5; color: #1a2a22; }
  .notes-input:focus { outline: none; border-color: #2d6b50; }
  .save-note { margin-top: .35rem; padding: .35rem .75rem; background: #eaf3ee;
               border: 1px solid #2d6b50; border-radius: 4px; font-size: .75rem;
               color: #1a3a28; cursor: pointer; font-weight: 600; }
  .save-note:hover { background: #d4edda; }
  .saved-msg { font-size: .72rem; color: #2d6b50; display: none; }

  /* Status messages */
  .toast { position: fixed; bottom: 1.5rem; right: 1.5rem; background: #1a3a28;
           color: #edf0ec; padding: .75rem 1.25rem; border-radius: 6px;
           font-size: .85rem; display: none; z-index: 999; }
</style>
</head>
<body>

<header>
  <h1>&#9998; Getawayguide CMS</h1>
  <span id="article-count"></span>
</header>

<div class="toolbar">
  <label for="filter" style="font-size:.85rem;font-weight:500">Filter:</label>
  <select id="filter" onchange="renderGrid()">
    <option value="all">All articles</option>
    <option value="published">Published only</option>
    <option value="draft">Drafts only</option>
  </select>
</div>

<div class="grid" id="grid"></div>
<div class="toast" id="toast"></div>

<script>
let articles = [];

async function load() {
  const res = await fetch('/api/articles');
  const data = await res.json();
  articles = data.articles;
  document.getElementById('article-count').textContent = articles.length + ' articles';
  renderGrid();
}

function renderGrid() {
  const filter = document.getElementById('filter').value;
  const grid = document.getElementById('grid');
  grid.innerHTML = '';

  const filtered = filter === 'all' ? articles
    : articles.filter(a => a.status === filter);

  filtered.forEach(a => {
    const isDraft = a.status === 'draft';
    const card = document.createElement('div');
    card.className = 'card' + (isDraft ? ' draft' : '');
    card.dataset.id = a.id;

    card.innerHTML = `
      <div class="thumb">
        <img src="/image/${encodeURIComponent(a.thumbnail).replace(/%2F/g,'/')}"
             alt="${a.title}" loading="lazy">
      </div>
      <div class="card-body">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:.5rem">
          <span class="card-tag">${a.tag}</span>
          <span class="badge ${a.status}">${a.status}</span>
        </div>
        <div class="card-title">${a.title}</div>
        <div class="card-date">${a.date}</div>

        <button class="btn-toggle ${isDraft ? 'publish' : 'draft'}"
                onclick="toggleStatus('${a.id}', '${isDraft ? 'published' : 'draft'}')">
          ${isDraft ? '&#9654; Publish' : '&#9646;&#9646; Set as Draft'}
        </button>

        <div>
          <div class="notes-label">Notes (private)</div>
          <textarea class="notes-input" id="notes-${a.id}"
                    placeholder="Add to-do notes, reminders, ideas...">${a.notes || ''}</textarea>
          <div style="display:flex;align-items:center;gap:.75rem;margin-top:.35rem">
            <button class="save-note" onclick="saveNote('${a.id}')">Save note</button>
            <span class="saved-msg" id="saved-${a.id}">Saved</span>
          </div>
        </div>
      </div>`;

    grid.appendChild(card);
  });

  if (filtered.length === 0) {
    grid.innerHTML = '<p style="padding:2rem;color:#888;grid-column:1/-1">No articles found.</p>';
  }
}

async function toggleStatus(id, newStatus) {
  const res = await fetch('/api/articles/' + id, {
    method: 'PUT',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({status: newStatus})
  });
  const data = await res.json();
  const idx = articles.findIndex(a => a.id === id);
  if (idx !== -1) articles[idx] = data.article;
  renderGrid();
  showToast(newStatus === 'draft'
    ? 'Set to draft — hidden from site'
    : 'Published — now visible on site');
}

async function saveNote(id) {
  const notes = document.getElementById('notes-' + id).value;
  await fetch('/api/articles/' + id, {
    method: 'PUT',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({notes})
  });
  const idx = articles.findIndex(a => a.id === id);
  if (idx !== -1) articles[idx].notes = notes;
  const msg = document.getElementById('saved-' + id);
  msg.style.display = 'inline';
  setTimeout(() => msg.style.display = 'none', 2000);
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 3000);
}

load();
</script>
</body>
</html>"""


if __name__ == "__main__":
    print(f"\nGetawayguide CMS")
    print(f"Open http://localhost:5001 in your browser\n")
    app.run(debug=False, port=5001)
