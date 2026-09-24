"""Run the image pipeline on a DRAFT article, so its photos can be reviewed compressed before
the page is published.

The pipeline tools (recompress_desktop, add_picture_mobile, gen_mobile_jpg, gen_mobile_webp,
fix_img_perf, fix_case) only look at published pages: <country>/<page>.html, with body images
at ../Images/... and a class="article-body" marker. A draft under Drafts/.Full Articles/<c>/
sits three levels deep, so its paths are ../../../Images/..., and the artifact template calls
its body "artbody". This script bridges that gap without changing the tools:

  1. copy the draft to <country>/_draft-<name>.html with the paths flattened and the marker added
  2. run the pipeline on it (each tool is a no-op for every other page that is already done)
  3. copy the result back into the draft with the paths deepened again, marker removed
  4. delete the temporary page

Originals under Images/<Country>/ are never modified; the variants land in Images/web/<Country>/.

Usage:
    python tools/draft_images.py "Drafts/.Full Articles/armenia/armenia-itinerary.html"
    python tools/draft_images.py <draft> --dry-run      # show what the tools would do
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIPELINE = [
    ["recompress_desktop.py", "--new-only"],   # desktop jpg for every original the body references
    ["add_picture_mobile.py"],                  # <picture> with the desktop source
    ["gen_mobile_jpg.py"],                      # -mob-2x.jpg + the fallback src repointed at it
    ["gen_image_tiers.py", "--country", "{country}"],   # the -1x/-2x/-3x and -mob-1x/-3x FILES, JPEG + WebP
    ["gen_mobile_webp.py"],                     # webp next to the mobile jpg
    ["fix_img_perf.py"],                        # loading= and intrinsic width/height
    ["fix_case.py"],                            # case-exact paths for GitHub Pages
]
# gen_image_tiers.py makes the tier files from the archival originals but writes no HTML:
# nothing in tools/ yet rewrites a <picture> block's srcsets into the 1x/2x/3x form
# (Kosovo's were written by hand on 2026-09-23). Until that exists, a draft leaves here
# with every tier ON DISK and the single-size srcset the older tools write.


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    draft = (ROOT / sys.argv[1]).resolve()
    dry = "--dry-run" in sys.argv
    if not draft.exists():
        sys.exit(f"! no such draft: {draft}")
    depth = len(draft.relative_to(ROOT).parts) - 1           # Drafts/.Full Articles/armenia/x.html -> 3
    up = "../" * depth
    country = draft.parent.name
    live_dir = ROOT / country
    if not live_dir.is_dir():
        sys.exit(f"! no published folder {country}/ to stage the draft in")
    tmp = live_dir / f"_draft-{draft.stem}.html"

    src = draft.read_text(encoding="utf-8", newline="")
    # the country the tier tool works on is whichever Images/<Country>/ the body draws from
    m_c = re.search(r'Images/(?!web/)([^/"]+)/', src)
    country_name = m_c.group(1) if m_c else None
    staged = src.replace(f"{up}Images/", "../Images/")
    # the tools scan from the literal marker class="article-body"; the artifact template's body
    # is "artbody", so a hidden marker span goes in right after its opening tag (and out again)
    MARK = '<span class="article-body" hidden></span>'
    if 'class="article-body"' not in staged:
        m = re.search(r'<div class="artbody"[^>]*>', staged)
        if m:
            staged = staged[:m.end()] + MARK + staged[m.end():]
    if 'class="article-body"' not in staged:
        sys.exit("! could not find the article body (no artbody or article-body class)")
    tmp.write_text(staged, encoding="utf-8", newline="")
    print(f"staged {draft.name} as {tmp.relative_to(ROOT)} ({staged.count('../Images/')} image paths flattened)")
    try:
        for tool in PIPELINE:
            if "{country}" in tool:
                if not country_name:
                    print('\n== ' + tool[0] + '\n   skipped: no Images/<Country>/ path in the body')
                    continue
                tool = [t.replace("{country}", country_name) for t in tool]
            cmd = [sys.executable, str(ROOT / "tools" / tool[0])] + tool[1:] + (["--dry-run"] if dry else [])
            print(f"\n== {' '.join(tool)}")
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
            lines = [l for l in (r.stdout + r.stderr).splitlines() if tmp.name in l or "NEW" in l or "MISSING" in l or "encoded" in l or "page(s)" in l or "Traceback" in l or "Error" in l]
            print("   " + "\n   ".join(lines[-12:]) if lines else "   (nothing for this page)")
            if r.returncode:
                print(r.stdout[-1500:], r.stderr[-1500:]); sys.exit(f"! {tool[0]} failed")
        if not dry:
            out = tmp.read_text(encoding="utf-8", newline="")
            out = out.replace("../Images/", f"{up}Images/").replace(MARK, "", 1)
            draft.write_text(out, encoding="utf-8", newline="")
            pics = len(re.findall(r"<picture>", out))
            orig = len(re.findall(r'<img[^>]*src="[^"]*Images/(?!web/)', out[out.find('class="artbody"'):]))
            print(f"\ndraft updated: {pics} <picture> blocks, {orig} originals still used as src")
    finally:
        if tmp.exists():
            tmp.unlink()
            print(f"removed {tmp.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
