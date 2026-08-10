"""Pull the transcript of a YouTube video so it can be read and discussed here.

Usage
-----
    python tools/youtube_transcript.py <url-or-id> [options]

    --list          show the caption tracks available, fetch nothing
    --lang XX       preferred language (default: en). Falls back to an
                    auto-translation of whatever track exists.
    --timestamps    prefix each paragraph with [mm:ss]
    --print         dump the whole transcript to stdout as well as the file
    --out FILE      write somewhere other than .tmp/transcripts/<id>.txt
    --force         refetch even if the file is already cached

By default it writes the transcript to a file and prints only the path, the
title and the first few lines -- a lecture runs 5,000+ words, and pasting all
of it into a conversation buries the thing you actually wanted from it. Read
the file, or pass --print.

Why it is built this way
------------------------
There is no stdlib-friendly public captions API, and the two obvious routes
both fail on their own, so this tries three in order:

  1. The InnerTube `player` endpoint as the WEB client. This is what the site
     itself calls, and it is the only one that reliably carries
     `captions.playerCaptionsTracklistRenderer` for videos with captions off
     by default.
  2. The watch page, scraped for `ytInitialPlayerResponse`. Works when
     InnerTube answers with a playability error but the page still renders.
  3. The ANDROID client, which is the one that still answers for videos the
     WEB client refuses (age gates, some regional blocks). It is tried last
     because its response OMITS caption tracks about half the time -- when it
     works it works, but it cannot be the primary.

The caption `baseUrl` then wants `&fmt=json3`. The XML that comes back
without it is the legacy format and HTML-escapes entities twice, so `&amp;#39;`
lands in the text as a literal. json3 hands back clean UTF-8 segments.

NETWORK NOTE: run this from PowerShell, not the Bash tool -- Bash here has no
route to the internet, and the failure looks like a DNS error, not a firewall.
"""

import argparse
import gzip
import io
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# The public InnerTube key. It is not a secret -- it ships in the page source
# of every youtube.com URL -- so it does not belong in .env.
INNERTUBE_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"

# Order matters, and it is not the obvious one -- see the module docstring.
CLIENTS = {
    "ios": {
        "context": {"client": {
            "clientName": "IOS",
            "clientVersion": "20.10.4",
            "deviceMake": "Apple", "deviceModel": "iPhone16,2",
            "osName": "iPhone", "osVersion": "18.3.2.22D82",
            "hl": "en", "gl": "US",
        }},
        "ua": ("com.google.ios.youtube/20.10.4 (iPhone16,2; U; "
               "CPU iOS 18_3_2 like Mac OS X)"),
    },
    "android": {
        "context": {"client": {
            "clientName": "ANDROID",
            "clientVersion": "20.10.38",
            "androidSdkVersion": 30,
            "hl": "en", "gl": "US",
        }},
        "ua": "com.google.android.youtube/20.10.38 (Linux; U; Android 11) gzip",
    },
    "web": {
        "context": {"client": {
            "clientName": "WEB",
            "clientVersion": "2.20250612.01.00",
            "hl": "en", "gl": "US",
        }},
        "ua": UA,
    },
}


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def ssl_ctx():
    """Verify against certifi's bundle, not the Windows store.

    This machine has a CA installed whose Basic Constraints are not marked
    critical. Schannel (so PowerShell's Invoke-WebRequest) shrugs at that;
    OpenSSL 3 refuses the whole chain, and EVERY https call from Python dies
    with CERTIFICATE_VERIFY_FAILED before a byte moves. Pointing at certifi
    sidesteps the malformed cert without turning verification off."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return None                      # stock behaviour; may fail as above


def video_id(s):
    """Accept any YouTube URL shape, or a bare 11-character id."""
    s = s.strip().strip('"').strip("'")
    if re.fullmatch(r"[\w-]{11}", s):
        return s
    u = urllib.parse.urlparse(s if "//" in s else "https://" + s)
    host = u.netloc.lower().replace("www.", "")
    if host == "youtu.be":
        return u.path.lstrip("/").split("/")[0]
    if "youtube" in host:
        q = urllib.parse.parse_qs(u.query)
        if q.get("v"):
            return q["v"][0]
        # /shorts/<id>, /live/<id>, /embed/<id>
        parts = [p for p in u.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed", "v"):
            return parts[1]
    die(f"could not find a video id in {s!r}")


def get(url, data=None, headers=None, timeout=30):
    """One GET/POST. Handles gzip, which InnerTube sends whether asked or not."""
    hdrs = {
        "User-Agent": UA,
        "Accept-Language": "en-US,en;q=0.9",
        # Skips the EU consent interstitial, which otherwise returns a form
        # page instead of the video and reads as "no captions".
        "Cookie": "CONSENT=YES+cb; SOCS=CAI",
        "Accept-Encoding": "gzip",
    }
    hdrs.update(headers or {})
    body = json.dumps(data).encode() if data is not None else None
    if body is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx()) as r:
            raw = r.read()
    except urllib.error.URLError as e:
        # Only TLS trust failures fall through to PowerShell -- a 404 or a
        # timeout means the same thing on either transport, and retrying it
        # just doubles the wait.
        if not isinstance(getattr(e, "reason", None), ssl.SSLError):
            raise
        raw = ps_fetch(url, method="POST" if body is not None else "GET",
                       headers=hdrs, body=body, timeout=timeout)
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    return raw.decode("utf-8", "replace")


def ps_fetch(url, method, headers, body, timeout):
    """The same request, made by Invoke-WebRequest. See tools/_ps_fetch.ps1
    for why this exists at all."""
    here = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(here, "_ps_fetch.ps1")
    if not os.path.exists(script):
        die(f"TLS verification failed and the fallback {script} is missing")

    with tempfile.TemporaryDirectory() as tmp:
        spec = os.path.join(tmp, "spec.json")
        out = os.path.join(tmp, "body.bin")
        with open(spec, "w", encoding="utf-8") as f:
            json.dump({"url": url, "method": method, "headers": headers,
                       "body": body.decode() if body else None}, f)
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", script,
             "-Spec", spec, "-Out", out],
            capture_output=True, text=True, timeout=timeout + 30)
        if r.returncode != 0 or not os.path.exists(out):
            die("powershell fetch failed:\n" + (r.stderr or r.stdout).strip()[:800])
        with open(out, "rb") as f:
            return f.read()


def candidates(vid, errs):
    """Yield (player-json, route-name) for every route that has caption
    tracks, best first.

    A generator rather than a single answer because "has a captionTracks
    array" does NOT mean "that URL will serve". The watch page hands back a
    perfectly well-formed track whose baseUrl returns 200 with an EMPTY body
    -- anonymous timedtext now wants a proof-of-origin token the page would
    normally mint in JS. The mobile-app clients are exempt and still serve, so
    they go first and the caller keeps asking until bytes actually arrive."""
    for name, c in CLIENTS.items():
        try:
            txt = get(
                f"https://www.youtube.com/youtubei/v1/player?key={INNERTUBE_KEY}",
                data={"context": c["context"], "videoId": vid,
                      "contentCheckOk": True, "racyCheckOk": True},
                headers={"User-Agent": c["ua"]},
            )
            d = json.loads(txt)
            if tracks_of(d):
                yield d, f"innertube:{name}"
            else:
                status = (d.get("playabilityStatus") or {}).get("status")
                errs.append(f"innertube:{name} -> no caption tracks (status {status})")
        except Exception as e:                       # noqa: BLE001 - report, try next
            errs.append(f"innertube:{name} -> {e}")

    # Last resort: it carries the richest metadata and works when every
    # client is age-gated, even though its caption URL usually will not serve.
    try:
        html = get(f"https://www.youtube.com/watch?v={vid}&hl=en")
        m = re.search(r"ytInitialPlayerResponse\s*=\s*(\{.+?\})\s*;\s*(?:var|</script>)",
                      html, re.S)
        if not m:
            errs.append("watchpage -> ytInitialPlayerResponse not found")
        else:
            d = json.loads(m.group(1))
            if tracks_of(d):
                yield d, "watchpage"
            else:
                errs.append("watchpage -> no caption tracks")
    except Exception as e:                           # noqa: BLE001
        errs.append(f"watchpage -> {e}")


def tracks_of(data):
    return (((data.get("captions") or {})
             .get("playerCaptionsTracklistRenderer") or {})
            .get("captionTracks") or [])


def pick(tracks, lang):
    """Prefer a human track in the wanted language, then an auto one, then
    anything at all translated into it. Auto-generated captions are marked
    kind == 'asr' and are noticeably worse at proper nouns, so they lose a
    tie."""
    human = [t for t in tracks if t.get("kind") != "asr"]
    auto = [t for t in tracks if t.get("kind") == "asr"]
    for pool in (human, auto):
        for t in pool:
            if (t.get("languageCode") or "").startswith(lang):
                return t, False
    if tracks:
        return tracks[0], True          # translate whatever exists
    return None, False


def fetch_track(track, lang, translate):
    url = track["baseUrl"]
    if translate:
        url += "&tlang=" + urllib.parse.quote(lang)
    sep = "&" if "?" in url else "?"
    txt = get(url + sep + "fmt=json3")
    if not txt.strip():
        return []                        # dead route; the caller tries the next
    data = json.loads(txt)
    out = []
    for ev in data.get("events") or []:
        segs = ev.get("segs")
        if not segs:
            continue
        # Auto-captions scroll: an appended event REPEATS the line it is
        # growing, so keeping them triples the word count and reads like a
        # stutter. Only the settled events carry new text.
        if ev.get("aAppend"):
            continue
        s = "".join(x.get("utf8", "") for x in segs)
        s = s.replace("\n", " ").strip()
        if s:
            out.append((ev.get("tStartMs", 0) / 1000.0, s))
    return out


def paragraphs(cues, stamps):
    """Caption cues are 3-second fragments; a wall of them is unreadable.
    Regroup into paragraphs on a long pause or roughly every 90 words."""
    paras, cur, start, words, prev_end = [], [], None, 0, None

    def flush():
        nonlocal cur, start, words
        if cur:
            body = re.sub(r"\s+", " ", " ".join(cur)).strip()
            if stamps and start is not None:
                body = f"[{int(start) // 60:d}:{int(start) % 60:02d}] {body}"
            paras.append(body)
        cur, start, words = [], None, 0

    for t, s in cues:
        if start is None:
            start = t
        gap = t - prev_end if prev_end is not None else 0
        if cur and (gap > 2.5 or words > 90):
            flush()
            start = t
        cur.append(s)
        words += len(s.split())
        prev_end = t
    flush()
    return paras


def main():
    ap = argparse.ArgumentParser(description="Fetch a YouTube transcript.")
    ap.add_argument("url", help="video URL or 11-character id")
    ap.add_argument("--lang", default="en")
    ap.add_argument("--list", action="store_true", help="list caption tracks only")
    ap.add_argument("--timestamps", action="store_true")
    ap.add_argument("--print", dest="dump", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    vid = video_id(a.url)
    out = a.out or os.path.join(".tmp", "transcripts", f"{vid}.txt")

    if os.path.exists(out) and not a.force and not a.list:
        print(f"cached: {out}  (--force to refetch)")
        if a.dump:
            with open(out, encoding="utf-8") as f:
                print(f.read())
        return

    errs, cues, det, track, route = [], None, {}, None, None
    for data, name in candidates(vid, errs):
        det = det or (data.get("videoDetails") or {})
        tracks = tracks_of(data)

        if a.list:
            d = data.get("videoDetails") or {}
            print(f"{d.get('title')} — {d.get('author')}  [{name}]")
            for t in tracks:
                kind = "auto" if t.get("kind") == "asr" else "human"
                label = ((t.get("name") or {}).get("simpleText")
                         or (t.get("name") or {}).get("runs", [{}])[0].get("text", ""))
                print(f"  {t.get('languageCode'):<8} {kind:<6} {label}")
            return

        track, translate = pick(tracks, a.lang)
        got = fetch_track(track, a.lang, translate) if track else []
        if got:
            cues, route = got, name
            break
        errs.append(f"{name} -> track served an empty body")

    if a.list:
        die("no caption tracks from any route:\n  " + "\n  ".join(errs))
    if not cues:
        die("could not get caption text:\n  " + "\n  ".join(errs))

    title = det.get("title") or "(untitled)"
    author = det.get("author") or "(unknown)"
    secs = int(det.get("lengthSeconds") or 0)

    paras = paragraphs(cues, a.timestamps)
    body = "\n\n".join(paras)
    head = (f"{title}\n{author}\n"
            f"https://www.youtube.com/watch?v={vid}\n"
            f"{secs // 60}m{secs % 60:02d}s · {track.get('languageCode')}"
            f"{' (auto)' if track.get('kind') == 'asr' else ''}"
            f"{' (translated)' if translate else ''} · via {route}\n"
            + "-" * 60 + "\n\n")

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(head + body + "\n")

    nwords = len(body.split())
    print(f"{title} — {author}")
    print(f"{nwords:,} words, {len(paras)} paragraphs -> {out}")
    if a.dump:
        print()
        print(head + body)
    else:
        print()
        print("\n\n".join(paras[:2])[:600] + " ...")


if __name__ == "__main__":
    main()
