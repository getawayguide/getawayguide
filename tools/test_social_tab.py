"""The editor's Social tab (social.js + tools/social_tab.py; Kevin, 2026-11-08). Everything runs against a SCRATCH copy of
.tmp/ig/armenia (manifest, the seed's plan + map, three web copies) in a temp folder (SOCIAL_IG_DIR) with a scratch output
folder (SOCIAL_OUT_DIR), served by the standalone `python tools/social_tab.py --serve <port>`; the editor is loaded from
file:// with window.SOCIAL_API pointed at that port, so the live photo server is never needed and the real store
(.tmp/ig/<slug>/state), the real renders (.tmp/previews/social-pilot) and Pictures\\IG are never written.

  API    the country list (flag, artifact, has_plan, story counts); the store: a catalog built from the manifest with the
         photo id as its asset, the seed's carousel/main + map copied in, set / delete / batch writing one plain JSON per
         document (what ig_post.py / ig_carousel.py / ig_story.py --state read), a bad path refused, 304 on an unchanged
         version; the blob route; an ArtifactData export (wrapped {data, version} bodies) imported; a second render
         refused while one runs
  RENDER a real carousel render through the job route: 7 slides at 1080x1350 with the sRGB profile + caption.txt, listed
         by /renders and served by the file route; then the Travel Blog STORIES (7 frames at 1080x1920) and the Personal
         post's stories from one final slot (--no-copy: Pictures untouched)
  PAGE   tools/ig_post_page.html served with the local runtime shim: 125 cards from the local catalog, a shortlist key
         writes photos/<id>.json, the template mode opens on the plan; the shortcut hint line is gone (a "?" button holds
         the keys); ?embed=1 drops the page's header (html.embed)
  UI     the Social tab in the bar between Launch and Queue (Ctrl+Alt+6): the two OUTPUT TYPES at the top (Personal post /
         Travel Blog post) driving the maker iframe over postMessage, the sub-tabs with the page's live counts, the
         article filter, Ready, Post / Slides / Stories / Pins with the rendered thumbnails, the Stories strip and the
         lightbox, 393 px without sideways scroll, the restart message on a 404
  REDESIGN (Kevin, 2026-11-11) the old shortlist migrated into the slots (with a state.bak copy, duplicates moved, nothing
         lost); the Shortlist sub-tab is the slots; five to a row with no sideways scroll; empty slots hold only a +;
         a slotted photo has just edit and remove; Photos cards have no star and no caption overlay; + / − on the Final
         row (Instagram's 20 cap); the grid's keys; the four ratios (4:5, 3:4, 1:1, 1.91:1) from the tab's switch; the
         preview (black, big, side arrows, the iframe fills the window); the crop editor's side arrows; Render marks the
         post ready and renders it (a real one-photo render, 1080x1350 then 1080x565 at 1.91:1); photos added from the
         photo library (mocked album routes, a scratch backup folder)
  ROUND 2 (Kevin, 2026-11-11) a drop INSERTS and the rest move along one in order, never a swap (big grid I1-I3, strip
         L3-L6, a Photos card onto the strip); rows are packed, a drop past the last photo goes to the row's end, Delete
         closes the gap (G1-G2), gaps in a stored store are packed on the first read but never on a poll (M2); − / + on
         both strip rows and on the Shortlist row head (L0-L2, post/main.nshort); a click on a photo opens the preview
         with Edit / Add to shortlist / Move to Final / Move to Shortlist / Remove, S and Enter there, the strip and the
         Shortlist view preview their own row (V1-V8)
  POST + EMAIL (Kevin, 2026-11-11: "can the post tab show how it would look on IG?", "add a button to email to my phone")
         E0-E7: /post (the account, the Final row with crops, caption, render state), the render refused before it exists
         and when stale (a crop, the order, the ratio), the request for Claude without Gmail keys (01.jpg.. in order, the
         render's own files with sha256, parts under the cap, caption first, no address), the CLI (--email-requests /
         -sent / -failed, a changed file flagged), and with keys a real SMTP send to a FAKE sink on loopback (bytes
         identical, From/To from the scratch .env, the password nowhere, a refused sign-in explained); a test store never
         reaches smtp.gmail.com. IG1-IG8 + EM1: the preview in the Post tab (account, 4:5 carousel from the render, 1/3,
         dots, arrows, swipe, keys, the caption cut at ~125 characters with "more", the profile grid's 3:4 tile), the send
         from the button, the stale warning with the crops drawn instead, the request state turning to Sent, 393 px
  REAL   the real store, renders and Pictures\\IG untouched (no email request there either)

    python tools/test_social_tab.py
"""
import base64
import email
import email.policy
import hashlib
import json
import os
import pathlib
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

from PIL import Image
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent.parent
os.chdir(ROOT)
EDITOR = (ROOT / "editor.html").as_uri()
SHOTS = ROOT / ".tmp" / "previews"
SHOTS.mkdir(parents=True, exist_ok=True)
REAL = ROOT / ".tmp" / "ig" / "armenia"
REAL_OUT = ROOT / ".tmp" / "previews" / "social-pilot"
PICS = pathlib.Path(os.environ.get("USERPROFILE", str(pathlib.Path.home()))) / "Pictures" / "IG" / "armenia"
checks = []


def ok(name, cond, info=""):
    checks.append((name, bool(cond), info))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  :: " + str(info)[:700]), flush=True)


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def wait_port(port, t=30):
    end = time.time() + t
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


class Api:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"

    def call(self, path, body=None, raw=False):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"} if body is not None else {}, method="POST" if body is not None else "GET")
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = r.read()
                    return r.status, (data if raw else (json.loads(data.decode("utf-8")) if data else {}))
            except urllib.error.HTTPError as e:
                try:
                    return e.code, json.loads(e.read().decode("utf-8"))
                except Exception:
                    return e.code, {}
            except (ConnectionResetError, ConnectionAbortedError, urllib.error.URLError):
                if attempt == 2:
                    raise
                time.sleep(0.3)

    def wait_job(self, jid, t=300):
        end = time.time() + t
        while time.time() < end:
            st, jj = self.call("/api/social/job/" + jid)
            if jj.get("job", {}).get("status") in ("done", "failed"):
                return jj.get("job", {})
            time.sleep(2)
        return jj.get("job", {})


def snapshot(folder):
    return sorted((str(p.relative_to(folder)), p.stat().st_mtime_ns, p.stat().st_size) for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else None


# ----------------------------------------------------------------------------- scratch fixtures
if not (REAL / "manifest.json").is_file():
    print("no .tmp/ig/armenia/manifest.json: run  python tools/ig_post.py Armenia  first"); sys.exit(1)
SCRATCH = pathlib.Path(tempfile.mkdtemp(prefix="social_tab_test_"))
IG = SCRATCH / "ig"; OUT = SCRATCH / "out"
(IG / "armenia" / "web").mkdir(parents=True); (IG / "armenia" / "carousel").mkdir(); OUT.mkdir()
shutil.copyfile(REAL / "manifest.json", IG / "armenia" / "manifest.json")
for n in ("main.json", "map.json"):
    shutil.copyfile(REAL / "carousel" / n, IG / "armenia" / "carousel" / n)
man = json.loads((REAL / "manifest.json").read_text(encoding="utf-8"))
web_ids = []
for p in man["photos"][:3]:
    src = REAL / "web" / (p["id"] + ".jpg")
    if src.is_file():
        shutil.copyfile(src, IG / "armenia" / "web" / src.name); web_ids.append(p["id"])
real_state0, real_out0 = snapshot(REAL / "state"), snapshot(REAL_OUT)
pics0 = snapshot(PICS)
real_free0 = snapshot(REAL / "out")
# a scratch photo library (the Library app's backed-up albums) with one album and two photos
BACKUP = SCRATCH / "backup"; ALBUM = "Armenia (2025)"
(BACKUP / ALBUM).mkdir(parents=True)
lib_names = []
for i, wid in enumerate(web_ids[:2]):
    shutil.copyfile(REAL / "web" / (wid + ".jpg"), BACKUP / ALBUM / ("IMG_90%02d.JPG" % i)); lib_names.append("IMG_90%02d.JPG" % i)

# Email to my phone (Kevin, 2026-11-11): a FAKE SMTP sink on loopback and a scratch .env, so no real email is ever
# sent; the values below are made-up stand-ins (no address in this file). Rewriting ENVF switches the server between
# sending (keys present) and writing a request for Claude (no keys): it reads the file at send time.
MAILS, SINK_PW, SINK_USER, SINK_TO = [], "sink-pass-" + uuid.uuid4().hex[:8], "sender-test", "phone-test"


class _Sink(socketserver.StreamRequestHandler):
    def w(self, s):
        self.wfile.write((s + "\r\n").encode())

    def handle(self):
        self.w("220 fake sink")
        data, mail = None, {"rcpt": []}
        while True:
            line = self.rfile.readline()
            if not line:
                return
            if data is not None:
                if line in (b".\r\n", b".\n"):
                    mail["data"] = b"".join(data); MAILS.append(mail); mail = {"rcpt": [], "auth": mail.get("auth")}; data = None; self.w("250 ok"); continue
                data.append(line[1:] if line.startswith(b"..") else line); continue
            cmd = line.decode("utf-8", "replace").strip(); u = cmd.upper()
            if u.startswith("EHLO"):
                self.w("250-fake"); self.w("250-AUTH PLAIN LOGIN"); self.w("250 SIZE 60000000")
            elif u.startswith("AUTH PLAIN"):
                parts = base64.b64decode(cmd.split()[2]).split(b"\0")
                mail["auth"] = [x.decode() for x in parts[1:]]
                self.w("235 ok" if parts[-1].decode() == SINK_PW else "535 5.7.8 bad credentials")
            elif u.startswith("MAIL FROM"):
                mail["from"] = cmd[10:]; self.w("250 ok")
            elif u.startswith("RCPT TO"):
                mail["rcpt"].append(cmd[8:]); self.w("250 ok")
            elif u == "DATA":
                data = []; self.w("354 go")
            elif u == "QUIT":
                self.w("221 bye"); return
            else:
                self.w("250 ok")


SINK = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Sink); SINK.daemon_threads = True
threading.Thread(target=SINK.serve_forever, daemon=True).start()
ENVF = SCRATCH / "env.txt"


def env_keys(on, pw=None):
    ENVF.write_text(("GMAIL_SMTP_USER=%s\nGMAIL_APP_PASSWORD=%s\nIG_EMAIL_TO=%s\n" % (SINK_USER, pw or SINK_PW, SINK_TO)) if on else "# no keys\n", encoding="utf-8")


env_keys(True)
PORT = free_port()
env = dict(os.environ, SOCIAL_IG_DIR=str(IG), SOCIAL_OUT_DIR=str(OUT), SOCIAL_BACKUP_DIR=str(BACKUP), PYTHONIOENCODING="utf-8",
           SOCIAL_ENV_FILE=str(ENVF), SOCIAL_SMTP_HOST="127.0.0.1", SOCIAL_SMTP_PORT=str(SINK.server_address[1]), SOCIAL_SMTP_SSL="0",
           # the cap per email, lowered so the three test files split [[0, 1], [2]] (the real one is 18 MiB)
           SOCIAL_EMAIL_PART_BYTES=str(sum((IG / "armenia" / "web" / (w + ".jpg")).stat().st_size for w in web_ids[:2]) + 1))
for _k in ("GMAIL_SMTP_USER", "GMAIL_APP_PASSWORD", "IG_EMAIL_TO"):
    env.pop(_k, None)
REAL_REQ0 = sorted(str(p) for p in (ROOT / ".tmp" / "ig").glob("*/email_request.json"))
srv = subprocess.Popen([sys.executable, str(ROOT / "tools" / "social_tab.py"), "--serve", str(PORT)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000 if sys.platform == "win32" else 0)
if not wait_port(PORT):
    srv.kill(); raise RuntimeError("social_tab server did not start")
api = Api(PORT)
IDX_HANDLE = re.search(r"instagram\.com/([A-Za-z0-9_.]{1,30})", (ROOT / "index.html").read_text(encoding="utf-8")).group(1)
REQF, FREE = IG / "armenia" / "email_request.json", IG / "armenia" / "out"
E3 = [man["photos"][k]["id"] for k in (20, 21, 22)]
CROP2 = {"aspect": "4:5", "cx": 0.48, "cy": 0.52, "w": 0.7, "rot": 1.5}
CAP = "A test caption that runs long enough to be cut in the feed: " + "lavash and apricots in Yerevan, " * 4 + "the end."


def setdoc(path, data):
    return api.call("/api/social/armenia/db", {"op": "set", "path": path, "data": data})


def cli(*args):
    """tools/social_tab.py's command line on the scratch store (the server's env)"""
    p_ = subprocess.run([sys.executable, str(ROOT / "tools" / "social_tab.py"), *args], env=env, capture_output=True, timeout=60)
    out = p_.stdout.decode("utf-8", "replace")
    try:
        return p_.returncode, json.loads(out)
    except ValueError:
        return p_.returncode, out + p_.stderr.decode("utf-8", "replace")


try:
    # ----------------------------------------------------------------------------- API
    st, j = api.call("/api/social")
    c = next((x for x in j.get("countries", []) if x["slug"] == "armenia"), None)
    ok("A1 /api/social lists the scratch country with its flag, artifact, counts (stories too) and plan", st == 200 and c and c["country"] == "Armenia" and c["photos"] == len(man["photos"])
       and c["flag"] == "Images/web/flags/am.png" and "claude.ai/artifact/" in (c["artifact"] or "") and c["has_plan"] and c["has_seed"]
       and c["renders"] == {"carousel": 0, "free": 0, "pins": 0, "stories_carousel": 0, "stories_free": 0}, (st, c))
    st, db = api.call("/api/social/armenia/db")
    cat = db.get("docs", {}).get("catalog/main") or {}
    ok("A2 the store's first read builds catalog/main from the manifest (asset = photo id) and copies the seed's plan + map in",
       st == 200 and len(cat.get("items") or []) == len(man["photos"]) and cat["items"][0]["asset"] == cat["items"][0]["id"] and cat.get("country") == "Armenia"
       and "carousel/main" in db["docs"] and "carousel/map" in db["docs"] and db["docs"]["carousel/main"].get("stops") and len(db["version"]) == 16
       and (IG / "armenia" / "state" / "catalog" / "main.json").is_file() and (IG / "armenia" / "state" / "carousel" / "main.json").is_file(), (st, sorted(db.get("docs", {}))))
    v0 = db["version"]
    st, _ = api.call("/api/social/armenia/db?version=" + v0, raw=True)
    ok("A2b the same version answers 304", st == 304, st)
    pid = man["photos"][0]["id"]
    st, w = api.call("/api/social/armenia/db", {"op": "set", "path": "photos/" + pid, "data": {"state": "shortlist", "slot": None, "order": 1, "note": "", "crop": None}})
    f = IG / "armenia" / "state" / "photos" / (pid + ".json")
    ok("A3 set writes one plain JSON per document (what ig_post.py --state reads) and the version moves", st == 200 and w["version"] != v0 and f.is_file() and json.loads(f.read_text(encoding="utf-8"))["state"] == "shortlist", (st, w))
    st, w2 = api.call("/api/social/armenia/db", {"op": "batch", "ops": [{"op": "set", "path": "post/main", "data": {"status": "draft", "aspect": "4:5"}}, {"op": "set", "path": "captions/c1", "data": {"text": "hello", "stars": 3}}]})
    st2, w3 = api.call("/api/social/armenia/db", {"op": "delete", "path": "captions/c1"})
    ok("A3b batch writes several, delete removes the file", st == 200 and (IG / "armenia" / "state" / "post" / "main.json").is_file() and st2 == 200 and not (IG / "armenia" / "state" / "captions" / "c1.json").is_file(), (st, st2))
    st, e = api.call("/api/social/armenia/db", {"op": "set", "path": "../../evil", "data": {}})
    st2, e2 = api.call("/api/social/armenia/db", {"op": "set", "path": "secrets/x", "data": {}})
    ok("A3c a path outside the five collections, or with dots, is refused (400)", st == 400 and st2 == 400 and not (SCRATCH / "evil.json").is_file(), (st, st2))
    st, e = api.call("/api/social/nowhere/db")
    ok("A3d an unknown country is a 404", st == 404, st)

    # the old shortlist becomes the slots (Kevin, 2026-11-11): two 'shortlist' photos in their order, and two photos
    # claiming one slot, all land in free slots after the Final row; the state is copied to state.bak-* first
    p2, p3, p4 = (man["photos"][k]["id"] for k in (1, 2, 3))
    for path, data in (("photos/" + p2, {"state": "shortlist", "slot": None, "order": 0, "note": "keep me", "crop": None}),
                       ("photos/" + p3, {"state": "final", "slot": 3, "order": 0, "note": "", "crop": None}),
                       ("photos/" + p4, {"state": "final", "slot": 3, "order": 0, "note": "", "crop": None})):
        api.call("/api/social/armenia/db", {"op": "set", "path": path, "data": data})
    st, db = api.call("/api/social/armenia/db")
    d = db.get("docs", {})
    first3, other3 = sorted([p3, p4])
    baks = list((IG / "armenia").glob("state.bak-*"))
    ok("M1 GET migrates: the old shortlist goes into slots 11, 12 in its order (the note kept); two photos on slot 3 of an empty Final row are packed to 1 and 2; a state.bak copy holds the old docs",
       d.get("photos/" + p2, {}).get("slot") == 11 and d["photos/" + p2]["state"] == "final" and d["photos/" + p2]["note"] == "keep me"
       and d.get("photos/" + pid, {}).get("slot") == 12 and d["photos/" + pid]["state"] == "final"
       and d.get("photos/" + first3, {}).get("slot") == 1 and d.get("photos/" + other3, {}).get("slot") == 2
       and len(baks) == 1 and json.loads((baks[0] / "photos" / (p2 + ".json")).read_text(encoding="utf-8"))["state"] == "shortlist",
       ({k: (v.get("state"), v.get("slot")) for k, v in d.items() if k.startswith("photos/")}, baks))
    st, db2 = api.call("/api/social/armenia/db")
    ok("M1b a second read migrates nothing more (no new backup, same slots)", len(list((IG / "armenia").glob("state.bak-*"))) == 1
       and db2["docs"]["photos/" + p2]["slot"] == 11, len(list((IG / "armenia").glob("state.bak-*"))))
    for x in (p2, p3, p4):
        api.call("/api/social/armenia/db", {"op": "delete", "path": "photos/" + x})
    api.call("/api/social/armenia/db", {"op": "set", "path": "photos/" + pid, "data": {"state": "final", "slot": 11, "order": 0, "note": "", "crop": None}})
    # PACKED SLOTS (Kevin, 2026-11-11, second round: "no blanks in between photos, only at the end"): a store with gaps is
    # packed in its own order on the page's first read (a backup first), never on a poll (which may cross a move being written)
    q = [man["photos"][k]["id"] for k in (5, 6, 7, 8)]
    for x, s in zip(q, (1, 3, 12, 15)):
        api.call("/api/social/armenia/db", {"op": "set", "path": "photos/" + x, "data": {"state": "final", "slot": s, "order": 0, "note": "", "crop": None}})
    time.sleep(1.1)                                                       # a new second, so a new state.bak-<time>
    st, dbp = api.call("/api/social/armenia/db?version=stale")
    poll_slots = [dbp.get("docs", {}).get("photos/" + x, {}).get("slot") for x in q]
    st, dbg = api.call("/api/social/armenia/db")
    got = [dbg["docs"].get("photos/" + x, {}).get("slot") for x in q] + [dbg["docs"].get("photos/" + pid, {}).get("slot")]
    baks = sorted((IG / "armenia").glob("state.bak-*"))
    bak_q1 = baks[-1] / "photos" / (q[1] + ".json") if baks else None
    ok("M2 gaps are packed on the first read, in order: Final 1, 3 -> 1, 2; Shortlist 11, 12, 15 -> 11, 12, 13; a poll leaves them as stored; the new state.bak holds the gaps",
       poll_slots == [1, 3, 12, 15] and got == [1, 2, 12, 13, 11] and len(baks) == 2 and bak_q1 and bak_q1.is_file()
       and json.loads(bak_q1.read_text(encoding="utf-8"))["slot"] == 3, (poll_slots, got, [b.name for b in baks]))
    for x in q:
        api.call("/api/social/armenia/db", {"op": "delete", "path": "photos/" + x})
    if web_ids:
        st, raw = api.call("/api/social/armenia/blob/" + web_ids[0], raw=True)
        st2, _ = api.call("/api/social/armenia/blob/not-a-photo", raw=True)
        ok("A4 the blob route serves the web copy (JPEG) and 404s a missing id", st == 200 and raw[:2] == b"\xff\xd8" and st2 == 404, (st, st2))
    exp = ROOT / ".tmp" / ("social_tab_test_" + SCRATCH.name[-8:]) / "export"; (exp / "photos").mkdir(parents=True); (exp / "post").mkdir()   # an export must sit under .tmp (the server's rule)
    (exp / "photos" / "zz-imported.json").write_text(json.dumps({"id": "zz-imported", "version": "v1", "data": {"state": "final", "slot": 1, "order": 0, "note": "", "crop": None}}), encoding="utf-8")
    (exp / "post" / "main.json").write_text(json.dumps({"status": "ready", "aspect": "1:1"}), encoding="utf-8")
    st, imp = api.call("/api/social/armenia/import", {"dir": str(exp)})
    st_ok = st == 200 and imp.get("copied") == 2
    st2, imp2 = api.call("/api/social/armenia/import", {"dir": str(ROOT / ".tmp")})       # a folder under .tmp but inside the repo is allowed; one elsewhere is refused
    st3, e3 = api.call("/api/social/armenia/import", {"dir": str(SCRATCH)})
    ok("A5 import copies an ArtifactData export (wrapped bodies unwrapped) into the store; a folder outside .tmp is refused", st_ok
       and json.loads((IG / "armenia" / "state" / "photos" / "zz-imported.json").read_text(encoding="utf-8")) == {"state": "final", "slot": 1, "order": 0, "note": "", "crop": None}
       and json.loads((IG / "armenia" / "state" / "post" / "main.json").read_text(encoding="utf-8"))["status"] == "ready" and st3 == 400, (st, imp, st3, e3))
    api.call("/api/social/armenia/db", {"op": "delete", "path": "photos/zz-imported"})
    api.call("/api/social/armenia/db", {"op": "set", "path": "post/main", "data": {"status": "draft", "aspect": "4:5"}})
    shutil.rmtree(exp.parent, ignore_errors=True)

    # ----------------------------------------------------------------------------- POST VIEW + EMAIL TO MY PHONE (API, CLI, fake SMTP)
    PS = "/api/social/armenia/"
    st, p0 = api.call(PS + "post")
    ok("E0 GET /post: the account (the site's own Instagram handle), the site icon as avatar, the country; nothing final yet, so no slides and no render; the scratch keys are found",
       st == 200 and p0.get("handle") == IDX_HANDLE and p0.get("avatar") == "Images/web/icons/icon-192.png" and (ROOT / p0["avatar"]).is_file() and p0.get("country") == "Armenia"
       and p0.get("slides") == [] and p0["render"]["state"] == "none" and p0.get("direct") is True and p0.get("email") is None, p0)
    st, e1 = api.call(PS + "email", {})
    ok("E1 Email before any render is refused (400, 'Render first') and writes nothing", st == 400 and e1.get("code") == "none" and "Render" in e1.get("error", "")
       and not REQF.is_file(), (st, e1))
    for k, x in enumerate(E3):
        setdoc("photos/" + x, {"state": "final", "slot": k + 1, "order": 0, "note": "", "crop": CROP2 if k == 1 else None})
    setdoc("post/main", {"status": "draft", "aspect": "4:5", "finalCaption": CAP, "hashtags": "#armenia #yerevan"})
    # a render on disk exactly as ig_post.py leaves one: three real JPEGs + render.json
    FREE.mkdir(parents=True, exist_ok=True)
    rec = []
    for k, x in enumerate(E3):
        fn = "%02d-%s.jpg" % (k + 1, x)
        shutil.copyfile(IG / "armenia" / "web" / (web_ids[k] + ".jpg"), FREE / fn)
        rec.append({"n": k + 1, "id": x, "aspect": "4:5", "crop": CROP2 if k == 1 else {}, "file": fn})
    (FREE / "render.json").write_text(json.dumps({"rendered": "2026-11-11T10:00:00", "post_aspect": "4:5", "slides": rec}), encoding="utf-8")
    sizes = [(FREE / r_["file"]).stat().st_size for r_ in rec]
    st, p1 = api.call(PS + "post")
    ok("E2 /post: the Final row in order (its crop kept, 4:5), the caption + hashtags as they stand now, the render current (ok, 3 files with their sizes)",
       st == 200 and [s["id"] for s in p1["slides"]] == E3 and p1["slides"][1]["crop"] == CROP2 and p1["slides"][0]["aspect"] == "4:5" and p1["caption"] == CAP
       and p1["hashtags"] == "#armenia #yerevan" and p1["render"]["state"] == "ok" and [f["bytes"] for f in p1["render"]["files"]] == sizes
       and p1["slides"][0]["blob"].endswith("/blob/" + E3[0]) and p1["render"]["emails"] == 2, (p1.get("render"), [s["id"] for s in p1.get("slides", [])]))
    setdoc("photos/" + E3[1], {"state": "final", "slot": 2, "order": 0, "note": "", "crop": dict(CROP2, w=0.6)})
    _, ps1 = api.call(PS + "post"); st, es1 = api.call(PS + "email", {})
    ok("E3 a crop changed after the render makes it stale ('slide 2's crop changed') and Email is refused (409 stale)", ps1["render"]["state"] == "stale"
       and "slide 2" in ps1["render"]["reason"] and st == 409 and es1.get("code") == "stale" and not REQF.is_file(), (ps1["render"], st, es1))
    setdoc("photos/" + E3[1], {"state": "final", "slot": 2, "order": 0, "note": "", "crop": CROP2})
    setdoc("photos/" + E3[0], {"state": "final", "slot": 2, "order": 0, "note": "", "crop": None}); setdoc("photos/" + E3[1], {"state": "final", "slot": 1, "order": 0, "note": "", "crop": CROP2})
    _, ps2 = api.call(PS + "post")
    setdoc("photos/" + E3[0], {"state": "final", "slot": 1, "order": 0, "note": "", "crop": None}); setdoc("photos/" + E3[1], {"state": "final", "slot": 2, "order": 0, "note": "", "crop": CROP2})
    setdoc("post/main", {"status": "draft", "aspect": "1:1", "finalCaption": CAP, "hashtags": "#armenia #yerevan"})
    _, ps3 = api.call(PS + "post")
    setdoc("post/main", {"status": "draft", "aspect": "4:5", "finalCaption": CAP, "hashtags": "#armenia #yerevan"})
    _, ps4 = api.call(PS + "post")
    ok("E3b reordered slides and another ratio are stale too, each with its plain reason; put back, the render is current again",
       ps2["render"]["state"] == "stale" and "reordered" in ps2["render"]["reason"] and ps3["render"]["state"] == "stale" and "ratio" in ps3["render"]["reason"]
       and ps4["render"]["state"] == "ok", (ps2["render"]["reason"], ps3["render"]["reason"], ps4["render"]["state"]))
    # no Gmail keys: the button writes the request for Claude
    env_keys(False)
    st, rq = api.call(PS + "email", {})
    R = json.loads(REQF.read_text(encoding="utf-8")) if REQF.is_file() else {}
    ok("E4 without Gmail keys the button writes the request for Claude: status requested, no job; the files are the render's own (absolute paths, sizes, sha256) "
       "named 01.jpg to 03.jpg in slide order, split in two under the cap, the subject 'IG post: Armenia (3 slides)', the caption + hashtags at the very top, no address anywhere",
       st == 200 and rq.get("job") is None and rq["email"]["status"] == "requested" and R.get("via") == "claude" and R.get("status") == "requested"
       and [f["filename"] for f in R["files"]] == ["01.jpg", "02.jpg", "03.jpg"] and [pathlib.Path(f["path"]).name for f in R["files"]] == [r_["file"] for r_ in rec]
       and all(pathlib.Path(f["path"]).is_absolute() and "\\" not in f["path"] for f in R["files"]) and [f["bytes"] for f in R["files"]] == sizes
       and [f["sha256"] for f in R["files"]] == [hashlib.sha256((FREE / r_["file"]).read_bytes()).hexdigest() for r_ in rec]
       and R["parts"] == [[0, 1], [2]] and R["subject"] == "IG post: Armenia (3 slides)" and R["body"].startswith(CAP + "\n\n#armenia #yerevan\n")
       and "How to post" in R["body"] and "01 to 03" in R["body"] and "@" not in REQF.read_text(encoding="utf-8"), (st, rq, {k: R.get(k) for k in ("parts", "subject", "via")}))
    st, rq_again = api.call(PS + "email", {})
    ok("E4b pressing it again while it waits does not double it (already, the same id)", st == 200 and rq_again.get("already") is True and rq_again["email"]["id"] == R.get("id"), rq_again)
    rc, lst = cli("--email-requests")
    one = lst[0] if isinstance(lst, list) and lst else {}
    refs = [a["ref"] for m in one.get("emails", []) for a in m["attachments"]]
    ok("E5 --email-requests hands it to Claude: one email per part ('1 of 2', '2 of 2'), attachments as sha256:<hex>:<absolute path> in order, the caption only in part 1, no problems",
       rc == 0 and len(lst) == 1 and one["slug"] == "armenia" and one["id"] == R["id"] and one["problems"] == []
       and [m["subject"] for m in one["emails"]] == ["IG post: Armenia (3 slides) 1 of 2", "IG post: Armenia (3 slides) 2 of 2"]
       and refs == ["sha256:%s:%s" % (f["sha256"], f["path"]) for f in R["files"]] and [a["filename"] for m in one["emails"] for a in m["attachments"]] == ["01.jpg", "02.jpg", "03.jpg"]
       and one["emails"][0]["body"].startswith(CAP) and CAP not in one["emails"][1]["body"] and "Part 2 of 2" in one["emails"][1]["body"], (rc, str(lst)[:600]))
    rc, _ = cli("--email-sent", "armenia", R["id"], "msg-a", "msg-b")
    _, g = api.call(PS + "email"); _, lst2 = cli("--email-requests")
    R2 = json.loads(REQF.read_text(encoding="utf-8"))
    ok("E5b --email-sent marks it sent (sent_at, the message ids); GET /email says sent and nothing is pending", rc == 0 and g["email"]["status"] == "sent" and g["email"]["sent_at"]
       and R2["message_ids"] == ["msg-a", "msg-b"] and lst2 == [], (rc, g, lst2))
    rc_bad, _ = cli("--email-failed", "armenia", "no-such-id", "x")
    st, rq3 = api.call(PS + "email", {})
    rc3, _ = cli("--email-failed", "armenia", rq3["email"]["id"], "Gmail", "said", "no")
    _, g2 = api.call(PS + "email")
    ok("E5c --email-failed records his reason (an unknown id is an error)", rc_bad != 0 and rc3 == 0 and g2["email"]["status"] == "failed" and g2["email"]["error"] == "Gmail said no", (rc_bad, rc3, g2))
    st, rq4 = api.call(PS + "email", {})
    keep = (FREE / rec[2]["file"]).read_bytes(); (FREE / rec[2]["file"]).write_bytes(keep + b"\0")
    _, lst3 = cli("--email-requests")
    (FREE / rec[2]["file"]).write_bytes(keep)
    cli("--email-failed", "armenia", rq4["email"]["id"], "test")
    ok("E5d a file that changed after the request is flagged to Claude (problems: 03.jpg changed)", isinstance(lst3, list) and lst3 and any("03.jpg" in p_ for p_ in lst3[0]["problems"]), lst3)
    # Gmail keys present: the button SENDS (here to the fake sink on loopback)
    env_keys(True)
    n0 = len(MAILS)
    st, rs = api.call(PS + "email", {})
    jd = api.wait_job(rs["job"]["id"], 60) if st == 200 and rs.get("job") else {}
    got = MAILS[n0:]
    msgs = [email.message_from_bytes(m["data"], policy=email.policy.default) for m in got]
    atts = [(a.get_filename(), a.get_content()) for msg in msgs for a in msg.iter_attachments()]
    R3 = json.loads(REQF.read_text(encoding="utf-8"))
    ok("E6 with the keys it SENDS (to the fake sink): one message per part, From/To from .env, '1 of 2' / '2 of 2', the attachments byte-identical to the render's JPEGs as 01.jpg to 03.jpg "
       "in order, a plain-text body with the caption first; the request says sent with the two Message-IDs",
       st == 200 and rs["job"]["kind"] == "email" and jd.get("status") == "done" and len(got) == 2 and [m_["subject"] for m_ in msgs] == ["IG post: Armenia (3 slides) 1 of 2", "IG post: Armenia (3 slides) 2 of 2"]
       and msgs[0]["To"] == SINK_TO and msgs[0]["From"] == SINK_USER and got[0].get("auth") == [SINK_USER, SINK_PW]
       and [a[0] for a in atts] == ["01.jpg", "02.jpg", "03.jpg"] and [a[1] for a in atts] == [(FREE / r_["file"]).read_bytes() for r_ in rec]
       and msgs[0].get_body(("plain",)).get_content().startswith(CAP) and R3["status"] == "sent" and len(R3.get("message_ids") or []) == 2 and R3["via"] == "smtp",
       (st, jd.get("status"), jd.get("error"), len(got), [m_["subject"] for m_ in msgs], [a[0] for a in atts]))
    leak = json.dumps(jd) + REQF.read_text(encoding="utf-8") + json.dumps(api.call(PS + "post")[1]) + json.dumps(api.call(PS + "email")[1]) + json.dumps(api.call("/api/social")[1])
    ok("E6b the password is nowhere: not in the job, the request file, /post, /email or /api/social", SINK_PW not in leak, "")
    env_keys(True, pw="wrong-pass-" + uuid.uuid4().hex[:6])
    st, rs2 = api.call(PS + "email", {})
    jd2 = api.wait_job(rs2["job"]["id"], 60) if st == 200 and rs2.get("job") else {}
    _, g3 = api.call(PS + "email")
    ok("E6c a refused sign-in fails with a plain reason (check the app password in .env) and echoes no password", jd2.get("status") == "failed" and "app password" in (jd2.get("error") or "")
       and g3["email"]["status"] == "failed" and "wrong-pass" not in json.dumps(jd2) + REQF.read_text(encoding="utf-8"), (jd2.get("status"), jd2.get("error")))
    env_keys(True)
    sys.path.insert(0, str(ROOT / "tools"))
    import social_tab as stb
    saved = {k: os.environ.get(k) for k in ("SOCIAL_SMTP_HOST", "SOCIAL_IG_DIR", "SOCIAL_ENV_FILE")}
    os.environ.update(SOCIAL_IG_DIR=str(IG), SOCIAL_ENV_FILE=str(ENVF)); os.environ.pop("SOCIAL_SMTP_HOST", None)
    guard = stb.smtp_config()
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    ok("E7 a test store never reaches smtp.gmail.com (no SOCIAL_SMTP_HOST = no send config); parts keep the order and a file over the cap goes alone",
       guard is None and stb.split_parts([5, 5, 5], 10) == [[0, 1], [2]] and stb.split_parts([30, 1], 10) == [[0], [1]], guard is None)
    for x in E3:
        api.call("/api/social/armenia/db", {"op": "delete", "path": "photos/" + x})
    setdoc("post/main", {"status": "draft", "aspect": "4:5"})
    shutil.rmtree(FREE, ignore_errors=True); REQF.unlink(missing_ok=True)

    # ----------------------------------------------------------------------------- RENDER (a real one, in the background while the UI is checked)
    st, r = api.call("/api/social/armenia/render", {"kind": "carousel"})
    job = r.get("job") or {}
    st2, r2 = api.call("/api/social/armenia/render", {"kind": "carousel"})
    ok("R1 a carousel render starts as a job; a second one for the same country is refused (409) while it runs", st == 200 and job.get("id") and job.get("status") in ("queued", "running") and st2 == 409, (st, r, st2, r2))
    st, lst = api.call("/api/social")
    ok("R1b /api/social says the country is rendering", st == 200 and next(x for x in lst["countries"] if x["slug"] == "armenia")["running"] is True, st)
    st, e = api.call("/api/social/armenia/render", {"kind": "nope"})
    ok("R1c an unknown kind is refused", st in (400, 409), (st, e))

    # ----------------------------------------------------------------------------- PAGE + UI
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        # the stored choices are wiped once per origin (sessionStorage flag), so a reload can still remember them
        ctx.add_init_script(f"window.SOCIAL_API = 'http://127.0.0.1:{PORT}'; try {{ if (!sessionStorage.getItem('so-test')) {{ localStorage.clear(); sessionStorage.setItem('so-test', '1'); }} }} catch (e) {{}}")
        errors = []
        pg = ctx.new_page(); pg.on("pageerror", lambda e: errors.append(str(e)))
        pend = {}
        pg.on("request", lambda r: pend.__setitem__(r.url, 1)); pg.on("requestfinished", lambda r: pend.pop(r.url, None)); pg.on("requestfailed", lambda r: pend.pop(r.url, None))
        try:
            pg.goto(f"http://127.0.0.1:{PORT}/social/armenia/page", wait_until="domcontentloaded")
            pg.wait_for_load_state("load", timeout=30000)
        except Exception as e:
            print("  (the page's load event is late; still waiting on: %s)" % list(pend)[:6], flush=True)
        pg.wait_for_selector(".card[data-id]", timeout=20000); pg.wait_for_timeout(500)
        n_cards = pg.locator(".card[data-id]").count()
        ok("P1 the page served with the shim loads every photo of the local catalog, titled for the country, with Claude's own runtime absent", n_cards == len(man["photos"]) and pg.inner_text("#title").startswith("Armenia")
           and pg.evaluate("window.claude && window.claude.local === true") and pg.evaluate("window.__socialDb.loaded()"), (n_cards, errors[:2]))
        if web_ids:
            src = pg.get_attribute(f".card[data-id='{web_ids[0]}'] img", "src")
            ok("P1b a card's image is the blob route of this server", src and f"/api/social/armenia/blob/{web_ids[0]}" in src, src)
        ok("P1c standalone (the artifacts' look): the page keeps its own header, the shortcut hint line is gone and a ? button holds the keys", pg.locator("header.top").is_visible()
           and pg.locator("#hint").count() == 0 and pg.locator("#helpBtn").count() == 1 and "Shift+arrows" in (pg.get_attribute("#helpBtn", "title") or "")
           and not pg.evaluate("document.documentElement.classList.contains('embed')"), pg.get_attribute("#helpBtn", "title"))
        pf = IG / "armenia" / "state" / "photos" / (pid + ".json")
        pg.locator(f".card[data-id='{pid}']").focus(); pg.keyboard.press("s"); pg.wait_for_timeout(700)
        ok("P2 the S key on a photo in the Shortlist takes it out through the shim: the page deletes photos/<id>.json", not pf.is_file(), pf.is_file())
        pg.keyboard.press("s"); pg.wait_for_timeout(700)
        ok("P2b ...and again puts it in the first free Shortlist slot (11): state final, slot 11", pf.is_file() and json.loads(pf.read_text(encoding="utf-8")).get("slot") == 11
           and json.loads(pf.read_text(encoding="utf-8")).get("state") == "final", pf.read_text(encoding="utf-8") if pf.is_file() else None)
        pg.locator("#modeSeg button[data-mode='template']").click(); pg.wait_for_timeout(800)
        ok("P3 the Itinerary template mode opens on the plan from the store (its stops drawn)", pg.locator("#modeSeg button[data-mode='template'][aria-pressed='true']").count() == 1
           and pg.evaluate("Object.keys(window.__socialDb.docs()).includes('carousel/main')") and pg.locator("main").inner_text().strip() != "", pg.locator("main").inner_text()[:120])
        ok("P4 no page errors in the maker", not errors, errors[:3])
        pg.close()

        # the editor
        pg2 = ctx.new_page(); errs2 = []
        pg2.on("pageerror", lambda e: errs2.append(str(e)))
        pg2.goto(EDITOR + "#social")
        pg2.wait_for_selector("#app-social:not([hidden]) .so-tabs", timeout=20000); pg2.wait_for_timeout(500)
        tab = pg2.locator(".app-tabs a[data-view='social']").first
        views = pg2.evaluate("[...document.querySelectorAll('.app-tabs a')].map(a => a.dataset.view)")
        ok("U1 the Social tab sits between Launch and Queue, tooltip says Ctrl+Alt+6", tab.count() and "Ctrl+Alt+6" in (tab.get_attribute("title") or "") and views[-3:] == ["launch", "social", "queue"], (views, tab.get_attribute("title")))
        pg2.keyboard.press("Control+Alt+7"); pg2.wait_for_timeout(200)
        v7 = pg2.evaluate("[document.getElementById('app-social').hidden, document.getElementById('app-queue').hidden]")
        pg2.keyboard.press("Control+Alt+6"); pg2.wait_for_timeout(300)
        v6 = pg2.evaluate("[document.getElementById('app-social').hidden, document.getElementById('app-queue').hidden, location.hash]")
        ok("U2 Ctrl+Alt+7 shows Queue, Ctrl+Alt+6 brings Social back (#social)", v7 == [True, False] and v6 == [False, True, "#social"], (v7, v6))
        ok("U3 the country picker shows Armenia with its flag, the claude.ai link and the maker iframe on this server in embed mode", pg2.locator(".so-country").input_value() == "armenia"
           and "flags/am.png" in (pg2.get_attribute(".so-pick img", "src") or "") and "claude.ai/artifact/" in (pg2.get_attribute(".so-btn.art", "href") or "")
           and f"127.0.0.1:{PORT}/social/armenia/page?embed=1" in (pg2.get_attribute("#so-frame", "src") or ""), pg2.get_attribute("#so-frame", "src"))
        seg = pg2.locator(".so-seg:not(.sm) button")
        ok("U3a the two output types sit at the top, Personal post on by default", seg.count() == 2 and seg.nth(0).inner_text() == "Personal post" and seg.nth(1).inner_text() == "Travel Blog post"
           and pg2.locator(".so-seg button.on[data-type='personal']").count() == 1, [seg.nth(i).inner_text() for i in range(seg.count())])
        fr = pg2.frame_locator("#so-frame")
        fr.locator(".card[data-id]").first.wait_for(timeout=20000)
        pg2.wait_for_function("document.querySelector('.so-tab[data-view=\"all\"] .n') !== null", timeout=15000)
        ok("U3b the maker inside the tab shows the photos with NO inner header (html.embed, the editor's fonts), and the Photos sub-tab carries the page's count",
           fr.locator(".card[data-id]").count() == len(man["photos"]) and fr.locator("header.top").is_hidden() and fr.locator("html.embed").count() == 1
           and pg2.inner_text(".so-tab[data-view='all'] .n") == str(len(man["photos"]))
           and "Hanken" in fr.locator("body").evaluate("e => getComputedStyle(e).fontFamily"), (pg2.inner_text(".so-tabs"), fr.locator("body").evaluate("e => getComputedStyle(e).fontFamily")))
        labels = pg2.evaluate("[...document.querySelectorAll('.so-tab')].map(b => b.dataset.view)")
        ok("U3c Personal post's sub-tabs: Photos, Shortlist (the slots), Archived, Caption | Post, Stories; the filter, the ratio switch, Add photos, Preview, ? and Render in the bar",
           labels == ["all", "final", "archived", "caption", "renders", "stories"] and pg2.inner_text(".so-tab[data-view='final']").lower().startswith("shortlist")
           and pg2.locator(".so-sel[data-act='article']").count() == 1 and pg2.locator(".so-seg.sm button[data-act='aspect']").count() == 4
           and pg2.locator(".so-btn[data-act='library']").count() == 1 and pg2.locator(".so-btn[data-act='preview']").count() == 1
           and "Shift+arrows" in (pg2.get_attribute(".so-ib[data-act='help']", "title") or "") and pg2.inner_text(".so-btn[data-act='rendernow']").strip().lower() == "render"
           and pg2.locator(".so-pill").count() == 0 and "ready to render" not in pg2.inner_text("#app-social").lower() and fr.locator("#hint").count() == 0, (labels, pg2.inner_text(".so-tabs .acts")))
        ok("U3g the three round buttons at the top (open in a window, import, reload) are gone; the claude.ai link stays",
           pg2.locator(".so-top .so-ib").count() == 0 and pg2.locator(".so-top [data-act='import'], .so-top [data-act='reload']").count() == 0 and pg2.locator(".so-top .so-btn.art").count() == 1, pg2.inner_html(".so-top")[-300:])
        cards = fr.locator(".card[data-id]")
        tops = fr.locator(".grid").evaluate("g => [...g.querySelectorAll('.card')].slice(0, 6).map(c => Math.round(c.getBoundingClientRect().top))")
        fo = fr.locator("html").evaluate("e => [e.scrollWidth, e.clientWidth]")
        ok("N1 Photos: five to a row, no sideways scroll, no caption overlay, and each card's only button is archive (no shortlist star)",
           len(set(tops[:5])) == 1 and tops[5] > tops[0] and fo[0] <= fo[1] and fr.locator(".card .over").count() == 0 and fr.locator(".card [data-act='star']").count() == 0
           and cards.first.locator("button").count() == 1 and cards.first.locator("button[data-act='arch']").count() == 1, (tops, fo))
        frame_el = pg2.evaluate_handle("document.getElementById('so-frame')")
        pg2.locator(".so-tab[data-view='final']").click(); pg2.wait_for_timeout(700)
        fin, sho = fr.locator(".rowwrap.final .frame"), fr.locator(".rowwrap.backlog .frame")
        ok("U3d the Shortlist sub-tab drives the page's view (the slots: a Final row of 10 and the Shortlist, the photo in its first slot; the iframe stays)",
           fin.count() == 10 and sho.count() >= 20 and fr.locator(f".rowwrap.backlog .frame[data-id='{pid}'][data-slot='11']").count() == 1
           and pg2.locator(".so-tab.on[data-view='final']").count() == 1 and pg2.evaluate("el => el === document.getElementById('so-frame')", frame_el), (fin.count(), sho.count()))
        tops = fr.locator(".rowwrap.final .slots").evaluate("g => [...g.querySelectorAll('.frame')].map(c => Math.round(c.getBoundingClientRect().top))")
        fo = fr.locator("html").evaluate("e => [e.scrollWidth, e.clientWidth]")
        slots_over = fr.locator(".slots").first.evaluate("e => [e.scrollWidth, e.clientWidth]")
        empty_txt = fr.locator(".rowwrap.final .frame[data-slot='1']").inner_text().strip()
        filled = fr.locator(f".frame[data-id='{pid}']")
        ok("N2 slots: five to a row, no sideways scroll; an empty slot holds only a +; a filled one has exactly two buttons (edit, remove), no caption line, and a Notes field",
           len(set(tops[:5])) == 1 and tops[5] > tops[0] and fo[0] <= fo[1] and slots_over[0] <= slots_over[1] and empty_txt == "+"
           and filled.locator("button").count() == 2 and filled.locator("button[data-act='crop']").count() == 1 and filled.locator("button[data-act='unslot']").count() == 1
           and fr.locator(".slot .cap").count() == 0 and fr.locator(f"textarea[data-note='{pid}']").get_attribute("placeholder") == "Notes", (tops, fo, slots_over, empty_txt))
        post_f = IG / "armenia" / "state" / "post" / "main.json"
        rd_post = lambda: json.loads(post_f.read_text(encoding="utf-8")) if post_f.is_file() else {}
        fr.locator(".rowwrap.final .rowhead .rbtn[data-slots='1']").click(); pg2.wait_for_timeout(800)
        ok("N3 + on the Final row adds slide 11: post/main.nfinal = 11 and the Shortlist moved one slot along (the photo in 11 is now in 12, not in the post)",
           rd_post().get("nfinal") == 11 and json.loads(pf.read_text(encoding="utf-8"))["slot"] == 12 and fr.locator(".rowwrap.final .frame").count() == 11
           and "/11" in pg2.inner_text(".so-tab[data-view='final'] .n"), (rd_post(), pf.read_text(encoding="utf-8")))
        fr.locator(".rowwrap.final .rowhead .rbtn[data-slots='-1']").click(); pg2.wait_for_timeout(800)
        ok("N3b − takes it back to 10 (the button only shows past 10; Instagram's cap of 20 hides the +)", rd_post().get("nfinal") == 10 and fr.locator(".rowwrap.final .frame").count() == 10
           and fr.locator(".rowwrap.final .rowhead .rbtn[data-slots='-1']").count() == 0, rd_post())
        # the grid's keys: arrows walk the slots (the empty ones too), 1 puts the photo on slide 1, Delete takes it out
        fr.locator(".rowwrap.final .frame[data-slot='1']").click(); pg2.wait_for_timeout(150)
        pg2.keyboard.press("ArrowRight"); pg2.keyboard.press("ArrowDown"); pg2.wait_for_timeout(150)
        at = fr.locator("html").evaluate("() => document.activeElement && document.activeElement.dataset.slot")
        fr.locator(f".frame[data-id='{pid}']").focus(); pg2.keyboard.press("1"); pg2.wait_for_timeout(700)
        on1 = json.loads(pf.read_text(encoding="utf-8")).get("slot")
        pg2.keyboard.press("Shift+ArrowRight"); pg2.wait_for_timeout(700)
        on2 = json.loads(pf.read_text(encoding="utf-8")).get("slot")
        ok("N4 keys: → then ↓ from slot 1 lands on slot 7; 1 puts the focused photo on slide 1; Shift+→ onto the empty slot 2 leaves it on 1 (no blank in front of a photo)",
           at == "7" and on1 == 1 and on2 == 1, (at, on1, on2))
        # the ratio switch in the tab: the post and the slots follow
        pg2.locator(".so-seg.sm button[data-aspect='1.91:1']").click(); pg2.wait_for_timeout(800)
        r = fr.locator(f".frame[data-id='{pid}']").evaluate("e => { const b = e.getBoundingClientRect(); return b.width / b.height; }")
        ok("N5 the ratio switch (4:5, 3:4, 1:1, 1.91:1) sets post/main.aspect and the slides draw at it (1.91:1 here)", rd_post().get("aspect") == "1.91:1" and abs(r - 1.91) < .08
           and pg2.locator(".so-seg.sm button.on[data-aspect='1.91:1']").count() == 1, (rd_post().get("aspect"), r))
        pg2.locator(".so-seg.sm button[data-aspect='3:4']").click(); pg2.wait_for_timeout(700)
        r = fr.locator(f".frame[data-id='{pid}']").evaluate("e => { const b = e.getBoundingClientRect(); return b.width / b.height; }")
        ok("N5b ...and 3:4 (Instagram's tall portrait since May 2025)", rd_post().get("aspect") == "3:4" and abs(r - .75) < .03, (rd_post().get("aspect"), r))
        pg2.locator(".so-seg.sm button[data-aspect='4:5']").click(); pg2.wait_for_timeout(500)
        # the preview: only the photos, big, on black; the iframe takes the window while it is open
        pg2.locator(".so-btn[data-act='preview']").click(); pg2.wait_for_timeout(900)
        pv = fr.locator("#pv")
        full_on = pg2.evaluate("document.getElementById('so-frame').classList.contains('full')")
        box = pg2.evaluate("(() => { const b = document.getElementById('so-frame').getBoundingClientRect(); return [b.width, b.height]; })()")
        bg = pv.evaluate("e => getComputedStyle(e).backgroundColor") if pv.is_visible() else None
        ok("N6 Preview: the iframe fills the window, the preview is black with the slide drawn big and the library's side arrows, nothing else",
           full_on and box == [1440, 900] and pv.is_visible() and bg == "rgb(0, 0, 0)" and fr.locator("#pvStage canvas").count() == 1 and fr.locator("#pv .sidenav").count() == 2
           and fr.locator("#pvStage canvas").evaluate("c => c.getBoundingClientRect().height") > 600, (full_on, box, bg))
        pg2.screenshot(path=str(SHOTS / "social-redesign-test-preview-1440.png"))
        pg2.keyboard.press("Escape"); pg2.wait_for_timeout(400)
        ok("N6b Esc closes it and the iframe goes back into the tab", not pv.is_visible() and not pg2.evaluate("document.getElementById('so-frame').classList.contains('full')"), "")
        # the crop editor: big side arrows, four ratios
        fr.locator(f".frame[data-id='{pid}']").hover(); fr.locator(f".frame[data-id='{pid}'] button[data-act='crop']").click(); pg2.wait_for_timeout(600)
        sl = fr.locator("#modal .sidenav.l").evaluate("e => { const b = e.getBoundingClientRect(); return [b.left, b.width, b.height]; }")
        ok("N7 the crop editor: Prev / Next are the big side arrows at the window's edges, the ratio buttons are 4:5 3:4 1:1 1.91:1, the iframe fills the window",
           fr.locator("#modal").is_visible() and sl[0] < 30 and sl[1] >= 50 and sl[2] >= 80 and fr.locator("#modal .nav #prevBtn").count() == 0
           and [b.inner_text() for b in fr.locator("#aspectSeg button").all()] == ["4:5", "3:4", "1:1", "1.91:1"]
           and pg2.evaluate("document.getElementById('so-frame').classList.contains('full')"), sl)
        pg2.screenshot(path=str(SHOTS / "social-redesign-test-crop-1440.png"))
        fr.locator("#doneBtn").click(); pg2.wait_for_timeout(400)
        fr.locator(f".frame[data-id='{pid}']").focus(); pg2.keyboard.press("Delete"); pg2.wait_for_timeout(700)
        ok("N8 Delete takes the photo out of its slot (back with the photos: no doc left)", not pf.is_file() and not pg2.evaluate("document.getElementById('so-frame').classList.contains('full')"), pf.is_file())
        api.call("/api/social/armenia/db", {"op": "set", "path": "photos/" + pid, "data": {"state": "final", "slot": 11, "order": 0, "note": "", "crop": None}})

        # ---- Kevin, 2026-11-11, second round. 1: a drop INSERTS and the rest move along one (no swap), in the big grid and
        # the strip; 2: + / − on both strip rows, drags between them; 3: no blanks between photos; 4: a click previews
        A, B, C, D = (man["photos"][k]["id"] for k in (4, 5, 6, 7))
        E, F = man["photos"][1]["id"], man["photos"][3]["id"]                 # two photos in the pool, on the Photos grid's first row
        def setp(x, s):
            api.call("/api/social/armenia/db", {"op": "set", "path": "photos/" + x, "data": {"state": "final", "slot": s, "order": 0, "note": "", "crop": None}})
        def slots(*ids):
            out = []
            for x in ids:
                f_ = IG / "armenia" / "state" / "photos" / (x + ".json")
                out.append(json.loads(f_.read_text(encoding="utf-8")).get("slot") if f_.is_file() else None)
            return out
        def repoll():
            fr.locator("html").evaluate("() => window.__socialDb.poll()"); pg2.wait_for_timeout(600)
        def drag(src, dst):
            src.scroll_into_view_if_needed(); a = src.bounding_box(); b_ = dst.bounding_box()
            x0, y0 = a["x"] + a["width"] / 2, a["y"] + a["height"] / 2
            pg2.mouse.move(x0, y0); pg2.mouse.down(); pg2.mouse.move(x0 + 14, y0 + 14, steps=3)
            pg2.mouse.move(b_["x"] + b_["width"] / 2, b_["y"] + b_["height"] / 2, steps=12); pg2.mouse.up(); pg2.wait_for_timeout(900)
        for x, s in ((A, 1), (B, 2), (C, 3), (D, 12)):
            setp(x, s)
        repoll()
        pg2.set_viewport_size({"width": 1440, "height": 1900}); pg2.wait_for_timeout(500)    # both rows of the big grid on screen at once
        fx = lambda x: fr.locator(f".slots .frame[data-id='{x}']")
        drag(fx(C), fx(A))
        ok("I1 big grid: C dragged from slide 3 onto slide 1 is INSERTED there; A and B move along one in their order (no swap): C1 A2 B3",
           slots(C, A, B) == [1, 2, 3] and fr.locator("#pv").is_hidden(), slots(C, A, B))
        drag(fx(D), fx(A))
        ok("I2 big grid: D dragged from the Shortlist onto slide 2 lands there, A and B move down: C1 D2 A3 B4, the Shortlist closes up (pid 11)",
           slots(C, D, A, B, pid) == [1, 2, 3, 4, 11], slots(C, D, A, B, pid))
        drag(fx(A), fx(pid))
        ok("I3 big grid: A dragged from slide 3 onto the Shortlist's first photo goes in front of it; the Final row closes its gap: C1 D2 B3 | A11 pid12",
           slots(C, D, B, A, pid) == [1, 2, 3, 11, 12], slots(C, D, B, A, pid))
        drag(fx(C), fr.locator(".rowwrap.final .frame[data-slot='9']"))
        ok("G1 a photo dropped on an empty slot past the row's last photo goes to the row's end, no blanks: D1 B2 C3",
           slots(D, B, C) == [1, 2, 3] and fr.locator(".rowwrap.final .frame.filled").count() == 3
           and fr.locator(".rowwrap.final .frame[data-slot='3'][data-id]").count() == 1 and fr.locator(".rowwrap.final .frame[data-slot='4'][data-id]").count() == 0, slots(D, B, C))
        fx(D).focus(); pg2.keyboard.press("Delete"); pg2.wait_for_timeout(800)
        ok("G2 Delete on slide 1 closes the gap: B1 C2 (D out, no doc), the Shortlist untouched (A11 pid12)",
           slots(B, C, A, pid, D) == [1, 2, 11, 12, None], slots(B, C, A, pid, D))
        ok("L0 the Shortlist row head has − / + too", fr.locator(".rowwrap.backlog .rowhead .rbtn[data-slots='1']").count() == 1
           and fr.locator(".rowwrap.backlog .rowhead .rbtn[data-slots='-1']").count() == 1, fr.locator(".rowwrap.backlog .rowhead").inner_html()[-300:])
        pg2.screenshot(path=str(SHOTS / "social-strip-test-grid-1440.png"))
        pg2.set_viewport_size({"width": 1440, "height": 900}); pg2.wait_for_timeout(300)

        # the strip (Photos view)
        pg2.locator(".so-tab[data-view='all']").click(); pg2.wait_for_timeout(700)
        mf, ms = fr.locator("#minis .mini"), fr.locator("#minisBl .mini")
        ok("L1 the strip: the Final row's 10 slots then +, the Shortlist's slots then − and +",
           mf.count() == 10 and ms.count() == 20 and fr.locator("#sbF button[data-slots='1']").count() == 1 and fr.locator("#sbF button[data-slots='-1']").count() == 0
           and fr.locator("#sbS button[data-slots='1']").count() == 1 and fr.locator("#sbS button[data-slots='-1']").count() == 1, (mf.count(), ms.count()))
        fr.locator("#sbF button[data-slots='1']").click(); pg2.wait_for_timeout(800)
        l1b = (rd_post().get("nfinal"), mf.count(), slots(A, pid))
        fr.locator("#sbF button[data-slots='-1']").click(); pg2.wait_for_timeout(800)
        ok("L1b strip + on the Final row adds slide 11 (the Shortlist moves along: A12 pid13), − takes it back (A11 pid12)",
           l1b == (11, 11, [12, 13]) and rd_post().get("nfinal") == 10 and mf.count() == 10 and slots(A, pid) == [11, 12], (l1b, rd_post(), slots(A, pid)))
        fr.locator("#sbS button[data-slots='1']").click(); pg2.wait_for_timeout(600)
        l2 = (rd_post().get("nshort"), ms.count())
        fr.locator("#sbS button[data-slots='-1']").click(); pg2.wait_for_timeout(500); fr.locator("#sbS button[data-slots='-1']").click(); pg2.wait_for_timeout(600)
        ok("L2 strip + on the Shortlist row adds a slot (post/main.nshort 21), − twice takes it to 19", l2 == (21, 21) and rd_post().get("nshort") == 19 and ms.count() == 19, (l2, rd_post(), ms.count()))
        mini = lambda n, row="#minis": fr.locator(f"{row} .mini[data-slot='{n}']")
        drag(mini(11, "#minisBl"), mini(1))
        ok("L3 strip: A dragged from the Shortlist row onto slide 1 is inserted there: A1 B2 C3 | pid11", slots(A, B, C, pid) == [1, 2, 3, 11], slots(A, B, C, pid))
        drag(mini(3), mini(1))
        ok("L4 strip: C dragged from slide 3 onto slide 1 inside the Final row: C1 A2 B3 (order kept, not swapped)", slots(C, A, B) == [1, 2, 3], slots(C, A, B))
        drag(fr.locator(f".card[data-id='{E}']"), mini(11, "#minisBl"))
        ok("L5 a photo dragged from the Photos grid onto the Shortlist row's first photo goes in front of it: E11 pid12, its card marked as shortlisted",
           slots(E, pid) == [11, 12] and fr.locator(f".card[data-id='{E}'] .badge.dot").count() == 1, slots(E, pid))
        drag(mini(2), mini(14, "#minisBl"))
        ok("L6 strip: a slide dropped on an empty Shortlist slot past the last photo goes to the Shortlist's end: C1 B2 | E11 pid12 A13", slots(C, B, E, pid, A) == [1, 2, 11, 12, 13], slots(C, B, E, pid, A))
        pg2.screenshot(path=str(SHOTS / "social-strip-test-strip-1440.png"))

        # the preview opens on a click
        fr.locator(f".card[data-id='{F}']").click(); pg2.wait_for_timeout(700)
        pvacts = lambda: [b.get_attribute("data-pva") for b in fr.locator("#pvActs button").all()]
        ok("V1 a click on a photo opens the preview on it (not the crop editor): the iframe fills the window, the photo whole, Edit and Add to shortlist under it",
           fr.locator("#pv").is_visible() and fr.locator("#modal").is_hidden() and F in (fr.locator("#pvStage img").get_attribute("src") or "")
           and pvacts() == ["edit", "short"] and pg2.evaluate("document.getElementById('so-frame').classList.contains('full')"), (pvacts(), fr.locator("#pv").is_visible()))
        pg2.screenshot(path=str(SHOTS / "social-strip-test-preview-1440.png"))
        fr.locator("#pvActs [data-pva='short']").click(); pg2.wait_for_timeout(700)
        v2 = (slots(F), pvacts())
        fr.locator("#pvActs [data-pva='tofinal']").click(); pg2.wait_for_timeout(700)
        v3 = (slots(F), pvacts(), fr.locator("#pvActs .pv-where").inner_text())
        fr.locator("#pvActs [data-pva='toshort']").click(); pg2.wait_for_timeout(700)
        v4 = (slots(F, E), pvacts())
        ok("V2 the preview's buttons: Add to shortlist -> the Shortlist's end (14), then Move to Final / Remove; Move to Final -> slide 3 (the end of the Final row), "
           "then Move to Shortlist -> the Shortlist's head (11, E 12); the preview stays open on it",
           v2 == ([14], ["edit", "tofinal", "unslot"]) and v3[0] == [3] and v3[1] == ["edit", "toshort"] and v3[2].lower().startswith("slide 3")
           and v4 == ([11, 12], ["edit", "tofinal", "unslot"]) and fr.locator("#pv").is_visible(), (v2, v3, v4))
        pg2.keyboard.press("s"); pg2.wait_for_timeout(700)
        v5 = (slots(F), pvacts())
        pg2.keyboard.press("s"); pg2.wait_for_timeout(700)
        ok("V3 S in the preview: a Shortlist photo comes out (no doc), and S again adds it to the Shortlist's end",
           v5 == ([None], ["edit", "short"]) and slots(F) == [14], (v5, slots(F)))
        pg2.keyboard.press("Enter"); pg2.wait_for_timeout(700)
        ok("V4 Enter in the preview opens the crop editor on that photo (the preview closes)", fr.locator("#modal").is_visible() and fr.locator("#pv").is_hidden()
           and fr.locator("#edTitle").inner_text().strip() == "Shortlist 4", fr.locator("#edTitle").inner_text())
        fr.locator("#doneBtn").click(); pg2.wait_for_timeout(500)
        mini(1).click(); pg2.wait_for_timeout(700)
        ok("V5 a click on a strip photo previews its row, cropped (canvas), 'Slide 1 of 10', the editor stays shut",
           fr.locator("#pv").is_visible() and fr.locator("#pvStage canvas").count() == 1 and fr.locator("#pvActs .pv-where").inner_text().lower() == "slide 1 of 10"
           and fr.locator("#modal").is_hidden(), fr.locator("#pvActs").inner_text())
        pg2.keyboard.press("ArrowRight"); pg2.wait_for_timeout(300)
        v5b = fr.locator("#pvActs .pv-where").inner_text().lower()
        fr.locator("#pvActs [data-pva='edit']").click(); pg2.wait_for_timeout(600)
        ok("V6 → steps along the row (slide 2 of 10); the Edit button opens the crop editor on it", v5b == "slide 2 of 10" and fr.locator("#modal").is_visible()
           and fr.locator("#edTitle").inner_text().strip() == "Slide 2", (v5b, fr.locator("#edTitle").inner_text()))
        fr.locator("#doneBtn").click(); pg2.wait_for_timeout(500)
        pg2.locator(".so-tab[data-view='final']").click(); pg2.wait_for_timeout(700)
        fx(E).click(); pg2.wait_for_timeout(600)
        ok("V7 Shortlist view: a click on a Shortlist photo opens the preview of the Shortlist ('Shortlist 1'), not the crop editor",
           fr.locator("#pv").is_visible() and fr.locator("#modal").is_hidden() and fr.locator("#pvActs .pv-where").inner_text().lower() == "shortlist 1", fr.locator("#pvActs").inner_text())
        pg2.keyboard.press("Escape"); pg2.wait_for_timeout(300)
        fx(E).focus(); pg2.keyboard.press("Enter"); pg2.wait_for_timeout(500)
        ok("V8 Enter on a focused slot still opens the crop editor directly", fr.locator("#modal").is_visible() and fr.locator("#pv").is_hidden(), "")
        fr.locator("#doneBtn").click(); pg2.wait_for_timeout(400)
        # clean up: back to pid alone in the Shortlist, as the later checks expect
        for x in (A, B, C, D, E, F):
            api.call("/api/social/armenia/db", {"op": "delete", "path": "photos/" + x})
        setp(pid, 11)
        repoll()
        pg2.locator(".so-tab[data-view='all']").click(); pg2.wait_for_timeout(600)
        pg2.locator(".so-sel[data-act='article']").select_option(index=1); pg2.wait_for_timeout(600)
        picked = pg2.evaluate("document.querySelector('.so-sel[data-act=\"article\"]').value")
        n_f = fr.locator(".card[data-id]").count()
        ok("U3e the article filter in the tab's bar filters the page", picked and 0 < n_f < len(man["photos"]) and n_f == sum(1 for x in man["photos"] if x["article"] == picked), (picked, n_f))
        pg2.locator(".so-sel[data-act='article']").select_option(value=""); pg2.wait_for_timeout(500)
        pg2.screenshot(path=str(SHOTS / "social-tab-personal-1440.png"))

        # photos from the photo library: the album routes are the photo server's own (mocked here), the add is real, from
        # the scratch backup folder (SOCIAL_BACKUP_DIR)
        jpg = (BACKUP / ALBUM / lib_names[0]).read_bytes()
        pg2.route(f"http://127.0.0.1:{PORT}/api/albums", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps({"albums": [{"name": "Albania (2025)"}, {"name": ALBUM}]})))
        pg2.route(f"http://127.0.0.1:{PORT}/api/backup_browse*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps({"album": ALBUM, "photos": [{"n": x, "picked": i == 0} for i, x in enumerate(lib_names)]})))
        pg2.route(f"http://127.0.0.1:{PORT}/bthumb*", lambda r: r.fulfill(status=200, content_type="image/jpeg", body=jpg))
        n_before = fr.locator(".card[data-id]").count()
        pg2.locator(".so-btn[data-act='library']").click()
        pg2.wait_for_selector("#so-lib:not([hidden]) .t", timeout=10000)
        ok("N9 Add photos opens the library picker on the country's album (Armenia (2025)), its photos as tiles, the blog pick marked",
           pg2.locator("#so-lib select").input_value() == ALBUM and pg2.locator("#so-lib .t").count() == 2 and pg2.locator("#so-lib .t.pk").count() == 1, pg2.locator("#so-lib select").input_value())
        pg2.locator("#so-lib .t").nth(1).click()
        add_txt = pg2.inner_text("#so-lib [data-l='add']")
        pg2.screenshot(path=str(SHOTS / "social-redesign-test-library-1440.png"))
        pg2.locator("#so-lib [data-l='add']").click()
        pg2.wait_for_function("document.getElementById('so-lib').hidden", timeout=60000)
        for _ in range(40):
            if fr.locator(".card[data-id]").count() == n_before + 1:
                break
            pg2.wait_for_timeout(500)
        man2 = json.loads((IG / "armenia" / "manifest.json").read_text(encoding="utf-8"))
        new = [x for x in man2["photos"] if x.get("source") == "library"]
        webf = IG / "armenia" / "web" / ((new[0]["id"] if new else "x") + ".jpg")
        im = Image.open(webf) if webf.is_file() else None
        ok("N9b Add 1 photo: a 1280 px sRGB web copy and a manifest entry (the album file as its original, 'Photo library: <album>'), and the page shows one more photo",
           add_txt.lower() == "add 1 photo" and len(new) == 1 and new[0]["name"] == lib_names[1] and new[0]["article_title"] == "Photo library: " + ALBUM
           and new[0]["original"].endswith(lib_names[1]) and im is not None and max(im.size) <= 1280 and bool(im.info.get("icc_profile"))
           and fr.locator(".card[data-id]").count() == n_before + 1 and fr.locator(f".card[data-id='{new[0]['id'] if new else 'x'}']").count() == 1, (add_txt, new, n_before, fr.locator(".card[data-id]").count()))
        from urllib.parse import quote
        st, libj = api.call("/api/social/armenia/library?album=" + quote(ALBUM))
        st2, again = api.call("/api/social/armenia/add", {"album": ALBUM, "names": [lib_names[1]]})
        st3, bad = api.call("/api/social/armenia/add", {"album": "../..", "names": ["x.jpg"]})
        ok("N9c /library names it as already in, adding it twice skips it, and an album outside the library is refused", st == 200 and libj.get("names") == [lib_names[1]]
           and st2 == 200 and again.get("added") == [] and again.get("skipped") == [lib_names[1]] and st3 == 400, (libj, again, st3))
        pg2.locator(".so-btn[data-act='library']").click(); pg2.wait_for_selector("#so-lib:not([hidden]) .t", timeout=10000)
        ok("N9d the picker shows the added photo as already in", pg2.locator(f"#so-lib .t.in[data-n='{lib_names[1]}']").count() == 1, "")
        pg2.keyboard.press("Escape"); pg2.wait_for_timeout(200)

        # Travel Blog post
        pg2.locator(".so-seg button[data-type='blog']").click(); pg2.wait_for_timeout(900)
        labels = pg2.evaluate("[...document.querySelectorAll('.so-tab')].map(b => b.dataset.view)")
        ok("U4 Travel Blog post: the page switches to the template mode on the plan (same iframe), sub-tabs Layout | Slides, Stories, Pins, the Layout count = the slides",
           labels == ["layout", "slides", "stories", "pins"] and fr.locator("#modeSeg button[data-mode='template'][aria-pressed='true']").count() == 1
           and fr.locator(".tslide").count() == 7 and pg2.inner_text(".so-tab[data-view='layout'] .n") == "7"
           and pg2.evaluate("el => el === document.getElementById('so-frame')", frame_el), (labels, fr.locator(".tslide").count()))
        pg2.screenshot(path=str(SHOTS / "social-tab-blog-1440.png"))
        pg2.locator(".so-tab[data-view='slides']").click(); pg2.wait_for_timeout(400)
        pg2.wait_for_timeout(600)
        _, rr = api.call("/api/social/armenia/renders")
        ok("U4b Slides shows its group with the Render carousel button, disabled exactly while the render runs (the earlier tests may outlast it)", pg2.locator(".so-group").count() == 1
           and pg2.locator(".so-btn[data-act='render'][data-kind='carousel']").count() == 1
           and (pg2.locator(".so-btn[data-act='render'][data-kind='carousel'][disabled]").count() == 1) == bool(rr.get("running")), (pg2.locator(".so-tabs .acts").inner_text(), rr.get("running")))
        pg2.locator(".so-tab[data-view='layout']").click(); pg2.wait_for_timeout(300)
        ok("U4c coming back to Layout keeps the same iframe (the page is not reloaded)", pg2.evaluate("el => el === document.getElementById('so-frame')", frame_el) and fr.locator(".tslide").count() == 7, "")
        pg2.reload(); pg2.wait_for_selector("#app-social:not([hidden]) .so-tabs", timeout=20000)
        fr = pg2.frame_locator("#so-frame"); fr.locator(".tslide").first.wait_for(timeout=20000)
        ok("U4d a reload remembers the output type (Travel Blog post) and opens the page in the template mode", pg2.locator(".so-seg button.on[data-type='blog']").count() == 1
           and fr.locator("#modeSeg button[data-mode='template'][aria-pressed='true']").count() == 1, pg2.inner_text(".so-seg"))
        frame_el = pg2.evaluate_handle("document.getElementById('so-frame')")

        # wait for the carousel render
        jd = api.wait_job(job["id"], 240)
        files = sorted(f.name for f in (OUT / "armenia").glob("*.png")) if (OUT / "armenia").is_dir() else []
        ok("R2 the render finishes (done) with the 7 slides + caption.txt in the scratch out folder", jd.get("status") == "done" and len(files) == 7 and files[0] == "01-cover.png" and (OUT / "armenia" / "caption.txt").is_file(), (jd.get("status"), jd.get("error"), files, (jd.get("log") or "")[-400:]))
        if files:
            im = Image.open(OUT / "armenia" / files[0])
            ok("R2b a slide is 1080x1350 PNG with the sRGB profile embedded", im.size == (1080, 1350) and im.format == "PNG" and bool(im.info.get("icc_profile")), (im.size, im.format, bool(im.info.get("icc_profile"))))
        st, rd = api.call("/api/social/armenia/renders")
        ok("R3 /renders lists them with the file route and the caption; nothing is running", st == 200 and len(rd["carousel"]) == 7 and rd["carousel"][0]["url"].startswith("/api/social/armenia/file/carousel/") and rd["caption"].strip() and rd["running"] is False, (st, rd.get("carousel", [])[:1]))
        st, raw = api.call(rd["carousel"][1]["url"], raw=True)
        st2, _ = api.call("/api/social/armenia/file/carousel/..%2F..%2Fmanifest.json", raw=True)
        ok("R3b the file route serves a slide (PNG) and refuses a path trick", st == 200 and raw[:4] == b"\x89PNG" and st2 in (400, 404), (st, st2))

        pg2.locator(".so-tab[data-view='slides']").click()
        pg2.wait_for_function("document.querySelectorAll('.so-grid.c .so-thumb').length === 7", timeout=15000)
        thumbs = pg2.locator(".so-grid.c .so-thumb")
        ok("U5 Slides now shows the 7 rendered thumbnails and the caption with a Copy button", thumbs.count() == 7 and pg2.locator("#so-cap-c").count() == 1 and "Comment GUIDE" in pg2.inner_text("#so-cap-c"), thumbs.count())
        pg2.wait_for_function("[...document.querySelectorAll('.so-grid.c img')].every(i => i.complete && i.naturalWidth > 0)", timeout=20000)
        ok("U5b every thumbnail image loaded from the file route", pg2.evaluate("[...document.querySelectorAll('.so-grid.c img')].every(i => i.naturalWidth === 1080)"), "")
        pg2.screenshot(path=str(SHOTS / "social-tab-test-slides.png"))
        thumbs.nth(1).click(); pg2.wait_for_selector("#so-light:not([hidden]) img", timeout=10000)
        pg2.wait_for_function("document.querySelector('#so-light img').naturalWidth > 0", timeout=10000)
        pos = pg2.inner_text("#so-light .pos")
        pg2.keyboard.press("ArrowRight"); pg2.wait_for_timeout(200)
        pos2 = pg2.inner_text("#so-light .pos")
        pg2.keyboard.press("Escape"); pg2.wait_for_timeout(200)
        ok("U6 the lightbox opens on the slide, → steps, Esc closes", pos.startswith("2 / 7") and pos2.startswith("3 / 7") and pg2.locator("#so-light").get_attribute("hidden") is not None, (pos, pos2))
        pg2.locator(".so-tab[data-view='pins']").click(); pg2.wait_for_timeout(300)
        ok("U7 Pins shows its empty state with the Render pins button enabled (the seed lists pins)", pg2.locator(".so-btn[data-act='render'][data-kind='pins']:not([disabled])").count() == 1 and "No pins yet" in pg2.locator(".so-body").inner_text(), pg2.locator(".so-body").inner_text()[:80])

        # ----------------------------------------------------------------------------- STORIES (Travel Blog post, from the tab's button)
        pg2.locator(".so-tab[data-view='stories']").click(); pg2.wait_for_timeout(300)
        ok("S1 Stories (Travel Blog post) shows its empty state with the Render stories button", "No story frames yet" in pg2.locator(".so-body").inner_text()
           and pg2.locator(".so-btn[data-act='render'][data-kind='stories-carousel']:not([disabled])").count() == 1, pg2.locator(".so-tabs .acts").inner_text())
        pg2.locator(".so-btn[data-act='render'][data-kind='stories-carousel']").click()
        pg2.wait_for_selector(".so-job.run", timeout=10000)
        sjob = pg2.evaluate("window.__social.job")
        ok("S1b the button starts a stories-carousel job (the spinner shows in the bar)", sjob and sjob.get("kind") == "stories-carousel", sjob)
        pg2.wait_for_function("document.querySelectorAll('.so-strip .so-thumb').length === 7", timeout=240000)
        sfiles = sorted(f.name for f in (OUT / "armenia" / "stories").glob("*.png"))
        ok("S2 7 story frames land in <out>/armenia/stories (cover, map, 4 stops, cta) and the Stories strip shows them", sfiles == ["01-cover.png", "02-map.png", "03-s0.png", "04-s1.png", "05-s2.png", "06-s3.png", "07-cta.png"], sfiles)
        if sfiles:
            im = Image.open(OUT / "armenia" / "stories" / sfiles[0])
            ok("S2b a story frame is 1080x1920 PNG with the sRGB profile", im.size == (1080, 1920) and bool(im.info.get("icc_profile")), (im.size, bool(im.info.get("icc_profile"))))
            px = Image.open(OUT / "armenia" / "stories" / "03-s0.png").convert("RGB")
            ok("S2c a stop frame: the photo on top, the green band under y = 1340 (the label sits above Instagram's bottom 340 px)", px.getpixel((40, 1900)) == (45, 107, 80) and px.getpixel((40, 1340 - 20)) != (45, 107, 80), (px.getpixel((40, 1900)), px.getpixel((40, 1320))))
        pg2.wait_for_function("[...document.querySelectorAll('.so-strip img')].every(i => i.complete && i.naturalWidth === 1080)", timeout=20000)
        pg2.screenshot(path=str(SHOTS / "social-tab-stories-1440.png"))
        pg2.locator(".so-strip .so-thumb").nth(0).click(); pg2.wait_for_selector("#so-light:not([hidden]) img.story", timeout=10000)
        ttl = pg2.inner_text("#so-light .ttl")
        pg2.wait_for_function("document.querySelector('#so-light img').naturalHeight === 1920", timeout=10000)
        pg2.screenshot(path=str(SHOTS / "social-tab-story-light-1440.png"))
        pg2.keyboard.press("Escape"); pg2.wait_for_timeout(200)
        ok("S3 the lightbox opens a story frame (titled as a 1080 × 1920 story)", "1080 × 1920" in ttl and pg2.locator("#so-light").get_attribute("hidden") is not None, ttl)
        st, rd = api.call("/api/social/armenia/renders")
        st2, lst = api.call("/api/social")
        ok("S4 /renders and /api/social count the story frames", st == 200 and len(rd["stories_carousel"]) == 7 and rd["stories_free"] == []
           and next(x for x in lst["countries"] if x["slug"] == "armenia")["renders"]["stories_carousel"] == 7, (st, len(rd.get("stories_carousel", []))))

        # the Personal post's stories: one final slot with a note, --no-copy on this scratch server
        api.call("/api/social/armenia/db", {"op": "set", "path": "photos/" + pid, "data": {"state": "final", "slot": 1, "order": 0, "note": "The lahmajun at Elie's, 600 dram", "crop": None}})
        st, r = api.call("/api/social/armenia/render", {"kind": "stories-free"})
        fj = api.wait_job((r.get("job") or {}).get("id", ""), 180) if st == 200 else {}
        ffiles = sorted(f.name for f in (IG / "armenia" / "stories").glob("*.png")) if (IG / "armenia" / "stories").is_dir() else []
        ok("S5 stories-free renders the one final slot into <ig>/armenia/stories as 01-<id>.png at 1080x1920 (the note is its line)", st == 200 and fj.get("status") == "done" and ffiles == ["01-%s.png" % pid]
           and Image.open(IG / "armenia" / "stories" / ffiles[0]).size == (1080, 1920) if ffiles else False, (st, fj.get("status"), ffiles, (fj.get("log") or "")[-300:]))
        pg2.locator(".so-seg button[data-type='personal']").click(); pg2.wait_for_timeout(500)
        pg2.locator(".so-tab[data-view='stories']").click()
        pg2.wait_for_function("document.querySelectorAll('.so-strip .so-thumb').length === 1", timeout=15000)
        ok("S5b Personal post > Stories shows that frame (its own list, not the Travel Blog's)", pg2.locator(".so-strip .so-thumb").count() == 1 and pid in (pg2.get_attribute(".so-strip .so-thumb", "title") or ""), pg2.get_attribute(".so-strip .so-thumb", "title"))

        # Render (was "Ready to render"): marks the post ready and renders the Final row, then shows it in Post
        pg2.locator(".so-tab[data-view='all']").click()
        pg2.wait_for_function("(document.querySelector('.so-tab[data-view=\"final\"] .n') || {}).textContent === '1/10'", timeout=20000)
        btn = pg2.locator(".so-btn[data-act='rendernow']")
        btn_ok = btn.is_enabled() and btn.inner_text().strip().lower() == "render"
        btn.click()
        pg2.wait_for_function("window.__social.job && window.__social.job.kind === 'free'", timeout=15000)
        fj = api.wait_job(pg2.evaluate("window.__social.job.id"), 240)
        pg2.wait_for_function("document.querySelectorAll('.so-grid.f .so-thumb').length === 1", timeout=30000)
        outf = IG / "armenia" / "out" / ("01-%s.jpg" % pid)
        ok("N10 Render marks the post ready, renders the Final row into the scratch out folder (1080x1350, the tab's own --out) and the tab moves to Post with it",
           btn_ok and fj.get("status") == "done" and outf.is_file() and Image.open(outf).size == (1080, 1350) and rd_post().get("status") == "ready"
           and pg2.locator(".so-tab.on[data-view='renders']").count() == 1, (btn_ok, fj.get("status"), (fj.get("log") or "")[-300:], rd_post().get("status")))
        pg2.locator(".so-tab[data-view='all']").click(); pg2.wait_for_timeout(500)
        pg2.locator(".so-seg.sm button[data-aspect='1.91:1']").click(); pg2.wait_for_timeout(700)
        pg2.locator(".so-btn[data-act='rendernow']").click()
        pg2.wait_for_function("window.__social.job && window.__social.job.kind === 'free' && window.__social.job.id !== '%s'" % fj.get("id"), timeout=15000)
        fj2 = api.wait_job(pg2.evaluate("window.__social.job.id"), 240)
        ok("N10b at 1.91:1 the same slide renders landscape, 1080x565", fj2.get("status") == "done" and outf.is_file() and Image.open(outf).size == (1080, 565), (fj2.get("status"), Image.open(outf).size if outf.is_file() else None))
        pg2.locator(".so-tab[data-view='all']").click(); pg2.wait_for_timeout(400)
        pg2.locator(".so-seg.sm button[data-aspect='4:5']").click(); pg2.wait_for_timeout(500)
        api.call("/api/social/armenia/db", {"op": "set", "path": "photos/" + pid, "data": {"state": "final", "slot": 11, "order": 0, "note": "", "crop": None}})

        # ---- the Post tab: the Instagram preview and Email to my phone (Kevin, 2026-11-11). Three photos with web copies in
        # the Final row (slide 2 cropped and tilted), a long caption, a real render
        IGS = web_ids[:3]
        for k, x in enumerate(IGS):
            setdoc("photos/" + x, {"state": "final", "slot": k + 1, "order": 0, "note": "", "crop": CROP2 if k == 1 else None})
        pm = rd_post(); pm.update(finalCaption=CAP, hashtags="#armenia #yerevan", aspect="4:5", status="ready")
        setdoc("post/main", pm)
        st, r = api.call("/api/social/armenia/render", {"kind": "free"})
        fj3 = api.wait_job((r.get("job") or {}).get("id", ""), 240) if st == 200 else {}
        pg2.locator(".so-tab[data-view='renders']").click()
        pg2.wait_for_function("document.querySelectorAll('.so-ig-media .so-ig-track img').length === 3", timeout=20000)
        pg2.wait_for_function("[...document.querySelectorAll('.so-ig-track img')].every(i => i.complete && i.naturalWidth > 0)", timeout=20000)
        ig_ar = pg2.evaluate("(() => { const b = document.querySelector('.so-ig-media').getBoundingClientRect(); return b.width / b.height; })()")
        ok("IG1 Post shows the Instagram preview: the account (handle + the site icon) and country, the carousel at 4:5 from the RENDERED files, 1/3, three dots, the four action icons",
           fj3.get("status") == "done" and pg2.inner_text(".so-ig-top b") == IDX_HANDLE and "icons/icon-192.png" in (pg2.get_attribute(".so-ig-top .so-ig-av", "src") or "")
           and pg2.inner_text(".so-ig-top .who span") == "Armenia" and abs(ig_ar - 0.8) < 0.01 and "/file/free/01-" in (pg2.get_attribute(".so-ig-track img", "src") or "")
           and pg2.inner_text(".so-ig-count") == "1/3" and pg2.locator(".so-ig-dots i").count() == 3 and pg2.locator(".so-ig-dots i.on").count() == 1
           and pg2.locator(".so-ig-acts .so-ig-ib").count() == 4 and "rendered files" in pg2.inner_text(".so-ig-note").lower(), (fj3.get("status"), ig_ar, pg2.inner_text(".so-ig-phone")[:200]))
        cap_txt = pg2.inner_text(".so-ig-cap")
        ok("IG2 the caption is cut like Instagram's feed: the handle, about 125 characters, then '… more', no hashtags yet",
           cap_txt.startswith(IDX_HANDLE) and cap_txt.endswith("… more") and len(cap_txt) <= len(IDX_HANDLE) + 1 + 125 + 7 and "#armenia" not in cap_txt, cap_txt)
        pg2.locator(".so-ig-more").click(); pg2.wait_for_timeout(200)
        full_txt = pg2.inner_text(".so-ig-cap")
        ok("IG2b 'more' opens the whole caption, then the hashtags in Instagram's link blue", CAP in full_txt and full_txt.rstrip().endswith("#armenia #yerevan")
           and pg2.locator(".so-ig-cap .tag").count() == 2 and pg2.locator(".so-ig-cap .tag").first.evaluate("e => getComputedStyle(e).color") == "rgb(0, 55, 107)", full_txt)
        pg2.locator(".so-ig-media").hover(); pg2.locator(".so-ig-nav.r").click(); pg2.wait_for_timeout(450)
        c2 = (pg2.inner_text(".so-ig-count"), pg2.evaluate("[...document.querySelectorAll('.so-ig-dots i')].findIndex(i => i.classList.contains('on'))"))
        bx = pg2.locator(".so-ig-media").bounding_box(); x0, y0 = bx["x"] + bx["width"] * .7, bx["y"] + bx["height"] / 2
        pg2.mouse.move(x0, y0); pg2.mouse.down(); pg2.mouse.move(x0 - 160, y0, steps=8); pg2.mouse.up(); pg2.wait_for_timeout(450)
        c3, r_hidden = pg2.inner_text(".so-ig-count"), pg2.locator(".so-ig-nav.r").is_hidden()
        pg2.locator(".so-ig-media").focus(); pg2.keyboard.press("ArrowLeft"); pg2.wait_for_timeout(450)
        c4 = pg2.inner_text(".so-ig-count")
        ok("IG3 the carousel moves: the arrow to 2/3 (the second dot lit), a swipe to 3/3 (the right arrow gone), ← back to 2/3", c2 == ("2/3", 1) and c3 == "3/3" and r_hidden and c4 == "2/3", (c2, c3, r_hidden, c4))
        pg2.screenshot(path=str(SHOTS / "social-igpreview-1440.png"))
        pg2.locator("[data-act='iggrid'][data-grid='1']").click(); pg2.wait_for_timeout(500)
        t_ar = pg2.evaluate("(() => { const b = document.querySelector('.so-ig-grid .tile').getBoundingClientRect(); return b.width / b.height; })()")
        fit = pg2.evaluate("(() => { const s = getComputedStyle(document.querySelector('.so-ig-grid .tile img')); return [s.objectFit, s.objectPosition]; })()")
        ok("IG4 Profile grid: the cover as the first tile at 3:4, cut to fill around its center, the carousel icon on it, the handle above",
           abs(t_ar - .75) < .01 and fit == ["cover", "50% 50%"] and "/file/free/01-" in (pg2.get_attribute(".so-ig-grid .tile img", "src") or "")
           and pg2.locator(".so-ig-grid .tile .multi").count() == 1 and IDX_HANDLE in pg2.inner_text(".so-ig-prof"), (t_ar, fit))
        pg2.screenshot(path=str(SHOTS / "social-igpreview-grid-1440.png"))
        pg2.locator("[data-act='iggrid'][data-grid='0']").click(); pg2.wait_for_timeout(300)
        mail_btn = pg2.locator(".so-btn[data-act='email']")
        ok("IG5 the email card says what goes out (3 slides, 01.jpg to 03.jpg) and Email to my phone is enabled", mail_btn.is_enabled() and "3 slides" in pg2.inner_text(".so-mail")
           and "01.jpg to 03.jpg" in pg2.inner_text(".so-mail"), pg2.inner_text(".so-mail"))
        n0 = len(MAILS)
        mail_btn.click()
        pg2.wait_for_function("window.__social.job && window.__social.job.kind === 'email' && ['done', 'failed'].includes(window.__social.job.status)", timeout=60000)
        pg2.wait_for_selector(".so-mail-st.ok", timeout=15000)
        rfiles = sorted(FREE.glob("*.jpg"))
        atts = [(a.get_filename(), a.get_content()) for m in MAILS[n0:] for a in email.message_from_bytes(m["data"], policy=email.policy.default).iter_attachments()]
        ok("EM1 the button sends it (the fake sink): the card says Sent, the attachments are the three rendered JPEGs byte for byte as 01.jpg to 03.jpg",
           pg2.inner_text(".so-mail-st.ok").startswith("Sent") and [a[0] for a in atts] == ["01.jpg", "02.jpg", "03.jpg"] and [a[1] for a in atts] == [f.read_bytes() for f in rfiles],
           (pg2.inner_text(".so-mail"), [a[0] for a in atts]))
        pg2.screenshot(path=str(SHOTS / "social-igpreview-sent-1440.png"))
        setdoc("photos/" + IGS[1], {"state": "final", "slot": 2, "order": 0, "note": "", "crop": dict(CROP2, w=0.55)})
        pg2.locator(".so-tab[data-view='renders']").click()
        pg2.wait_for_selector(".so-mail .warn", timeout=10000)
        pg2.wait_for_function("document.querySelectorAll('.so-ig-track canvas').length === 3", timeout=10000); pg2.wait_for_timeout(1200)
        painted = pg2.evaluate("[...document.querySelectorAll('.so-ig-track canvas')].map(c => c.dataset.painted === '1')")   # the photos are cross-origin here: the canvas can't be read back
        ok("IG6 the Final row changed after the render: the card warns (out of date: slide 2's crop) with Render again, Email is disabled, the preview draws the crops (3 canvases painted)",
           "out of date" in pg2.inner_text(".so-mail .warn") and "slide 2" in pg2.inner_text(".so-mail .warn") and pg2.locator(".so-mail [data-act='render'][data-kind='free']").count() == 1
           and pg2.locator(".so-btn[data-act='email'][disabled]").count() == 1 and painted == [True, True, True] and "out of date" in pg2.inner_text(".so-ig-note"), (pg2.inner_text(".so-mail"), painted))
        pg2.screenshot(path=str(SHOTS / "social-igpreview-stale-1440.png"))
        setdoc("photos/" + IGS[1], {"state": "final", "slot": 2, "order": 0, "note": "", "crop": CROP2})
        env_keys(False)
        pg2.locator(".so-tab[data-view='renders']").click()
        pg2.wait_for_selector(".so-btn[data-act='email']:not([disabled])", timeout=10000)
        pg2.locator(".so-btn[data-act='email']").click()
        pg2.wait_for_selector(".so-mail-st.run", timeout=10000)
        req_txt, note_txt = pg2.inner_text(".so-mail-st.run"), pg2.inner_text(".so-mail")
        rqu = json.loads(REQF.read_text(encoding="utf-8")) if REQF.is_file() else {}
        cli("--email-sent", "armenia", rqu.get("id", "x"), "msg-ui")
        try:
            pg2.wait_for_selector(".so-mail-st.ok", timeout=15000); sent_txt = pg2.inner_text(".so-mail-st.ok")
        except Exception:
            sent_txt = ""
        ok("IG7 without the keys the button writes the request ('Requested. Claude sends it in a moment.', the note says Claude sends it); once Claude marks it sent the card turns to Sent by itself",
           "Claude sends it in a moment" in req_txt and "Claude sends it for you" in note_txt and rqu.get("status") == "requested" and sent_txt.startswith("Sent"), (req_txt, sent_txt))
        env_keys(True)
        pg2.set_viewport_size({"width": 393, "height": 852}); pg2.wait_for_timeout(500)
        over = pg2.evaluate("[document.scrollingElement.scrollWidth, document.querySelector('.so-body').scrollWidth, document.querySelector('.so-body').clientWidth]")
        ig_w = pg2.evaluate("document.querySelector('.so-ig-phone').getBoundingClientRect().width")
        ok("IG8 393 px: the preview fills the column, no sideways scroll, the Email button keeps its words", over[0] <= 393 and over[1] <= over[2] and ig_w > 300
           and "email to my phone" in pg2.inner_text(".so-btn[data-act='email']").lower(), (over, ig_w))
        pg2.screenshot(path=str(SHOTS / "social-igpreview-393.png"))
        pg2.locator(".so-ig-acts").scroll_into_view_if_needed(); pg2.wait_for_timeout(200)
        pg2.screenshot(path=str(SHOTS / "social-igpreview-393-caption.png"))
        pg2.set_viewport_size({"width": 1440, "height": 900})
        for x in IGS:
            api.call("/api/social/armenia/db", {"op": "delete", "path": "photos/" + x})
        setdoc("photos/" + pid, {"state": "final", "slot": 11, "order": 0, "note": "", "crop": None})
        REQF.unlink(missing_ok=True)

        # phone
        pg2.set_viewport_size({"width": 393, "height": 852}); pg2.wait_for_timeout(400)
        pg2.locator(".so-seg button[data-type='blog']").click(); pg2.wait_for_timeout(300)
        pg2.locator(".so-tab[data-view='slides']").click(); pg2.wait_for_timeout(400)
        over = pg2.evaluate("[document.scrollingElement.scrollWidth, document.querySelector('.so-body').scrollWidth, document.querySelector('.so-body').clientWidth]")
        ok("U8 393 px: no sideways scroll", over[0] <= 393 and over[1] <= over[2], over)
        pg2.screenshot(path=str(SHOTS / "social-tab-blog-393.png"))
        pg2.locator(".so-tab[data-view='layout']").click(); pg2.wait_for_timeout(300)
        ok("U8b the maker iframe survived the Slides, Stories and Pins views (same element, still on the plan)", pg2.evaluate("el => el === document.getElementById('so-frame')", frame_el) and fr.locator(".tslide").count() == 7, "")
        pg2.locator(".so-seg button[data-type='personal']").click(); pg2.wait_for_timeout(700)
        over = pg2.evaluate("[document.scrollingElement.scrollWidth, document.querySelector('.so-body').scrollWidth, document.querySelector('.so-body').clientWidth]")
        ok("U8c 393 px, Personal post on the Photos view: no sideways scroll either", over[0] <= 393 and over[1] <= over[2], over)
        pg2.screenshot(path=str(SHOTS / "social-tab-personal-393.png"))
        pg2.locator(".so-tab[data-view='final']").click(); pg2.wait_for_timeout(800)
        over = pg2.evaluate("[document.scrollingElement.scrollWidth, document.querySelector('.so-body').scrollWidth, document.querySelector('.so-body').clientWidth]")
        fo = fr.locator("html").evaluate("e => [e.scrollWidth, e.clientWidth]")
        tops = fr.locator(".rowwrap.final .slots").evaluate("g => [...g.querySelectorAll('.frame')].slice(0, 4).map(c => Math.round(c.getBoundingClientRect().top))")
        ok("U8d 393 px, the Shortlist: three slots to a row on a phone, no sideways scroll in the tab or the maker", over[0] <= 393 and over[1] <= over[2] and fo[0] <= fo[1]
           and len(set(tops[:3])) == 1 and tops[3] > tops[0], (over, fo, tops))
        pg2.screenshot(path=str(SHOTS / "social-redesign-test-shortlist-393.png"))
        pg2.set_viewport_size({"width": 1440, "height": 900})
        ok("U9 no page errors in the editor", not errs2, errs2[:3])

        pg3 = ctx.new_page()
        pg3.route(f"http://127.0.0.1:{PORT}/api/social", lambda r: r.fulfill(status=404, body="not found"))
        pg3.goto(EDITOR + "#social"); pg3.wait_for_selector("#social-root .so-msg", timeout=20000)
        msg = pg3.inner_text("#social-root .so-msg")
        ok("U10 an old photo server (404) shows the restart message", "restart" in msg.lower() and "/api/social" in msg, msg[:120])
        pg3.close()
        b.close()
finally:
    srv.kill()
    SINK.shutdown()

ok("Z1 the real store, the real renders (the Personal post's .tmp/ig/armenia/out too) and Pictures\\IG were not touched; no email request in the real .tmp/ig",
   snapshot(REAL / "state") == real_state0 and snapshot(REAL_OUT) == real_out0 and snapshot(PICS) == pics0 and snapshot(REAL / "out") == real_free0
   and sorted(str(p) for p in (ROOT / ".tmp" / "ig").glob("*/email_request.json")) == REAL_REQ0, "")
shutil.rmtree(SCRATCH, ignore_errors=True)
n = sum(1 for _, c, _ in checks if c)
print(f"\n{n}/{len(checks)} passed; screenshots in {SHOTS}\\social-tab-*.png")
sys.exit(0 if n == len(checks) else 1)
