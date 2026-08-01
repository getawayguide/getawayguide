# City maps

Reusable "everything in the city" POI maps, built by `tools/city_map.py` from a
JSON config in this folder. Two variants come out of one config:

- **Desktop** — landscape street map + a floating category key, hover-synced.
- **Mobile** — portrait gesture map: drag to pan, pinch to zoom, tap a pin for
  its name, double-tap to open it in Google Maps. Fixed title + instructions
  overlay; no key. The base street layer is rasterised to a PNG and the pins are
  a light vector overlay on top, so it always paints.

## Build

```bash
python tools/city_map.py tools/city_maps/<slug>.json            # build PNGs + standalone preview (.tmp/previews/<slug>-city.html)
python tools/city_map.py tools/city_maps/<slug>.json --embed    # also embed into cfg["article"] between its markers
python tools/city_map.py tools/city_maps/<slug>.json --demo 7   # preview auto-opens pin #7's name bubble (handy for screenshots)
```

PNGs are written to `Images/web/city-maps/<slug>.png` and `<slug>-mobile.png`.

## Config fields

| field | notes |
|-------|-------|
| `slug` | output basename (`riga` → `riga.png`, `riga-mobile.png`) |
| `kicker`, `city` | title overlay, e.g. `LATVIA` / `Riga` |
| `osm` | path (repo-relative) to a pre-fetched Overpass export — see below |
| `article` | page to resolve pin links from + (with `--embed`) to embed into |
| `embed.pre` / `embed.post` | exact strings; the map replaces whatever is between them |
| `district_label` | optional faint neighborhood/district label baked onto the map `{text,lat,lon}` — add one whenever the article names a district (old town, downtown); place it in that district but clear of the top-right key |
| `hint` | mobile instruction lines (array; joined with `<br>`) |
| `desktop` / `mobile` | framing knobs only (see below) |
| `pois[]` | `{name, lat, lon, cat: see\|eat\|stay, match, href?}` in article order (earlier = lower number, drawn on top). `match` finds the pin's Google-Maps link in the article by link text; `href` overrides. `cat` = the article sub-section (What to Do→see, Where to Eat→eat, Where to Stay→stay). Pick up bullet subjects + named places listed inside a top-level bullet; skip incidental links buried in a sub-bullet's prose. |

STANDARD SIZE is fixed in the script (desktop 760×470, mobile 660×820 portrait,
`dar` 0.72, `pin_scale` 1.6 — see the `DESK_*`/`MOB_*` constants in `city_map.py`);
w/h/dar/pin_scale in a config are ignored. Configs set only the **framing**:
- `desktop`: `{ pad }`  — `pad` >1 adds margin, higher = more zoomed out
- `mobile`:  `{ pad, init_w, init_x, init_y }` — opening view: `init_w` = fraction of
  base width shown, `init_x`/`init_y` = 0–1 center position

Tune `pad`/`init_*` per city with `--demo` screenshots so the key cluster fills the
opening view and outliers are reachable by panning.

GROUPING: by default pins group into ATTRACTIONS / RESTAURANTS & BARS / ACCOMMODATION
via each POI's `cat`. To organize by **neighborhood** instead (like a big city's
article), add a `groups` list of `{key, label, color}` and give each POI a `group`
(a group's key) in place of `cat`. Neighborhood-map rules: **fold eat/stay POIs into
their neighborhood group** (no separate eat/stay groups — assign each by area), and
**add a faint neighborhood-NAME label per area** via `district_labels`. See
`buenos-aires.json` (PALERMO / RECOLETA & MONSERRAT / SAN TELMO / LA BOCA, plus five
`district_labels`). Colours must be dark enough for white pin numbers. Tall legends
on big cities can cover the opposite-corner cluster — shift the frame with `center`.

DISTRICT / ORIENTATION LABELS: `district_labels` is a list of `{text, lat, lon}` faint
uppercase place names baked on the map (a single `district_label` still works). Place
them clear of the title (top-left) and the key (top-right). **Every map should carry a
DOWNTOWN (or CBD / CENTRO) label plus one label per named neighborhood/area the article
discusses** (BONDI/CBD/MANLY, ST KILDA, SURFERS PARADISE, NOOSA NATIONAL PARK, SOUTH BANK…)
so readers can orient and know where to base. **A broad AREA name is a label, never a pin**
— pins are for specific places, so move area names (South Bank, Surfers Paradise) out of `pois`.

DAY TRIPS: `day_trips` is a list of `{name, time, match?, href?}` rendered as a final
legend-only **DAY TRIPS** section — a dash "–" marker (no number = clearly NOT on the map)
and a `(time)` estimate ("Half Day"/"Full Day"). Use it for the article's day-trip / far
nature spots that are too far to plot (reef & Daintree for Cairns, Blue Mountains for
Sydney, Great Ocean Road for Melbourne, Mostar for Sarajevo…). `match` finds the article's
Google-Maps link like a POI does.

LEGEND: long labels wrap to two lines automatically. The whole legend must still fit the
470px map — keep pins + day-trips + headers to **≈24–25 rows**; if it overflows, trim the
least-essential pins. Store `&` (not `&amp;`) in POI names — the tool escapes once.

Render note: the DESKTOP base map is a plain `<img>` (browsers/editors keep it);
MOBILE keeps the base inside the SVG so gestures move base+pins together. HTML editors
can strip the mobile pin overlay — if a page's mobile map shows the base with no pins,
just re-run `--embed` to regenerate it.

## Fetching OSM street data (PowerShell — the Bash tool has no network)

**Always fetch a bbox that's generously bigger than the frame** so the map stays
filled when you zoom out or nudge `pad`/`center` later (a tight fetch shows blank
land off the edge). Don't eyeball it — the tool computes the right box from the
config (covers BOTH variants at their current pad + margin):

```bash
python tools/city_map.py tools/city_maps/<slug>.json --bbox   # prints S,W,N,E
```

Then fetch (Overpass returns 406 without a real User-Agent and a form body; it
also 429/504s on big/dense boxes — retry with backoff, `-TimeoutSec 300`):

```powershell
$bbox = (& python tools/city_map.py tools/city_maps/<slug>.json --bbox).Trim()  # or paste an S,W,N,E
$q = @"
[out:json][timeout:240];
(
  way["highway"]($bbox);
  way["natural"="water"]($bbox);
  way["waterway"]($bbox);
  way["natural"="coastline"]($bbox);
  way["leisure"~"park|garden"]($bbox);
  way["landuse"="recreation_ground"]($bbox);
  relation["natural"="water"]($bbox);
);
out geom;
"@
$r = Invoke-WebRequest -Uri "https://overpass-api.de/api/interpreter" -Method Post `
       -Body @{data=$q} -UserAgent "getawayguide-citymap/1.0 (kevindphan@gmail.com)"
$r.Content | Out-File -Encoding utf8 .tmp/<slug>_osm.json
```

`out geom;` inlines node coordinates so the tool can project them offline.
