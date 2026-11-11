r"""One IG post draft drawn from more than one place's articles (Kevin, 2026-11-09: "I want to make a IG post for North
Macedonia + Meteora. Make a separate draft for this pulling photos from both sets of articles").

    python tools/ig_combine.py "North Macedonia + Meteora" --from north-macedonia --pages greece/meteora.html

Makes .tmp/ig/<slug>/ with its own manifest and web copies, so the draft shows up as its own entry in the editor's Social
tab with its own picks, crops and captions. The drafts it draws from are not touched. It takes:
  --from <slug>     every photo of an existing .tmp/ig/<slug>/manifest.json (repeatable)
  --pages <page>    the body photos of these article pages, collected the way ig_post.py collects a country (repeatable)
Run it again and it adds what's new; picks already made in the combined draft stay. Originals are never touched.
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ig_post as ig


def link_or_copy(src, dst):
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("label")
    ap.add_argument("--from", dest="sources", action="append", default=[])
    ap.add_argument("--pages", action="append", default=[])
    a = ap.parse_args()
    slug = ig.slugify(a.label)
    base = ig.TMP / slug
    web = base / "web"
    web.mkdir(parents=True, exist_ok=True)
    photos, taken, pages = [], set(), []

    for src in a.sources:
        man = json.loads((ig.TMP / src / "manifest.json").read_text(encoding="utf-8"))
        pages += ["%s/%s" % (src, p) for p in man["pages"]]
        for p in man["photos"]:
            pid = p["id"]
            if pid in taken:
                continue
            taken.add(pid)
            link_or_copy(ig.ROOT / p["web"], web / (pid + ".jpg"))
            photos.append(dict(p, web=str((web / (pid + ".jpg")).relative_to(ig.ROOT)).replace("\\", "/"), source=src))

    for rel in a.pages:
        page = ig.ROOT / rel
        country_dir = ig.images_folder(Path(rel).parts[0])
        pages.append(rel)
        for it in ig.collect([page], country_dir):
            orig = it["original"]
            im, icc = ig.open_upright(orig)
            t, gps = ig.exif_info(ig.Image.open(orig))
            pid = ig.photo_id(orig, country_dir, taken)
            taken.add(pid)
            dst = web / (pid + ".jpg")
            if not dst.exists():
                w, h = im.size
                s = ig.WEB_EDGE / max(w, h)
                small = im.resize((round(w * s), round(h * s)), ig.Image.LANCZOS) if s < 1 else im
                ig.save_srgb(ig.to_srgb(small, icc), dst, ig.WEB_Q)
            photos.append({"id": pid, "original": str(orig.relative_to(ig.ROOT)).replace("\\", "/"), "article": it["article"],
                           "article_title": it["article_title"], "section": it["section"], "caption": it["caption"], "alt": it["alt"],
                           "time": t, "gps": gps, "w": im.size[0], "h": im.size[1],
                           "web": str(dst.relative_to(ig.ROOT)).replace("\\", "/"), "web_bytes": dst.stat().st_size,
                           "icc": bool(icc), "source": rel})

    (base / "manifest.json").write_text(json.dumps({"country": a.label, "slug": slug, "drafts": True, "combined": True, "pages": pages,
                                                    "photos": photos}, indent=1, ensure_ascii=False), encoding="utf-8")
    by = {}
    for p in photos:
        by[p["article"]] = by.get(p["article"], 0) + 1
    sys.stdout.buffer.write(("%s: %d photos %s\n" % (base, len(photos), by)).encode("utf-8"))


if __name__ == "__main__":
    main()
