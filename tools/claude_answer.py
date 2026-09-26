#!/usr/bin/env python3
"""Answer a comment Kevin sent to Claude from the editor's review margin, as a LIVE edit.

A thread with kind "task" (or "rewrite") is an instruction on the article: rewrite this
section, look up this date, link this place. The answer is written back onto the thread as
    edit   {find, replace}   an exact substring of the article's HTML and its replacement
    reply  what was done, with the source for anything looked up
    status "answered"        the editor polls the thread and applies the edit in the open
                             text at once (Kevin, 2026-09-26: "edit in the live file while I'm
                             still editing instead of doing rounds of passes")
If the find no longer matches when the editor gets there, the editor marks it "failed" and
shows the replacement on the card with a Use button, so nothing is lost.

Two ways to produce the answer:
    python tools/claude_answer.py <key> <thread_id> --reply "..." --find-file f.html --replace-file r.html
        the session (me) answering by hand, after `python tools/redline.py tasks`
    python tools/claude_answer.py <key> <thread_id> --ask
        the photo server calls this when ANTHROPIC_API_KEY is in .env: the article, the
        thread and the voice guide go to the Claude API and the JSON answer is validated
        (the find must occur exactly once in the article) before it is written
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import redline  # noqa: E402

GUIDE = Path.home() / ".claude/projects/c--Users-kevin-OneDrive-Documents-Travel-Blog/memory/user_writing_style.md"
PATTERNS = Path.home() / ".claude/projects/c--Users-kevin-OneDrive-Documents-Travel-Blog/memory/feedback_draft_from_review_patterns.md"


def article_for(key):
    """'armenia__orgov-observatory' -> the draft or live file"""
    country, stem = key.split("__", 1)
    for p in (ROOT / "Drafts" / ".Full Articles" / country / (stem + ".html"),
              ROOT / "Drafts" / country / (stem + ".html"), ROOT / country / (stem + ".html")):
        if p.exists():
            return p
    raise SystemExit("no article for " + key)


def env_key(name):
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    import os
    return os.environ.get(name, "")


def write_answer(key, tid, reply, find=None, replace=None, status="answered", extra=None):
    data = redline.comments_load(key)
    for t in data.get("threads", []):
        if t["id"] == tid:
            t.setdefault("replies", []).append({"author": "Claude", "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                                "id": "re" + format(int(time.time() * 1000), "x"), "text": reply})
            t["status"] = status
            if find is not None and replace is not None:
                t["edit"] = {"find": find, "replace": replace}
            if extra:
                t.update(extra)
            redline.comments_save(key, data)
            return t
    raise SystemExit("no thread %s in %s" % (tid, key))


def body_html(html):
    i = html.find('<div class="article-body')
    j = html.find("<footer", i)
    return html[i:j] if i >= 0 and j > i else html


def ask_claude(key, tid):
    api = env_key("ANTHROPIC_API_KEY")
    if not api:
        raise SystemExit("no ANTHROPIC_API_KEY in .env")
    data = redline.comments_load(key)
    t = next((x for x in data.get("threads", []) if x["id"] == tid), None)
    if not t:
        raise SystemExit("no thread")
    path = article_for(key)
    html = path.read_text(encoding="utf-8")
    body = body_html(html)
    guide = GUIDE.read_text(encoding="utf-8", errors="replace") if GUIDE.exists() else ""
    patterns = PATTERNS.read_text(encoding="utf-8", errors="replace") if PATTERNS.exists() else ""
    a = t.get("anchor") or {}
    prompt = (
        "You are editing Kevin's travel-blog article in place. He left an instruction on a passage; carry it out.\n"
        "Rules: first person, direct, specific, American spelling, no em dashes in prose, keep every existing link, "
        "link every place name to Google Maps (https://www.google.com/maps/search/?api=1&query=Name works as a "
        "placeholder), never invent facts: a date, price or name you are not sure of stays out and you say so in the reply. "
        "For a lookup, put the answer in the text and the source in the reply.\n"
        "Return ONLY JSON: {\"reply\": \"what you did, one or two sentences\", \"find\": \"an exact substring of the ARTICLE HTML "
        "below, covering the whole passage to change (include the tags inside it exactly as they are)\", "
        "\"replace\": \"the new HTML for that passage\"}. The find must occur exactly once.\n\n"
        "VOICE GUIDE:\n" + guide[:7000] + "\n\nWHAT HIS REVIEWS ASK FOR:\n" + patterns[:3000] + "\n\n"
        "INSTRUCTION: " + t.get("text", "") + "\nON THE PASSAGE: " + a.get("quote", "") +
        "\n(context before: " + a.get("before", "") + " | after: " + a.get("after", "") + ")\n\n"
        "ARTICLE HTML:\n" + body[:60000])
    req = ROOT / ".tmp" / ("claude_task_%s.json" % tid)
    req.write_text(json.dumps({"model": "claude-sonnet-5", "max_tokens": 4000,
                               "messages": [{"role": "user", "content": prompt}]}), encoding="utf-8")
    # Python's TLS is broken on this box; PowerShell's is not (see the memory note)
    ps = ("$b = Get-Content -Raw -Encoding UTF8 '%s'; "
          "$r = Invoke-RestMethod -Uri 'https://api.anthropic.com/v1/messages' -Method Post -TimeoutSec 120 "
          "-Headers @{'x-api-key'='%s';'anthropic-version'='2023-06-01';'content-type'='application/json'} -Body $b; "
          "$r.content[0].text" % (str(req).replace("'", "''"), api))
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return write_answer(key, tid, "I couldn't reach Claude: " + (r.stderr or r.stdout).strip()[-200:], status="failed")
    txt = r.stdout.strip()
    m = re.search(r"\{.*\}", txt, re.S)
    try:
        ans = json.loads(m.group(0) if m else txt)
    except Exception:
        return write_answer(key, tid, "The answer wasn't valid JSON; nothing applied. Raw: " + txt[:300], status="failed")
    find, rep, reply = ans.get("find") or "", ans.get("replace") or "", ans.get("reply") or "Done."
    if not find or html.count(find) != 1:
        return write_answer(key, tid, reply + " (The passage to replace didn't match the article exactly, so it is offered "
                            "on the card instead of applied.)", find=a.get("quote", ""), replace=rep, status="answered")
    return write_answer(key, tid, reply, find=find, replace=rep)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("key"); ap.add_argument("thread")
    ap.add_argument("--reply", default=""); ap.add_argument("--find-file"); ap.add_argument("--replace-file")
    ap.add_argument("--ask", action="store_true", help="ask the Claude API (needs ANTHROPIC_API_KEY in .env)")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if a.ask:
        t = ask_claude(a.key, a.thread)
    else:
        f = Path(a.find_file).read_text(encoding="utf-8") if a.find_file else None
        r = Path(a.replace_file).read_text(encoding="utf-8") if a.replace_file else None
        if f is not None:
            html = article_for(a.key).read_text(encoding="utf-8")
            if html.count(f) != 1:
                print("warning: find occurs %d times in the file on disk (the editor matches its own copy)" % html.count(f))
        t = write_answer(a.key, a.thread, a.reply or "Done.", f, r)
    print("answered %s: status %s%s" % (t["id"], t.get("status"), ", edit attached" if t.get("edit") else ""))
