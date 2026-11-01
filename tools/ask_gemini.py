#!/usr/bin/env python3
"""Ask Gemini so Claude doesn't have to read the big input (Kevin, 2026-10-31: "lets do the 4 offload tasks").

The point is token saving: Claude passes a FILE PATH, this script sends the file to Gemini, and only Gemini's short
answer comes back into Claude's context. If Claude reads the file first and then calls this, nothing is saved.

Jobs it is for (see workflows/offload_to_gemini.md for when and the privacy rules):
  1. live-page error scan    --file <country>/field-notes.html --preset live-scan
  2. web fact lookup         --search --task "Taj Mahal foreign-visitor ticket price in 2026"
                             (NOT on the free tier: Search grounding has zero free quota, so it exits 75; fact
                             lookups stay on Claude. Kept for a paid key.)
  3. alt text / captions     --image <photo> --preset alt --context "Uluwatu Temple, Bali, Indonesia"
  4. digests                 --file <log or transcript> --preset digest

Never send: .env or anything with keys, Kevin's travel journal, unpublished drafts unless he says so (Google's free
tier may use prompts to improve its models), anything with private details about other people.

HTTP goes through PowerShell's Invoke-RestMethod because Python's TLS is broken on this machine (see
tools/youtube_transcript.py). The key is GEMINI_API_KEY in .env (or the environment); the model is
gemini-3.1-flash-lite unless GEMINI_MODEL or --model says otherwise (run --list-models to see what the key can use;
on the free tier flash-lite answers in 2-9 s while gemini-flash-latest and the other aliases queue for 30-77 s).
Exit 75 (GEMINI_UNAVAILABLE) means Gemini can't answer: do the job with Claude. Other tools import generate(). Every full response is saved to .tmp/gemini/ so Claude can
look at it only if it must; stdout is capped by --max-lines. A 429 (free-tier rate limit) waits and retries.

Examples:
  python tools/ask_gemini.py --list-models
  python tools/ask_gemini.py --file kosovo/field-notes.html --preset live-scan
  python tools/ask_gemini.py --search --task "Bali tourist levy amount and how to pay, 2026; give a source URL"
  python tools/ask_gemini.py --image "Images/web/Indonesia/Uluwatu/IMG_9085-2x.jpg" --preset alt --context "Uluwatu Temple, Bali, Indonesia"
  python tools/ask_gemini.py --file .tmp/some_log.txt --preset digest --max-lines 15
"""
import argparse, base64, json, mimetypes, os, re, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / ".tmp" / "gemini"
API = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-3.1-flash-lite"   # 1.7-9 s on the free tier; flash-latest and the other aliases queue 30-77 s
BLOCKED = (".env", "credentials.json", "token.json", "Travel Journals")

PRESETS = {
    # tightened 2026-10-31: the first version flagged every Maps link's entry=/g_ep= as "tracking" and offered rewording
    "live-scan": ("You check a published travel-blog page for errors. Report ONLY real errors, one per line, as "
                  "'- <quoted phrase> | <problem> | <fix>'. Report these kinds and nothing else: "
                  "(1) typos, misspellings and grammar mistakes, including wrong words such as it's/its, "
                  "their/there/they're, your/you're, then/than, loose/lose, a missing or doubled word, and a wrong "
                  "word form (e.g. 'a easy walk', 'the main reason were'); read every sentence and list every one you find; "
                  "(2) British spellings (the site uses American English; proper names such as 'Viaduct Harbour' or "
                  "'Centre Pompidou' are correct as they are); "
                  "(3) facts that are wrong or contradict each other on the page; "
                  "(4) a Google Maps link whose place name or coordinates clearly don't match the linked text (a "
                  "different place, city or country), and only then; "
                  "(5) ad-tracking parameters left in a URL, and only these: gclid, gbraid, wbraid, fbclid, utm_*, "
                  "campaign_id, ranking_uuid, source=ppc. "
                  "Google Maps links normally carry hl=, entry=, g_ep=, data=, ftid=, cid= and similar parameters: those "
                  "are NOT tracking strings and are never errors. "
                  "Never propose changes of tense, voice, tone, word choice or style, and never rewrite a sentence that "
                  "is already correct. Ignore the navigation, scripts and the Explore More block. Quote exactly. For "
                  "links and facts, leave out anything you are not sure is wrong. If nothing is wrong, answer "
                  "'No errors found.' "
                  "No preamble, no summary."),
    "alt": ("Write alt text for one travel-blog photo. House style: '<what is in the picture>, <place>, <country>', "
            "8 to 16 words, plain and specific, no 'image of' or 'photo of', no guessing names of places you are not "
            "given, no period at the end. Use the context for the place. Answer with the alt text only."),
    "caption": ("Write a one-sentence caption for this travel-blog photo in plain first person, specific, no clichés, "
                "American English, no em dash. Use the context for the place. Answer with the caption only."),
    "digest": ("Summarize this file for a busy reader in at most the requested number of lines: what happened, "
               "decisions, numbers, errors and anything left open. Bullets, no preamble."),
}


NO_KEY = "No GEMINI_API_KEY: create one at https://aistudio.google.com/apikey and add GEMINI_API_KEY=... to .env"


def env_key(required=True):
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*GEMINI_API_KEY\s*=\s*(.+?)\s*$", line)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    if required:
        sys.exit(NO_KEY)
    return None


def ps_request(method, url, body=None, timeout=120):
    """One HTTPS request through PowerShell; returns (status, text). The key travels in a header, never in argv.
    A request that outlives `timeout` (plus PowerShell's start-up) comes back as status -1."""
    key = env_key()
    with tempfile.TemporaryDirectory() as td:
        bodyf = Path(td) / "body.json"
        outf = Path(td) / "out.json"
        if body is not None:
            bodyf.write_text(json.dumps(body), encoding="utf-8")
        script = (
            "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue';"
            "$h=@{'x-goog-api-key'=$env:GEMINI_API_KEY_PS};"
            "try { $r = Invoke-WebRequest -UseBasicParsing -Method %s -Uri $env:GEMINI_URL -Headers $h -TimeoutSec %d %s;"
            " [IO.File]::WriteAllText($env:GEMINI_OUT, $r.Content, [Text.UTF8Encoding]::new($false)); exit 0 }"
            " catch { $code = 0; if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode;"
            " $sr = New-Object IO.StreamReader($_.Exception.Response.GetResponseStream());"
            " [IO.File]::WriteAllText($env:GEMINI_OUT, $sr.ReadToEnd(), [Text.UTF8Encoding]::new($false)) };"
            " Write-Output \"HTTP:$code\"; exit 3 }"
        ) % (method, timeout, ("-ContentType 'application/json; charset=utf-8' -InFile $env:GEMINI_BODY" if body is not None else ""))
        envv = dict(os.environ, GEMINI_API_KEY_PS=key, GEMINI_URL=url, GEMINI_OUT=str(outf), GEMINI_BODY=str(bodyf))
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                               text=True, env=envv, timeout=timeout + 8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return -1, '{"error": {"message": "no answer within %d s"}}' % timeout
        text = outf.read_text(encoding="utf-8") if outf.exists() else ""
        if r.returncode == 0:
            return 200, text
        m = re.search(r"HTTP:(\d+)", r.stdout or "")
        return (int(m.group(1)) if m else -1), (text or r.stderr or r.stdout)


FALLBACK_EXIT = 75   # callers (Claude, alt_writer.py) treat this as "do the job the old way, with Claude"


class Unavailable(Exception):
    """Gemini can't answer: quota used up, a feature not in the free tier (Search), a bad model name, an outage, no
    key, or no answer in time. Callers fall back to Claude."""
    def __init__(self, code, text):
        try:
            msg = json.loads(text).get("error", {}).get("message", "")
        except Exception:
            msg = (text or "")[:300]
        self.code, self.msg = code, (msg or "").strip()[:300]
        super().__init__("HTTP %s: %s" % (code, self.msg))


def unavailable(code, text):
    """Say so plainly and exit 75 so the caller falls back to Claude instead of stalling. Nothing is lost; the job
    just costs Claude tokens again."""
    e = text if isinstance(text, Unavailable) else Unavailable(code, text)
    print("GEMINI_UNAVAILABLE (HTTP %s): %s\nFall back: do this job with Claude as before." % (e.code, e.msg))
    sys.exit(FALLBACK_EXIT)


def generate(contents, model=None, tools=None, timeout=120, deadline=None):
    """The one call, for the CLI below and for tools that import this module (tools/alt_writer.py).
    `contents` is a list of parts (one user turn) or full Gemini turns. Returns (answer_text, response_json); raises
    Unavailable. `deadline` (a time.time() value) caps the whole thing, retries included, for callers that must answer
    fast; a 429/5xx with no time left to wait is Unavailable too."""
    if not env_key(required=False):
        raise Unavailable(0, '{"error": {"message": "%s"}}' % NO_KEY)
    if contents and "role" not in contents[0]:
        contents = [{"role": "user", "parts": contents}]
    body = {"contents": contents}
    if tools:
        body["tools"] = tools
    url = "%s/models/%s:generateContent" % (API, model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL))
    for attempt in range(4):
        left = timeout if deadline is None else int(deadline - time.time())
        if left < 2:
            raise Unavailable(-1, '{"error": {"message": "no time left for an answer"}}')
        code, text = ps_request("Post", url, body, timeout=min(timeout, left))
        if code == 200:
            data = json.loads(text)
            cand = (data.get("candidates") or [{}])[0]
            return "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", [])), data
        m = re.search(r'"retryDelay":\s*"(\d+)s"', text or "")
        # a per-minute limit says how long to wait; a used-up (or zero) daily/plan quota doesn't, so stop at once
        if code in (429, 500, 503) and (m or code != 429) and attempt < 3:
            wait = int(m.group(1)) + 1 if m else 10 * (attempt + 1)
            if deadline is not None and time.time() + wait + 2 > deadline:
                raise Unavailable(code, text)
            print("(rate limited or busy, HTTP %s; waiting %ds)" % (code, wait), file=sys.stderr)
            time.sleep(wait)
            continue
        raise Unavailable(code, text)
    raise Unavailable(code, text)


def guard(path):
    p = str(path).replace("\\", "/")
    if any(b.lower() in p.lower() for b in BLOCKED):
        sys.exit("Refusing to send %s to Gemini (private or secret file)." % path)


def build_parts(args):
    parts = []
    for f in args.file or []:
        guard(f)
        txt = Path(f).read_text(encoding="utf-8", errors="replace")
        if f.lower().endswith((".html", ".htm")) and not args.raw_html:
            txt = re.sub(r"<(script|style|svg)\b.*?</\1>", " ", txt, flags=re.S | re.I)
            txt = re.sub(r'<a [^>]*href="([^"]+)"[^>]*>', r" [link: \1] ", txt)   # keep hrefs so links can be checked
            txt = re.sub(r"<[^>]+>", " ", txt)
            txt = re.sub(r"[ \t]+", " ", txt)
            txt = re.sub(r"\n\s*\n+", "\n", txt)
        parts.append({"text": "FILE %s:\n%s" % (f, txt)})
    for im in args.image or []:
        guard(im)
        mt = mimetypes.guess_type(im)[0] or "image/jpeg"
        if mt in ("image/heic", "image/heif"):
            sys.exit("Send a JPEG/WebP copy (Images/web/... tiers), not the HEIC original.")
        parts.append({"inline_data": {"mime_type": mt, "data": base64.b64encode(Path(im).read_bytes()).decode()}})
    return parts


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--file", action="append", help="text/HTML file(s) to send (Claude never reads them)")
    ap.add_argument("--image", action="append", help="image(s) to send (JPEG/WebP)")
    ap.add_argument("--task", help="the instruction (added after the preset, if any)")
    ap.add_argument("--preset", choices=sorted(PRESETS), help="a built-in instruction")
    ap.add_argument("--context", help="extra facts for the model, e.g. the photo's place")
    ap.add_argument("--search", action="store_true", help="ground the answer in Google Search and list sources (no free-tier quota: exits 75)")
    ap.add_argument("--model", default=os.environ.get("GEMINI_MODEL", DEFAULT_MODEL))
    ap.add_argument("--max-lines", type=int, default=25, help="cap on lines printed back to Claude")
    ap.add_argument("--raw-html", action="store_true", help="send HTML as-is instead of stripped text")
    ap.add_argument("--list-models", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    if a.list_models:
        code, text = ps_request("Get", API + "/models?pageSize=200")
        if code != 200:
            sys.exit("HTTP %s: %s" % (code, text[:400]))
        for m in json.loads(text).get("models", []):
            if "generateContent" in m.get("supportedGenerationMethods", []):
                print(m["name"].split("/", 1)[1])
        return
    instr = "\n\n".join(x for x in (PRESETS.get(a.preset), a.task, ("Context: " + a.context) if a.context else None,
                                    "Answer in at most %d lines." % a.max_lines) if x)
    if not instr.strip():
        sys.exit("Give --task or --preset.")
    if not env_key(required=False):
        sys.exit(NO_KEY)
    parts = [{"text": instr}] + build_parts(a)
    try:
        answer, data = generate(parts, model=a.model, tools=[{"google_search": {}}] if a.search else None)
    except Unavailable as e:
        unavailable(e.code, e)
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (OUT / ("%s-%s.json" % (stamp, a.preset or "task"))).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    cand = (data.get("candidates") or [{}])[0]
    if a.preset == "alt":
        answer = answer.strip().strip('"').rstrip(".")       # house style: no trailing period, whatever the model does
    lines = [l for l in answer.strip().splitlines() if l.strip()]
    print("\n".join(lines[:a.max_lines]))
    if len(lines) > a.max_lines:
        print("(+%d more lines in .tmp/gemini/%s-%s.json)" % (len(lines) - a.max_lines, stamp, a.preset or "task"))
    if a.search:
        srcs = [c.get("web", {}) for c in cand.get("groundingMetadata", {}).get("groundingChunks", [])]
        for s in srcs[:5]:
            print("source: %s | %s" % (s.get("title", ""), s.get("uri", "")))
    u = data.get("usageMetadata", {})
    print("(gemini %s: %s in / %s out tokens)" % (a.model, u.get("promptTokenCount", "?"), u.get("candidatesTokenCount", "?")), file=sys.stderr)


if __name__ == "__main__":
    main()
