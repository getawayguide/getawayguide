"""Build the site's public icons from the desktop icon, so the logo has one source (Kevin, 2026-11-09: "use something
more like the cursive g used in the desktop icon"). Google's result favicon comes from these.

    python tools/gen_site_icons.py

Source: Images/web/icons/editor-512.png (never written here). Corners are flattened to white. Writes icon-192, icon-512,
icon-maskable-512, apple-touch-icon and a root favicon.ico (16/32/48).
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "Images/web/icons"


def flat(src, size):
    bg = Image.new("RGBA", src.size, (255, 255, 255, 255))
    bg.alpha_composite(src)
    im = bg.convert("RGB")
    return im if im.size == (size, size) else im.resize((size, size), Image.LANCZOS)


def main():
    src = Image.open(ICONS / "editor-512.png").convert("RGBA")
    for name, size in (("icon-192.png", 192), ("icon-512.png", 512), ("icon-maskable-512.png", 512), ("apple-touch-icon.png", 180)):
        flat(src, size).save(ICONS / name, optimize=True)
        print(name, size)
    flat(src, 256).save(ROOT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    print("favicon.ico 16/32/48")


if __name__ == "__main__":
    main()
