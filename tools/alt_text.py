#!/usr/bin/env python3
"""Write alt text for the photos on the site.

    python tools/alt_text.py --audit                    what's missing, empty, or a bare filename
    python tools/alt_text.py --dry-run                  generate for those, show old -> new, write nothing
    python tools/alt_text.py --write                    ...and write it into the pages
    python tools/alt_text.py --write --all              regenerate EVERY photo's alt, not just the weak ones
    python tools/alt_text.py --write --only kosovo/field-notes.html
    python tools/alt_text.py --export .tmp/alt_text/brief.json    hand the briefs (no API) to a person / Claude Code
    python tools/alt_text.py --apply  .tmp/alt_text/brief.json    write alts filled into that file

Engines (--engine):
  claude    default when ANTHROPIC_API_KEY is set (.env or the environment). The
            photo itself, downsized, goes to Claude (claude-sonnet-5, vision)
            with a brief of where it sits on the page, and the alt describes
            what is actually in the picture.
  context   no API. Composes the alt from the page alone: the bullet lead or
            caption, the place the photo was taken (GPS via tools/photo_geo.py),
            and the country. Honest but generic ("Prizren Fortress, Prizren, Kosovo").

House style, learned from the alts already on the site and the writing rules:
  "<what is in the picture>, <place>, <country>" in sentence case, one line,
  under 125 characters, no trailing period, no "image of / photo of", no em
  dashes, American spelling, real place names from the page. Logos on the
  resources page are just "<Name> logo".

Only the alt attribute is touched; the rest of the <img> tag and the page are
byte-for-byte unchanged. Every run writes a log to .tmp/alt_text/ with old and
new text per image, and API results are cached there so a re-run is free.
"""
import argparse
import base64
import glob
import hashlib
import html
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
OUT = ROOT / ".tmp" / "alt_text"
OUT.mkdir(parents=True, exist_ok=True)
CACHE = OUT / "cache.json"
MODEL = "claude-sonnet-5"
MAX_LEN = 125

SKIP_SRC = ("flags/", "continents/", "/icons/", "city-maps", "nav-map", "map-card", "data:")
NON_PHOTO = ("/logos/",)


# ------------------------------------------------------------------ pages ----
def pages(only=None):
    files = [f for f in glob.glob(str(ROOT / "**" / "*.html"), recursive=True)]
    out = []
    for f in files:
        rel = Path(f).relative_to(ROOT).as_posix()
        if rel.startswith(("archive/", ".tmp/", "node_modules/")) or rel == "editor.html":
            continue
        if only and not any(rel == o or rel.endswith("/" + o) or o in rel for o in only):
            continue
        out.append(rel)
    return sorted(out)


def country_of(rel):
    parts = Path(rel).parts
    if rel.startswith("Drafts/.Full Articles/"):
        slug = parts[2]
    elif rel.startswith("Drafts/"):
        slug = parts[1]
    elif len(parts) > 1:
        slug = parts[0]
    else:
        return ""
    return slug.replace("-", " ").title().replace("El Salvador", "El Salvador").replace("Of ", "of ")


def text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def alt_class(alt):
    """missing / empty / weak (a bare filename, a number, one short word) / ok"""
    if alt is None:
        return "missing"
    a = alt.strip()
    if not a:
        return "empty"
    if re.fullmatch(r"(photo|image|picture|img|IMG[_ -]?\d+.*|DSC.*|\d+|.*\.(jpe?g|png|webp))", a, re.I):
        return "weak"
    if len(a) < 12 and not re.search(r"\S+ logo$", a, re.I):   # "Notion logo" is fine
        return "weak"
    return "ok"


def find_images(rel):
    """Every content <img> on a page with the context around it."""
    s = (ROOT / rel).read_text(encoding="utf-8")
    title = re.search(r"<title>(.*?)</title>", s, re.S)
    title = text(title.group(1)).replace(" - getawayguide", "").replace(" - Getawayguide", "") if title else rel
    country = country_of(rel)
    # strip the chrome so "nearest heading" means the article's, not the nav's
    spans = [(m.start(), m.end()) for m in re.finditer(r"<(nav|head|script|style|footer)\b.*?</\1>", s, re.S)]
    def in_chrome(i):
        return any(a <= i < b for a, b in spans)
    items = []
    for m in re.finditer(r"<img\b[^>]*>", s):
        if in_chrome(m.start()):
            continue
        tag = m.group(0)
        src = re.search(r'\ssrc="([^"]*)"', tag)
        if not src:
            continue
        src = src.group(1)
        if any(k in src for k in SKIP_SRC) or 'aria-hidden="true"' in tag or 'role="presentation"' in tag:
            continue
        alt = re.search(r'\salt="([^"]*)"', tag)
        alt = html.unescape(alt.group(1)) if alt else None
        before_html = s[max(0, m.start() - 2500):m.start()]
        after_html = s[m.end():m.end() + 1500]
        heads = re.findall(r"<(?:h[1-4]|div class=\"d-t[^\"]*\"|div class=\"serif h-a\")[^>]*>(.*?)</", before_html, re.S)
        heading = text(heads[-1]) if heads else ""
        leads = re.findall(r"<li>(?:<a[^>]*>)?<b>(.*?)</b>", before_html, re.S)
        lead = text(leads[-1]) if leads else ""
        cap = re.search(r"<figcaption[^>]*>(.*?)</figcaption>", after_html, re.S)
        caption = text(cap.group(1)) if cap else ""
        before = text(re.sub(r"<img\b[^>]*>|<source\b[^>]*>|<picture>|</picture>", " ", before_html))[-300:]
        after = text(re.sub(r"<img\b[^>]*>|<source\b[^>]*>|<picture>|</picture>", " ", after_html))[:220]
        stem = re.sub(r"-(mob-)?\d?x$|-mob$", "", Path(urllib.parse.unquote(src)).stem)
        # the first named thing after the image: a resources row name, a hero
        # slide title, or a bold lead in the paragraph the photo introduces
        nm = re.search(r'<(?:div|span) class="(?:rrow-name|serif hero-h|hero-h)"[^>]*>(.*?)</(?:div|span)>|<b>(.*?)</b>', after_html, re.S)
        name_after = text(nm.group(1) or nm.group(2)) if nm else ""
        items.append({"page": rel, "title": title, "country": country, "src": src, "alt": alt, "name_after": name_after,
                      "cls": alt_class(alt), "heading": heading, "lead": lead, "caption": caption,
                      "before": before, "after": after, "stem": stem, "start": m.start(), "end": m.end(),
                      "logo": any(k in src for k in NON_PHOTO)})
    return items


# --------------------------------------------------------------- location ----
_orig_index = None


def original_for(stem):
    """The full-res original under Images/<Country>/ for a served variant, if any."""
    global _orig_index
    if _orig_index is None:
        _orig_index = {}
        for p in (ROOT / "Images").rglob("*"):
            if p.is_file() and "web" not in p.parts and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".heic"):
                _orig_index.setdefault(p.stem.lower(), p)
    return _orig_index.get(stem.lower())


def place_of(item):
    """'Khor Virap, near Yerevan' from the original's GPS, via the site's place index."""
    o = original_for(item["stem"])
    if not o:
        return ""
    try:
        import photo_geo
        geo, _ = photo_geo.locate([o])
        g = geo.get(o.name)
        if not g or g["lat"] is None:
            return ""
        place, city = photo_geo.assign(g["lat"], g["lon"])
        if place.startswith(("Unplaced",)):
            return ""
        return place if place.startswith(("Elsewhere", "Near")) else f"{place}, {city}" if city and city != "Other" else place
    except Exception:
        return ""


# ------------------------------------------------------------------ engines ----
def load_env():
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return os.environ.get("ANTHROPIC_API_KEY")


def tidy(alt):
    a = text(alt or "")
    a = re.sub(r"^(an?\s+)?(image|photo|photograph|picture)\s+(of|showing)\s+", "", a, flags=re.I)
    a = a.replace("—", ",").replace(" ,", ",").replace(",,", ",")
    a = a.strip(" .")
    if len(a) > MAX_LEN:
        cut = a[:MAX_LEN]
        a = cut[:cut.rfind(",")] if "," in cut[60:] else cut.rsplit(" ", 1)[0]
    return a[:1].upper() + a[1:] if a else a


def context_alt(item):
    if item["logo"]:
        # the resources page names each tool right after its tile
        return tidy(f"{item['name_after'] or item['stem'].replace('-', ' ').title()} logo")
    stem = item["stem"]
    if re.search(r"kevin", stem, re.I):
        return "Kevin, the author of getawayguide"
    # a hero slide / stock frame named h2.jpg or hero-nz-4256: the slide's own
    # title says what it is for; the filename says nothing
    codey = re.match(r"^(h\d+|hero(-\w+)*|img[_ -]?\d+.*|dsc.*|\d+)$", stem, re.I)
    subject = item["caption"] or item["lead"] or ("" if codey else re.sub(r"\s+\d+$", "", stem)) \
        or item["name_after"] or item["heading"]
    place = place_of(item)
    parts = [p for p in (subject, place, item["country"]) if p]
    seen, uniq = set(), []
    for p in parts:                       # "Prizren Fortress, Prizren Fortress, Prizren" -> once
        for q in [x.strip() for x in p.split(",")]:
            if q and q.lower() not in seen:
                seen.add(q.lower()); uniq.append(q)
    return tidy(", ".join(uniq))


def image_b64(item, max_px=1024):
    p = (ROOT / item["page"]).parent / urllib.parse.unquote(item["src"])
    p = p.resolve()
    if not p.exists():
        return None, None
    im = ImageOps.exif_transpose(Image.open(p)).convert("RGB")
    im.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii"), p


STYLE = ("Write the alt text for this photo on a personal travel blog. One line, under 125 characters, "
         "sentence case, no trailing period, no 'image of' or 'photo of', no em dashes, American spelling. "
         "Describe what is actually in the picture (the subject, then where it is), using the real place "
         "names from the context when they fit: the pattern on this site is "
         "'<what is in the picture>, <place>, <country>' e.g. 'Sevanavank Monastery above Lake Sevan, Armenia' "
         "or 'Pupusas on a griddle at Casa Coco, Santa Ana, El Salvador'. Do not repeat the caption word for word, "
         "do not guess names of people, and do not invent a place the context does not support. "
         "Reply with JSON only: {\"alt\": \"...\"}")


def claude_alt(item, key, cache):
    b64, path = image_b64(item)
    if not b64:
        return None, "file not found"
    ck = hashlib.sha1((str(path) + str(path.stat().st_mtime_ns) + item["heading"] + item["lead"] + item["caption"]).encode()).hexdigest()
    if ck in cache:
        return cache[ck], "cached"
    brief = {k: item[k] for k in ("title", "country", "heading", "lead", "caption", "before", "after") if item[k]}
    place = place_of(item)
    if place:
        brief["gps_place"] = place
    if item["alt"]:
        brief["current_alt"] = item["alt"]
    body = {
        "model": MODEL, "max_tokens": 120,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
            {"type": "text", "text": STYLE + "\n\nContext:\n" + json.dumps(brief, ensure_ascii=False, indent=1)},
        ]}],
    }
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json", "x-api-key": key,
                                          "anthropic-version": "2023-06-01"}, method="POST")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.loads(r.read())
            txt = "".join(c.get("text", "") for c in d.get("content", []))
            m = re.search(r'\{.*?"alt"\s*:\s*"(.*?)"\s*\}', txt, re.S)
            alt = tidy(m.group(1) if m else txt)
            if alt:
                cache[ck] = alt
                return alt, "claude"
            return None, "empty reply"
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:200]
            if e.code in (401, 403):
                return None, f"auth error {e.code}: {msg}"
            if e.code == 429 or e.code >= 500:
                time.sleep(2 * (attempt + 1)); continue
            return None, f"HTTP {e.code}: {msg}"
        except Exception as e:
            time.sleep(1 + attempt)
            err = f"{type(e).__name__}: {e}"
    return None, err


# -------------------------------------------------------------------- write ----
def write_alts(changes):
    """changes: list of items with 'new'. Rewrites only the alt attribute of each tag."""
    by_page = {}
    for it in changes:
        by_page.setdefault(it["page"], []).append(it)
    for rel, items in by_page.items():
        p = ROOT / rel
        s = p.read_text(encoding="utf-8")
        for it in sorted(items, key=lambda x: -x["start"]):        # from the end, offsets stay valid
            tag = s[it["start"]:it["end"]]
            assert tag.startswith("<img"), (rel, it["start"])
            esc = html.escape(it["new"], quote=True)
            if re.search(r'\salt="[^"]*"', tag):
                new_tag = re.sub(r'\salt="[^"]*"', f' alt="{esc}"', tag, count=1)
            else:
                new_tag = "<img alt=\"" + esc + "\"" + tag[4:]
            s = s[:it["start"]] + new_tag + s[it["end"]:]
        p.write_text(s, encoding="utf-8")
    return len(by_page)


# --------------------------------------------------------------------- main ----
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--all", action="store_true", help="regenerate every alt, not only missing/empty/weak")
    ap.add_argument("--only", nargs="*", help="page path(s) or fragments to limit to")
    ap.add_argument("--engine", choices=["claude", "context"])
    ap.add_argument("--export", metavar="JSON", help="write the briefs out for someone else to fill")
    ap.add_argument("--apply", metavar="JSON", help="write alts from a filled brief file")
    a = ap.parse_args()

    items = [it for rel in pages(a.only) for it in find_images(rel)]
    if a.audit or not (a.dry_run or a.write or a.export or a.apply):
        from collections import Counter
        per = {}
        for it in items:
            per.setdefault(it["page"], Counter())[it["cls"]] += 1
        print(f"{'page':48} {'imgs':>4} {'ok':>4} {'weak':>4} {'empty':>5} {'missing':>7}")
        for rel, c in sorted(per.items(), key=lambda kv: -(kv[1]['weak'] + kv[1]['empty'] + kv[1]['missing'])):
            print(f"{rel:48} {sum(c.values()):>4} {c['ok']:>4} {c['weak']:>4} {c['empty']:>5} {c['missing']:>7}")
        tot = Counter(it["cls"] for it in items)
        print(f"\n{len(items)} content images on {len(per)} pages: {dict(tot)}")
        if not a.audit:
            print("\nRun with --dry-run to generate, --write to apply. See --help.")
        return

    if a.apply:
        filled = json.loads(Path(a.apply).read_text(encoding="utf-8"))
        key = {(f["page"], f["src"]): f.get("new") for f in filled if f.get("new")}
        changes = []
        for it in items:
            new = key.get((it["page"], it["src"]))
            if new and tidy(new) != (it["alt"] or ""):
                it["new"] = tidy(new); changes.append(it)
        n = write_alts(changes)
        print(f"applied {len(changes)} alts on {n} pages")
        return

    todo = [it for it in items if a.all or it["cls"] != "ok"]
    if a.export:
        for it in todo:
            it["gps_place"] = place_of(it)
            it["suggested"] = context_alt(it)
        Path(a.export).write_text(json.dumps([{k: v for k, v in it.items() if k not in ("start", "end")} for it in todo],
                                             ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"exported {len(todo)} briefs to {a.export}  (fill 'new' on each, then --apply)")
        return

    key = load_env()
    engine = a.engine or ("claude" if key else "context")
    if engine == "claude" and not key:
        sys.exit("ANTHROPIC_API_KEY is not set: add it to .env (the file is gitignored) or use --engine context")
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}

    print(f"{len(todo)} images to write ({'all' if a.all else 'missing/empty/weak only'}), engine={engine}, model={MODEL if engine=='claude' else '-'}")
    results = []
    if engine == "claude":
        def one(it):
            if it["logo"]:
                return it, context_alt(it), "rule"
            alt, how = claude_alt(it, key, cache)
            return it, alt, how
        with ThreadPoolExecutor(max_workers=4) as ex:
            for it, alt, how in ex.map(one, todo):
                results.append((it, alt, how))
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    else:
        results = [(it, context_alt(it), "context") for it in todo]

    changes, failed = [], []
    for it, alt, how in results:
        if not alt:
            failed.append((it, how)); continue
        if alt != (it["alt"] or ""):
            it["new"] = alt; it["how"] = how; changes.append(it)
    w = max((len(it["page"]) for it in todo), default=20)
    for it in changes:
        print(f"  {it['page']:{w}}  {it['alt']!r:34} -> {it['new']!r}   [{it['how']}]")
    for it, how in failed:
        print(f"  {it['page']:{w}}  FAILED {how}   {it['src'][-50:]}")
    log = OUT / f"log-{time.strftime('%Y%m%d-%H%M%S')}.json"
    log.write_text(json.dumps([{"page": it["page"], "src": it["src"], "old": it["alt"], "new": it["new"], "how": it["how"]}
                               for it in changes], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(changes)} to change, {len(failed)} failed, {len(todo) - len(changes) - len(failed)} unchanged  -> log {log.relative_to(ROOT)}")
    if a.write and changes:
        n = write_alts(changes)
        print(f"written to {n} pages")
    elif changes:
        print("dry run: nothing written (add --write)")


if __name__ == "__main__":
    main()
