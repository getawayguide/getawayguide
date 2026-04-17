#!/usr/bin/env python3
"""
Getawayguide CMS — combined dashboard + article editor

Run with:
    python tools/cms.py

Then open http://localhost:5001 in your browser.

Features:
- Dashboard: thumbnail preview, draft/publish toggle, private notes
- Editor: click "Edit" on any card to open the rich-text article editor
- Save articles directly to disk via File System Access API (Chrome/Edge required)
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
    """
    data = load_articles()
    article = next((a for a in data["articles"] if a["id"] == article_id), None)
    if not article:
        return

    filename = Path(article["path"]).name
    country  = article["country"]

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
            text = re.sub(
                r'<!--DRAFT:' + re.escape(article_id) + r'(.*?)DRAFT-->',
                lambda m: m.group(1).strip(),
                text, flags=re.DOTALL
            )
        else:
            text = re.sub(
                r'(<article class="post-card"[^>]*onclick="location\.href=\'[^\']*'
                + re.escape(filename) + r'\'[^>]*>.*?</article>)',
                r'<!--DRAFT:' + article_id + r'\n\1\nDRAFT-->',
                text, flags=re.DOTALL
            )
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
    return COMBINED_HTML


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
    full = PROJECT / img_path
    if full.exists():
        return send_file(full)
    return "", 404


# ---------------------------------------------------------------------------
# Combined HTML (dashboard + editor, single-page app)
# ---------------------------------------------------------------------------

COMBINED_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Getawayguide CMS</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:ital,wght@0,400;0,700;1,400&family=Fraunces:ital,wght@0,300;0,400;1,300&family=Jost:wght@300;400;500&family=Lora:ital,wght@0,400;0,500;1,400&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --ink: #1C2821;
    --cream: #F7F7F3;
    --terra: #2D6B50;
    --mist: #EDEDE7;
    --b-subtle: rgba(28,40,33,.08);
    --b-medium: rgba(28,40,33,.14);
  }

  body {
    font-family: 'Jost', sans-serif;
    background: var(--cream);
    color: var(--ink);
    min-height: 100vh;
  }

  /* ---- Shared Header ---- */
  header {
    background: var(--ink);
    color: var(--cream);
    padding: 0 2rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 56px;
    position: sticky;
    top: 0;
    z-index: 100;
    flex-shrink: 0;
  }
  .header-left { display: flex; align-items: center; gap: 1.25rem; }
  .logo {
    font-family: 'Fraunces', serif;
    font-size: 1.05rem;
    font-weight: 300;
    letter-spacing: .02em;
    color: var(--cream);
  }
  .file-label {
    font-family: 'Space Mono', monospace;
    font-size: .58rem;
    letter-spacing: .18em;
    text-transform: uppercase;
    color: rgba(237,232,220,.4);
  }
  #file-name {
    font-family: 'Space Mono', monospace;
    font-size: .62rem;
    letter-spacing: .1em;
    color: rgba(237,232,220,.7);
    background: rgba(255,255,255,.06);
    padding: .3rem .8rem;
    border-radius: 4px;
  }
  .header-actions { display: flex; align-items: center; gap: .75rem; }

  /* Shared button base */
  .btn {
    font-family: 'Space Mono', monospace;
    font-size: .58rem;
    letter-spacing: .15em;
    text-transform: uppercase;
    border: none;
    cursor: pointer;
    padding: .55rem 1.1rem;
    border-radius: 3px;
    transition: all .2s;
  }
  .btn-back {
    background: rgba(255,255,255,.1);
    color: var(--cream);
    border: 1px solid rgba(255,255,255,.15);
  }
  .btn-back:hover { background: rgba(255,255,255,.18); }
  .btn-open-file {
    background: rgba(255,255,255,.1);
    color: var(--cream);
    border: 1px solid rgba(255,255,255,.15);
  }
  .btn-open-file:hover { background: rgba(255,255,255,.18); }
  .btn-save {
    background: var(--terra);
    color: #fff;
  }
  .btn-save:hover { background: #255c43; }
  .btn-save:disabled { background: rgba(255,255,255,.1); color: rgba(237,232,220,.3); cursor: default; }

  .save-status {
    font-family: 'Space Mono', monospace;
    font-size: .55rem;
    letter-spacing: .12em;
    text-transform: uppercase;
    color: rgba(237,232,220,.35);
    min-width: 80px;
    text-align: right;
  }
  .save-status.saved { color: #6EC99A; }
  .save-status.unsaved { color: rgba(242,193,36,.7); }

  /* =========================================================
     DASHBOARD VIEW
     ========================================================= */

  .filter-bar {
    padding: 1rem 2rem;
    display: flex;
    gap: .75rem;
    align-items: center;
    border-bottom: 1px solid #ddd;
    background: #fff;
  }
  .filter-bar select {
    padding: .4rem .75rem;
    border: 1px solid #ccc;
    border-radius: 4px;
    font-size: .85rem;
    background: #fff;
  }

  /* Dashboard grid */
  .dash-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 1.5rem;
    padding: 2rem;
  }

  /* Dashboard card */
  .dash-card {
    background: #fff;
    border-radius: 8px;
    overflow: hidden;
    box-shadow: 0 1px 4px rgba(0,0,0,.08);
    display: flex;
    flex-direction: column;
  }
  .dash-card.draft { opacity: .65; }
  .dash-card.draft .thumb::after {
    content: 'DRAFT';
    position: absolute; top: .6rem; left: .6rem;
    background: #c0392b; color: #fff;
    font-size: .62rem; font-weight: 700;
    letter-spacing: .1em; padding: .25rem .6rem; border-radius: 3px;
  }

  .thumb { position: relative; aspect-ratio: 4/3; overflow: hidden; background: var(--ink); }
  .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }

  .badge {
    display: inline-block;
    font-size: .65rem; font-weight: 600;
    letter-spacing: .08em; text-transform: uppercase;
    padding: .2rem .55rem; border-radius: 3px;
  }
  .badge.published { background: #d4edda; color: #155724; }
  .badge.draft     { background: #f8d7da; color: #721c24; }

  .card-body {
    padding: 1rem;
    display: flex;
    flex-direction: column;
    gap: .65rem;
    flex: 1;
  }
  .card-tag {
    font-size: .7rem; text-transform: uppercase;
    letter-spacing: .12em; color: var(--terra); font-weight: 600;
  }
  .card-title { font-size: .95rem; font-weight: 500; line-height: 1.35; }
  .card-date  { font-size: .75rem; color: #888; }

  /* Card action row */
  .card-actions { display: flex; gap: .5rem; margin-top: auto; }
  .btn-edit {
    flex: 1;
    padding: .5rem .75rem;
    border: 1px solid var(--terra);
    background: #fff;
    color: var(--terra);
    border-radius: 5px;
    font-size: .75rem;
    font-weight: 600;
    cursor: pointer;
    transition: all .2s;
    font-family: 'Space Mono', monospace;
    letter-spacing: .08em;
  }
  .btn-edit:hover { background: var(--terra); color: #fff; }

  .btn-toggle {
    flex: 1;
    padding: .5rem .75rem;
    border: none;
    border-radius: 5px;
    font-size: .75rem;
    font-weight: 600;
    cursor: pointer;
    transition: background .2s;
    font-family: 'Space Mono', monospace;
    letter-spacing: .08em;
  }
  .btn-toggle.publish { background: var(--ink); color: #fff; }
  .btn-toggle.publish:hover { background: var(--terra); }
  .btn-toggle.draft { background: #f8d7da; color: #721c24; }
  .btn-toggle.draft:hover { background: #f1aeb5; }

  .notes-label {
    font-size: .72rem; text-transform: uppercase;
    letter-spacing: .1em; color: #888;
  }
  .notes-input {
    width: 100%; border: 1px solid #ddd; border-radius: 4px;
    padding: .5rem; font-size: .82rem;
    font-family: inherit; resize: vertical; min-height: 60px;
    line-height: 1.5; color: var(--ink);
  }
  .notes-input:focus { outline: none; border-color: var(--terra); }

  .save-note {
    padding: .35rem .75rem;
    background: #eaf3ee; border: 1px solid var(--terra);
    border-radius: 4px; font-size: .75rem;
    color: var(--ink); cursor: pointer; font-weight: 600;
    font-family: 'Space Mono', monospace;
  }
  .save-note:hover { background: #d4edda; }
  .saved-msg { font-size: .72rem; color: var(--terra); display: none; }

  .toast {
    position: fixed; bottom: 1.5rem; right: 1.5rem;
    background: var(--ink); color: var(--cream);
    padding: .75rem 1.25rem; border-radius: 6px;
    font-size: .85rem; display: none; z-index: 999;
  }

  /* =========================================================
     EDITOR VIEW
     ========================================================= */

  #view-edit {
    display: none;
    flex-direction: column;
    height: calc(100vh - 56px);
  }

  /* Editor notice / unsupported banners */
  #editor-notice {
    display: none;
    background: rgba(45,107,80,.08);
    border: 1px solid rgba(45,107,80,.2);
    color: var(--terra);
    font-family: 'Space Mono', monospace;
    font-size: .6rem;
    letter-spacing: .1em;
    padding: .6rem 2rem;
    text-align: center;
    flex-shrink: 0;
  }
  #unsupported {
    display: none;
    background: rgba(200,50,30,.08);
    border: 1px solid rgba(200,50,30,.2);
    color: #c83218;
    font-family: 'Space Mono', monospace;
    font-size: .6rem;
    letter-spacing: .1em;
    padding: .6rem 2rem;
    text-align: center;
    flex-shrink: 0;
  }

  /* Editor workspace layout */
  .workspace {
    display: grid;
    grid-template-columns: 240px 1fr;
    flex: 1;
    overflow: hidden;
  }

  /* Editor sidebar */
  .sidebar {
    background: #fff;
    border-right: 1px solid var(--b-medium);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .sidebar-head {
    padding: 1.25rem 1.25rem .75rem;
    border-bottom: 1px solid var(--b-subtle);
    flex-shrink: 0;
  }
  .sidebar-label {
    font-family: 'Space Mono', monospace;
    font-size: .52rem;
    letter-spacing: .2em;
    text-transform: uppercase;
    color: rgba(28,40,33,.35);
  }
  .article-list {
    overflow-y: auto;
    flex: 1;
    padding: .5rem 0;
  }
  .article-item {
    padding: .75rem 1.25rem;
    cursor: pointer;
    border-left: 2px solid transparent;
    transition: all .15s;
  }
  .article-item:hover { background: var(--mist); }
  .article-item.active {
    border-left-color: var(--terra);
    background: rgba(45,107,80,.06);
  }
  .article-item-name {
    font-size: .85rem;
    font-weight: 400;
    line-height: 1.3;
    color: var(--ink);
  }
  .article-item-file {
    font-family: 'Space Mono', monospace;
    font-size: .5rem;
    letter-spacing: .08em;
    color: rgba(28,40,33,.35);
    margin-top: .2rem;
  }

  /* Formatting toolbar */
  .fmt-toolbar {
    background: #fff;
    border-bottom: 1px solid var(--b-medium);
    padding: .5rem 2rem;
    display: flex;
    align-items: center;
    gap: .25rem;
    flex-wrap: wrap;
    flex-shrink: 0;
  }
  .tool-btn {
    font-family: 'Space Mono', monospace;
    font-size: .65rem; font-weight: 700;
    border: 1px solid var(--b-medium);
    background: #fff; color: var(--ink);
    padding: .3rem .55rem; border-radius: 3px;
    cursor: pointer; transition: all .15s; line-height: 1;
  }
  .tool-btn:hover { background: var(--mist); }
  .tool-btn.active { background: var(--ink); color: var(--cream); border-color: var(--ink); }
  .tool-divider { width: 1px; height: 18px; background: var(--b-medium); margin: 0 .35rem; }
  .tool-select {
    font-family: 'Space Mono', monospace;
    font-size: .55rem; letter-spacing: .08em;
    border: 1px solid var(--b-medium); background: #fff; color: var(--ink);
    padding: .28rem .4rem; border-radius: 3px; cursor: pointer;
    height: 26px; outline: none;
  }
  .tool-select:focus { border-color: var(--terra); }

  /* Color picker */
  .color-picker-wrap { position: relative; display: flex; align-items: center; }
  .color-swatch {
    width: 28px; height: 26px; border-radius: 3px;
    border: 1px solid var(--b-medium); cursor: pointer;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 2px; padding: 3px 5px; background: #fff; transition: all .15s;
  }
  .color-swatch:hover { background: var(--mist); }
  .color-swatch-letter { font-family: 'Space Mono', monospace; font-size: .65rem; font-weight: 700; line-height: 1; color: var(--ink); }
  .color-swatch-bar { width: 14px; height: 3px; border-radius: 1px; background: #e63946; }
  .color-dropdown {
    display: none; position: absolute; top: calc(100% + 4px); left: 0;
    background: #fff; border: 1px solid var(--b-medium); border-radius: 5px;
    padding: .5rem; box-shadow: 0 4px 16px rgba(0,0,0,.1); z-index: 200; width: 160px;
  }
  .color-dropdown.open { display: block; }
  .color-dropdown-label {
    font-family: 'Space Mono', monospace; font-size: .48rem;
    letter-spacing: .15em; text-transform: uppercase;
    color: rgba(28,40,33,.35); margin-bottom: .4rem; display: block;
  }
  .color-swatches-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 3px; margin-bottom: .5rem; }
  .color-dot {
    width: 18px; height: 18px; border-radius: 3px; cursor: pointer;
    border: 1.5px solid transparent; transition: transform .1s;
  }
  .color-dot:hover { transform: scale(1.2); border-color: rgba(0,0,0,.2); }
  .color-custom-row {
    display: flex; align-items: center; gap: .4rem;
    margin-top: .3rem; padding-top: .4rem; border-top: 1px solid var(--b-subtle);
  }
  .color-custom-label {
    font-family: 'Space Mono', monospace; font-size: .48rem;
    letter-spacing: .12em; text-transform: uppercase;
    color: rgba(28,40,33,.4); flex-shrink: 0;
  }
  #color-input {
    width: 22px; height: 22px; border: 1px solid var(--b-medium);
    border-radius: 3px; padding: 0; cursor: pointer; background: none;
  }

  /* Editor scroll area */
  .editor-wrap { display: flex; flex-direction: column; overflow: hidden; }
  .editor-scroll { flex: 1; overflow-y: auto; padding: 3rem 4rem; }
  #editor {
    max-width: 720px; margin: 0 auto;
    outline: none; min-height: 400px;
  }

  /* Article typography in editor */
  #editor h1, #editor h2 { font-family: 'Fraunces', serif; font-weight: 400; }
  #editor h2 { font-size: 1.7rem; margin: 2.5rem 0 1rem; letter-spacing: -.01em; border-bottom: 1px solid var(--b-subtle); padding-bottom: .75rem; }
  #editor h3 { font-family: 'Fraunces', serif; font-size: 1.1rem; font-weight: 400; font-style: italic; color: var(--terra); margin: 1.75rem 0 .6rem; padding-left: .75rem; border-left: 2px solid var(--terra); }
  #editor p { font-family: 'Jost', sans-serif; font-size: 1rem; line-height: 1.85; color: rgba(28,40,33,.75); margin-bottom: 1.1rem; font-weight: 300; }
  #editor a { color: var(--terra); }
  #editor strong { font-weight: 500; color: var(--ink); }
  #editor em { font-style: italic; }
  #editor ul, #editor ol { padding-left: 1.5rem; margin: 1rem 0 1.5rem; }
  #editor li { font-family: 'Jost', sans-serif; font-size: 1rem; line-height: 1.85; color: rgba(28,40,33,.75); margin-bottom: .4rem; font-weight: 300; }
  #editor img { max-width: 100%; height: auto; cursor: pointer; }
  #editor img.img-selected { outline: 2px solid var(--terra); outline-offset: 2px; }
  #editor img.img-cropped { cursor: grab; }
  #editor img.img-cropped.img-panning { cursor: grabbing; }
  #editor blockquote { border-left: 3px solid var(--terra); padding-left: 1.25rem; margin: 1.5rem 0; opacity: .75; }

  /* Floating image toolbar */
  #img-floating-toolbar {
    display: none; position: fixed; z-index: 400;
    background: #1C2821; border-radius: 4px;
    padding: .2rem .3rem; gap: .1rem; align-items: center;
    box-shadow: 0 3px 12px rgba(0,0,0,.3);
  }
  .img-tool-btn {
    font-family: 'Space Mono', monospace; font-size: .55rem;
    background: transparent; color: rgba(237,232,220,.75);
    border: none; cursor: pointer; padding: .28rem .45rem;
    border-radius: 2px; white-space: nowrap; transition: background .1s, color .1s;
  }
  .img-tool-btn:hover { background: rgba(255,255,255,.12); color: #fff; }
  .img-tool-btn.active-align { color: #6EC99A; }
  .img-tool-divider { width: 1px; height: 14px; background: rgba(255,255,255,.15); margin: 0 .1rem; flex-shrink: 0; }
  #img-width-input {
    width: 44px; font-family: 'Space Mono', monospace; font-size: .55rem;
    background: rgba(255,255,255,.1); border: 1px solid rgba(255,255,255,.18);
    color: #fff; border-radius: 2px; padding: .18rem .3rem; text-align: center; outline: none;
  }
  #img-resize-handle {
    display: none; position: fixed;
    width: 11px; height: 11px;
    background: var(--terra); border: 2px solid #fff;
    border-radius: 2px; cursor: se-resize; z-index: 401;
    box-shadow: 0 1px 4px rgba(0,0,0,.35);
  }

  /* Empty state */
  .empty-state {
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    height: 100%; gap: 1rem; color: rgba(28,40,33,.3); text-align: center;
  }
  .empty-icon { font-size: 3rem; opacity: .3; }
  .empty-title { font-family: 'Fraunces', serif; font-size: 1.4rem; font-weight: 300; }
  .empty-sub {
    font-family: 'Space Mono', monospace; font-size: .55rem;
    letter-spacing: .15em; text-transform: uppercase; opacity: .6;
  }

  /* Image insert modal */
  .img-modal-backdrop {
    display: none; position: fixed; inset: 0;
    background: rgba(0,0,0,.45); z-index: 500;
    align-items: center; justify-content: center;
  }
  .img-modal-backdrop.open { display: flex; }
  .img-modal {
    background: #fff; border-radius: 6px; padding: 1.5rem;
    width: 380px; max-width: 90vw;
    box-shadow: 0 8px 32px rgba(0,0,0,.18);
  }
  .img-modal-title {
    font-family: 'Space Mono', monospace; font-size: .52rem;
    letter-spacing: .2em; text-transform: uppercase;
    color: rgba(28,40,33,.4); margin-bottom: 1rem;
  }
  .img-modal-preview {
    width: 100%; max-height: 180px; object-fit: contain;
    border-radius: 3px; margin-bottom: 1rem; background: var(--mist); display: none;
  }
  .img-modal-label {
    font-family: 'Space Mono', monospace; font-size: .5rem;
    letter-spacing: .12em; text-transform: uppercase;
    color: rgba(28,40,33,.4); margin-bottom: .3rem; display: block;
  }
  .img-modal-input {
    width: 100%; font-family: 'Space Mono', monospace; font-size: .65rem;
    color: var(--ink); border: 1px solid var(--b-medium); border-radius: 3px;
    padding: .5rem .7rem; margin-bottom: .9rem; outline: none;
  }
  .img-modal-input:focus { border-color: var(--terra); }
  .img-modal-actions { display: flex; gap: .5rem; justify-content: flex-end; margin-top: .25rem; }
  .btn-modal-cancel {
    font-family: 'Space Mono', monospace; font-size: .58rem;
    letter-spacing: .12em; text-transform: uppercase;
    background: rgba(28,40,33,.07); color: var(--ink);
    border: 1px solid var(--b-medium); padding: .5rem 1rem;
    border-radius: 3px; cursor: pointer;
  }
  .btn-modal-insert {
    font-family: 'Space Mono', monospace; font-size: .58rem;
    letter-spacing: .12em; text-transform: uppercase;
    background: var(--terra); color: #fff; border: none;
    padding: .5rem 1rem; border-radius: 3px; cursor: pointer;
  }
</style>
</head>
<body>

<!-- =====================================================================
     FLOATING / FIXED EDITOR ELEMENTS (always in DOM)
     ===================================================================== -->

<div class="img-modal-backdrop" id="img-modal-backdrop">
  <div class="img-modal">
    <div class="img-modal-title">Insert Image</div>
    <img id="img-modal-preview" class="img-modal-preview" alt="preview">
    <label class="img-modal-label">Image Path (relative to site root)</label>
    <input class="img-modal-input" id="img-path-input" type="text" placeholder="Images/folder/filename.jpg">
    <label class="img-modal-label">Alt Text</label>
    <input class="img-modal-input" id="img-alt-input" type="text" placeholder="Describe the image">
    <div class="img-modal-actions">
      <button class="btn-modal-cancel" onclick="closeImgModal()">Cancel</button>
      <button class="btn-modal-insert" onclick="confirmInsertImage()">Insert</button>
    </div>
  </div>
</div>

<div id="img-floating-toolbar" style="display:none;position:fixed;z-index:400">
  <button class="img-tool-btn" onclick="imgAlign('left')">&#8592; Left</button>
  <button class="img-tool-btn" onclick="imgAlign('center')">&#8861; Center</button>
  <button class="img-tool-btn" onclick="imgAlign('right')">Right &#8594;</button>
  <div class="img-tool-divider"></div>
  <button class="img-tool-btn" onclick="imgSetWidth('25%')">25%</button>
  <button class="img-tool-btn" onclick="imgSetWidth('50%')">50%</button>
  <button class="img-tool-btn" onclick="imgSetWidth('100%')">100%</button>
  <button class="img-tool-btn" onclick="imgSetWidth('calc(39% - 0.5rem)')" title="Portrait article photo">Art&#8597;</button>
  <button class="img-tool-btn" onclick="imgSetWidth('calc(55.5% - 0.85rem)')" title="Wide article photo">Art&#8596;</button>
  <input id="img-width-input" type="text" placeholder="width" title="e.g. 300px or 50%">
  <div class="img-tool-divider"></div>
  <button class="img-tool-btn" onclick="imgPan()" title="Pan/reposition">&#8596; Pan</button>
  <button class="img-tool-btn" onclick="imgCrop('4/5')">&#9645; 4:5</button>
  <button class="img-tool-btn" onclick="imgCrop('7/5')">&#9645; 7:5</button>
  <button class="img-tool-btn" onclick="imgCrop('16/8.5')">&#9645; Wide</button>
  <button class="img-tool-btn" onclick="imgCrop('1/1')">&#9645; 1:1</button>
  <button class="img-tool-btn" onclick="imgCrop('none')">&#9986; Off</button>
  <div class="img-tool-divider"></div>
  <button class="img-tool-btn" onclick="imgDelete()" style="color:#ff8080">&#10005;</button>
</div>
<div id="img-resize-handle"></div>

<!-- =====================================================================
     DASHBOARD HEADER
     ===================================================================== -->

<header id="hdr-dash">
  <span class="logo">&#9998; Getawayguide CMS</span>
  <span id="article-count" style="font-size:.8rem;opacity:.5"></span>
</header>

<!-- =====================================================================
     EDITOR HEADER
     ===================================================================== -->

<header id="hdr-edit" style="display:none">
  <div class="header-left">
    <button class="btn btn-back" onclick="showView('dashboard')">&#8592; Dashboard</button>
    <span class="file-label">&#8212;</span>
    <span id="file-name">no file open</span>
  </div>
  <div class="header-actions">
    <span class="save-status" id="save-status"></span>
    <button class="btn btn-open-file" id="btn-open">Open File</button>
    <button class="btn btn-save" id="btn-save" disabled>Save</button>
  </div>
</header>

<!-- =====================================================================
     DASHBOARD VIEW
     ===================================================================== -->

<div id="view-dash">
  <div class="filter-bar">
    <label for="filter" style="font-size:.85rem;font-weight:500">Filter:</label>
    <select id="filter" onchange="renderGrid()">
      <option value="all">All articles</option>
      <option value="published">Published only</option>
      <option value="draft">Drafts only</option>
    </select>
  </div>
  <div class="dash-grid" id="grid"></div>
  <div class="toast" id="toast"></div>
</div>

<!-- =====================================================================
     EDITOR VIEW
     ===================================================================== -->

<div id="view-edit">
  <div id="unsupported">
    Your browser does not support the File System Access API. Please use Chrome or Edge to edit articles.
  </div>
  <div id="editor-notice">
    Images and structural divs are hidden in the editor &mdash; only text content is shown. They are preserved on save.
  </div>

  <div class="workspace">
    <aside class="sidebar">
      <div class="sidebar-head">
        <div class="sidebar-label">Articles</div>
        <div id="folder-status" style="margin-top:.6rem">
          <button id="btn-connect-folder" onclick="connectFolder()"
            style="width:100%;font-family:'Space Mono',monospace;font-size:.5rem;letter-spacing:.12em;text-transform:uppercase;background:var(--terra);color:#fff;border:none;border-radius:3px;padding:.45rem .6rem;cursor:pointer;text-align:left">
            &#9881; Connect Site Folder
          </button>
        </div>
      </div>
      <div class="article-list" id="article-list"></div>
    </aside>

    <div class="editor-wrap">
      <div class="fmt-toolbar" id="fmt-toolbar">
        <button class="tool-btn" onclick="fmt('bold')"><b>B</b></button>
        <button class="tool-btn" onclick="fmt('italic')"><i>I</i></button>
        <button class="tool-btn" onclick="fmt('underline')"><u>U</u></button>
        <div class="tool-divider"></div>
        <button class="tool-btn" onclick="insertHeading('h2')">H2</button>
        <button class="tool-btn" onclick="insertHeading('h3')">H3</button>
        <button class="tool-btn" onclick="insertHeading('p')">&#182;</button>
        <div class="tool-divider"></div>
        <button class="tool-btn" onclick="textAlign('left')">&#8801;&#8592;</button>
        <button class="tool-btn" onclick="textAlign('center')">&#8801;</button>
        <button class="tool-btn" onclick="textAlign('right')">&#8801;&#8594;</button>
        <div class="tool-divider"></div>
        <button class="tool-btn" onclick="insertLink()">Link</button>
        <button class="tool-btn" onclick="insertImage()">+ Image</button>
        <div class="tool-divider"></div>
        <button class="tool-btn" onclick="fmt('insertUnorderedList')">&#8226; List</button>
        <button class="tool-btn" onclick="fmt('insertOrderedList')">1. List</button>
        <button class="tool-btn" onclick="insertTermList()">&#8801; Term</button>
        <div class="tool-divider"></div>
        <button class="tool-btn" onclick="undo()">&#8617; Undo</button>
        <button class="tool-btn" onclick="redo()">&#8618; Redo</button>
        <div class="tool-divider"></div>
        <select class="tool-select" onmousedown="saveSelection()" onchange="applyFontFamily(this, this.value)">
          <option value="">Font</option>
          <option value="Jost, sans-serif">Jost</option>
          <option value="__jost-bold__">Jost Bold</option>
          <option value="Fraunces, serif">Fraunces</option>
          <option value="Lora, serif">Lora</option>
          <option value="'Space Mono', monospace">Mono</option>
        </select>
        <select class="tool-select" onmousedown="saveSelection()" onchange="applyFontSize(this, this.value)">
          <option value="">Size</option>
          <option value="0.85rem">Small</option>
          <option value="1rem">Body</option>
          <option value="1.2rem">Large</option>
          <option value="1.5rem">XLarge</option>
        </select>
        <div class="tool-divider"></div>
        <div class="color-picker-wrap" id="color-picker-wrap">
          <div class="color-swatch" onclick="toggleColorPicker()">
            <span class="color-swatch-letter">A</span>
            <div class="color-swatch-bar" id="color-swatch-bar"></div>
          </div>
          <div class="color-dropdown" id="color-dropdown">
            <span class="color-dropdown-label">Theme</span>
            <div class="color-swatches-grid" id="color-theme-grid"></div>
            <span class="color-dropdown-label" style="margin-top:.5rem;padding-top:.5rem;border-top:1px solid var(--b-subtle);display:block">All Colors</span>
            <div class="color-swatches-grid" id="color-swatches-grid"></div>
            <div class="color-custom-row">
              <span class="color-custom-label">Custom</span>
              <input type="color" id="color-input" value="#e63946">
            </div>
          </div>
        </div>
      </div>

      <div class="editor-scroll">
        <div id="editor-container">
          <div class="empty-state" id="empty-state">
            <div class="empty-icon">&#10022;</div>
            <div class="empty-title">No article open</div>
            <div class="empty-sub">Click Edit on any article card, or use Open File</div>
          </div>
          <div id="editor" contenteditable="false" spellcheck="true" style="display:none"></div>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
// =============================================================================
// VIEW SWITCHING
// =============================================================================

function showView(view) {
  const isDash = view === 'dashboard';
  document.getElementById('hdr-dash').style.display  = isDash ? 'flex' : 'none';
  document.getElementById('hdr-edit').style.display  = isDash ? 'none' : 'flex';
  document.getElementById('view-dash').style.display = isDash ? 'block' : 'none';
  document.getElementById('view-edit').style.display = isDash ? 'none' : 'flex';
}

// =============================================================================
// DASHBOARD LOGIC
// =============================================================================

let articles = [];

async function loadArticles() {
  const res = await fetch('/api/articles');
  const data = await res.json();
  articles = data.articles;
  document.getElementById('article-count').textContent = articles.length + ' articles';
  renderGrid();
  refreshArticleList();   // keep editor sidebar in sync
}

function renderGrid() {
  const filter = document.getElementById('filter').value;
  const grid = document.getElementById('grid');
  grid.innerHTML = '';

  const filtered = filter === 'all' ? articles : articles.filter(a => a.status === filter);

  filtered.forEach(a => {
    const isDraft = a.status === 'draft';
    const card = document.createElement('div');
    card.className = 'dash-card' + (isDraft ? ' draft' : '');
    card.dataset.id = a.id;

    card.innerHTML =
      '<div class="thumb">' +
        '<img src="/image/' + encodeURIComponent(a.thumbnail).replace(/%2F/g, '/') + '" alt="' + a.title + '" loading="lazy">' +
      '</div>' +
      '<div class="card-body">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;gap:.5rem">' +
          '<span class="card-tag">' + a.tag + '</span>' +
          '<span class="badge ' + a.status + '">' + a.status + '</span>' +
        '</div>' +
        '<div class="card-title">' + a.title + '</div>' +
        '<div class="card-date">' + a.date + '</div>' +
        '<div class="card-actions">' +
          '<button class="btn-edit" onclick="openArticleFromDashboard(' + JSON.stringify(a.id) + ')">&#9998; Edit</button>' +
          '<button class="btn-toggle ' + (isDraft ? 'publish' : 'draft') + '" ' +
            'onclick="toggleStatus(' + JSON.stringify(a.id) + ', ' + JSON.stringify(isDraft ? 'published' : 'draft') + ')">' +
            (isDraft ? '&#9654; Publish' : '&#9646;&#9646; Draft') +
          '</button>' +
        '</div>' +
        '<div>' +
          '<div class="notes-label">Notes (private)</div>' +
          '<textarea class="notes-input" id="notes-' + a.id + '" placeholder="Add to-do notes, reminders, ideas...">' + (a.notes || '') + '</textarea>' +
          '<div style="display:flex;align-items:center;gap:.75rem;margin-top:.35rem">' +
            '<button class="save-note" onclick="saveNote(' + JSON.stringify(a.id) + ')">Save note</button>' +
            '<span class="saved-msg" id="saved-' + a.id + '">Saved</span>' +
          '</div>' +
        '</div>' +
      '</div>';

    grid.appendChild(card);
  });

  if (filtered.length === 0) {
    grid.innerHTML = '<p style="padding:2rem;color:#888;grid-column:1/-1">No articles found.</p>';
  }
}

async function toggleStatus(id, newStatus) {
  const res = await fetch('/api/articles/' + id, {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({status: newStatus})
  });
  const data = await res.json();
  const idx = articles.findIndex(a => a.id === id);
  if (idx !== -1) articles[idx] = data.article;
  renderGrid();
  showToast(newStatus === 'draft' ? 'Set to draft \u2014 hidden from site' : 'Published \u2014 now visible on site');
}

async function saveNote(id) {
  const notes = document.getElementById('notes-' + id).value;
  await fetch('/api/articles/' + id, {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
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

// Open article from dashboard Edit button
async function openArticleFromDashboard(articleId) {
  const article = articles.find(a => a.id === articleId);
  if (!article) return;

  showView('editor');

  if (!('showOpenFilePicker' in window)) {
    document.getElementById('unsupported').style.display = 'block';
    return;
  }

  if (!folderHandle) {
    // Prompt user to connect folder first
    const ok = await connectFolder();
    if (!ok) return;
  } else {
    const granted = await ensureFolderHandle();
    if (!granted) return;
  }

  await openArticleFile(article.path, article.id);
}

// =============================================================================
// EDITOR LOGIC
// =============================================================================

if (!('showOpenFilePicker' in window)) {
  // Will show the banner when editor view is opened
}

let fileHandle = null;
let originalHTML = '';
let isDirty = false;
let folderHandle = null;
let lastFileHandle = null;

// --- IndexedDB helpers ---
function idbRequest(fn) {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open('editor-handles', 1);
    req.onupgradeneeded = e => e.target.result.createObjectStore('handles');
    req.onerror = () => reject(req.error);
    req.onsuccess = e => {
      try { fn(e.target.result, resolve, reject); }
      catch(err) { reject(err); }
    };
  });
}
function saveFolderHandle(h) {
  return idbRequest((db, resolve, reject) => {
    const tx = db.transaction('handles', 'readwrite');
    tx.objectStore('handles').put(h, 'folder');
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
}
function loadFolderHandle() {
  return idbRequest((db, resolve) => {
    const tx = db.transaction('handles', 'readonly');
    const req = tx.objectStore('handles').get('folder');
    req.onsuccess = () => resolve(req.result || null);
    req.onerror = () => resolve(null);
  });
}

// Restore stored handle on page load
(async () => {
  try {
    const stored = await loadFolderHandle();
    if (stored) {
      folderHandle = stored;
      const perm = await stored.queryPermission({ mode: 'readwrite' });
      if (perm === 'granted') updateFolderUI();
    }
  } catch(e) {}
})();

async function ensureFolderHandle() {
  if (!folderHandle) return false;
  const perm = await folderHandle.queryPermission({ mode: 'readwrite' });
  if (perm === 'granted') return true;
  const granted = await folderHandle.requestPermission({ mode: 'readwrite' });
  return granted === 'granted';
}

async function connectFolder() {
  try {
    const h = await window.showDirectoryPicker({
      id: 'travel-blog',
      mode: 'readwrite',
      startIn: lastFileHandle || 'documents'
    });
    folderHandle = h;
    await saveFolderHandle(h);
    updateFolderUI();
    refreshArticleList();
    return true;
  } catch(e) {
    return false;
  }
}

function updateFolderUI() {
  const status = document.getElementById('folder-status');
  if (!folderHandle) {
    status.innerHTML =
      '<button onclick="connectFolder()" style="width:100%;font-family:\'Space Mono\',monospace;font-size:.5rem;letter-spacing:.12em;text-transform:uppercase;background:var(--terra);color:#fff;border:none;border-radius:3px;padding:.45rem .6rem;cursor:pointer;text-align:left">&#9881; Connect Site Folder</button>';
    return;
  }
  status.innerHTML =
    '<div style="font-family:\'Space Mono\',monospace;font-size:.5rem;letter-spacing:.1em;color:rgba(28,40,33,.5);text-transform:uppercase;margin-bottom:.3rem">Connected</div>' +
    '<div style="display:flex;align-items:center;justify-content:space-between;gap:.4rem">' +
      '<span style="font-family:\'Space Mono\',monospace;font-size:.55rem;color:var(--terra);overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="' + folderHandle.name + '">' + folderHandle.name + '</span>' +
      '<button onclick="connectFolder()" title="Change folder" style="font-family:\'Space Mono\',monospace;font-size:.5rem;background:none;border:1px solid var(--b-medium);border-radius:2px;padding:.15rem .35rem;cursor:pointer;color:rgba(28,40,33,.4);flex-shrink:0">&#8634;</button>' +
    '</div>';
}

// Populate editor sidebar with CMS articles
function refreshArticleList() {
  const list = document.getElementById('article-list');
  list.innerHTML = '';
  articles.forEach(a => {
    const item = document.createElement('div');
    item.className = 'article-item';
    item.id = 'sidebar-item-' + a.id;
    item.innerHTML =
      '<div class="article-item-name">' + a.title + '</div>' +
      '<div class="article-item-file">' + a.path + '</div>';
    item.onclick = () => {
      if (!folderHandle) {
        connectFolder().then(ok => { if (ok) openArticleFile(a.path, a.id); });
      } else {
        openArticleFile(a.path, a.id);
      }
    };
    list.appendChild(item);
  });
}

// Open an article by its relative path (e.g. "el-salvador/santa-ana.html")
async function openArticleFile(path, articleId) {
  if (!folderHandle) return;
  const ok = await ensureFolderHandle();
  if (!ok) return;
  try {
    // Traverse subdirectories if path contains "/"
    const parts = path.split('/');
    let dirHandle = folderHandle;
    for (let i = 0; i < parts.length - 1; i++) {
      dirHandle = await dirHandle.getDirectoryHandle(parts[i]);
    }
    const fh = await dirHandle.getFileHandle(parts[parts.length - 1]);
    await loadFile(fh);

    // Highlight active item in sidebar
    document.querySelectorAll('.article-item').forEach(el => el.classList.remove('active'));
    if (articleId) {
      const sidebarItem = document.getElementById('sidebar-item-' + articleId);
      if (sidebarItem) sidebarItem.classList.add('active');
    }
  } catch (e) {
    alert('Could not open ' + path + '.\\nMake sure the Travel Blog root folder is connected (not a subfolder).');
  }
}

async function loadFile(fh) {
  fileHandle = fh;
  lastFileHandle = fh;
  const file = await fh.getFile();
  originalHTML = await file.text();

  document.getElementById('file-name').textContent = fh.name;
  document.getElementById('btn-save').disabled = false;

  const parser = new DOMParser();
  const doc = parser.parseFromString(originalHTML, 'text/html');
  const body = doc.querySelector('.article-body');

  if (!body) {
    alert('No .article-body found in this file. Only article pages can be edited here.');
    return;
  }

  const editor = document.getElementById('editor');
  editor.innerHTML = body.innerHTML;

  // Convert image src paths to blob URLs for display
  const imgEls = editor.querySelectorAll('img');
  await Promise.all(Array.from(imgEls).map(async img => {
    const rawSrc = img.getAttribute('src');
    if (!rawSrc || rawSrc.startsWith('blob:') || rawSrc.startsWith('data:') || rawSrc.startsWith('http')) return;
    img.setAttribute('data-path', rawSrc);
    if (folderHandle) {
      try {
        const parts = rawSrc.replace(/^\\.\\.\\//, '').split('/');
        let handle = folderHandle;
        for (let i = 0; i < parts.length - 1; i++) {
          handle = await handle.getDirectoryHandle(parts[i]);
        }
        const fileH = await handle.getFileHandle(parts[parts.length - 1]);
        const blob = await fileH.getFile();
        img.src = URL.createObjectURL(blob);
      } catch(e) {}
    }
  }));

  // Re-apply canvas crop for saved images
  editor.querySelectorAll('img').forEach(img => {
    if (img.style.objectFit === 'cover' && img.style.aspectRatio) {
      img.classList.add('img-cropped');
      img.dataset.originalSrc = img.src;
      img.dataset.crop = img.style.aspectRatio.replace(/\\s/g, '');
      const pos = (img.style.objectPosition || '50% 50%').split(' ');
      img.dataset.cropX = (parseFloat(pos[0]) / 100).toFixed(3);
      img.dataset.cropY = (parseFloat(pos[1]) / 100).toFixed(3);
      img.style.aspectRatio = '';
      img.style.objectFit = '';
      img.style.objectPosition = '';
      applyCanvasCrop(img);
    } else if (img.style.objectFit === 'cover') {
      img.classList.add('img-cropped');
    }
  });

  editor.contentEditable = 'true';
  editor.spellcheck = true;
  editor.style.display = 'block';
  document.getElementById('empty-state').style.display = 'none';
  document.getElementById('editor-notice').style.display = 'none';

  setDirty(false);
  editor.addEventListener('input', () => setDirty(true), { once: false });
  editor.focus();
}

function setDirty(dirty) {
  isDirty = dirty;
  const status = document.getElementById('save-status');
  if (dirty) {
    status.textContent = '&#9679; Unsaved';
    status.className = 'save-status unsaved';
  } else {
    status.textContent = '';
    status.className = 'save-status';
  }
}

// Open File button (arbitrary file, no folder needed)
document.getElementById('btn-open').onclick = async () => {
  try {
    const [fh] = await window.showOpenFilePicker({ multiple: false });
    await loadFile(fh);
    const fname = fh.name;
    document.querySelectorAll('.article-item').forEach(el => {
      el.classList.toggle('active', el.querySelector('.article-item-file')?.textContent.endsWith(fname));
    });
  } catch (e) {
    if (e.name !== 'AbortError') alert('Error opening file: ' + e.message);
  }
};

// Save button
document.getElementById('btn-save').onclick = async () => {
  if (!fileHandle) return;
  await saveFile();
};

// Ctrl+S
document.addEventListener('keydown', async (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault();
    if (fileHandle) await saveFile();
  }
});

async function saveFile() {
  const editor = document.getElementById('editor');
  const parser = new DOMParser();
  const doc = parser.parseFromString(originalHTML, 'text/html');
  const originalBody = doc.querySelector('.article-body');
  if (!originalBody) return;

  const saveEl = document.createElement('div');
  saveEl.innerHTML = editor.innerHTML;

  // Restore blob URLs to real paths
  saveEl.querySelectorAll('img[data-path]').forEach(img => {
    img.setAttribute('src', img.getAttribute('data-path'));
    img.removeAttribute('data-path');
  });

  // Strip editor-only classes
  saveEl.querySelectorAll('.img-selected').forEach(el => el.classList.remove('img-selected'));
  saveEl.querySelectorAll('.img-cropped').forEach(el => el.classList.remove('img-cropped'));
  saveEl.querySelectorAll('.img-panning').forEach(el => el.classList.remove('img-panning'));

  // Wrap canvas-cropped images in a span so aspect-ratio doesn't conflict with EXIF orientation
  saveEl.querySelectorAll('img[data-crop]').forEach(img => {
    const ratio = img.getAttribute('data-crop');
    const cx = (parseFloat(img.getAttribute('data-crop-x') || '0.5') * 100).toFixed(1);
    const cy = (parseFloat(img.getAttribute('data-crop-y') || '0.5') * 100).toFixed(1);
    const w = img.style.width || '100%';
    const mw = img.style.maxWidth || '100%';
    const margin = img.style.margin || '1rem auto';
    const br = img.style.borderRadius || '2px';
    const wrapper = document.createElement('span');
    wrapper.style.cssText = 'display:block;position:relative;overflow:hidden;' +
      'width:' + w + ';max-width:' + mw + ';aspect-ratio:' + ratio + ';' +
      'margin:' + margin + ';border-radius:' + br + ';';
    img.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;' +
      'object-fit:cover;object-position:' + cx + '% ' + cy + '%;';
    img.removeAttribute('data-crop');
    img.removeAttribute('data-crop-x');
    img.removeAttribute('data-crop-y');
    img.removeAttribute('data-original-src');
    img.parentNode.insertBefore(wrapper, img);
    wrapper.appendChild(img);
  });

  originalBody.innerHTML = saveEl.innerHTML;
  let newHTML = '<!DOCTYPE html>\\n' + doc.documentElement.outerHTML;

  try {
    const writable = await fileHandle.createWritable();
    await writable.write(newHTML);
    await writable.close();
    originalHTML = newHTML;
    setDirty(false);
    const status = document.getElementById('save-status');
    status.textContent = 'Saved';
    status.className = 'save-status saved';
    setTimeout(() => { if (!isDirty) { status.textContent = ''; status.className = 'save-status'; } }, 2500);
  } catch (e) {
    alert('Save failed: ' + e.message);
  }
}

// ---- Formatting commands ----
function fmt(cmd, value) {
  document.getElementById('editor').focus();
  document.execCommand(cmd, false, value || null);
}
function insertLink() {
  const url = prompt('Enter URL:');
  if (url) fmt('createLink', url);
}
function undo() { fmt('undo'); }
function redo() { fmt('redo'); }

let _imgSavedRange = null;
let _imgBlobUrl = null;
let _savedRange = null;

function saveSelection() {
  const sel = window.getSelection();
  if (sel && sel.rangeCount) _savedRange = sel.getRangeAt(0).cloneRange();
}
function restoreSelection() {
  const editor = document.getElementById('editor');
  editor.focus({ preventScroll: true });
  if (_savedRange) {
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(_savedRange);
  }
}

function insertHeading(tag) {
  document.getElementById('editor').focus();
  document.execCommand('formatBlock', false, tag);
  if (tag === 'h2' || tag === 'h3') {
    const sel = window.getSelection();
    if (sel.anchorNode) {
      const node = sel.anchorNode.nodeType === 3 ? sel.anchorNode.parentElement : sel.anchorNode;
      const h = node.tagName && node.tagName.toLowerCase() === tag ? node : node.closest(tag);
      if (h) h.className = tag === 'h2' ? 'article-h2' : 'article-h3';
    }
  }
}
function textAlign(dir) {
  document.getElementById('editor').focus();
  const map = { left: 'justifyLeft', center: 'justifyCenter', right: 'justifyRight' };
  document.execCommand(map[dir], false, null);
}
function applyFontFamily(sel, family) {
  if (!family) return;
  restoreSelection();
  if (family === '__jost-bold__') {
    const s = window.getSelection();
    if (s && s.rangeCount && !s.isCollapsed) {
      const range = s.getRangeAt(0);
      const strong = document.createElement('strong');
      strong.style.color = 'var(--ink)';
      range.surroundContents(strong);
      s.removeAllRanges();
    }
  } else {
    document.execCommand('fontName', false, family);
  }
  sel.value = '';
}
function applyFontSize(sel, size) {
  if (!size) return;
  restoreSelection();
  document.execCommand('fontSize', false, '7');
  document.getElementById('editor').querySelectorAll('font[size="7"]').forEach(el => {
    el.removeAttribute('size');
    el.style.fontSize = size;
  });
  sel.value = '';
}

function insertTermList() {
  const editor = document.getElementById('editor');
  editor.focus();
  const ul = document.createElement('ul');
  ul.style.cssText = 'list-style:none;padding:0;margin:1rem 0 1.5rem';
  const li = document.createElement('li');
  li.style.cssText = "padding:.7rem 0;border-bottom:1px solid rgba(26,22,16,.1);font-family:'Jost',sans-serif;font-weight:300";
  li.innerHTML = '<strong style="color:var(--ink)">Term</strong> &#8212; Description';
  ul.appendChild(li);
  const sel = window.getSelection();
  if (sel && sel.rangeCount) {
    const range = sel.getRangeAt(0);
    range.deleteContents();
    range.insertNode(ul);
    const newRange = document.createRange();
    newRange.selectNodeContents(li);
    newRange.collapse(false);
    sel.removeAllRanges();
    sel.addRange(newRange);
  } else {
    editor.appendChild(ul);
  }
}

async function insertImage() {
  const editor = document.getElementById('editor');
  const sel = window.getSelection();
  _imgSavedRange = sel.rangeCount ? sel.getRangeAt(0).cloneRange() : null;
  await ensureFolderHandle();
  try {
    const [imgHandle] = await window.showOpenFilePicker({
      types: [{ description: 'Images', accept: {
        'image/jpeg': ['.jpg', '.jpeg', '.JPG', '.JPEG'],
        'image/png':  ['.png', '.PNG'],
        'image/webp': ['.webp', '.WEBP'],
        'image/gif':  ['.gif', '.GIF'],
      }}],
      multiple: false
    });
    const file = await imgHandle.getFile();
    if (_imgBlobUrl) URL.revokeObjectURL(_imgBlobUrl);
    _imgBlobUrl = URL.createObjectURL(file);

    let suggestedPath = '';
    if (folderHandle) {
      try {
        const parts = await folderHandle.resolve(imgHandle);
        if (parts) suggestedPath = parts.join('/');
      } catch(e) {}
    }
    if (!suggestedPath) suggestedPath = 'Images/' + imgHandle.name;

    const preview = document.getElementById('img-modal-preview');
    preview.src = _imgBlobUrl;
    preview.style.display = 'block';
    document.getElementById('img-path-input').value = suggestedPath;
    document.getElementById('img-alt-input').value = '';
    document.getElementById('img-modal-backdrop').classList.add('open');
    document.getElementById('img-alt-input').focus();
  } catch(e) {
    if (e.name !== 'AbortError') alert('Error: ' + e.message);
  }
}

function confirmInsertImage() {
  const src = document.getElementById('img-path-input').value.trim();
  const alt = document.getElementById('img-alt-input').value.trim();
  if (!src) { document.getElementById('img-path-input').focus(); return; }
  const displaySrc = _imgBlobUrl || src;
  _imgBlobUrl = null;
  document.getElementById('img-modal-backdrop').classList.remove('open');
  document.getElementById('img-modal-preview').style.display = 'none';
  const imgHTML = '<img src="' + displaySrc + '" data-path="' + src + '" alt="' + alt + '" style="display:block;max-width:100%;height:auto;aspect-ratio:auto;border-radius:2px;margin:1rem auto">';
  const editor = document.getElementById('editor');
  editor.focus();
  const sel = window.getSelection();
  if (_imgSavedRange) { sel.removeAllRanges(); sel.addRange(_imgSavedRange); }
  document.execCommand('insertHTML', false, imgHTML);
  setDirty(true);
}

function closeImgModal() {
  document.getElementById('img-modal-backdrop').classList.remove('open');
  if (_imgBlobUrl) { URL.revokeObjectURL(_imgBlobUrl); _imgBlobUrl = null; }
  document.getElementById('img-modal-preview').style.display = 'none';
}

document.getElementById('img-modal-backdrop').addEventListener('click', function(e) {
  if (e.target === this) closeImgModal();
});

// ---- Image selection + floating toolbar ----
let _selImg = null;

document.getElementById('editor').addEventListener('click', function(e) {
  if (e.target.tagName === 'IMG') {
    e.preventDefault();
    selectImg(e.target);
  } else {
    deselectImg();
  }
}, true);

document.addEventListener('click', function(e) {
  const tb = document.getElementById('img-floating-toolbar');
  const rh = document.getElementById('img-resize-handle');
  if (tb && !tb.contains(e.target) && rh && !rh.contains(e.target) && e.target.tagName !== 'IMG') {
    deselectImg();
  }
});

function selectImg(img) {
  if (_selImg && _selImg !== img) _selImg.classList.remove('img-selected');
  _selImg = img;
  img.classList.add('img-selected');
  updateImgUI();
  document.getElementById('img-width-input').value = img.style.width || img.getAttribute('width') || '';
}

function deselectImg() {
  if (_selImg) _selImg.classList.remove('img-selected');
  _selImg = null;
  document.getElementById('img-floating-toolbar').style.display = 'none';
  document.getElementById('img-resize-handle').style.display = 'none';
}

function updateImgUI() {
  if (!_selImg) return;
  const rect = _selImg.getBoundingClientRect();
  const tb = document.getElementById('img-floating-toolbar');
  tb.style.display = 'flex';
  const tbH = tb.offsetHeight || 32;
  tb.style.top = Math.max(4, rect.top - tbH - 6) + 'px';
  tb.style.left = Math.max(4, Math.min(rect.left, window.innerWidth - tb.offsetWidth - 4)) + 'px';
  const rh = document.getElementById('img-resize-handle');
  rh.style.display = 'block';
  rh.style.left = (rect.right - 6) + 'px';
  rh.style.top  = (rect.bottom - 6) + 'px';
}

document.querySelector('.editor-scroll').addEventListener('scroll', () => { if (_selImg) updateImgUI(); });
window.addEventListener('resize', () => { if (_selImg) updateImgUI(); });

function imgAlign(align) {
  if (!_selImg) return;
  _selImg.style.float = '';
  _selImg.style.display = 'block';
  _selImg.style.marginLeft = '0';
  _selImg.style.marginRight = '0';
  if (align === 'center') { _selImg.style.marginLeft = 'auto'; _selImg.style.marginRight = 'auto'; }
  else if (align === 'right') { _selImg.style.float = 'right'; _selImg.style.marginLeft = '1rem'; }
  else if (align === 'left')  { _selImg.style.float = 'left';  _selImg.style.marginRight = '1rem'; }
  setTimeout(updateImgUI, 30);
  setDirty(true);
}

function imgSetWidth(w) {
  if (!_selImg) return;
  _selImg.style.width = w;
  _selImg.style.maxWidth = '100%';
  _selImg.style.height = 'auto';
  document.getElementById('img-width-input').value = w;
  setTimeout(updateImgUI, 30);
  setDirty(true);
}

document.getElementById('img-width-input').addEventListener('change', function() {
  if (_selImg && this.value) imgSetWidth(this.value);
});

function imgDelete() {
  if (!_selImg) return;
  _selImg.remove();
  deselectImg();
  setDirty(true);
}

function imgPan() {
  if (!_selImg) return;
  _selImg.style.aspectRatio = '';
  _selImg.style.objectFit = 'cover';
  _selImg.style.objectPosition = _selImg.style.objectPosition || 'center';
  _selImg.classList.add('img-cropped');
  setTimeout(updateImgUI, 30);
  setDirty(true);
}

function applyCanvasCrop(img) {
  const ratio = img.dataset.crop;
  if (!ratio) return;
  const [rw, rh] = ratio.split('/').map(Number);
  const targetRatio = rw / rh;
  const posX = parseFloat(img.dataset.cropX || '0.5');
  const posY = parseFloat(img.dataset.cropY || '0.5');
  const src = img.dataset.originalSrc || img.src;
  fetch(src)
    .then(r => r.blob())
    .then(blob => createImageBitmap(blob))
    .then(bitmap => {
      const nw = bitmap.width, nh = bitmap.height;
      if (!nw || !nh) return;
      const naturalRatio = nw / nh;
      let cropW, cropH, cropX, cropY;
      if (naturalRatio > targetRatio) {
        cropH = nh; cropW = nh * targetRatio;
        cropX = (nw - cropW) * posX; cropY = 0;
      } else {
        cropW = nw; cropH = nw / targetRatio;
        cropX = 0; cropY = (nh - cropH) * posY;
      }
      const canvas = document.createElement('canvas');
      canvas.width = Math.round(cropW); canvas.height = Math.round(cropH);
      canvas.getContext('2d').drawImage(bitmap, cropX, cropY, cropW, cropH, 0, 0, canvas.width, canvas.height);
      bitmap.close();
      canvas.toBlob(blob => {
        const oldSrc = img.src;
        img.src = URL.createObjectURL(blob);
        if (oldSrc && oldSrc.startsWith('blob:') && oldSrc !== img.dataset.originalSrc) URL.revokeObjectURL(oldSrc);
        setTimeout(updateImgUI, 30);
      }, 'image/jpeg', 0.95);
    })
    .catch(() => {
      const srcImg = new Image();
      srcImg.onload = () => {
        const nw = srcImg.naturalWidth, nh = srcImg.naturalHeight;
        if (!nw || !nh) return;
        const naturalRatio = nw / nh;
        let cropW, cropH, cropX, cropY;
        if (naturalRatio > targetRatio) {
          cropH = nh; cropW = nh * targetRatio;
          cropX = (nw - cropW) * posX; cropY = 0;
        } else {
          cropW = nw; cropH = nw / targetRatio;
          cropX = 0; cropY = (nh - cropH) * posY;
        }
        const canvas = document.createElement('canvas');
        canvas.width = Math.round(cropW); canvas.height = Math.round(cropH);
        canvas.getContext('2d').drawImage(srcImg, cropX, cropY, cropW, cropH, 0, 0, canvas.width, canvas.height);
        canvas.toBlob(blob => {
          const oldSrc = img.src;
          img.src = URL.createObjectURL(blob);
          if (oldSrc && oldSrc.startsWith('blob:') && oldSrc !== img.dataset.originalSrc) URL.revokeObjectURL(oldSrc);
          setTimeout(updateImgUI, 30);
        }, 'image/jpeg', 0.95);
      };
      srcImg.src = src;
    });
}

function imgCrop(ratio) {
  if (!_selImg) return;
  if (ratio === 'none') {
    const prev = _selImg.src;
    if (_selImg.dataset.originalSrc) {
      _selImg.src = _selImg.dataset.originalSrc;
      if (prev !== _selImg.dataset.originalSrc && prev.startsWith('blob:')) URL.revokeObjectURL(prev);
    }
    _selImg.style.aspectRatio = '';
    _selImg.style.objectFit = '';
    _selImg.style.objectPosition = '';
    delete _selImg.dataset.crop;
    delete _selImg.dataset.cropX;
    delete _selImg.dataset.cropY;
    delete _selImg.dataset.originalSrc;
    _selImg.classList.remove('img-cropped');
  } else {
    if (!_selImg.dataset.originalSrc) _selImg.dataset.originalSrc = _selImg.src;
    _selImg.dataset.crop = ratio;
    _selImg.dataset.cropX = _selImg.dataset.cropX || '0.5';
    _selImg.dataset.cropY = _selImg.dataset.cropY || '0.5';
    _selImg.style.aspectRatio = '';
    _selImg.style.objectFit = '';
    _selImg.style.objectPosition = '';
    applyCanvasCrop(_selImg);
    _selImg.classList.add('img-cropped');
  }
  setDirty(true);
}

// Drag-to-resize
document.getElementById('img-resize-handle').addEventListener('mousedown', function(e) {
  if (!_selImg) return;
  e.preventDefault();
  const startX = e.clientX;
  const startW = _selImg.getBoundingClientRect().width;
  function onMove(e) {
    const newW = Math.max(40, startW + (e.clientX - startX));
    _selImg.style.width = newW + 'px';
    _selImg.style.maxWidth = '100%';
    _selImg.style.height = 'auto';
    document.getElementById('img-width-input').value = Math.round(newW) + 'px';
    updateImgUI();
  }
  function onUp() {
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
    setDirty(true);
  }
  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onUp);
});

// Drag-to-pan
let _panImg = null, _panStart = null, _panStartPos = null;

document.getElementById('editor').addEventListener('mousedown', function(e) {
  if (e.target.tagName !== 'IMG') return;
  const img = e.target;
  if (!img.classList.contains('img-cropped')) return;
  e.preventDefault();
  _panImg = img;
  img.classList.add('img-panning');
  _panStart = { x: e.clientX, y: e.clientY };
  if (img.dataset.crop) {
    const posX = parseFloat(img.dataset.cropX || '0.5') * 100;
    const posY = parseFloat(img.dataset.cropY || '0.5') * 100;
    const prevSrc = img.src;
    img.src = img.dataset.originalSrc;
    if (prevSrc !== img.dataset.originalSrc && prevSrc.startsWith('blob:')) URL.revokeObjectURL(prevSrc);
    img.style.aspectRatio = img.dataset.crop;
    img.style.objectFit = 'cover';
    img.style.objectPosition = posX + '% ' + posY + '%';
    _panStartPos = { x: posX, y: posY };
  } else {
    const pos = (img.style.objectPosition || '50% 50%').split(' ');
    _panStartPos = { x: parseFloat(pos[0]) || 50, y: parseFloat(pos[1]) || 50 };
  }
  document.addEventListener('mousemove', onPanMove);
  document.addEventListener('mouseup', onPanUp);
});

function onPanMove(e) {
  if (!_panImg) return;
  const rect = _panImg.getBoundingClientRect();
  const nw = _panImg.naturalWidth, nh = _panImg.naturalHeight;
  if (!nw || !nh) return;
  const scale = Math.max(rect.width / nw, rect.height / nh);
  const overflowX = nw * scale - rect.width;
  const overflowY = nh * scale - rect.height;
  const dx = e.clientX - _panStart.x;
  const dy = e.clientY - _panStart.y;
  const nx = overflowX > 0 ? Math.max(0, Math.min(100, _panStartPos.x - dx / overflowX * 100)) : 50;
  const ny = overflowY > 0 ? Math.max(0, Math.min(100, _panStartPos.y - dy / overflowY * 100)) : 50;
  _panImg.style.objectPosition = nx.toFixed(1) + '% ' + ny.toFixed(1) + '%';
}

function onPanUp() {
  if (_panImg) {
    _panImg.classList.remove('img-panning');
    if (_panImg.dataset.crop) {
      const pos = (_panImg.style.objectPosition || '50% 50%').split(' ');
      _panImg.dataset.cropX = (parseFloat(pos[0]) / 100).toFixed(3);
      _panImg.dataset.cropY = (parseFloat(pos[1]) / 100).toFixed(3);
      _panImg.style.aspectRatio = '';
      _panImg.style.objectFit = '';
      _panImg.style.objectPosition = '';
      applyCanvasCrop(_panImg);
    }
    setDirty(true);
  }
  _panImg = null; _panStart = null; _panStartPos = null;
  document.removeEventListener('mousemove', onPanMove);
  document.removeEventListener('mouseup', onPanUp);
}

// ---- Color picker ----
const THEME_COLORS = [
  { hex: '#1C2821', name: 'Ink' },
  { hex: '#555E59', name: 'Body text' },
  { hex: '#2D6B50', name: 'Terra (links)' },
  { hex: '#5E9A78', name: 'Green mid' },
  { hex: '#7ED4A8', name: 'Light green' },
  { hex: '#EDF0EC', name: 'Mist' },
  { hex: '#F2C124', name: 'Gold' },
];
const COLORS = [
  '#000000','#1C2821','#374151','#6B7280','#9CA3AF','#D1D5DB','#ffffff',
  '#e63946','#c1121f','#9b2226','#e76f51','#f4a261','#e9c46a','#f1faee',
  '#2D6B50','#40916c','#52b788','#74c69d','#b7e4c7','#d8f3dc','#95d5b2',
  '#023e8a','#0077b6','#0096c7','#00b4d8','#48cae4','#90e0ef','#caf0f8',
  '#7b2d8b','#9d4edd','#c77dff','#e0aaff','#f72585','#b5179e','#7209b7',
];

const themeGrid = document.getElementById('color-theme-grid');
THEME_COLORS.forEach(c => {
  const dot = document.createElement('div');
  dot.className = 'color-dot';
  dot.style.background = c.hex;
  dot.style.border = '1.5px solid rgba(0,0,0,.12)';
  dot.title = c.name + ' ' + c.hex;
  dot.onclick = () => applyColor(c.hex);
  themeGrid.appendChild(dot);
});

const colorGrid = document.getElementById('color-swatches-grid');
COLORS.forEach(c => {
  const dot = document.createElement('div');
  dot.className = 'color-dot';
  dot.style.background = c;
  if (c === '#ffffff') dot.style.border = '1.5px solid #d1d5db';
  dot.title = c;
  dot.onclick = () => applyColor(c);
  colorGrid.appendChild(dot);
});

document.getElementById('color-input').addEventListener('input', function() {
  applyColor(this.value);
});

function applyColor(color) {
  document.getElementById('color-swatch-bar').style.background = color;
  document.getElementById('color-input').value = color;
  document.getElementById('editor').focus();
  document.execCommand('foreColor', false, color);
  closeColorPicker();
}
function toggleColorPicker() {
  document.getElementById('color-dropdown').classList.toggle('open');
}
function closeColorPicker() {
  document.getElementById('color-dropdown').classList.remove('open');
}
document.addEventListener('click', function(e) {
  if (!document.getElementById('color-picker-wrap').contains(e.target)) closeColorPicker();
});

// =============================================================================
// INIT
// =============================================================================

loadArticles();
</script>
</body>
</html>"""


if __name__ == "__main__":
    print(f"\nGetawayguide CMS")
    print(f"Open http://localhost:5001 in your browser\n")
    app.run(debug=False, port=5001)
