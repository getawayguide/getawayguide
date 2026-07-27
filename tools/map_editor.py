#!/usr/bin/env python3
"""
Itinerary-map label editor — drag the labels/pills of any map and save.

Run:
    python tools/map_editor.py
Then open http://localhost:5002

Pick a map from the sidebar, drag any place label, day count, transport pill,
dot label or airport label to reposition it, then hit Save. Save writes the new
offsets back to tools/itinerary_maps/<slug>.json, rebuilds the SVG and re-embeds
it into the article page (same as running embed_itinerary_map.py).

The canvas shows each map exactly as it will appear on the page (height-capped,
annotations at the standard font size), so what you arrange is what you get.
"""
import json
import sys
import subprocess
from pathlib import Path

from flask import Flask, jsonify, request, Response

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import itinerary_map as im  # noqa: E402

CFG_DIR = ROOT / "tools" / "itinerary_maps"
PAGES = {
    "el-salvador": "el-salvador/el-salvador-itinerary.html",
    "australia": "Drafts/australia/field-notes.html",
}

app = Flask(__name__)


def page_for(slug):
    if slug in PAGES:
        return PAGES[slug]
    # prefer the live page; fall back to the Drafts/ copy if not yet published
    if (ROOT / slug / "field-notes.html").exists():
        return f"{slug}/field-notes.html"
    if (ROOT / "Drafts" / slug / "field-notes.html").exists():
        return f"Drafts/{slug}/field-notes.html"
    return f"{slug}/field-notes.html"


def load_cfg(slug):
    return json.loads((CFG_DIR / f"{slug}.json").read_text(encoding="utf-8"))


def final_afs(cfg):
    """The annotation-scale a tall map ends up with (see itinerary_map.render)."""
    _, _, vh = im.build(cfg)
    if vh <= im.MAX_VH:
        return 1.0
    afs = vh / im.MAX_VH
    for _ in range(5):
        _, _, vh = im.build({**cfg, "_afs": afs})
        nxt = vh / im.MAX_VH
        if abs(nxt - afs) < 0.005:
            break
        afs = nxt
    return round(afs, 4)


def edit_svg(cfg):
    afs = final_afs(cfg)
    svg, _, _ = im.build({**cfg, "_edit": True, "_afs": afs})
    return svg, afs


@app.route("/api/maps")
def api_maps():
    slugs = sorted(p.stem for p in CFG_DIR.glob("*.json"))
    return jsonify(slugs)


@app.route("/api/map/<slug>")
def api_map(slug):
    cfg = load_cfg(slug)
    svg, afs = edit_svg(cfg)
    return jsonify(config=cfg, svg=svg, afs=afs, page=page_for(slug))


@app.route("/api/preview/<slug>", methods=["POST"])
def api_preview(slug):
    cfg = request.get_json()["config"]
    svg, afs = edit_svg(cfg)
    return jsonify(svg=svg, afs=afs)


@app.route("/api/map/<slug>", methods=["POST"])
def api_save(slug):
    cfg = request.get_json()["config"]
    (CFG_DIR / f"{slug}.json").write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # rebuild the standalone SVG (final render, with the height/afs treatment)
    svg, cw, ch = im.render(cfg)
    (ROOT / ".tmp" / "previews").mkdir(parents=True, exist_ok=True)
    (ROOT / ".tmp" / "previews" / f"{slug}-map.svg").write_text(svg, encoding="utf-8")
    # re-embed into the article page
    page = page_for(slug)
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "embed_itinerary_map.py"),
                        slug, page], cwd=str(ROOT), capture_output=True, text=True)
    ok = r.returncode == 0
    esvg, afs = edit_svg(cfg)
    return jsonify(ok=ok, log=(r.stdout + r.stderr).strip(), svg=esvg, afs=afs, page=page)


@app.route("/")
def index():
    return Response(PAGE, mimetype="text/html")


PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<title>Map label editor</title>
<style>
  :root{--green:#2D6B50;--ink:#1C2821;--line:#e3e0d8}
  *{box-sizing:border-box}
  body{margin:0;font-family:'Montserrat',system-ui,sans-serif;color:var(--ink);background:#f4f2ec;display:flex;height:100vh;overflow:hidden}
  #side{width:210px;flex:none;background:#fff;border-right:1px solid var(--line);overflow-y:auto;padding:14px 0}
  #side h1{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:#8a9;margin:0 16px 12px}
  .mapbtn{display:block;width:100%;text-align:left;border:0;background:none;padding:8px 16px;font:inherit;font-size:13px;color:var(--ink);cursor:pointer;text-transform:capitalize}
  .mapbtn:hover{background:#f0efe8}
  .mapbtn.active{background:#eaf1ec;color:var(--green);font-weight:600;box-shadow:inset 3px 0 var(--green)}
  #main{flex:1;display:flex;flex-direction:column;min-width:0}
  #bar{flex:none;display:flex;align-items:center;gap:14px;padding:10px 18px;background:#fff;border-bottom:1px solid var(--line)}
  #bar .title{font-weight:600;text-transform:capitalize;font-size:15px}
  #bar button{font:inherit;font-size:13px;border:1px solid var(--green);background:var(--green);color:#fff;border-radius:5px;padding:7px 16px;cursor:pointer}
  #bar button.ghost{background:#fff;color:var(--green)}
  #bar button:disabled{opacity:.4;cursor:default}
  #status{font-size:12px;color:#7a8a7f;margin-left:auto}
  #hint{font-size:12px;color:#98a}
  #stage{flex:1;overflow:auto;display:flex;align-items:flex-start;justify-content:center;padding:28px}
  #canvas{background:#F7F7F3;box-shadow:0 6px 30px rgba(0,0,0,.12);border-radius:4px;max-width:760px;width:100%}
  #canvas svg{width:100%;height:auto;display:block}
  .eh{cursor:grab}
  .eh:hover>.hit{stroke:var(--green);stroke-dasharray:3 3;stroke-width:1}
  .eh.drag{cursor:grabbing}
  .eh.drag>.hit{stroke:var(--green);stroke-dasharray:none;stroke-width:1.4}
  .hit{fill:transparent;stroke:transparent;vector-effect:non-scaling-stroke}
  #empty{margin:auto;color:#9a8;font-size:14px}
</style></head>
<body>
<div id="side"><h1>Maps</h1><div id="list"></div></div>
<div id="main">
  <div id="bar">
    <span class="title" id="ttl">Select a map</span>
    <span id="hint">drag any label / pill · double-click a place label to flip its side</span>
    <button class="ghost" id="reset" disabled>Revert</button>
    <button id="save" disabled>Save &amp; embed</button>
    <span id="status"></span>
  </div>
  <div id="stage"><div id="empty">Pick a map on the left</div></div>
</div>
<script>
const SVGNS="http://www.w3.org/2000/svg";
let slug=null, cfg=null, afs=1, dirty=false;
const listEl=document.getElementById('list'), stage=document.getElementById('stage');
const saveBtn=document.getElementById('save'), resetBtn=document.getElementById('reset');
const ttl=document.getElementById('ttl'), statusEl=document.getElementById('status');

fetch('/api/maps').then(r=>r.json()).then(maps=>{
  maps.forEach(m=>{
    const b=document.createElement('button'); b.className='mapbtn'; b.textContent=m.replace(/-/g,' ');
    b.onclick=()=>selectMap(m,b); listEl.appendChild(b);
  });
});

function selectMap(m,btn){
  document.querySelectorAll('.mapbtn').forEach(x=>x.classList.remove('active'));
  btn.classList.add('active');
  fetch('/api/map/'+m).then(r=>r.json()).then(d=>{
    slug=m; cfg=d.config; afs=d.afs; dirty=false;
    ttl.textContent=m.replace(/-/g,' '); statusEl.textContent='';
    render(d.svg); saveBtn.disabled=true; resetBtn.disabled=false;
  });
}

function render(svg){
  stage.innerHTML='<div id="canvas">'+svg+'</div>';
  const root=stage.querySelector('svg');
  root.querySelectorAll('.eh').forEach(g=>{
    const bb=g.getBBox();
    const hit=document.createElementNS(SVGNS,'rect');
    hit.setAttribute('class','hit');
    hit.setAttribute('x',bb.x-3); hit.setAttribute('y',bb.y-3);
    hit.setAttribute('width',bb.width+6); hit.setAttribute('height',bb.height+6);
    g.insertBefore(hit,g.firstChild);
    g.addEventListener('pointerdown',startDrag);
    g.addEventListener('dblclick',flipAnchor);
  });
}

function startDrag(e){
  e.preventDefault();
  const g=e.currentTarget; g.classList.add('drag');
  const inv=g.getScreenCTM().inverse();
  const p=(x,y)=>{const pt=g.ownerSVGElement.createSVGPoint();pt.x=x;pt.y=y;return pt.matrixTransform(inv);};
  const s=p(e.clientX,e.clientY);
  let d={dx:0,dy:0};
  function move(ev){
    const c=p(ev.clientX,ev.clientY);
    d={dx:c.x-s.x, dy:c.y-s.y};
    g.setAttribute('transform',`translate(${d.dx} ${d.dy})`);
  }
  function up(){
    window.removeEventListener('pointermove',move);
    g.classList.remove('drag');
    if(Math.abs(d.dx)>0.5||Math.abs(d.dy)>0.5){ commit(g,d); preview(); }
  }
  window.addEventListener('pointermove',move);
  window.addEventListener('pointerup',up,{once:true});
}

function round1(v){return Math.round(v*10)/10;}

function commit(g,d){
  const k=g.dataset.kind, i=+g.dataset.idx;
  if(k==='stop'){const o=cfg.stops[i]; o.dx=round1(+g.dataset.dx+d.dx); o.dy=round1(+g.dataset.dy+d.dy);}
  else if(k==='dot'){const o=cfg.dots[i]; o.dx=round1(+g.dataset.dx+d.dx); o.dy=round1(+g.dataset.dy+d.dy);}
  else if(k==='airport'){const o=cfg.airport; o.dx=round1(+g.dataset.dx+d.dx); o.dy=round1(+g.dataset.dy+d.dy);}
  else if(k==='leg'){const o=cfg.legs[i]; o.chip_off=0; o.chip_side=0; o.chip_dx=round1(+g.dataset.ox+d.dx); o.chip_dy=round1(+g.dataset.oy+d.dy);}
  markDirty();
}

function flipAnchor(e){
  const g=e.currentTarget, k=g.dataset.kind, i=+g.dataset.idx;
  let o = k==='stop'?cfg.stops[i] : k==='dot'?cfg.dots[i] : k==='airport'?cfg.airport : null;
  if(!o) return;                       // pills are centered; nothing to flip
  const cur=o.anchor||'start';
  o.anchor = cur==='start'?'end':(cur==='end'?'middle':'start');
  // nudge dx to the sensible side so the label doesn't sit on the pin
  if(o.anchor==='start') o.dx=Math.abs(o.dx||12);
  else if(o.anchor==='end') o.dx=-Math.abs(o.dx||12);
  else o.dx=0;
  markDirty(); preview();
}

function markDirty(){ dirty=true; saveBtn.disabled=false; statusEl.textContent='unsaved changes'; }

function preview(){
  fetch('/api/preview/'+slug,{method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({config:cfg})}).then(r=>r.json()).then(d=>{afs=d.afs; render(d.svg);});
}

saveBtn.onclick=()=>{
  statusEl.textContent='saving…'; saveBtn.disabled=true;
  fetch('/api/map/'+slug,{method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({config:cfg})}).then(r=>r.json()).then(d=>{
      dirty=false; afs=d.afs; render(d.svg);
      statusEl.textContent = d.ok ? ('saved → '+d.page) : ('embed error: '+d.log);
    });
};
resetBtn.onclick=()=>{ if(slug) fetch('/api/map/'+slug).then(r=>r.json()).then(d=>{cfg=d.config;afs=d.afs;dirty=false;render(d.svg);saveBtn.disabled=true;statusEl.textContent='reverted';}); };
window.addEventListener('beforeunload',e=>{ if(dirty){e.preventDefault();e.returnValue='';} });
</script>
</body></html>"""


if __name__ == "__main__":
    print("Map label editor → http://localhost:5002")
    app.run(port=5002, debug=False)
