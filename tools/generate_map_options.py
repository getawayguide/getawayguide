"""
Generate static map card image options from CartoDB dark_nolabels tiles.
Stitches zoom-2 tiles into a full world map, then crops to portrait 2:3 ratio
for 3 different views.
Output: Images/web/map-card-option-[A/B/C].jpg
"""

import urllib.request
import os
import sys

try:
    from PIL import Image
    import io
except ImportError:
    print("Pillow not installed. Run: pip install pillow")
    sys.exit(1)

OUTPUT_DIR = r"c:\Users\kevin\OneDrive\Documents\Travel Blog\Images\web"
TILE_URL = "https://a.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png"
HEADERS = {"User-Agent": "Mozilla/5.0 (travel-blog static-map-generator)"}

ZOOM = 2          # 4x4 grid = 1024x1024 full world
TILE_SIZE = 256
GRID = 4          # 2^zoom tiles per axis

def fetch_tile(z, x, y):
    url = TILE_URL.format(z=z, x=x, y=y)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return Image.open(io.BytesIO(resp.read())).convert("RGB")

def build_world():
    """Stitch all zoom-2 tiles into one 1024x1024 image."""
    world = Image.new("RGB", (TILE_SIZE * GRID, TILE_SIZE * GRID))
    for row in range(GRID):
        for col in range(GRID):
            print(f"  Fetching tile ({col},{row})...", end="\r")
            tile = fetch_tile(ZOOM, col, row)
            world.paste(tile, (col * TILE_SIZE, row * TILE_SIZE))
    print()
    return world

def save_option(world, name, left, top, right, bottom, label):
    """Crop and save one option."""
    crop = world.crop((left, top, right, bottom))
    # Resize to a clean 480x720 for web use
    crop = crop.resize((480, 720), Image.LANCZOS)
    path = os.path.join(OUTPUT_DIR, f"map-card-{name}.jpg")
    crop.save(path, "JPEG", quality=90)
    print(f"Saved {label} -> {path}")

# ── Crop definitions (left, top, right, bottom) within the 1024x1024 world ──
# The 1024x1024 world at zoom 2 spans: x=0..1024 (180°W..180°E), y=0..1024 (85°N..85°S)
# Approx pixel positions:
#   Longitude: 0px=180°W, 512px=0°, 1024px=180°E
#   Latitude:  0px=85°N, 512px=0°, 1024px=85°S
# Portrait 2:3 crop = width:height = 2:3 e.g. 340w x 510h

OPTIONS = [
    # Option A: Europe/Africa centered (current Leaflet feel)
    # Center around lon=15°, lat=10° => x≈590, y≈468
    # Crop 340w x 510h centred there
    ("option-A", 420, 210, 760, 720, "Option A — Europe/Africa centre (current feel)"),

    # Option B: Wider global view, centred on Atlantic
    # Centre lon=-20°, lat=5° => x≈448, y≈490
    # Wider crop for more of the world showing
    ("option-B", 240, 150, 680, 810, "Option B — Atlantic centred, more global"),

    # Option C: Americas + Europe balanced, portrait tight
    # Centre lon=-30°, lat=15° => x≈426, y≈445
    ("option-C", 100, 100, 540, 760, "Option C — Americas/Europe split"),
]

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print("Building world map from CartoDB dark_nolabels tiles (zoom 2)...")
    world = build_world()

    print("Cropping options...")
    for name, l, t, r, b, label in OPTIONS:
        save_option(world, name, l, t, r, b, label)

    print("\nDone. 3 options saved to Images/web/")

if __name__ == "__main__":
    main()
