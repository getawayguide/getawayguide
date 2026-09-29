#!/usr/bin/env python3
"""Live alt text for the editor: Claude looks at the photo while you crop it (Kevin, 2026-09-28: "I don't like
writing Alt text. When I choose an image, I would like the alt text to be prepopulated").

How it works: a small copy of the photo (768px) goes to ONE warm Claude Code session (the VS Code
extension's claude.exe, Kevin's login; no API key), with what is known about where it was taken:
the photo's GPS place, the city and country, and the article section it is going into. A warm
session answers in about 3 s; starting one costs ~15 s, so the photo server keeps it alive.

Accuracy: Claude may only name the places it is given. Asked cold, it called the Cascade's
obelisk "the Mother Armenia monument" (a different statue across town); given the place, it
described "the tiered limestone staircase and sculpture garden leading up to the obelisk".

Answers are cached per file (path + size + mtime) in .tmp/alt_text_cache.json.

Speed (2026-09-28, Kevin: "it doesn't start automatically for some photos"): the answer itself takes
1.5 to 3 s, but starting a session took 54 s on this machine and decoding a 5712px HEIC 6 s. So the
session is never shut down for being idle (that restart is what made the first photo after a break
seem to get nothing), a session that has described 40 photos is replaced by one built in the
background while the old one keeps answering, and the photo server hands over the 2000px preview it
already built for the sidebar instead of the HEIC.

The site-wide audit and batch writer is tools/alt_text.py (API or page context); this one needs
no API key and is fast enough to fill the field while the crop dialog is open.

Command line:
  python tools/alt_writer.py <photo> [--place "Cascade Complex"] [--city Yerevan] [--country Armenia] [--section "..."]
  python tools/alt_writer.py --page "Drafts/.Full Articles/armenia/gyumri.html" [--dry-run]
      fills in alt text ONLY where an <img> has none (or only its file name); never rewrites alt text
      you wrote
"""
import base64, collections, io, json, os, queue, re, subprocess, sys, threading, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".tmp" / "alt_text_cache.json"
WORK = ROOT / ".tmp" / "claude_cli"

BRIEF = """You write alt text for photos on a travel blog, one photo per message.
House style (the site's existing alt text): "<what is in the picture>, <place>, <country>".
Rules:
- One line in sentence case, under 125 characters, no period at the end.
- Start with what a reader would see: the main subject, the setting, anything notable. Plain and concrete.
- End with the place and country when you are given them, e.g. "Tiered limestone steps and bronze sculptures at the Cascade Complex, Yerevan, Armenia".
- Name a place ONLY if it is given to you as PLACE, CITY or COUNTRY in the message. Never guess a landmark's name from how it looks.
- ARTICLE SECTION is the heading the photo sits under. Use a place name from it only when the photo plausibly shows that place; headings like "Where to Eat" are not places.
- Do not start with "Image of", "Photo of" or "A picture of". No emoji, no hashtags, American spelling, no em dashes.
- If people are in the photo, describe them generally ("two travelers", "a vendor"); never guess who they are.
Reply with ONLY the alt text.
Reply to this message with the single word READY."""


def _load_cache():
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _key(path):
    st = os.stat(path)
    return f"{Path(path).resolve()}|{st.st_size}|{int(st.st_mtime)}"


def _small_jpeg(path):
    """a 768px JPEG for Claude; `path` may be the original or an already-shrunk preview of it"""
    from PIL import Image, ImageOps
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:
        pass
    im = Image.open(path)
    im.thumbnail((1600, 1600))                    # shrink first: a full HEIC rotate is slow
    im = ImageOps.exif_transpose(im).convert("RGB")
    im.thumbnail((768, 768))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode()


class AltWriter:
    """one warm session, kept alive; after 40 photos a fresh one is built in the background"""
    MAX = 40

    def __init__(self):
        self.p = None; self.n = 0; self.last = 0; self.lock = threading.Lock()
        self.swapping = False
        self.events = collections.deque(maxlen=40)     # what the session last said, for /api/alt_status
        self.cache = _load_cache()

    def _start(self):
        self.p = self._spawn(); self.n = 0; self.last = time.time()

    def _spawn(self):
        """a started, briefed session (slow: up to a minute), not yet in use"""
        sys.path.insert(0, str(ROOT / "tools"))
        import claude_answer
        exe = claude_answer.cli_path()
        if not exe:
            raise RuntimeError("no Claude Code CLI on this machine")
        WORK.mkdir(parents=True, exist_ok=True)
        nomcp = WORK / "no-mcp.json"
        if not nomcp.exists():
            nomcp.write_text('{"mcpServers": {}}', encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        p = subprocess.Popen([exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                                   "--model", "sonnet", "--strict-mcp-config", "--mcp-config", str(nomcp)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                  encoding="utf-8", errors="replace", cwd=str(WORK), env=env, bufsize=1,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        # a reader thread, so a session that goes quiet times out instead of blocking readline() forever
        p.q = queue.Queue()

        def read():
            for line in p.stdout:
                try:
                    j = json.loads(line)
                except ValueError:
                    continue
                self.events.append((round(time.time(), 1), p.pid, j.get("type"), j.get("subtype") or ""))
                p.q.put(j)
            p.q.put(None)
        threading.Thread(target=read, daemon=True, name="alt-read").start()
        try:
            self._ask(BRIEF, 180, p)
        except Exception:
            _kill(p); raise
        return p

    def _ask(self, content, timeout, p=None):
        p = p or self.p
        while not p.q.empty():                        # a late answer to an earlier, timed-out question
            p.q.get_nowait()
        p.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n")
        p.stdin.flush()
        end = time.time() + timeout
        while True:
            try:
                j = p.q.get(timeout=max(0.1, end - time.time()))
            except queue.Empty:
                raise RuntimeError("the Claude session timed out")
            if j is None:
                raise RuntimeError("the Claude session exited")
            if j.get("type") == "result":
                return (j.get("result") or "").strip()

    def stop(self):
        _kill(self.p)
        self.p = None

    def alive(self):
        return bool(self.p and self.p.poll() is None)

    def status(self):
        return {"alive": self.alive(), "busy": self.lock.locked(), "pid": self.p.pid if self.p else None,
                "answered": self.n, "swapping": self.swapping, "recent": list(self.events)[-12:]}

    def warm(self):
        with self.lock:
            if not self.alive():
                try:
                    self._start()
                except Exception:
                    self.stop()

    def _replace_later(self):
        """the next session is built while this one keeps answering, then swapped in"""
        if self.swapping:
            return
        self.swapping = True

        def run():
            try:
                new = self._spawn()
                with self.lock:
                    old, self.p, self.n, self.last = self.p, new, 0, time.time()
                _kill(old)
            except Exception:
                pass
            finally:
                self.swapping = False
        threading.Thread(target=run, daemon=True, name="alt-swap").start()

    def describe(self, path, place="", city="", country="", section="", small=None):
        key = _key(path) + "|" + "|".join([place, city, country, section])
        if key in self.cache:
            return self.cache[key]
        img = _small_jpeg(small if small and os.path.exists(small) else path)
        ctx = "\n".join(f"{k}: {v}" for k, v in (("PLACE", place), ("CITY", city), ("COUNTRY", country),
                                                  ("ARTICLE SECTION", section)) if v)
        text = "Alt text for this photo." + ("\n" + ctx if ctx else "\nNo place is known: describe it without naming any place.")
        msg = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img}},
               {"type": "text", "text": text}]
        with self.lock:
            for attempt in (1, 2):                        # a session that died quietly gets one fresh retry
                if not self.alive():
                    self._start()
                try:
                    alt = self._ask(msg, 90)
                    if len(clean(alt)) > 125:              # one retry for length, same photo in context
                        alt = self._ask("That is %d characters. Shorten it to under 125, keeping the place and country. Reply with only the alt text." % len(clean(alt)), 60)
                    break
                except Exception:
                    self.stop()
                    if attempt == 2:
                        raise
            self.n += 1; self.last = time.time()
            if self.n >= self.MAX:
                self._replace_later()
        alt = clean(alt)
        self.cache[key] = alt
        try:
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(self.cache, indent=1, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return alt


def _kill(p):
    try:
        if p:
            p.stdin.close(); p.terminate()
    except Exception:
        pass


def clean(alt):
    alt = alt.strip().strip('"').strip()
    alt = re.sub(r"^(an? )?(image|photo|picture) of\s+", "", alt, flags=re.I)
    alt = alt.replace(" — ", ", ").replace("—", ", ")
    alt = alt[:1].upper() + alt[1:]
    return alt.rstrip(".")[:160]


def _fill_page(page, dry):
    """alt text only where an image has none (or only its file name); yours is never touched"""
    s = open(page, encoding="utf-8", newline="").read()
    country = Path(page).parent.name.replace("-", " ").title()
    w = AltWriter(); n = 0
    def fix(m):
        nonlocal n
        tag = m.group(0)
        src = re.search(r'src="([^"]+)"', tag)
        alt = re.search(r'alt="([^"]*)"', tag)
        if not src or "Images/" not in src.group(1):
            return tag
        cur = alt.group(1).strip() if alt else ""
        stem = Path(src.group(1)).stem
        if cur and cur.lower() not in (stem.lower(), stem.split("-")[0].lower()):
            return tag                                           # alt text you wrote: kept
        from urllib.parse import unquote
        rel = unquote(src.group(1)).split("Images/", 1)[1]
        orig = next((p for p in (ROOT / "Images" / rel, ROOT / "Images" / "web" / rel) if p.exists()), None)
        if not orig:
            return tag
        before = s[:m.start()]
        sec = re.findall(r"<h[23][^>]*>(.*?)</h[23]>", before, re.S)
        section = re.sub(r"<[^>]+>", "", sec[-1]).strip() if sec else ""
        new = w.describe(str(orig), country=country, section=section)
        n += 1
        print(f"  {Path(rel).name}: {new}")
        esc = new.replace("&", "&amp;").replace('"', "&quot;")
        return re.sub(r'alt="[^"]*"', f'alt="{esc}"', tag) if alt else tag.replace("<img ", f'<img alt="{esc}" ', 1)
    out = re.sub(r"<img\b[^>]*>", fix, s)
    w.stop()
    print(f"{n} image(s) given alt text" + (" (dry run, nothing written)" if dry else ""))
    if not dry and out != s:
        open(page, "w", encoding="utf-8", newline="").write(out)


if __name__ == "__main__":
    import argparse
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("photo", nargs="?")
    ap.add_argument("--place", default=""); ap.add_argument("--city", default=""); ap.add_argument("--country", default="")
    ap.add_argument("--section", default=""); ap.add_argument("--page"); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.page:
        _fill_page(a.page, a.dry_run)
    elif a.photo:
        w = AltWriter(); print(w.describe(a.photo, a.place, a.city, a.country, a.section)); w.stop()
    else:
        ap.print_help()
