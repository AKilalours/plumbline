"""Interactive visuals for the app: animated SVG and plain JavaScript, computed in the browser.

Streamlit reruns the whole page on every click, so a slider that waits on the server feels slow. These
visuals carry their (already measured) numbers into the page and do the arithmetic there: a slider, a
size picker or a card answers at once. Nothing here measures anything; every number comes from the
caller, which reads it from the warehouse or a saved result, so a test can check the two agree.

    page(kind, data) -> one self-contained HTML document (no outside scripts; fonts only)

Kinds: tiles, flow, sizes, worth, scale, game, gate, onboard, history, city, threshold, funnel, waterfall, timeline, trace, people, agent,
lineage, stream, kit, polite, incidents, cost, snapshots, incident, scorecard, rowflow, proofwall.
"""
from __future__ import annotations

import json

KINDS = ("tiles", "flow", "sizes", "worth", "scale", "game", "gate", "onboard", "history", "city", "threshold", "funnel", "waterfall", "timeline", "trace", "people", "agent", "lineage", "stream", "kit", "polite", "incidents", "cost", "snapshots", "incident", "scorecard", "rowflow", "proofwall")

_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap');
:root{--blue:#2941B9;--navy:#11196F;--gold:#E5B74D;--ink:#050505;--muted:#6B6B6B;--line:#E6E6E6;--tint:#F3F5FC;
--green:#1F8A5B;--red:#C2412D;--amber:#B7791F}
*{box-sizing:border-box}html,body{margin:0;padding:0;background:#fff;color:var(--ink);font-family:Geist,system-ui,sans-serif}
.mono{font-family:'Geist Mono',monospace;text-transform:uppercase;letter-spacing:.06em;font-size:11px;color:var(--muted)}
.num{font-variant-numeric:tabular-nums}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.tile{border:1px solid var(--line);border-radius:16px;padding:16px 18px;position:relative;overflow:hidden;transition:transform .15s,box-shadow .15s,border-color .15s;cursor:default}
.tile:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(17,25,111,.10);border-color:var(--blue)}
.tile .v{font-size:34px;font-weight:500;line-height:1.05;margin-top:8px}
.tile .s{font-size:13px;color:var(--muted);margin-top:6px;min-height:16px}
.tile .bar{position:absolute;left:0;top:0;bottom:0;width:4px}
.tone-pass .bar{background:var(--green)}.tone-warn .bar{background:var(--gold)}.tone-fail .bar{background:var(--red)}.tone-info .bar{background:var(--blue)}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:999px;padding:3px;gap:2px;background:#fff;flex-wrap:wrap}
.seg button{border:0;background:transparent;border-radius:999px;padding:6px 14px;font:500 13px Geist,sans-serif;color:var(--ink);cursor:pointer;transition:background .15s,color .15s}
.seg button:hover{background:var(--tint)}.seg button.on{background:var(--blue);color:#fff}
.band{background:linear-gradient(120deg,var(--navy) 0%,var(--blue) 70%,#4A63D8 100%);border-radius:20px;padding:20px 22px;color:#fff}
.band .mono{color:rgba(255,255,255,.75)}.band .seg{background:rgba(255,255,255,.12);border-color:rgba(255,255,255,.25)}
.band .seg button{color:#fff}.band .seg button.on{background:var(--gold);color:var(--ink)}
.band .grid{margin-top:16px;grid-template-columns:repeat(2,minmax(0,1fr))}.band .k .v{font-size:36px;font-weight:500;margin-top:4px}.band .k .s{font-size:12px;color:rgba(255,255,255,.75);margin-top:4px}
.cap{font-size:13px;color:var(--muted);min-height:18px;margin-top:8px}
.card{border:1px solid var(--line);border-radius:18px;padding:18px 20px;background:#fff}
.pill{display:inline-block;border-radius:999px;padding:3px 11px;font:500 11px 'Geist Mono',monospace;text-transform:uppercase;letter-spacing:.05em}
.p-pass{background:#E8F5EE;color:var(--green)}.p-fail{background:#FBEDEA;color:var(--red)}.p-warn{background:#FFF4DA;color:#7A5A06}.p-info{background:var(--tint);color:var(--blue)}
.decide{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));margin-top:14px}
.decide .card{position:relative;overflow:hidden}.decide .w{font-size:26px;font-weight:600;letter-spacing:-.01em;margin:8px 0 4px}
.decide .card::before{content:"";position:absolute;inset:0 auto 0 0;width:5px}
.decide .pass::before{background:var(--green)}.decide .fail::before{background:var(--red)}.decide .info::before{background:var(--blue)}
.decide .pass .w{color:var(--green)}.decide .fail .w{color:var(--red)}.decide .info .w{color:var(--blue)}
.decide .t{font-size:13px;color:var(--muted);line-height:1.4}
input[type=range]{width:100%;margin:0;accent-color:var(--blue);height:28px}
.row{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.bars .b{display:grid;grid-template-columns:minmax(120px,210px) 1fr 92px;align-items:center;gap:12px;padding:7px 0;cursor:default}
.bars .b:hover .track{background:#E9ECF8}
.bars .lab{font-size:14px}.bars .lab small{display:block;color:var(--muted);font-size:12px}
.track{height:14px;background:#F2F3F6;border-radius:99px;position:relative;overflow:hidden;transition:background .15s}
.fill{position:absolute;inset:0 auto 0 0;border-radius:99px;transition:width .6s cubic-bezier(.2,.8,.2,1)}
.bars .val{text-align:right;font-weight:500}
.fade{animation:rise .45s ease both}@keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.flowdash{stroke-dasharray:2 22;animation:dash 1.6s linear infinite}@keyframes dash{to{stroke-dashoffset:-24}}
.draw{stroke-dasharray:1;stroke-dashoffset:1;animation:draw .9s ease forwards}@keyframes draw{to{stroke-dashoffset:0}}
.grow{transform-origin:left;animation:grow .7s cubic-bezier(.2,.8,.2,1) both}@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}
.kbd{display:inline-block;border:1px solid currentColor;border-radius:5px;padding:0 5px;font:500 11px 'Geist Mono',monospace;margin-left:6px;opacity:.75}
.btn{border-radius:999px;padding:10px 20px;font:600 14px Geist,sans-serif;cursor:pointer;border:1px solid var(--ink);background:#fff;color:var(--ink);transition:all .15s}
.btn:hover{background:var(--ink);color:#fff}.btn.blue{background:var(--blue);border-color:var(--blue);color:#fff}.btn.blue:hover{background:var(--navy)}
.btn:disabled{opacity:.4;cursor:default}
.recs{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.rec{border:1px solid var(--line);border-radius:14px;padding:12px 14px}
.f{display:flex;justify-content:space-between;gap:8px;padding:5px 0;border-bottom:1px dashed #EEE;font-size:14px}
.f span{text-align:right;overflow-wrap:anywhere;min-width:0}.f:last-child{border:0}.f b{font:500 11px 'Geist Mono',monospace;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;padding-top:2px}
.agree{color:var(--green)}.differ{color:var(--red);font-weight:600}.missing{color:#B0B0B0}
.dots{display:flex;gap:4px;flex-wrap:wrap}.dots i{width:9px;height:9px;border-radius:50%;background:var(--line);display:inline-block}
.dots i.on{background:var(--blue)}.dots i.r{background:var(--green)}.dots i.w{background:var(--red)}
.flyR{transform:translateX(60px) rotate(2deg);opacity:0;transition:all .28s}.flyL{transform:translateX(-60px) rotate(-2deg);opacity:0;transition:all .28s}
.chip{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:3px 11px;font-size:12px;margin:3px 4px 0 0;max-width:100%;overflow-wrap:anywhere}
.chip.gone{text-decoration:line-through;color:var(--red);border-color:#F2C9C0;background:#FDF5F3}
.chip.new{color:var(--blue);border-color:#C9D1F2;background:var(--tint)}
@media (max-width:560px){.recs{grid-template-columns:1fr}.bars .b{grid-template-columns:1fr 70px}.bars .track{grid-column:1/3;grid-row:2}.tile .v{font-size:28px}}
.stage{display:grid;grid-template-columns:130px 1fr 150px;gap:10px;align-items:center;margin-top:14px}
.box{border:2px solid var(--line);border-radius:16px;padding:12px;text-align:center;min-height:96px;display:flex;flex-direction:column;justify-content:center}
.box.api{border-color:#C9D1F2;background:var(--tint)}.box.wh{border-color:var(--blue)}
.box .v{font-size:30px;font-weight:600}.lane{position:relative;height:96px;border-radius:14px;background:repeating-linear-gradient(90deg,#F6F7FA 0 18px,#fff 18px 36px);overflow:hidden}
.pg{position:absolute;top:24px;left:-70px;width:64px;height:48px;border-radius:10px;background:var(--blue);color:#fff;display:flex;flex-direction:column;align-items:center;justify-content:center;font:600 13px Geist;box-shadow:0 6px 16px rgba(41,65,185,.25)}
.pg small{font:500 10px 'Geist Mono';opacity:.8}.pg.dup{background:#9AA3C7}.pg.lost{background:var(--red)}
.boom{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font:700 22px Geist;color:var(--red);background:rgba(251,237,234,.92);animation:rise .3s both}
.curs{display:inline-flex;gap:8px;align-items:center;border:1px dashed var(--blue);border-radius:10px;padding:6px 10px;font:500 12px 'Geist Mono';color:var(--blue);margin-top:10px;transition:background .3s}
.curs.flash{background:#FFF4DA}
.result{margin-top:14px;border-radius:14px;padding:14px 16px;font-size:15px;animation:rise .4s both}
.result.ok{background:#E8F5EE;color:#11583A}.result.bad{background:#FBEDEA;color:#7E2414}
.lights{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}.lights .card{padding:14px}
.light{display:flex;gap:10px;align-items:flex-start;padding:6px 0;font-size:13px}.light i{flex:none;width:12px;height:12px;border-radius:50%;margin-top:3px}
.light i.p{background:var(--green);box-shadow:0 0 0 4px #E8F5EE}.light i.f{background:var(--red);box-shadow:0 0 0 4px #FBEDEA}.light i.s{background:#C9CCD6}
pre.code{background:#0B1030;color:#DCE2FF;border-radius:14px;padding:14px 16px;font:12.5px/1.55 'Geist Mono',monospace;overflow:auto;margin:10px 0 0}
pre.code .hl{background:rgba(229,183,77,.25);display:inline-block;width:100%;border-left:3px solid var(--gold);margin-left:-8px;padding-left:5px}
.tl{position:relative;height:150px;margin-top:16px;border-bottom:2px solid var(--line);overflow-x:clip}
.tl .q{position:absolute;bottom:0;transform:translateX(-50%);display:flex;flex-direction:column;align-items:center;gap:4px;cursor:default}
.tl .q b{width:16px;height:16px;border-radius:50%;display:block;border:3px solid #fff;box-shadow:0 0 0 1px var(--line)}
.tl .q span{font:500 10px 'Geist Mono';color:var(--muted);white-space:nowrap}
.tl .w{position:absolute;bottom:-14px;height:8px;border-radius:4px;background:repeating-linear-gradient(45deg,var(--gold) 0 4px,#F6E3B0 4px 8px)}
.tl .now{position:absolute;top:0;bottom:0;width:2px;background:var(--ink);transition:left .25s linear}
.lanes .ln{display:grid;grid-template-columns:150px 1fr;gap:12px;align-items:center;padding:10px 0;border-bottom:1px dashed #EEE}
.track2{position:relative;height:34px}.track2 .rd{position:absolute;top:11px;width:12px;height:12px;border-radius:50%;transform:translateX(-50%);cursor:pointer}
.track2 .rd.ok{background:var(--green)}.track2 .rd.no{background:var(--red)}.track2 .rd.hand{background:#fff;border:3px solid var(--amber)}
.track2 .hurt{position:absolute;top:4px;height:26px;border-radius:8px;z-index:2;min-width:16px;background:rgba(194,65,45,.13);border:1px solid rgba(194,65,45,.45);cursor:pointer;transition:background .15s}
.track2 .hurt:hover,.track2 .hurt.on{background:rgba(194,65,45,.28)}
.snaps{display:flex;align-items:flex-end;gap:10px;overflow-x:auto;padding:10px 2px}
.snap{flex:none;display:flex;flex-direction:column;align-items:center;gap:6px;cursor:pointer}
.snap b{display:block;border-radius:50%;background:var(--blue);transition:transform .15s}.snap.on b{outline:3px solid var(--gold);outline-offset:3px}
.snap:hover b{transform:scale(1.08)}.snap span{font:500 10px 'Geist Mono';color:var(--muted)}
@media (max-width:560px){.stage{grid-template-columns:1fr}.lights{grid-template-columns:1fr}.lanes .ln{grid-template-columns:1fr}}
.rail{display:flex;align-items:flex-start;gap:0;margin:18px 0 6px;overflow-x:auto;padding-bottom:6px}
.ph{flex:1 0 92px;display:flex;flex-direction:column;align-items:center;gap:6px;cursor:pointer;position:relative;opacity:.35;transition:opacity .25s}
.ph.lit{opacity:1}.ph::before{content:'';position:absolute;top:15px;left:-50%;right:50%;height:3px;background:var(--line)}
.ph:first-child::before{display:none}.ph.lit::before{background:var(--blue)}
.ph b{width:32px;height:32px;border-radius:50%;background:#fff;border:3px solid var(--line);display:flex;align-items:center;justify-content:center;font:600 13px Geist;z-index:1;transition:all .25s}
.ph.lit b{border-color:var(--blue);background:var(--blue);color:#fff}.ph.on b{box-shadow:0 0 0 6px rgba(41,65,185,.18)}
.ph.fix.lit b{background:var(--green);border-color:var(--green)}.ph.inject.lit b{background:var(--red);border-color:var(--red)}
.ph span{font:500 11px 'Geist Mono';text-transform:uppercase;letter-spacing:.06em;color:var(--muted);text-align:center}
.ba{margin:12px 0}.ba .bar2{height:16px;border-radius:8px;background:var(--red);transition:width 1.2s cubic-bezier(.4,.1,.2,1)}
.kvs{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-top:10px}
.kvs div{background:var(--tint);border-radius:12px;padding:10px 12px}.kvs b{display:block;font-size:17px;margin-top:3px;word-break:break-word}
.sc-row{display:grid;grid-template-columns:minmax(150px,1.1fr) 2fr 90px;gap:14px;align-items:center;padding:12px 0;border-bottom:1px dashed #EEE;cursor:default}
.sc-row .bars{display:flex;flex-direction:column;gap:5px}.sc-row .bb{height:12px;border-radius:6px;transition:width .9s cubic-bezier(.4,.1,.2,1);width:0}
.sc-row .lbl{font:500 11px 'Geist Mono';color:var(--muted)}
@media (max-width:560px){.sc-row{grid-template-columns:1fr}}
@keyframes drawin{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}@keyframes travel{from{stroke-dashoffset:24}to{stroke-dashoffset:0}}
.fl .bnd{animation:drawin .9s cubic-bezier(.2,.7,.2,1) both}.fl .dash{animation:travel 1.6s linear infinite}
.fl g.st{cursor:pointer;transition:opacity .2s}.fl g.st.dim{opacity:.18}
.fl text{font-family:Geist,system-ui,sans-serif}
.pw{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.pc{height:250px;perspective:1200px}.pc button{all:unset;display:block;position:relative;width:100%;height:100%;cursor:pointer;transform-style:preserve-3d;transition:transform .55s cubic-bezier(.3,.7,.2,1)}
.pc.flip button{transform:rotateY(180deg)}.pc .face{position:absolute;inset:0;backface-visibility:hidden;border-radius:18px;padding:18px;display:flex;flex-direction:column;justify-content:space-between;box-sizing:border-box}
.pc .front{background:#fff;border:1px solid var(--line)}.pc .back{background:var(--navy);color:#EEF1FF;transform:rotateY(180deg)}
.pc .ttl{font-size:16px;font-weight:600;line-height:1.25}.pc .hint{font:500 10.5px 'Geist Mono';color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.pc .back p{font-size:12.5px;line-height:1.45;margin:8px 0;opacity:.9;flex:1;overflow:auto;min-height:0}.pc .open{align-self:flex-start;background:#fff;color:var(--navy);border-radius:999px;padding:6px 14px;font-weight:600;font-size:13px;text-decoration:none}
.pc .miss{color:var(--red)}.pc .big{font-size:34px;font-weight:300;line-height:1;font-variant-numeric:tabular-nums}
.vs .b{height:12px;border-radius:6px;background:var(--tint);margin-top:4px}.vs .b i{display:block;height:100%;border-radius:6px;width:0;transition:width 1s cubic-bezier(.2,.7,.2,1)}
.dt{display:flex;gap:5px;flex-wrap:wrap}.dt i{width:14px;height:14px;border-radius:50%;transform:scale(0);transition:transform .3s cubic-bezier(.3,1.6,.5,1)}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""

_JS = r"""
const D = JSON.parse(document.getElementById('data').textContent);
const app = document.getElementById('app');
const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const F = {
  int: v => Math.round(v).toLocaleString('en-US'),
  f3: v => v.toFixed(3), f4: v => v.toFixed(4), pct0: v => (100*v).toFixed(0)+'%', pct1: v => (100*v).toFixed(1)+'%',
  usd3: v => '$'+v.toFixed(3), usd0: v => '$'+Math.round(v).toLocaleString('en-US'),
  h: v => Math.round(v).toLocaleString('en-US')+' h', min: v => v.toFixed(1)+' min', s: v => v.toFixed(2)+' s',
  plus4: v => (v>=0?'+':'')+v.toFixed(4), raw: v => String(v),
};
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function count(el, to, fmt, from) {
  const f = F[fmt] || F.int;
  if (typeof to !== 'number' || reduce) { el.textContent = typeof to === 'number' ? f(to) : to; return; }
  const a = (typeof from === 'number') ? from : 0, t0 = performance.now(), dur = 850;
  // rAF's timestamp is when the frame began, which can be before t0: unclamped, the first frame drew "-336 rows",
  // and in a collapsed section (no further frames) it stayed that way.
  const step = t => { const p = Math.max(0, Math.min(1, (t - t0) / dur)), e = 1 - Math.pow(1 - p, 3);
    el.textContent = f(a + (to - a) * e); if (p < 1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}
window.parent.postMessage({isStreamlitMessage: true, type: 'streamlit:componentReady', apiVersion: 1}, '*');
function fit() {
  const h = Math.ceil(document.getElementById('app').getBoundingClientRect().height) + 12;
  // The frame shares the app's origin, so it can size itself; otherwise the fixed height the caller gave stays.
  try { const f = window.frameElement; if (f && Math.abs(f.offsetHeight - h) > 2) { f.style.height = h + 'px'; f.height = h;
    const box = f.closest('[data-testid="stElementContainer"]') || f.parentElement; if (box) box.style.height = 'auto'; } } catch (e) {}
}
new ResizeObserver(fit).observe(document.body);
function seg(opts, on, cb) {
  const s = document.createElement('div'); s.className = 'seg';
  opts.forEach(o => { const b = document.createElement('button'); b.textContent = o; if (o === on) b.className = 'on';
    b.onclick = () => { s.querySelectorAll('button').forEach(x => x.className = ''); b.className = 'on'; cb(o); }; s.appendChild(b); });
  return s;
}
function tiles(items, cls) {
  const g = document.createElement('div'); g.className = 'grid ' + (cls || '');
  items.forEach((t, i) => { const d = document.createElement('div'); d.className = 'tile fade tone-' + (t.tone || 'info');
    d.style.animationDelay = (i * 60) + 'ms'; d.title = t.hint || '';
    d.innerHTML = `<span class="bar"></span><div class="mono">${esc(t.label)}</div><div class="v num"></div><div class="s">${esc(t.sub || '')}</div>`;
    g.appendChild(d); count(d.querySelector('.v'), t.value, t.fmt); });
  return g;
}

const R = {};
R.tiles = () => app.appendChild(tiles(D.items));

// Streams from sources (left) to outcomes (right), width = volume. Hover a stream or a node for the exact count.
R.flow = () => {
  const views = D.views, wrap = document.createElement('div');
  const top = document.createElement('div'); top.className = 'row';
  const cap = document.createElement('div'); cap.className = 'cap';
  const box = document.createElement('div');
  if (views.length > 1) top.appendChild(seg(views.map(v => v.name), views[0].name, n => draw(views.find(v => v.name === n))));
  wrap.append(top, box, cap); app.appendChild(wrap);
  let last = null; new ResizeObserver(() => { if (last && Math.abs(box.clientWidth - last.w) > 20) draw(last.v); }).observe(box);
  function draw(v) {
    // Drawn at the real width with fixed-size text: a scaled-down picture is unreadable on a phone.
    const W = Math.max(320, box.clientWidth || 800), narrow = W < 600;
    const X0 = narrow ? 84 : Math.round(W * 0.2), X1 = narrow ? W - 150 : Math.round(W * 0.6), GAP = 16, MIN = narrow ? 34 : 30, BAND = narrow ? 200 : 230;
    const FS = narrow ? 14 : 17, FL = narrow ? 11 : 12.5; last = {w: W, v};
    const L = v.left, Rt = v.right, tot = L.reduce((a, n) => a + n.value, 0) || 1;
    let y = 30; const ln = {}; L.forEach(n => { const h = Math.max(4, BAND * n.value / tot); ln[n.key] = {y, h, used: 0}; y += h + GAP; });
    const rtot = Rt.reduce((a, n) => a + n.value, 0) || 1; let ry = 30; const rn = {};
    Rt.forEach(n => { const h = Math.max(4, BAND * n.value / rtot); rn[n.key] = {y: ry, h, used: 0}; ry += Math.max(h, MIN) + GAP; });
    const H = Math.max(y, ry) + 20;
    let s = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" style="display:block;max-width:100%">`;
    v.links.forEach((k, i) => {
      const a = ln[k.from], b = rn[k.to]; if (!a || !b || !k.value) return;
      const wa = a.h * k.value / L.find(n => n.key === k.from).value, wb = b.h * k.value / Rt.find(n => n.key === k.to).value;
      const ya = a.y + a.used + wa / 2, yb = b.y + b.used + wb / 2; a.used += wa; b.used += wb;
      const w = Math.max(1.5, (wa + wb) / 2), m = (X0 + X1) / 2, d = `M${X0},${ya} C${m},${ya} ${m},${yb} ${X1},${yb}`;
      const c = L.find(n => n.key === k.from).color;
      s += `<g class="lk" data-from="${k.from}" data-to="${k.to}" data-v="${k.value}" style="cursor:pointer;transition:opacity .2s">
        <path d="${d}" fill="none" stroke="${c}" stroke-opacity=".28" stroke-width="${w}" pathLength="1" class="draw" style="animation-delay:${i*70}ms"/>
        <path d="${d}" fill="none" stroke="${c}" stroke-width="${Math.min(Math.max(1.5, w * .25), 4)}" stroke-linecap="round" class="flowdash" style="animation-duration:${k.live ? 0.8 : 1.8}s"/></g>`;
    });
    L.forEach(n => { const a = ln[n.key];
      s += `<g class="nd" data-k="${n.key}" style="cursor:pointer"><rect x="${X0 - 10}" y="${a.y}" width="10" height="${a.h}" rx="3" fill="${n.color}"/>
      <text x="${X0 - 16}" y="${a.y + a.h / 2 - 3}" text-anchor="end" font-size="${FS}" font-weight="500" class="num">${F.int(n.value)}</text>
      <text x="${X0 - 16}" y="${a.y + a.h / 2 + 14}" text-anchor="end" font-size="${FL}" fill="#6B6B6B">${esc(narrow && n.short ? n.short : n.label)}</text></g>`; });
    Rt.forEach(n => { const b = rn[n.key];
      s += `<g class="nd" data-k="${n.key}" style="cursor:pointer"><rect x="${X1}" y="${b.y}" width="10" height="${b.h}" rx="3" fill="${n.color}"/>
      <text x="${X1 + 18}" y="${b.y + b.h / 2 - 2}" font-size="${FS}" font-weight="500" class="num">${F.int(n.value)}${narrow ? '' :
        `<tspan font-size="13" font-weight="400" fill="#6B6B6B" dx="8">${esc(n.label)}</tspan>`}</text>
      <text x="${X1 + 18}" y="${b.y + b.h / 2 + 15}" font-size="${FL}" fill="#6B6B6B">${esc(narrow ? n.label : (n.sub || ''))}</text></g>`; });
    s += `</svg>`; box.innerHTML = s;
    const tip = v.hint || 'Hover a stream for the exact count.'; cap.textContent = tip;
    const lks = [...box.querySelectorAll('.lk')], all = [...lks];
    const name = k => (L.find(n => n.key === k) || Rt.find(n => n.key === k)).label;
    const focus = (f) => all.forEach(g => g.style.opacity = f(g) ? 1 : .15);
    lks.forEach(g => { g.onmouseenter = () => { focus(x => x === g);
        cap.textContent = `${name(g.dataset.from)} → ${name(g.dataset.to)}: ${F.int(+g.dataset.v)} ${v.unit}`; };
      g.onmouseleave = () => { focus(() => true); cap.textContent = tip; }; });
    box.querySelectorAll('.nd').forEach(g => { const k = g.dataset.k;
      g.onmouseenter = () => { focus(x => x.dataset.from === k || x.dataset.to === k);
        const n = L.find(x => x.key === k) || Rt.find(x => x.key === k); cap.textContent = `${n.label}: ${F.int(n.value)} ${v.unit}${n.note ? ' · ' + n.note : ''}`; };
      g.onmouseleave = () => { focus(() => true); cap.textContent = tip; }; });
  }
  draw(views[0]);
};

// The same pipeline measured at several sizes; switching counts each number from the old value to the new.
R.sizes = () => {
  const b = document.createElement('div'); b.className = 'band';
  const head = document.createElement('div'); head.className = 'row';
  head.innerHTML = `<div class="mono" style="flex:1">${esc(D.title)}</div>`;
  const g = document.createElement('div'); g.className = 'grid';
  const keys = D.metrics; const els = {}; let cur = null;
  keys.forEach(m => { const k = document.createElement('div'); k.className = 'k';
    k.innerHTML = `<div class="mono">${esc(m.label)}</div><div class="v num"></div><div class="s"></div>`; g.appendChild(k); els[m.key] = k; });
  const show = size => { const r = D.sizes[size];
    keys.forEach(m => { const e = els[m.key], val = r[m.key];
      count(e.querySelector('.v'), val, m.fmt, cur ? cur[m.key] : 0); e.querySelector('.s').textContent = (r.sub && r.sub[m.key]) || m.sub || ''; });
    cur = r; };
  head.appendChild(seg(Object.keys(D.sizes), D.start, show));
  b.append(head, g); app.appendChild(b); show(D.start);
};

// Should we buy this data? The value of a buyer is a slider; worth per record is recomputed at once.
R.worth = () => {
  const w = document.createElement('div');
  w.innerHTML = `<div class="row"><div style="flex:1;min-width:0;width:100%"><div class="mono">What one home buyer is worth to the client</div>
    <input type="range" min="100" max="10000" step="100" value="${D.value}" id="val"></div>
    <div style="font-size:40px;font-weight:500" class="num" id="vv"></div></div>
    <div class="mono" style="margin-top:14px">Most a record is worth paying, per person scored</div><div class="bars" id="bars"></div>
    <div class="cap" id="cap">${esc(D.hint)}</div><div class="decide" id="dec"></div>`;
  app.appendChild(w);
  const bars = w.querySelector('#bars'), max = Math.max(...D.sources.map(s => s.lost_per_person)) * 10000 || 1;
  D.sources.forEach(s => { const r = document.createElement('div'); r.className = 'b';
    // "AUC -0.0496" under a source read as the source's own score; it is the change when the source is removed.
    // A source the model has no inputs from cannot be measured this way, and is said so rather than shown as $0.
    const note = !s.used ? 'not an input to this model, so not measured'
      : `without it, AUC ${F.plus4(s.auc_change)} (95%: ${F.plus4(s.lo)} to ${F.plus4(s.hi)})`;
    r.innerHTML = `<div class="lab">${esc(s.label)}<small>${esc(s.cost)} · ${note}</small></div>
      <div class="track"><div class="fill" style="background:${s.measured ? s.color : '#C9C9C9'};width:0"></div></div><div class="val num">$0.000</div>`;
    r.onmouseenter = () => w.querySelector('#cap').textContent = s.why; r.onmouseleave = () => w.querySelector('#cap').textContent = D.hint;
    bars.appendChild(r); s.el = r; });
  let prev = {};
  const upd = v => { w.querySelector('#vv').textContent = F.usd0(v);
    D.sources.forEach(s => { const p = s.measured ? s.lost_per_person * v : 0;
      s.el.querySelector('.fill').style.width = (100 * p / (max * D.max_value / 10000)) + '%';
      if (s.measured) count(s.el.querySelector('.val'), p, 'usd3', prev[s.label] || 0);
      else s.el.querySelector('.val').textContent = s.used ? 'no effect' : 'n/a';
      prev[s.label] = p; });
    const per10k = D.sources[0].lost_per_person * v * 10000;
    const dec = w.querySelector('#dec');
    dec.innerHTML = D.decisions.map(d => `<div class="card ${d.tone} fade"><div class="mono">${esc(d.label)}</div><div class="w">${esc(d.word)}</div>
      <div class="t">${esc(d.text.replace('{per10k}', F.usd0(per10k)))}</div></div>`).join(''); };
  const inp = w.querySelector('#val'); let t = 0;
  inp.oninput = () => { clearTimeout(t); const v = +inp.value; t = setTimeout(() => upd(v), 40); };
  upd(D.value);
};

// Past one machine: projected hours per stage at the size picked; red stages cost more per extra person.
R.scale = () => {
  D.stages = D.stages.filter(s => s.key in D.projected[D.start]);
  const w = document.createElement('div');
  const top = document.createElement('div'); top.className = 'row';
  top.innerHTML = `<div class="mono" style="flex:1">${esc(D.title)}</div>`;
  const k = document.createElement('div'); k.style.marginTop = '12px';
  const bars = document.createElement('div'); bars.className = 'bars'; bars.style.marginTop = '10px';
  const cap = document.createElement('div'); cap.className = 'cap'; cap.textContent = D.hint;
  w.append(top, k, bars, cap); app.appendChild(w);
  let cur = null, prev = {};
  const show = t => { const p = D.projected[t], tot = Object.values(p).reduce((a, x) => a + x, 0), mx = Math.max(...Object.values(p));
    k.innerHTML = ''; k.appendChild(tiles([
      {label: 'One 2-core machine', value: tot / 60, fmt: 'h', sub: `for ${F.int(D.targets[t])} people`, tone: 'info'},
      {label: 'Grows faster than the data', value: D.stages.filter(s => s.rising).length, fmt: 'int', sub: 'stages, in red', tone: 'fail'},
      {label: 'Warehouse per person', value: D.bytes_per_person, fmt: 'int', sub: 'bytes', tone: 'info'}]));
    if (!bars.children.length) D.stages.forEach(s => { const r = document.createElement('div'); r.className = 'b';
      r.innerHTML = `<div class="lab">${esc(s.label)}<small>${s.rising ? 'cost per person rising' : 'cost per person flat'}</small></div>
        <div class="track"><div class="fill" style="background:${s.rising ? 'var(--red)' : 'var(--blue)'};width:0"></div></div><div class="val num"></div>`;
      r.onmouseenter = () => cap.textContent = `${s.label}: moves to ${s.next}`; r.onmouseleave = () => cap.textContent = D.hint;
      bars.appendChild(r); s.el = r; });
    const order = [...D.stages].sort((a, b) => p[b.key] - p[a.key]);
    order.forEach(s => { bars.appendChild(s.el); s.el.querySelector('.fill').style.width = (100 * p[s.key] / mx) + '%';
      count(s.el.querySelector('.val'), p[s.key] / 60, 'h', prev[s.key] || 0); prev[s.key] = p[s.key] / 60; });
    cur = t; };
  top.appendChild(seg(Object.keys(D.targets), D.start, show)); show(D.start);
};

// You be the matcher: two records, same person or not? Then the matcher's call and the answer key.
R.game = () => {
  const P = D.pairs; let i = 0, you = 0, it = 0, seen = 0; const res = [];
  const w = document.createElement('div'); app.appendChild(w);
  const ring = p => { const r = 30, L = 2 * Math.PI * r, off = L * (1 - p), c = p >= .9 ? 'var(--green)' : p >= .5 ? 'var(--blue)' : 'var(--red)';
    return `<svg viewBox="0 0 76 76" width="86" height="86" style="transform:rotate(-90deg)"><circle cx="38" cy="38" r="${r}" fill="none" stroke="#EEE" stroke-width="7"/>
      <circle cx="38" cy="38" r="${r}" fill="none" stroke="${c}" stroke-width="7" stroke-linecap="round" stroke-dasharray="${L}" stroke-dashoffset="${L}">
      <animate attributeName="stroke-dashoffset" from="${L}" to="${off}" dur=".8s" fill="freeze" calcMode="spline" keySplines=".2 .8 .2 1"/></circle>
      <text x="38" y="38" transform="rotate(90 38 38)" text-anchor="middle" dominant-baseline="central" font-size="16" font-weight="500">${Math.round(100 * p)}%</text></svg>`; };
  const rec = (r, o) => `<div class="rec"><div class="mono">${esc(r.source)} record</div>${D.fields.map(f =>
      `<div class="f"><b>${esc(f)}</b><span class="${o[f]}">${esc(r[f] || '·')}</span></div>`).join('')}</div>`;
  function show(dir) {
    const p = P[i % P.length];
    const dots = P.map((_, n) => `<i class="${res[n] === undefined ? (n === i % P.length ? 'on' : '') : res[n] ? 'r' : 'w'}"></i>`).join('');
    w.innerHTML = `<div class="row" style="margin-bottom:10px"><div class="dots">${dots}</div><div style="flex:1"></div>
      <span class="pill p-info">you ${you}/${seen}</span><span class="pill p-info">matcher ${it}/${seen}</span></div>
      <div class="card fade" id="c"><div class="row" style="justify-content:space-between"><div class="mono">${esc(p.kind)}</div>
      <div class="mono">${i % P.length + 1} of ${P.length}</div></div>
      <div class="recs" style="margin-top:10px">${rec(p.a, p.agree)}${rec(p.b, p.agree)}</div>
      <div class="row" id="act" style="margin-top:16px"><button class="btn blue" id="s">Same person<span class="kbd">S</span></button>
      <button class="btn" id="d">Different people<span class="kbd">D</span></button><span class="cap" style="margin:0">Green fields agree, red differ, grey missing.</span></div></div>`;
    w.querySelector('#s').onclick = () => answer(true); w.querySelector('#d').onclick = () => answer(false);
  }
  function answer(same) {
    const p = P[i % P.length], n = i % P.length; if (res[n] !== undefined) return;
    const youRight = same === p.truth, itRight = p.matcher === p.truth; seen++; if (youRight) you++; if (itRight) it++; res[n] = youRight;
    const act = w.querySelector('#act');
    act.innerHTML = `<div class="row" style="width:100%;align-items:center">${ring(p.p)}<div style="flex:1;min-width:220px">
      <div class="mono">matcher's belief they are one person</div>
      <div style="font-size:22px;font-weight:600;margin:4px 0" class="${p.truth ? 'agree' : 'differ'}">${p.truth ? 'One person' : 'Two people'}</div>
      <div class="cap" style="margin:0">Answer key. You: ${youRight ? 'right' : 'wrong'}. Matcher: ${p.matcher ? 'joined them' : 'kept them apart'}, ${itRight ? 'right' : 'wrong'}.
      ${esc(p.why || '')}</div></div><button class="btn blue" id="n">Next<span class="kbd">→</span></button></div>`;
    w.querySelector('#n').onclick = next;
    w.querySelectorAll('.dots i')[n].className = youRight ? 'r' : 'w';
    w.querySelectorAll('.pill')[0].textContent = `you ${you}/${seen}`; w.querySelectorAll('.pill')[1].textContent = `matcher ${it}/${seen}`;
  }
  function next() { const c = w.querySelector('#c'); c.className = 'card flyR'; setTimeout(() => { i++; show(); }, 260); }
  document.addEventListener('keydown', e => { const k = e.key.toLowerCase();
    if (k === 's') answer(true); else if (k === 'd') answer(false); else if (k === 'arrowright' || k === 'enter') { if (w.querySelector('#n')) next(); } });
  show();
};

// Release gate: each challenger, four gates, every one must pass.
R.gate = () => {
  const g = document.createElement('div'); g.className = 'decide'; app.appendChild(g);
  const cap = document.createElement('div'); cap.className = 'cap'; cap.textContent = D.hint; app.appendChild(cap);
  D.challengers.forEach((c, n) => { const d = document.createElement('div'); d.className = 'card fade ' + (c.release ? 'pass' : 'fail');
    d.style.animationDelay = (n * 90) + 'ms';
    d.innerHTML = `<div class="mono">${esc(c.label)}</div><div class="w">${c.release ? 'Released' : 'Held'}</div>
      <div class="t num">AUC ${c.auc.toFixed(3)} vs champion ${D.champion_auc.toFixed(3)}</div>
      <div style="margin-top:12px">${c.gates.map((x, k) => `<div class="gt" data-t="${esc(x.detail)}" style="margin:9px 0;cursor:default">
        <div class="row" style="justify-content:space-between;font-size:13px"><span>${esc(x.name)}</span>
        <span class="pill ${x.passed ? 'p-pass' : 'p-fail'}">${x.passed ? 'pass' : 'fail'}</span></div>
        <div class="track" style="height:6px;margin-top:5px"><div class="fill grow" style="width:100%;background:${x.passed ? 'var(--green)' : 'var(--red)'};animation-delay:${n * 90 + k * 120}ms"></div></div></div>`).join('')}</div>`;
    d.querySelectorAll('.gt').forEach(x => { x.onmouseenter = () => cap.textContent = x.dataset.t; x.onmouseleave = () => cap.textContent = D.hint; });
    g.appendChild(d); });
};

// Onboarding a public dataset: the four timed steps, and what was dropped or held back.
R.onboard = () => {
  const r = D.run, tot = r.total_seconds || 1;
  app.appendChild(tiles([
    {label: 'Time to onboard', value: r.total_seconds, fmt: 's', sub: 'sync, draft, check, load', tone: 'pass'},
    {label: 'Rows landed', value: r.rows_landed, fmt: 'int', sub: `${r.requests} requests, ${r.retries} retried`, tone: 'info'},
    {label: 'Rows loaded', value: r.rows_loaded, fmt: 'int', sub: `${r.rows_quarantined} quarantined`, tone: r.rows_quarantined ? 'warn' : 'pass'},
    {label: 'Personal columns', value: r.dropped_personal.length, fmt: 'int', sub: 'dropped before writing', tone: 'pass'}]));
  const t = document.createElement('div'); t.style.marginTop = '16px';
  t.innerHTML = `<div class="mono">Each step, timed</div><div style="display:flex;height:34px;border-radius:12px;overflow:hidden;margin-top:8px;background:#F2F3F6">
    ${Object.entries(r.seconds).map(([k, v], n) => `<div title="${k}: ${v.toFixed(2)} s" style="width:${Math.max(3, 100 * v / tot)}%;
      background:${['var(--navy)', 'var(--blue)', 'var(--gold)', 'var(--green)'][n % 4]};animation-delay:${n * 160}ms;color:#fff;
      font:500 12px Geist,sans-serif;display:flex;align-items:center;padding-left:8px;white-space:nowrap;overflow:hidden" class="grow seg">${k}</div>`).join('')}</div>
    <div class="row" style="gap:16px;margin-top:6px">${Object.entries(r.seconds).map(([k, v], n) => `<span class="cap" style="margin:0">
      <span style="display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px;background:${['var(--navy)', 'var(--blue)', 'var(--gold)', 'var(--green)'][n % 4]}"></span>${k} ${v.toFixed(2)} s</span>`).join('')}</div>
    <div style="margin-top:14px"><div class="mono">Columns that name a person, never written</div>${r.dropped_personal.map(c => `<span class="chip gone">${esc(c)}</span>`).join('') || '<span class="cap">none</span>'}</div>
    ${r.drift.length ? `<div style="margin-top:10px"><div class="mono">New columns, reported and held back</div>${r.drift.map(c => `<span class="chip new">${esc(c)}</span>`).join('')}</div>` : ''}`;
  app.appendChild(t);
  // A step too short to hold its own name shows only its colour; the legend below names it. Cut-off names ("draf",
  // "vali") read as typos.
  t.querySelectorAll('.seg').forEach(e => { if (e.scrollWidth > e.clientWidth) e.textContent = ''; });
};

// Every scheduled run against the live API: rows pulled, and the whole record on hover. Failed runs stay red.
R.history = () => {
  const runs = D.runs, S = D.summary;
  if (!runs.length) {
    app.innerHTML = `<div class="card fade"><div class="mono">Scheduled sync</div>
      <div style="font-size:22px;font-weight:600;margin:8px 0">The first scheduled run has not happened yet</div>
      <div class="cap" style="margin:0">Every few hours the job pulls what NYC changed since its last cursor and records the run here, failures included.</div></div>`;
    return;
  }
  const last = S.last, days = Math.max(1, Math.round((Date.parse(last.run_at) - Date.parse(S.first)) / 864e5) + 1);
  app.appendChild(tiles([
    {label: 'On schedule since', value: S.first.slice(0, 10), fmt: 'raw', sub: `${days} day${days > 1 ? 's' : ''}`, tone: 'info'},
    {label: 'Runs that succeeded', value: S.succeeded, fmt: 'int', sub: `of ${S.runs}`, tone: S.succeeded === S.runs ? 'pass' : 'warn'},
    {label: 'Rows pulled from NYC', value: S.rows, fmt: 'int', sub: `${F.int(S.retries)} requests retried`, tone: 'info'},
    last.source_newest
      ? {label: 'Behind the source', value: last.hours_behind_source ?? 0, fmt: 'raw', sub: "hours behind NYC's newest update, at the last run",
         tone: (last.hours_behind_source ?? 99) <= 26 ? 'pass' : 'fail'}
      : {label: 'Newest row landed', value: last.hours_behind_source ?? 0, fmt: 'raw', sub: 'hours old at the last run', tone: 'info'}]));
  if (last.reload_skipped) {
    const b = document.createElement('div'); b.className = 'card fade'; b.style.marginTop = '12px'; b.style.borderColor = 'var(--gold)';
    b.innerHTML = `<div class="mono">Publisher reload skipped</div><div style="margin-top:6px;font-size:14px">NYC stamped ${F.int(last.reload_skipped.rows)} rows
      with one update time (${esc(last.reload_skipped.timestamp)}): the table republished, not that many changes. The run counted it with one
      request, recorded it here and moved past it, so the real changes after it landed.</div>`;
    app.appendChild(b);
  }
  if (last.bulk_rewrite) {
    const b = document.createElement('div'); b.className = 'card fade'; b.style.marginTop = '12px'; b.style.borderColor = 'var(--gold)';
    b.innerHTML = `<div class="mono">Publisher reloaded the table</div><div style="margin-top:6px;font-size:14px">${F.int(last.bulk_rewrite.rows)} of ${F.int(last.bulk_rewrite.of)} rows
      carry one update time (${esc(last.bulk_rewrite.timestamp)}). Unchanged rows are stored once by their hash; the run is flagged, not hidden.</div>`;
    app.appendChild(b);
  }
  const W = Math.max(320, app.clientWidth || 800), H = 150, n = runs.length, bw = Math.max(4, Math.min(28, (W - 20) / n - 4));
  const mx = Math.max(1, ...runs.map(r => r.rows || 0));
  const box = document.createElement('div'); box.style.marginTop = '14px';
  let svg = `<div class="mono">Each run</div><svg width="${W}" height="${H + 22}" style="display:block;max-width:100%">`;
  runs.forEach((r, i) => { const h = r.ok ? Math.max(3, (H - 10) * Math.sqrt((r.rows || 0) / mx)) : H - 10, x = 10 + i * (bw + 4);
    svg += `<rect class="hr" data-i="${i}" x="${x}" y="${H - h}" width="${bw}" height="${h}" rx="3" fill="${r.ok ? (r.rows_quarantined ? 'var(--gold)' : 'var(--blue)') : 'var(--red)'}"
      style="cursor:pointer;transform-origin:${x}px ${H}px;animation:rise .5s ${i * 25}ms both"/>`; });
  svg += `<line x1="0" x2="${W}" y1="${H}" y2="${H}" stroke="#E6E6E6"/>
    <text x="10" y="${H + 16}" font-size="11" fill="#6B6B6B">${esc(runs[0].run_at.slice(0, 10))}</text>
    <text x="${W - 10}" y="${H + 16}" font-size="11" fill="#6B6B6B" text-anchor="end">${esc(last.run_at.slice(0, 10))}</text></svg>`;
  box.innerHTML = svg; app.appendChild(box);
  const cap = document.createElement('div'); cap.className = 'cap';
  const tell = r => r.ok ? `${r.run_at.slice(0, 16).replace('T', ' ')} UTC: ${F.int(r.rows)} new rows`
      + (r.reread ? ` (${F.int(r.reread)} read again and already held)` : '') + ` in ${r.pages} pages, ${r.retries} retried`
      + (Object.keys(r.retries_by_cause || {}).length ? ` (${Object.entries(r.retries_by_cause).map(([k, v]) => v + '×' + k).join(', ')})` : '')
      + (r.rows_loaded !== undefined ? `; ${F.int(r.rows_loaded)} loaded, ${F.int(r.rows_quarantined)} quarantined` : '') + `; ${r.seconds} s`
      + (r.drift && r.drift.length ? `; new columns held back: ${r.drift.join(', ')}` : '')
      + (r.capped ? '; stopped at its page budget, the next run continues from there' : '')
      + (r.reload_skipped ? `; skipped a reload of ${F.int(r.reload_skipped.rows)} rows` : '')
    : `${r.run_at.slice(0, 16).replace('T', ' ')} UTC: failed. ${r.error || ''}`;
  cap.textContent = tell(last); app.appendChild(cap);
  box.querySelectorAll('.hr').forEach(e => { e.onmouseenter = () => cap.textContent = tell(runs[+e.dataset.i]); e.onmouseleave = () => cap.textContent = tell(last); });
};

// The city's data, live: rows leave the sources, pass the contract gate (or drop into quarantine, or hit
// the wall of people who asked to be forgotten), and the records of one person snap together into one node,
// placed in the borough of their real NYC address. The animation plays a sample of real people; the
// counters are the whole city's totals.
R.city = () => {
  const T = D.totals, P = D.people, SRC = D.sources, HELD = D.held || [];
  const order = ['crm', 'vendor', 'store', 'email', 'privacy'].filter(k => SRC[k]);
  const SHORT = {crm: 'CRM', vendor: 'Vendor', store: 'Store', email: 'Email', privacy: 'Privacy'};
  const BORO = {MN: 'Manhattan', BX: 'Bronx', BK: 'Brooklyn', QN: 'Queens', SI: 'Staten Island'};
  // Rough shape of the five boroughs, in the people area's own 0..1 coordinates (north is up).
  const GEO = {BX: {x: .50, y: .13, rx: .15, ry: .10}, MN: {x: .33, y: .38, rx: .065, ry: .20}, QN: {x: .74, y: .42, rx: .19, ry: .20},
               BK: {x: .50, y: .74, rx: .16, ry: .15}, SI: {x: .13, y: .84, rx: .10, ry: .10}};
  const head = document.createElement('div'); head.className = 'grid'; head.style.gridTemplateColumns = 'repeat(auto-fit,minmax(118px,1fr))';
  const counters = [['Rows in', () => Object.values(T.rows_in).reduce((a, b) => a + b, 0), 'info'], ['Loaded', () => T.loaded, 'pass'],
    ['Already had', () => T.already, 'info'], ['Quarantined', () => T.quarantined, 'warn'], ['Blocked: forgotten', () => T.suppressed, 'fail'],
    ['People', () => T.people, 'pass']];
  const cEls = counters.map(([l, , tone]) => { const d = document.createElement('div'); d.className = 'tile tone-' + tone; d.style.padding = '12px 14px';
    d.innerHTML = `<span class="bar"></span><div class="mono">${l}</div><div class="v num" style="font-size:26px">0</div>`; head.appendChild(d); return d.querySelector('.v'); });
  const ctl = document.createElement('div'); ctl.className = 'row'; ctl.style.margin = '12px 0 6px';
  const btn = (t, cls) => { const b = document.createElement('button'); b.className = 'btn ' + (cls || ''); b.style.padding = '7px 16px'; b.textContent = t; return b; };
  const play = btn('Pause', 'blue'), again = btn('Replay'), bad = btn('Replay a real quarantined row');
  let speed = 1; const sp = seg(['1×', '3×', '10×'], '1×', v => speed = parseFloat(v));
  ctl.append(play, again, sp, bad);
  const note = document.createElement('div'); note.className = 'cap'; note.style.margin = '0 0 8px';
  note.textContent = `Animated: ${P.length} real people of ${F.int(T.people)}, each in the borough of their address. Counters: the whole city. Click a person or a source.`;
  const wrap = document.createElement('div'); wrap.style.cssText = 'display:grid;gap:12px;grid-template-columns:minmax(0,1fr)';
  const cv = document.createElement('canvas'); cv.style.cssText = 'width:100%;display:block;border-radius:18px;border:1px solid var(--line);cursor:pointer;background:linear-gradient(180deg,#FBFCFF 0%,#F3F5FC 100%)';
  const feed = document.createElement('div'); feed.className = 'card'; feed.style.cssText += ';padding:12px 14px;font-size:12.5px;min-height:150px;max-height:260px;overflow:hidden';
  feed.innerHTML = '<div class="mono">Live feed</div><div id="fd"></div>';
  wrap.append(cv, feed);
  const panel = document.createElement('div'); panel.className = 'cap'; panel.style.minHeight = '22px';
  app.append(head, ctl, note, wrap, panel);
  const fd = feed.querySelector('#fd'); const log = [];
  function say(html, tone) { log.unshift({html, tone, at: performance.now()}); if (log.length > 9) log.pop();
    fd.innerHTML = log.map((e, k) => `<div style="padding:4px 0;border-bottom:1px dashed #EEE;opacity:${1 - k * .09};${k === 0 ? 'animation:rise .35s both' : ''}">
      <span style="display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:7px;background:${e.tone}"></span>${e.html}</div>`).join(''); }
  const ctx = cv.getContext('2d'); let W = 0, H = 0, narrow = false, geo = {};
  let nodes = [], parts = [], t = 0, running = true, last = performance.now(), pick = null, hover = null, only = null, flash = null;
  const DUR = 26;
  const col = k => (SRC[k] || {color: '#9A9A9A'}).color;
  function mulberry(a) { return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
  function layout() {
    W = Math.max(320, cv.clientWidth); narrow = W < 620; H = narrow ? 560 : 500;
    const wide = !narrow && app.clientWidth > 1050;
    wrap.style.gridTemplateColumns = wide ? 'minmax(0,1fr) 280px' : 'minmax(0,1fr)';
    W = Math.max(320, cv.clientWidth);
    const dpr = window.devicePixelRatio || 1; cv.width = W * dpr; cv.height = H * dpr; cv.style.height = H + 'px'; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const dockW = narrow ? 58 : 112, gx = (narrow ? 12 : 22) + dockW + (narrow ? 20 : 40);
    const zx0 = gx + (narrow ? 16 : 36), zx1 = W - 14, zy0 = 40, zy1 = H - (narrow ? 96 : 84);
    const Z = (b, fx, fy) => [zx0 + (zx1 - zx0) * fx, zy0 + (zy1 - zy0) * fy];
    const srcY = {}; order.forEach((k, i) => srcY[k] = 52 + i * ((H - 150) / Math.max(1, order.length - 1)));
    geo = {gx, dockW, srcX: narrow ? 12 : 22, srcY, zy1, zx0, zx1, zy0, Z, binY: H - 46, binX: narrow ? 12 : 22};
    const rnd = mulberry(7), byB = {};
    P.forEach((p, k) => (byB[p.borough || 'none'] = byB[p.borough || 'none'] || []).push(k));
    nodes = P.map(p => ({p, x: 0, y: 0, got: [], r: 0}));
    Object.entries(byB).forEach(([b, ks]) => { const g = GEO[b] || {x: .93, y: .9, rx: .05, ry: .06};
      ks.forEach((k, j) => { const a = j * 2.39996, rr = Math.sqrt((j + .5) / ks.length) * .92;           // a sunflower: even, organic
        const [x, y] = Z(b, g.x + Math.cos(a) * g.rx * rr, g.y + Math.sin(a) * g.ry * rr); nodes[k].x = x + (rnd() - .5) * 3; nodes[k].y = y + (rnd() - .5) * 3; }); });
    build();
  }
  function build() {
    const rnd = mulberry(11); parts = [];
    nodes.forEach(n => n.p.records.forEach((r, j) => parts.push({kind: 'rec', src: r.source, node: n, rec: r, t0: rnd() * 0.72 + j * 0.01})));
    const q = Math.min(T.quarantined, 10), sup = Math.min(T.suppressed, 10), em = Math.min(60, Math.round(P.length * 0.6));
    for (let i = 0; i < q; i++) { const h = HELD[i % Math.max(1, HELD.length)] || {source: 'email', reason: 'held'}; parts.push({kind: 'q', src: h.source, held: h, t0: 0.1 + rnd() * 0.7}); }
    for (let i = 0; i < sup; i++) parts.push({kind: 'sup', src: order[Math.floor(rnd() * Math.min(3, order.length))], t0: 0.1 + rnd() * 0.7});
    if (SRC.privacy) for (let i = 0; i < Math.min(8, T.rows_in.privacy || 0); i++) parts.push({kind: 'sup', req: true, src: 'privacy', t0: 0.02 + rnd() * 0.6});
    for (let i = 0; i < em && SRC.email; i++) parts.push({kind: 'email', src: 'email', node: nodes[Math.floor(rnd() * nodes.length)], t0: 0.05 + rnd() * 0.8});
    parts.sort((a, b) => a.t0 - b.t0); nodes.forEach(n => { n.got = []; n.r = 0; });
  }
  const ease = x => x < .5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2;
  const dim = s => only && s !== only;
  function land(p) {
    if (p.kind === 'rec') { const first = !p.node.got.length; p.node.got.push(p.src);
      if (Math.random() < (speed > 3 ? .12 : .35)) say(`<b style="color:${col(p.src)}">${esc(SHORT[p.src] || p.src)}</b> record for ${esc(p.rec.name || 'someone')} ${first ? 'became a new person' : 'joined <b>' + esc(p.node.p.name) + '</b>'}${p.node.p.borough ? ' · ' + BORO[p.node.p.borough] : ''}`, col(p.src)); }
    else if (p.kind === 'email') p.node.pulse = 1;
    else if (p.kind === 'q') say(`Quarantined a row from <b>${esc(SHORT[p.src] || p.src)}</b>: ${esc(p.held.reason)}${p.held.row ? ' (row ' + p.held.row + ')' : ''}`, '#E5B74D');
    else if (p.req) say('Privacy request: that person is forgotten in every product', '#9A9A9A');
    else say(`Blocked a row from <b>${esc(SHORT[p.src] || p.src)}</b>: that person asked to be forgotten`, '#C2412D');
  }
  function draw() {
    ctx.clearRect(0, 0, W, H);
    // the boroughs
    Object.entries(GEO).forEach(([b, g]) => { const [x, y] = geo.Z(b, g.x, g.y), rx = (geo.zx1 - geo.zx0) * g.rx * 1.08, ry = (geo.zy1 - geo.zy0) * g.ry * 1.12;
      ctx.fillStyle = 'rgba(41,65,185,.045)'; ctx.strokeStyle = 'rgba(41,65,185,.14)'; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.ellipse(x, y, rx, ry, b === 'MN' ? -.35 : 0, 0, 7); ctx.fill(); ctx.stroke();
      ctx.fillStyle = 'rgba(17,25,111,.5)'; ctx.font = `500 ${narrow ? 9.5 : 10.5}px 'Geist Mono', monospace`; ctx.textAlign = 'center';
      const sh = D.borough_share && D.borough_share[b];
      const lt = (narrow ? b : BORO[b].toUpperCase()) + (sh && !narrow ? ` · ${(100 * sh).toFixed(0)}%` : '');
      if (b === 'MN') { ctx.textAlign = 'right'; ctx.fillText(lt, x - rx - 6, y); } else ctx.fillText(lt, x, y - ry - 5); ctx.textAlign = 'left'; });
    // sources
    ctx.font = `500 ${narrow ? 11 : 12.5}px Geist, sans-serif`; ctx.textBaseline = 'middle';
    order.forEach(k => { const y = geo.srcY[k]; ctx.globalAlpha = dim(k) ? .3 : 1; ctx.fillStyle = col(k); ctx.beginPath(); ctx.roundRect(geo.srcX, y - 15, geo.dockW, 30, 9); ctx.fill();
      if (only === k) { ctx.strokeStyle = '#050505'; ctx.lineWidth = 2; ctx.stroke(); }
      ctx.fillStyle = '#fff'; ctx.fillText(narrow ? SHORT[k] || k : SRC[k].label, geo.srcX + 8, y); ctx.globalAlpha = 1; });
    // the contract gate, the bins
    ctx.strokeStyle = 'rgba(41,65,185,.35)'; ctx.setLineDash([4, 6]); ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(geo.gx, 26); ctx.lineTo(geo.gx, geo.zy1 + 8); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = '#2941B9'; ctx.font = `500 10.5px 'Geist Mono', monospace`; ctx.fillText('CONTRACTS', geo.gx - 30, 16);
    const bw = narrow ? 70 : 104, wx = geo.binX + bw + 12;
    [[geo.binX, 'QUARANTINE', 'rgba(229,183,77,.16)', 'rgba(229,183,77,.7)', '#7A5A06'], [wx, 'FORGOTTEN', 'rgba(194,65,45,.12)', 'rgba(194,65,45,.6)', '#8E2A1B']]
      .forEach(([x, l, f, s, c]) => { ctx.fillStyle = f; ctx.strokeStyle = s; ctx.beginPath(); ctx.roundRect(x, geo.binY - 18, bw, 40, 10); ctx.fill(); ctx.stroke();
        ctx.fillStyle = c; ctx.font = `500 10px 'Geist Mono', monospace`; ctx.fillText(l, x + 8, geo.binY - 6); });
    if (!narrow) { ctx.fillStyle = '#6B6B6B'; ctx.font = `400 11.5px Geist, sans-serif`; ctx.textAlign = 'right';
      ctx.fillText('ring: each record, colored by its source  ·  red center: a stranger\'s record was joined (answer key)', W - 16, H - 14); ctx.textAlign = 'left'; }
    // where the selected person's records came from
    const focus = pick || hover;
    if (focus && focus.got.length) focus.p.records.forEach((r, j) => { const sy = geo.srcY[r.source]; if (sy === undefined) return;
      ctx.strokeStyle = col(r.source); ctx.globalAlpha = .75; ctx.lineWidth = 1.6; ctx.setLineDash([2, 4]);
      ctx.beginPath(); ctx.moveTo(geo.srcX + geo.dockW, sy); ctx.bezierCurveTo(geo.gx + 40, sy, focus.x - 60, focus.y + j * 4, focus.x, focus.y); ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1; });
    // people
    nodes.forEach(n => { const isDim = only && !n.got.includes(only);
      if (!n.got.length) { ctx.fillStyle = 'rgba(17,25,111,.08)'; ctx.beginPath(); ctx.arc(n.x, n.y, 2, 0, 7); ctx.fill(); return; }
      const R = 3.5 + 1.8 * Math.sqrt(n.got.length) * (narrow ? .8 : 1.1); n.r += (R - n.r) * .15;
      ctx.globalAlpha = isDim ? .18 : 1;
      const per = (Math.PI * 2) / n.got.length; n.got.forEach((s, j) => { ctx.strokeStyle = col(s); ctx.lineWidth = narrow ? 2.5 : 3.2;
        ctx.beginPath(); ctx.arc(n.x, n.y, n.r, j * per - Math.PI / 2, (j + 1) * per - Math.PI / 2 - .12); ctx.stroke(); });
      ctx.fillStyle = n.p.mixed && n.got.length === n.p.records.length ? 'rgba(194,65,45,.85)' : (n === pick ? '#2941B9' : '#fff');
      ctx.beginPath(); ctx.arc(n.x, n.y, Math.max(1.5, n.r - 2.6), 0, 7); ctx.fill(); ctx.globalAlpha = 1;
      if (n === focus) { ctx.strokeStyle = '#050505'; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.arc(n.x, n.y, n.r + 4, 0, 7); ctx.stroke(); } });
    // The name goes on a white pill above everything, flipped left near the edge, so other people never cover it.
    if (focus && focus.got.length) { ctx.font = `600 12.5px Geist, sans-serif`; ctx.textBaseline = 'middle';
      const label = focus.p.name + (focus.p.borough ? ' · ' + BORO[focus.p.borough] : ''), tw = ctx.measureText(label).width, pw = tw + 18, ph = 24;
      let lx = focus.x + focus.r + 10, ly = focus.y - focus.r - ph - 4;
      if (lx + pw > W - 6) lx = focus.x - focus.r - 10 - pw;
      if (ly < 4) ly = focus.y + focus.r + 6;
      ctx.shadowColor = 'rgba(17,25,111,.18)'; ctx.shadowBlur = 10; ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.roundRect(lx, ly, pw, ph, 12); ctx.fill();
      ctx.shadowBlur = 0; ctx.strokeStyle = '#050505'; ctx.lineWidth = 1; ctx.stroke();
      ctx.fillStyle = '#050505'; ctx.fillText(label, lx + 9, ly + ph / 2 + .5); }
    // moving rows
    parts.forEach(p => { const age = t - p.t0; if (age < 0) return;
      const sx = geo.srcX + geo.dockW, sy = geo.srcY[p.src] ?? geo.srcY[order[0]];
      ctx.globalAlpha = dim(p.src) ? .12 : 1;
      if (p.kind === 'rec' || p.kind === 'email') {
        const a1 = .07, a2 = .11, tx = p.node.x, ty = p.node.y;
        let x, y; if (age < a1) { const e = ease(age / a1); x = sx + (geo.gx - sx) * e; y = sy; }
        else if (age < a1 + a2) { const e = ease((age - a1) / a2); x = geo.gx + (tx - geo.gx) * e; y = sy + (ty - sy) * e - Math.sin(e * Math.PI) * 22; }
        else { if (!p.done) { p.done = true; land(p); } ctx.globalAlpha = 1; return; }
        ctx.fillStyle = col(p.src); ctx.globalAlpha *= p.kind === 'email' ? .5 : .95; ctx.beginPath(); ctx.arc(x, y, p.kind === 'email' ? 2 : 3.2, 0, 7); ctx.fill();
      } else {
        const a1 = .07, a2 = .09, tx = p.kind === 'q' ? geo.binX + 18 + (p.t0 * 997 % (bw - 30)) : wx + 18 + (p.t0 * 991 % (bw - 30)), ty = geo.binY + 4;
        let x, y, alpha = 1; if (age < a1) { const e = ease(age / a1); x = sx + (geo.gx - 6 - sx) * e; y = sy; }
        else if (age < a1 + a2) { const e = ease((age - a1) / a2); x = geo.gx - 6 + (tx - geo.gx + 6) * e; y = sy + (ty - sy) * e * e; }
        else { x = tx; y = ty; alpha = p.kind === 'sup' ? Math.max(.25, 1 - (age - a1 - a2) * 4) : 1; if (!p.done) { p.done = true; land(p); } }
        ctx.globalAlpha *= alpha; ctx.fillStyle = p.kind === 'q' ? '#E5B74D' : p.req ? '#9A9A9A' : '#C2412D'; ctx.beginPath(); ctx.arc(x, y, p.big ? 5 : 3.4, 0, 7); ctx.fill();
        if (age > a1 - .004 && age < a1 + .012) { ctx.strokeStyle = p.kind === 'q' ? '#E5B74D' : '#C2412D'; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(geo.gx, sy, 10, 0, 7); ctx.stroke(); }
        if (p.big && age < a1 + a2 + .12) { ctx.globalAlpha = 1; ctx.fillStyle = '#7A5A06'; ctx.font = `600 12px Geist, sans-serif`;
          ctx.fillText(`${p.held.reason}`, Math.min(x + 10, W - 170), y - 10); }
      }
      ctx.globalAlpha = 1; });
    nodes.forEach(n => { if (n.pulse > 0) { ctx.strokeStyle = `rgba(124,143,224,${n.pulse})`; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(n.x, n.y, n.r + 6 * (1 - n.pulse) + 2, 0, 7); ctx.stroke(); n.pulse -= .04; } });
    const prog = Math.min(1, t / 0.92);
    counters.forEach(([, f], i) => cEls[i].textContent = F.int(f() * prog));
  }
  function loop(now) { const dt = Math.min(.05, (now - last) / 1000); last = now;
    if (running && !reduce) t = Math.min(1.4, t + dt * speed / DUR); draw(); requestAnimationFrame(loop); }
  const xy = e => { const b = cv.getBoundingClientRect(); return [e.clientX - b.left, e.clientY - b.top]; };
  function at(x, y) { let best = null, d = 1e9;
    nodes.forEach(n => { if (!n.got.length) return; const dd = Math.hypot(n.x - x, n.y - y); if (dd < d && dd < n.r + 8) { d = dd; best = n; } }); return best; }
  const dockAt = (x, y) => order.find(k => x >= geo.srcX && x <= geo.srcX + geo.dockW && Math.abs(y - geo.srcY[k]) <= 16);
  function show(n) { if (!n) { panel.textContent = only ? `Showing ${SRC[only].label} only. Click it again to show every source.` : 'Click a person to see the records they were built from, or a source to isolate it.'; return; }
    panel.innerHTML = `<b>${esc(n.p.name)}</b>${n.p.borough ? ' · ' + BORO[n.p.borough] : ''} · ${n.p.records.length} records${n.p.mixed ? ' · <span class="differ">the answer key says one of these belongs to someone else: a matcher mistake, shown on purpose</span>' : ''}<br>` +
      n.p.records.map(r => `<span class="chip" style="border-color:${col(r.source)}"><b style="color:${col(r.source)}">${esc(SRC[r.source] ? SRC[r.source].label : r.source)}</b> ${esc(r.name)} · ${esc(r.email || 'no email')} · ${esc(r.address)}</span>`).join(''); }
  cv.onmousemove = e => { const [x, y] = xy(e); hover = at(x, y); cv.style.cursor = hover || dockAt(x, y) ? 'pointer' : 'default'; };
  cv.onmouseleave = () => hover = null;
  cv.onclick = e => { const [x, y] = xy(e), k = dockAt(x, y);
    if (k) { only = only === k ? null : k; pick = null; show(null); return; }
    pick = at(x, y); show(pick); };
  play.onclick = () => { running = !running; play.textContent = running ? 'Pause' : 'Play'; };
  again.onclick = () => { t = 0; build(); pick = null; log.length = 0; fd.innerHTML = ''; show(null); running = true; play.textContent = 'Pause'; };
  let hi = 0; bad.onclick = () => { if (!HELD.length) { say('Nothing was quarantined in this city.', '#9A9A9A'); return; }
    const h = HELD[hi++ % HELD.length]; parts.push({kind: 'q', src: h.source, held: h, t0: t, big: true}); running = true; play.textContent = 'Pause';
    if (t > 1.2) t = 1.0; };
  layout(); show(null); if (reduce) t = 1.4;
  let lw = cv.clientWidth; new ResizeObserver(() => { if (Math.abs(cv.clientWidth - lw) > 20) { lw = cv.clientWidth; const keep = t; layout(); t = keep;
    parts.forEach(p => { if (t - p.t0 > .18 && p.kind === 'rec') { p.done = true; p.node.got.push(p.src); } }); } }).observe(cv);
  requestAnimationFrame(loop);
};

// Where to cut the matcher's score: drag the line; the answer key scores every pair either side of it.
R.threshold = () => {
  const E = D.edges, S = D.same, Df = D.diff, n = S.length, Rl = D.rules;
  const toP = w => 1 / (1 + Math.pow(2, -w));
  const k0 = Math.max(0, E.findIndex(e => Math.abs(e - D.prod) < 1e-9));
  const top = document.createElement('div'); app.appendChild(top);
  const W = Math.max(320, app.clientWidth || 800), H = 230, padL = 8, bw = (W - padL * 2) / n;
  const mx = Math.max(...S, ...Df, 1), sc = v => (H / 2 - 18) * Math.sqrt(v / mx);
  const box = document.createElement('div'); box.style.marginTop = '12px'; app.appendChild(box);
  const inp = document.createElement('input'); inp.type = 'range'; inp.min = 0; inp.max = n; inp.value = k0; app.appendChild(inp);
  const cap = document.createElement('div'); cap.className = 'cap'; app.appendChild(cap);
  const rules = document.createElement('div'); rules.className = 'decide'; app.appendChild(rules);
  rules.innerHTML = `<div class="card fail fade"><div class="mono">The score alone, at ${D.prod_p}</div><div class="w num">${F.int(Rl.score_says_join_two_people)}</div>
      <div class="t">pairs of two different people it would join: mostly family at one address, same surname, often one inbox</div></div>
    <div class="card pass fade"><div class="mono">After the safety rules</div><div class="w num">${F.int(Rl.joined_two_people)}</div>
      <div class="t">pairs of two people production actually joined; the rest were refused (for example, two dates of birth disagree)</div></div>
    <div class="card info fade"><div class="mono">The cost of caution</div><div class="w num">${F.int(Rl.same_person_kept_apart)}</div>
      <div class="t">pairs of one person kept apart, of ${F.int(Rl.same_person_pairs)}</div></div>`;
  let prev = null;
  function draw(k) {
    const tp = S.slice(k).reduce((a, b) => a + b, 0), fp = Df.slice(k).reduce((a, b) => a + b, 0), fn = S.slice(0, k).reduce((a, b) => a + b, 0);
    const prec = tp + fp ? tp / (tp + fp) : 1, rec = tp + fn ? tp / (tp + fn) : 0, f1 = prec + rec ? 2 * prec * rec / (prec + rec) : 0;
    const cut = k < n ? E[k] : E[n];
    top.innerHTML = ''; top.appendChild(tiles([
      {label: 'Cut at', value: k === k0 ? D.prod_p : toP(cut), fmt: 'pct1', sub: k === k0 ? 'production’s threshold' : 'match probability', tone: 'info'},
      {label: 'Precision', value: prec, fmt: 'f3', sub: `${F.int(fp)} wrong joins`, tone: prec > .9 ? 'pass' : 'fail'},
      {label: 'Recall', value: rec, fmt: 'f3', sub: `${F.int(fn)} missed`, tone: rec > .9 ? 'pass' : 'warn'},
      {label: 'F1, pairs, score alone', value: f1, fmt: 'f3', sub: 'before the safety rules', tone: 'info'}]));
    let g = `<svg width="${W}" height="${H + 20}" style="display:block;max-width:100%">`;
    for (let i = 0; i < n; i++) { const x = padL + i * bw, on = i >= k;
      g += `<rect class="hb" data-i="${i}" x="${x}" y="${H / 2 - sc(S[i])}" width="${Math.max(1, bw - 1)}" height="${sc(S[i])}" fill="var(--green)" opacity="${on ? .9 : .25}"/>`;
      g += `<rect class="hb" data-i="${i}" x="${x}" y="${H / 2}" width="${Math.max(1, bw - 1)}" height="${sc(Df[i])}" fill="var(--red)" opacity="${on ? .9 : .25}"/>`; }
    const lx = padL + k * bw, px = padL + k0 * bw;
    g += `<line x1="0" x2="${W}" y1="${H / 2}" y2="${H / 2}" stroke="#DDD"/>
      <line x1="${px}" x2="${px}" y1="4" y2="${H - 4}" stroke="#6B6B6B" stroke-dasharray="3 4"/>
      <line x1="${lx}" x2="${lx}" y1="0" y2="${H}" stroke="var(--blue)" stroke-width="3"/>
      <text x="${padL}" y="14" font-size="11.5" fill="var(--green)">one person (answer key) ↑</text>
      <text x="${padL}" y="${H - 4}" font-size="11.5" fill="var(--red)">two people ↓</text>
      <text x="${W - 8}" y="14" font-size="11.5" fill="#6B6B6B" text-anchor="end">joined →</text>
      <text x="${padL}" y="${H + 16}" font-size="11" fill="#6B6B6B">${(100 * toP(E[0])).toFixed(1)}%</text>
      <text x="${px}" y="${H + 16}" font-size="11" fill="#6B6B6B" text-anchor="middle">${(100 * D.prod_p).toFixed(0)}%</text>
      <text x="${W - 8}" y="${H + 16}" font-size="11" fill="#6B6B6B" text-anchor="end">${(100 * toP(E[n])).toFixed(4)}%</text></svg>`;
    box.innerHTML = g;
    box.querySelectorAll('.hb').forEach(e => { e.onmouseenter = () => { const i = +e.dataset.i;
      cap.textContent = `Score ${(100 * toP(E[i])).toFixed(1)}% to ${(100 * toP(E[i + 1])).toFixed(1)}%: ${F.int(S[i])} pairs of one person, ${F.int(Df[i])} of two people.`; };
      e.onmouseleave = () => cap.textContent = D.hint; });
    cap.textContent = D.hint;
  }
  inp.oninput = () => draw(+inp.value); draw(k0);
};

// Who each rule removes: every step is the one before minus the people it names.
R.funnel = () => {
  const st = D.steps, mx = Math.max(1, st[0].value);
  const w = document.createElement('div'); app.appendChild(w);
  const cap = document.createElement('div'); cap.className = 'cap'; cap.textContent = 'Hover a step.';
  st.forEach((x, i) => { const r = document.createElement('div'); r.className = 'b fade'; r.style.animationDelay = (i * 90) + 'ms';
    r.style.cssText += ';display:grid;grid-template-columns:1fr 70px;column-gap:12px;row-gap:4px;align-items:center;padding:6px 0;cursor:default';
    const c = {info: 'var(--blue)', fail: 'var(--red)', pass: 'var(--green)'}[x.tone] || 'var(--blue)';
    r.innerHTML = `<div style="font-size:14px;grid-column:1/3">${esc(x.label)}${x.note ? ` <span style="color:var(--muted);font-size:12px">· ${esc(x.note)}</span>` : ''}</div>
      <div style="background:#F2F3F6;border-radius:8px;height:22px;overflow:hidden"><div class="grow" style="height:22px;border-radius:8px;background:${c};opacity:${x.tone === 'fail' ? .75 : .95};
      width:${Math.max(1.5, 100 * x.value / mx)}%;animation-delay:${i * 90}ms"></div></div><div class="num v" style="text-align:right;font-weight:600"></div>`;
    w.appendChild(r); count(r.querySelector('.v'), x.value, 'int');
    r.onmouseenter = () => cap.textContent = `${x.label}: ${F.int(x.value)} people${x.note ? ' · ' + x.note : ''}`; r.onmouseleave = () => cap.textContent = 'Hover a step.'; });
  app.appendChild(cap);
};

// One person's score, step by step: the average person, then each input's push, ending at the model's own score.
R.waterfall = () => {
  const P = D.walk, base = D.base_rate; let i = 0, playing = null;
  const top = document.createElement('div'); top.className = 'row';
  const prev = document.createElement('button'); prev.className = 'btn'; prev.textContent = '←'; prev.style.padding = '7px 14px';
  const next = document.createElement('button'); next.className = 'btn'; next.textContent = '→'; next.style.padding = '7px 14px';
  const play = document.createElement('button'); play.className = 'btn blue'; play.textContent = 'Play'; play.style.padding = '7px 16px';
  const who = document.createElement('div'); who.style.flex = '1'; who.style.minWidth = '200px';
  top.append(prev, next, play, who); app.appendChild(top);
  const dots = document.createElement('div'); dots.className = 'dots'; dots.style.margin = '10px 0'; app.appendChild(dots);
  P.forEach((p, k) => { const d = document.createElement('i'); d.title = `${(100 * p.score).toFixed(1)}%`; d.style.cursor = 'pointer'; d.onclick = () => go(k); dots.appendChild(d); });
  const box = document.createElement('div'); app.appendChild(box);
  const cap = document.createElement('div'); cap.className = 'cap'; app.appendChild(cap);
  const sig = z => 1 / (1 + Math.exp(-z));
  function go(k) { i = (k + P.length) % P.length; draw(); }
  function draw(n) {
    const p = P[i], steps = p.steps, shown = n === undefined ? steps.length : n;
    dots.querySelectorAll('i').forEach((d, k) => d.className = k === i ? 'on' : (P[k].bought ? 'r' : ''));
    who.innerHTML = `<div class="mono">${p.top_decile ? 'Top decile' : 'Everyone else'} · ${esc(p.person_key.slice(-6))}</div>
      <div style="font-size:26px;font-weight:600" class="num">${(100 * p.score).toFixed(2)}% <span style="font-size:14px;font-weight:400;color:var(--muted)">
      home-buyer score · base rate ${(100 * base).toFixed(2)}% · ${p.bought ? '<span class="agree">bought within 180 days</span>' : 'did not buy'} (answer key)</span></div>`;
    let z = p.start; const pts = [z]; steps.forEach(([, v]) => { z += v; pts.push(z); });
    const lo = Math.min(...pts) - .2, hi = Math.max(...pts) + .2, W = Math.max(300, app.clientWidth || 800), narrow = W < 560, lab = narrow ? 128 : 210;
    const X = v => lab + (W - lab - (narrow ? 112 : 150)) * (v - lo) / (hi - lo), rowH = 26, cut = narrow ? 19 : 30;
    let g = `<svg width="${W}" height="${(steps.length + 2) * rowH + 10}" style="display:block;max-width:100%">`;
    const row = (y, name, a, b, color, text, k) => `<g class="wf" data-k="${k}" style="cursor:default">
      <text x="${lab - 8}" y="${y + 17}" text-anchor="end" font-size="${narrow ? 11.5 : 12.5}" fill="#050505"><title>${esc(name)}</title>${esc(name.length > cut ? name.slice(0, cut - 1) + '…' : name)}</text>
      <rect x="${Math.min(X(a), X(b))}" y="${y + 5}" width="${Math.max(2, Math.abs(X(b) - X(a)))}" height="${rowH - 10}" rx="4" fill="${color}" class="grow" style="animation-delay:${k * 70}ms"/>
      <text x="${Math.max(X(a), X(b)) + 6}" y="${y + 17}" font-size="11.5" fill="#6B6B6B" class="num">${text}</text></g>`;
    g += row(0, 'an average person', lo, p.start, '#C9CFE8', `${(100 * sig(p.start)).toFixed(2)}%`, 0);
    let c = p.start;
    steps.slice(0, shown).forEach(([name, v], k) => { const a = c; c += v; g += row((k + 1) * rowH, name, a, c, v > 0 ? 'var(--blue)' : 'var(--gold)',
      narrow ? `${(100 * sig(c)).toFixed(2)}%` : `${v > 0 ? '+' : ''}${v.toFixed(3)} → ${(100 * sig(c)).toFixed(2)}%`, k + 1); });
    if (shown === steps.length) g += row((steps.length + 1) * rowH, 'the model’s score', lo, c, 'var(--navy)', `${(100 * sig(c)).toFixed(2)}%`, steps.length + 1);
    box.innerHTML = g + '</svg>';
    cap.textContent = D.hint;
  }
  prev.onclick = () => go(i - 1); next.onclick = () => go(i + 1);
  play.onclick = () => { if (playing) { clearInterval(playing); playing = null; play.textContent = 'Play'; return; }
    let n = 0; play.textContent = 'Stop'; draw(0);
    playing = setInterval(() => { n++; draw(n); if (n >= P[i].steps.length) { clearInterval(playing); playing = null; play.textContent = 'Play'; } }, 420); };
  draw();
};

// This visitor's actions in time: hover a dot for what it did and found.
R.timeline = () => {
  const E = D.events; if (!E.length) { app.innerHTML = '<div class="cap">Nothing yet.</div>'; return; }
  const kinds = [...new Set(E.map(e => e.kind))], pal = ['var(--blue)', 'var(--green)', 'var(--gold)', 'var(--red)', 'var(--navy)', '#7C8FE0', '#9A9A9A'];
  const W = Math.max(320, app.clientWidth || 800), lab = W < 560 ? 90 : 150, rowH = 30, H = kinds.length * rowH + 30;
  const t = E.map(e => Date.parse(e.at.replace(' ', 'T') + 'Z')), t0 = Math.min(...t), t1 = Math.max(...t, t0 + 60000);
  const X = v => lab + (W - lab - 20) * (v - t0) / (t1 - t0);
  let g = `<svg width="${W}" height="${H}" style="display:block;max-width:100%">`;
  kinds.forEach((k, r) => { g += `<text x="${lab - 10}" y="${r * rowH + 20}" text-anchor="end" font-size="12.5">${esc(k)}</text>
    <line x1="${lab}" x2="${W - 10}" y1="${r * rowH + 16}" y2="${r * rowH + 16}" stroke="#EEE"/>`; });
  E.forEach((e, i) => { const r = kinds.indexOf(e.kind);
    g += `<circle class="ev" data-i="${i}" cx="${X(t[i])}" cy="${r * rowH + 16}" r="7" fill="${pal[r % pal.length]}" style="cursor:pointer;animation:rise .4s ${i * 30}ms both"/>`; });
  g += `<text x="${lab}" y="${H - 4}" font-size="11" fill="#6B6B6B">${esc(E[0].at)} UTC</text>
    <text x="${W - 10}" y="${H - 4}" font-size="11" fill="#6B6B6B" text-anchor="end">${esc(E[E.length - 1].at)} UTC</text></svg>`;
  const box = document.createElement('div'); box.innerHTML = g; app.appendChild(box);
  const cap = document.createElement('div'); cap.className = 'card'; cap.style.marginTop = '8px'; cap.style.fontSize = '13.5px'; app.appendChild(cap);
  const tell = e => `<div class="mono">${esc(e.at)} UTC</div><div style="font-weight:600;margin:4px 0">${esc(e.action)}</div><div style="color:var(--muted);overflow-wrap:anywhere">${esc(e.result)}</div>`;
  cap.innerHTML = tell(E[E.length - 1]);
  box.querySelectorAll('.ev').forEach(c => { c.onmouseenter = () => cap.innerHTML = tell(E[+c.dataset.i]); c.onclick = c.onmouseenter; });
};

// An incident, step by step: what the agent did, how long each step took, and the detail behind it.
R.trace = () => {
  const E = D.events, PH = ['inject', 'detect', 'inspect', 'propose', 'approve', 'fix', 'verify'];
  const tot = Math.max(.1, E.reduce((a, e) => a + e.seconds, 0));
  const w = document.createElement('div'); app.appendChild(w);
  E.forEach((e, i) => { const r = document.createElement('div'); r.className = 'fade'; r.style.animationDelay = (i * 120) + 'ms';
    r.style.cssText += ';display:grid;grid-template-columns:22px 1fr 60px;gap:10px;align-items:start;padding:7px 0;border-bottom:1px dashed #EEE';
    const c = /fail|refus|risk/i.test(e.label + e.detail) ? 'var(--red)' : /verif|pass|approv/i.test(e.phase + e.label) ? 'var(--green)' : 'var(--blue)';
    r.innerHTML = `<div style="width:14px;height:14px;border-radius:50%;background:${c};margin-top:3px;box-shadow:0 0 0 4px ${'#F3F5FC'}"></div>
      <div><div class="mono">${esc(e.phase)}</div><div style="font-size:14px;font-weight:500">${esc(e.label)}</div>
      ${e.detail ? `<div class="cap" style="margin:2px 0 0;overflow-wrap:anywhere">${esc(e.detail)}</div>` : ''}
      <div class="track" style="height:5px;margin-top:6px"><div class="fill grow" style="width:${Math.max(2, 100 * e.seconds / tot)}%;background:${c};animation-delay:${i * 120}ms"></div></div></div>
      <div class="num" style="text-align:right;font-size:13px;color:var(--muted)">${e.seconds.toFixed(1)} s</div>`;
    w.appendChild(r); });
};

// Open anyone: their records fly in from each source and land in the person (or persons) the pipeline
// built; the golden profile shows, field by field, which value won and why, against every value that lost.
R.people = () => {
  const P = D.people, KIND = {correct: ['Found despite typos', 'pass'], split: ['Split in two', 'fail'], merged: ['Merged with someone', 'fail']};
  const SC = {crm: '#2941B9', vendor: '#E5B74D', store: '#11196F', email: '#7C8FE0'};
  let kind = 'merged', q = '', cur = null;
  const top = document.createElement('div'); top.className = 'row';
  const kinds = seg(Object.keys(KIND).map(k => `${KIND[k][0]} (${P.filter(p => p.kind === k).length})`), null, v => { kind = Object.keys(KIND).find(k => v.startsWith(KIND[k][0])); list(); });
  kinds.querySelectorAll('button')[2].className = 'on';
  const search = document.createElement('input'); search.placeholder = 'Search a name'; search.style.cssText = 'flex:1;min-width:160px;border:1px solid var(--line);border-radius:999px;padding:8px 14px;font:14px Geist,sans-serif';
  search.oninput = () => { q = search.value.toLowerCase(); list(); };
  top.append(kinds, search); app.appendChild(top);
  const names = document.createElement('div'); names.style.cssText = 'margin:10px 0;display:flex;gap:6px;flex-wrap:wrap;max-height:84px;overflow:auto'; app.appendChild(names);
  const stage = document.createElement('div'); app.appendChild(stage);
  function list() {
    const pool = P.filter(p => (q ? p.name.toLowerCase().includes(q) : p.kind === kind));
    const dup = n => pool.filter(x => x.name === n).length > 1;      // two real people can share a name: that is a merge's whole story
    names.innerHTML = ''; pool.forEach(p => { const b = document.createElement('button'); b.className = 'chip'; b.style.cursor = 'pointer'; b._p = p;
      b.textContent = p.name + (dup(p.name) ? ` · ${p.id.slice(-4)}` : '') + (q ? ' · ' + KIND[p.kind][0].toLowerCase() : '');
      b.onclick = () => open(p); names.appendChild(b); });
    if (!pool.length) names.innerHTML = '<span class="cap">Nobody by that name here.</span>';
    if (!cur || !pool.includes(cur)) open(pool[0]);
  }
  function open(p) {
    if (!p) { stage.innerHTML = ''; return; } cur = p;
    names.querySelectorAll('button').forEach(b => { const on = b._p === p; b.style.background = on ? 'var(--blue)' : '#fff'; b.style.color = on ? '#fff' : 'inherit'; });
    const [kl, tone] = KIND[p.kind];
    const boxes = [...new Set([...p.keys, ...p.records.map(r => r.person_key).filter(Boolean)])];
    const verdict = p.kind === 'correct' ? `${p.records.length} records from ${new Set(p.records.map(r => r.source)).size} sources, joined into one person. Nobody else mixed in.`
      : p.kind === 'split' ? `Their records landed in ${boxes.length} persons: one real person counted ${boxes.length} times.`
      : `Someone else's record was joined to them: ${[...new Set(p.strangers.map(s => s.really === p.name ? `another person also named ${s.really}` : s.really))].join(', ')}.`;
    stage.innerHTML = `<div class="card fade" style="margin-top:4px"><div class="row" style="justify-content:space-between">
        <div><div class="mono">Answer key</div><div style="font-size:24px;font-weight:600">${esc(p.name)}</div></div>
        <span class="pill p-${tone}">${esc(kl)}</span></div><div class="cap" style="margin-top:4px">${esc(verdict)}</div>
      <div id="lanes" style="display:grid;gap:12px;margin-top:14px;grid-template-columns:repeat(auto-fit,minmax(230px,1fr))"></div></div>
      <div class="card fade" style="margin-top:12px"><div class="mono">The golden profile: which value won, and why</div><div id="gold"></div>
      <div class="cap" id="why" style="margin-top:8px">${esc(D.rule)} Click a field.</div></div>`;
    const lanes = stage.querySelector('#lanes');
    boxes.forEach((k, bi) => { const box = document.createElement('div'); box.style.cssText = `border:2px dashed ${boxes.length > 1 ? 'var(--red)' : 'var(--line)'};border-radius:16px;padding:10px;min-height:120px`;
      box.innerHTML = `<div class="mono">Pipeline person ${bi + 1} · ${esc(k.slice(-6))}</div>`;
      const mine = p.records.filter(r => r.person_key === k), strange = p.strangers.filter(s => s.person_key === k);
      mine.forEach((r, i) => { const c = document.createElement('div'); c.className = 'rec';
        c.style.cssText = `margin-top:8px;border-left:4px solid ${SC[r.source] || '#999'};animation:fly${i % 2} .6s ${bi * 200 + i * 160}ms both`;
        c.innerHTML = `<div class="mono" style="color:${SC[r.source]}">${esc(r.label)} · ${esc(r.id)}</div><div style="font-weight:600;font-size:14px">${esc(r.name)}</div>
          <div class="cap" style="margin:2px 0 0;overflow-wrap:anywhere">${esc(r.email || 'no email')}${r.phone ? ' · ' + esc(r.phone) : ''}<br>${esc(r.address)}</div>`; box.appendChild(c); });
      strange.forEach((s_, i) => { const c = document.createElement('div'); c.className = 'rec';
        c.style.cssText = `margin-top:8px;border:1px solid #F2C9C0;background:#FDF5F3;animation:fly1 .6s ${bi * 200 + (mine.length + i) * 160}ms both`;
        c.innerHTML = `<div class="mono" style="color:var(--red)">${esc(s_.source)} record of someone else</div><div style="font-weight:600;font-size:14px;color:var(--red)">${s_.really === p.name ? `another ${esc(s_.really)}: same name, a different person` : `really ${esc(s_.really)}`}</div>`; box.appendChild(c); });
      lanes.appendChild(box); });
    const gold = stage.querySelector('#gold'), why = stage.querySelector('#why'), byAttr = {};
    p.fields.filter(f => f.person_key === boxes[0]).forEach(f => (byAttr[f.attr] = byAttr[f.attr] || []).push(f));
    Object.entries(byAttr).forEach(([attr, vs], i) => { const w = vs.find(v => v.won) || vs[0], lose = vs.filter(v => v !== w);
      const r = document.createElement('div'); r.className = 'f fade'; r.style.cssText += `;cursor:pointer;animation-delay:${600 + i * 60}ms;align-items:center`;
      r.innerHTML = `<b style="min-width:110px">${esc(attr.replace(/_/g, ' '))}</b><span style="flex:1;text-align:left;font-weight:600">${esc(w.value)}
        <span class="chip" style="font-weight:400;border-color:${SC[w.source] || '#999'};color:${SC[w.source] || '#999'}">${esc(w.source)}</span>
        ${lose.map(l => `<span class="chip gone" style="font-weight:400">${esc(l.value)}</span>`).join('')}</span>`;
      r.onclick = () => { why.innerHTML = `<b>${esc(attr.replace(/_/g, ' '))}</b>: <b>${esc(w.value)}</b> won. Seen ${w.observations}× by ${w.sources} source${w.sources > 1 ? 's' : ''}, last on ${w.last}, most trusted source ${esc(w.source)}.` +
        (lose.length ? ' Lost: ' + lose.map(l => `${esc(l.value)} (seen ${l.observations}×, last ${l.last})`).join('; ') + '.' : ' No other value was ever seen.') +
        (attr === 'address' && lose.length ? ' Address rule: the newest address still being confirmed wins, so an old one a stale source keeps repeating does not.' : ''); };
      gold.appendChild(r); });
    if (!Object.keys(byAttr).length) gold.innerHTML = '<div class="cap">No golden profile for this person.</div>';
  }
  const st = document.createElement('style'); st.textContent = '@keyframes fly0{from{opacity:0;transform:translateX(-40px)}to{opacity:1;transform:none}}@keyframes fly1{from{opacity:0;transform:translateX(40px)}to{opacity:1;transform:none}}';
  document.head.appendChild(st); list();
};

// The agent, step by step, on real runs: who asked, the skill the router chose, the read-only tool or the
// documents it used, the answer, and for a change, the approval gate that stops it until a second person agrees.
R.agent = () => {
  const S = D.scenes; let timer = null;
  const chips = document.createElement('div'); chips.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap';
  const lane = document.createElement('div'); lane.style.cssText = 'margin-top:14px;display:flex;flex-wrap:wrap;row-gap:10px;align-items:center';
  const cards = document.createElement('div'); cards.style.marginTop = '10px';
  app.append(chips, lane, cards);
  S.forEach((sc, i) => { const b = document.createElement('button'); b.className = 'chip'; b.style.cursor = 'pointer';
    b.textContent = `${sc.steps[0].who}: ${sc.question}`; b.onclick = () => run(i); chips.appendChild(b); });
  function nodes(sc) {
    const out = [], a = sc.steps[0];
    out.push({t: 'Asked', v: `${a.who} (${a.roles.join(', ') || 'no role'})`, d: a.asked, c: 'var(--navy)'});
    out.push({t: 'Router chose', v: a.skill || 'none', d: 'Questions are matched to a skill; the skill decides which tools it may use.', c: 'var(--blue)'});
    if (a.tool) out.push({t: a.proposals.length ? 'Change tool' : 'Read-only tool', v: a.tool, d: a.proposals.length ? 'Writes nothing: it files a proposal.' : 'Reads the live warehouse; cannot change it.', c: a.proposals.length ? 'var(--gold)' : 'var(--blue)'});
    if (a.sources.length) out.push({t: 'Cited', v: a.sources.join(', '), d: 'The answer quotes this document.', c: 'var(--blue)'});
    out.push({t: a.refused ? 'Refused' : a.proposals.length ? 'Proposed' : 'Answered', v: a.refused ? 'not in the documents' : a.proposals.length ? a.proposals[0] : 'with evidence', d: a.text, c: a.refused ? 'var(--red)' : a.proposals.length ? 'var(--gold)' : 'var(--green)'});
    sc.steps.slice(1).forEach(s2 => out.push({t: s2.refused ? 'Approval refused' : 'Approved', v: s2.who, d: s2.text, c: s2.refused ? 'var(--red)' : 'var(--green)'}));
    return out;
  }
  function run(i) {
    clearInterval(timer); chips.querySelectorAll('button').forEach((b, k) => { b.style.background = k === i ? 'var(--blue)' : '#fff'; b.style.color = k === i ? '#fff' : 'inherit'; });
    const ns = nodes(S[i]); lane.innerHTML = ''; cards.innerHTML = '';
    ns.forEach((n, k) => { const el = document.createElement('div'); el.style.cssText = 'display:flex;align-items:center;flex:0 0 auto';
      el.innerHTML = `${k ? '<div class="ln" style="width:34px;height:3px;background:#E6E6E6;position:relative;overflow:hidden"><div style="position:absolute;inset:0;background:' + n.c + ';transform:scaleX(0);transform-origin:left;transition:transform .35s"></div></div>' : ''}
        <div class="nd" style="border:2px solid #E6E6E6;border-radius:14px;padding:8px 12px;min-width:120px;max-width:190px;opacity:.35;transition:all .3s;cursor:pointer">
        <div class="mono">${esc(n.t)}</div><div style="font-weight:600;font-size:13.5px;overflow-wrap:anywhere">${esc(n.v)}</div></div>`;
      el.querySelector('.nd').onclick = () => detail(n); lane.appendChild(el); });
    let k = 0; const step = () => { const el = lane.children[k]; if (!el) { clearInterval(timer); return; }
      const nd = el.querySelector('.nd'), ln = el.querySelector('.ln div'); if (ln) ln.style.transform = 'scaleX(1)';
      nd.style.opacity = 1; nd.style.borderColor = ns[k].c; nd.style.boxShadow = `0 6px 18px rgba(17,25,111,.10)`; detail(ns[k]); k++; };
    step(); timer = setInterval(step, reduce ? 0 : 750);
  }
  function detail(n) { cards.innerHTML = `<div class="card fade" style="border-left:5px solid ${n.c}"><div class="mono">${esc(n.t)}</div>
    <div style="font-size:14px;margin-top:6px;white-space:pre-wrap;overflow-wrap:anywhere">${esc(n.d)}</div></div>`; }
  run(0);
};

// Where every product comes from: click any table to light up everything upstream and downstream of it.
R.lineage = () => {
  // Reference tables sit with the loader's, seeds with staging: six columns leave room for whole names.
  const COL = {Reference: 'Loader', Seeds: 'Staging'};
  const N = D.nodes.map(n => ({...n, stage: COL[n.stage] || n.stage})), E = D.edges, ST = D.stages.filter(s => !COL[s]), by = Object.fromEntries(N.map(n => [n.id, n]));
  const up = {}, down = {}; E.forEach(([a, b]) => { (down[a] = down[a] || []).push(b); (up[b] = up[b] || []).push(a); });
  const walk = (s, g) => { const seen = new Set(), st = [s]; while (st.length) { const x = st.pop(); (g[x] || []).forEach(y => { if (!seen.has(y)) { seen.add(y); st.push(y); } }); } return seen; };
  const W = Math.max(300, app.clientWidth || 900), narrow = W < 700;
  const cols = ST.filter(s => N.some(n => n.stage === s));
  const groups = cols.map(s => N.filter(n => n.stage === s));
  const nw = narrow ? Math.min(150, (W - 20) / 2 - 8) : Math.min(150, (W - 20) / cols.length - 14), nh = 34, gap = 9;
  let pos = {}, H;
  if (!narrow) { const colW = (W - 10) / cols.length; H = Math.max(...groups.map(g => g.length)) * (nh + gap) + 40;
    groups.forEach((g, c) => g.forEach((n, i) => pos[n.id] = {x: 5 + c * colW + (colW - nw) / 2, y: 30 + i * (nh + gap) + (H - 40 - g.length * (nh + gap)) / 2}));
  } else { let y = 10; groups.forEach(g => { y += 22; g.forEach((n, i) => { pos[n.id] = {x: 10 + (i % 2) * (nw + 16), y: y + Math.floor(i / 2) * (nh + gap)}; });
      y += Math.ceil(g.length / 2) * (nh + gap) + 6; }); H = y + 10; }
  const box = document.createElement('div'); app.appendChild(box);
  const cap = document.createElement('div'); cap.className = 'card'; cap.style.marginTop = '10px'; app.appendChild(cap);
  const color = n => n.kind === 'delivery' ? (n.real ? 'var(--green)' : 'var(--navy)') : n.stage === 'Products' ? 'var(--blue)' : n.stage === 'Identity' ? '#7C8FE0' : n.kind === 'seed' ? 'var(--gold)' : '#9AA3C7';
  let sel = null;
  const fit = (t, px) => { const n = Math.max(6, Math.floor(px / 6.4)); return t.length > n ? t.slice(0, n - 1) + '…' : t; };
  function draw() {
    const ups = sel ? walk(sel, up) : null, downs = sel ? walk(sel, down) : null;
    const on = id => !sel || id === sel || ups.has(id) || downs.has(id);
    const lit = (a, b) => sel && ((b === sel && ups.has(a)) || (a === sel && downs.has(b)) || (ups.has(a) && ups.has(b)) || (downs.has(a) && downs.has(b)));
    let g = `<svg width="${W}" height="${H}" style="display:block;max-width:100%">`;
    if (!narrow) cols.forEach((s, c) => { const colW = (W - 10) / cols.length; g += `<text x="${5 + c * colW + colW / 2}" y="14" text-anchor="middle" font-size="10.5" font-family="Geist Mono" fill="#6B6B6B">${esc(s.toUpperCase())}</text>`; });
    else { let y = 10; groups.forEach((gr, c) => { g += `<text x="10" y="${y + 16}" font-size="10.5" font-family="Geist Mono" fill="#6B6B6B">${esc(cols[c].toUpperCase())}</text>`; y += 22 + Math.ceil(gr.length / 2) * (nh + gap) + 6; }); }
    E.forEach(([a, b]) => { const p = pos[a], q = pos[b]; if (!p || !q) return; const l = lit(a, b);
      const x1 = narrow ? p.x + nw / 2 : p.x + nw, y1 = narrow ? p.y + nh : p.y + nh / 2, x2 = narrow ? q.x + nw / 2 : q.x, y2 = narrow ? q.y : q.y + nh / 2;
      const d = narrow ? `M${x1},${y1} C${x1},${(y1 + y2) / 2} ${x2},${(y1 + y2) / 2} ${x2},${y2}` : `M${x1},${y1} C${(x1 + x2) / 2},${y1} ${(x1 + x2) / 2},${y2} ${x2},${y2}`;
      g += `<path d="${d}" fill="none" stroke="${l ? 'var(--blue)' : '#D7DBEA'}" stroke-width="${l ? 1.8 : 1}" opacity="${sel && !l ? .18 : l ? .85 : .9}"/>`; });
    N.forEach(n => { const p = pos[n.id], o = on(n.id) ? 1 : .2, c = color(n);
      g += `<g class="ln" data-id="${esc(n.id)}" style="cursor:pointer" opacity="${o}"><rect x="${p.x}" y="${p.y}" width="${nw}" height="${nh}" rx="9" fill="#fff" stroke="${n.id === sel ? '#050505' : c}" stroke-width="${n.id === sel ? 2.5 : 1.5}"/>
        <rect x="${p.x}" y="${p.y}" width="5" height="${nh}" rx="2" fill="${c}"/>
        <text x="${p.x + 11}" y="${p.y + 14}" font-size="11" font-weight="600"><title>${esc(n.id)}</title>${esc(fit(n.id, nw - 16))}</text>
        <text x="${p.x + 11}" y="${p.y + 27}" font-size="10" fill="#6B6B6B">${esc(fit((n.rows == null ? '' : F.int(n.rows) + ' rows') + (n.tests ? ` · ${n.tests.length} test${n.tests.length > 1 ? 's' : ''}` : ''), nw - 14))}</text></g>`; });
    box.innerHTML = g + '</svg>';
    box.querySelectorAll('.ln').forEach(e => e.onclick = () => { const id = e.dataset.id; sel = id === sel ? null : id; draw(); tell(); });
  }
  function tell() {
    if (!sel) { cap.innerHTML = `<div class="mono">Lineage</div><div style="font-size:14px;margin-top:4px">${esc(D.hint)}</div>`; return; }
    const n = by[sel], u = walk(sel, up), d = walk(sel, down), roots = [...u].filter(x => by[x].kind === 'delivery');
    cap.innerHTML = `<div class="mono">${esc(n.stage)} · ${esc(n.kind)}</div><div style="font-size:18px;font-weight:600;margin:4px 0">${esc(sel)}</div>
      ${n.description ? `<div style="font-size:14px">${esc(n.description)}</div>` : ''}
      <div class="cap" style="margin-top:6px">${n.rows == null ? '' : F.int(n.rows) + ' rows today · '}${n.written_by ? 'built by ' + esc(n.written_by) : 'arrives from outside'}</div>
      <div style="margin-top:6px;font-size:13.5px">Built from <b>${u.size}</b> upstream, feeds <b>${d.size}</b> downstream.${roots.length ? ' Comes from: ' + roots.map(r => `<span class="chip">${esc(r)}</span>`).join('') : ''}</div>
      ${n.tests ? `<div style="margin-top:6px">${n.tests.map(t => `<span class="chip new">${esc(t.length > 48 ? t.slice(0, 47) + '…' : t)}</span>`).join('')}</div>` : ''}`;
  }
  sel = D.start || null; draw(); tell();
};

// A live stream, second by second: what arrived, of which type, where the run hung up on purpose and resumed,
// and whether anything after the reconnect was a repeat. Click a run below to see its window; click a type to hide it.
R.stream = () => {
  const runs = D.runs || [], TY = ['edit', 'categorize', 'new', 'log', 'other'];
  const COL = {edit: 'var(--blue)', categorize: '#9AA8E8', new: 'var(--green)', log: 'var(--gold)', other: '#C9CCD6'};
  const all = runs.map(r => ({...r, label: r.run_at.slice(0, 16).replace('T', ' ') + ' UTC', real: true}));
  if (D.local) all.push({...D.local, label: 'Local test stream, your settings', real: false});
  if (!all.length) {
    app.innerHTML = `<div class="card fade"><div class="mono">Live stream</div>
      <div style="font-size:22px;font-weight:600;margin:8px 0">The first scheduled window has not run yet</div>
      <div class="cap" style="margin:0">Every few hours the job reads two minutes of Wikimedia's live edit stream, hangs up half-way on purpose and resumes, and records the run here, failures included. Try the connector below meanwhile.</div></div>`;
    return;
  }
  let cur = all.length - 1; const hidden = new Set();
  const top = document.createElement('div'), lane = document.createElement('div'), legend = document.createElement('div'),
        cap = document.createElement('div'), picks = document.createElement('div');
  legend.className = 'row'; legend.style.margin = '10px 0 4px'; cap.className = 'cap'; picks.style.marginTop = '12px';
  app.append(top, legend, lane, cap, picks);
  function draw() {
    const r = all[cur];
    if (!r.ok) { top.innerHTML = ''; top.appendChild(tiles([{label: 'This run', value: 'failed', fmt: 'raw', sub: (r.error || '').slice(0, 90), tone: 'fail'}]));
      lane.innerHTML = ''; legend.innerHTML = ''; cap.textContent = r.label; picker(); return; }
    const rc = r.resume_check, pub = r.published;
    top.innerHTML = `<div class="mono" style="margin-bottom:8px;color:${r.real ? 'var(--blue)' : 'var(--muted)'}">${esc(r.label)} · ${r.real ? 'live, from stream.wikimedia.org' : 'not live data: the local stand-in, misbehaving as you set it'}</div>`;
    top.appendChild(tiles([
      {label: 'Events in the window', value: r.events, fmt: 'int', sub: `${r.events_per_second} a second · ${r.wikis ?? '?'} wikis`, tone: 'info'},
      {label: 'Landed, each once', value: r.rows_loaded ?? r.rows, fmt: 'int', sub: `${F.int(r.duplicates)} repeats dropped · ${F.int(r.rows_quarantined || 0)} quarantined`, tone: (r.rows_quarantined || 0) ? 'warn' : 'pass'},
      {label: 'Lag, median', value: r.lag_p50 == null ? 'n/a' : r.lag_p50.toFixed(2) + ' s', fmt: 'raw',
       sub: r.lag_p50 == null ? 'measured on the live stream only: the test stream has no real clock' : `stream's event time to landed; 95th percentile ${r.lag_p95.toFixed(2)} s`, tone: 'info'},
      {label: 'Sent again after the hang-up', value: rc ? rc.repeated : 'n/a', fmt: rc ? 'int' : 'raw',
       sub: rc ? (rc.repeated ? `all dropped; ${F.int(rc.new)} new events after it` : `${F.int(rc.new)} new events after the reconnect, none seen before`)
         : 'the window ended first', tone: rc ? 'pass' : 'info'},
      {label: 'Cloud warehouse', value: pub ? pub.rows_after : r.publish_error ? 'failed' : 'off', fmt: pub ? 'int' : 'raw',
       sub: pub ? `rows in MotherDuck, +${F.int(pub.rows_new)} this run` : r.publish_error ? r.publish_error.split('\n')[0].slice(0, 110)
         : 'no cloud token set, so nothing is published or claimed', tone: pub ? 'pass' : r.publish_error ? 'fail' : 'info'}]));
    legend.innerHTML = '';
    TY.forEach(t => { const tot = (r.timeline || []).reduce((a, row) => a + row[1 + TY.indexOf(t)], 0); if (!tot) return;
      const b = document.createElement('button'); b.className = 'btn'; b.style.cssText = `padding:5px 12px;font-size:12.5px;opacity:${hidden.has(t) ? .35 : 1}`;
      b.innerHTML = `<span style="display:inline-block;width:9px;height:9px;border-radius:3px;background:${COL[t]};margin-right:6px"></span>${t} · ${F.int(tot)}`;
      b.onclick = () => { hidden.has(t) ? hidden.delete(t) : hidden.add(t); draw(); }; legend.appendChild(b); });
    const T = r.timeline || [], W = Math.max(300, app.clientWidth || 800), H = 170, n = Math.max(1, T.length), tick = r.tick || 1;
    const secs = v => (v * tick).toFixed(tick < 1 ? 1 : 0) + ' s';
    const bw = (W - 20) / n, mx = Math.max(1, ...T.map(row => TY.reduce((a, t, k) => a + (hidden.has(t) ? 0 : row[1 + k]), 0)));
    let g = `<svg width="${W}" height="${H + 24}" style="display:block;max-width:100%">`;
    T.forEach((row, i) => { let y = H; const x = 10 + i * bw;
      TY.forEach((t, k) => { if (hidden.has(t) || !row[1 + k]) return; const h = (H - 14) * row[1 + k] / mx; y -= h;
        g += `<rect x="${x}" y="${y}" width="${Math.max(1, bw - 1)}" height="${h}" fill="${COL[t]}"/>`; });
      g += `<rect class="sec" data-i="${i}" x="${x}" y="0" width="${bw}" height="${H}" fill="transparent"/>`; });
    if (r.disconnect_second != null) { const x = 10 + r.disconnect_second * bw + bw / 2;
      g += `<line x1="${x}" x2="${x}" y1="4" y2="${H}" stroke="#050505" stroke-dasharray="4 3"/>
        <text x="${Math.min(x + 6, W - 150)}" y="14" font-size="11" font-weight="600">hung up on purpose, resumed</text>`; }
    g += `<line x1="0" x2="${W}" y1="${H}" y2="${H}" stroke="#E6E6E6"/><text x="10" y="${H + 16}" font-size="11" fill="#6B6B6B">0 s</text>
      <text x="${W - 10}" y="${H + 16}" font-size="11" fill="#6B6B6B" text-anchor="end">${secs(n)}</text></svg>`;
    lane.innerHTML = g;
    const base = `${r.label}${r.real ? ' · live from stream.wikimedia.org' : ''}: ${F.int(r.events)} events, ${r.reconnects} reconnect${r.reconnects === 1 ? '' : 's'}`
      + (Object.keys(r.reconnects_by_cause || {}).length ? ` (${Object.entries(r.reconnects_by_cause).map(([k, v]) => v + '× ' + k).join(', ')})` : '')
      + (r.drift && r.drift.length ? `; new fields held back: ${r.drift.join(', ')}` : '') + `. Hover a second.`;
    cap.textContent = base;
    lane.querySelectorAll('.sec').forEach(e => { e.onmouseenter = () => { const row = T[+e.dataset.i];
        cap.textContent = `At ${secs(row[0])}: ` + TY.map((t, k) => row[1 + k] ? `${row[1 + k]} ${t}` : '').filter(Boolean).join(', ') + (TY.some((t, k) => row[1 + k]) ? '' : 'nothing landed'); };
      e.onmouseleave = () => cap.textContent = base; });
    picker();
  }
  function picker() {
    if (all.length < 2) { picks.innerHTML = ''; return; }
    const mx = Math.max(1, ...all.map(r => r.events || 0));
    picks.innerHTML = `<div class="mono">Each run</div><div class="row" style="align-items:flex-end;gap:4px;height:46px;margin-top:6px">` +
      all.map((r, i) => `<div class="pk" data-i="${i}" title="${esc(r.label)}" style="cursor:pointer;width:14px;border-radius:3px;height:${r.ok ? Math.max(4, 40 * (r.events || 0) / mx) : 40}px;
        background:${!r.ok ? 'var(--red)' : r.real ? 'var(--blue)' : '#C9CCD6'};outline:${i === cur ? '2px solid #050505' : 'none'};outline-offset:2px"></div>`).join('') + '</div>';
    picks.querySelectorAll('.pk').forEach(e => e.onclick = () => { cur = +e.dataset.i; draw(); });
  }
  draw();
};

// The add-a-source kit, replayed from real runs (app/kit_demo.py). Pick a scenario: pages fly from the API to
// the warehouse, the cursor updates as the kit saves it, a crash interrupts, and the result says what is held.
R.kit = () => {
  const SC = {'Run it': 'clean', 'Read it again': 'again', 'Crash after page 1': 'crash', 'The careless cursor': 'careless'};
  if (D.stale) SC['A copy that is behind'] = 'stale';
  const top = document.createElement('div'); top.className = 'row';
  const go = document.createElement('button'); go.className = 'btn blue'; go.textContent = '▶ Play';
  let pick = 'Run it';
  top.append(seg(Object.keys(SC), pick, o => { pick = o; play(); }), go);
  const stage = document.createElement('div'); stage.className = 'stage';
  stage.innerHTML = `<div class="box api"><div class="mono">The API</div><div class="v num">${F.int(D.events)}</div><div class="cap">events, ${D.page_size} a page</div></div>
    <div class="lane"></div><div class="box wh"><div class="mono">Warehouse</div><div class="v num held">0</div><div class="cap sub">rows held</div></div>`;
  const lane = stage.querySelector('.lane'), held = stage.querySelector('.held'), sub = stage.querySelector('.sub');
  const curs = document.createElement('div'); curs.className = 'curs'; curs.innerHTML = 'cursor <b>none yet</b>';
  const res = document.createElement('div');
  const lights = document.createElement('div'); lights.className = 'lights';
  const code = document.createElement('details'); code.style.marginTop = '14px';
  code.innerHTML = `<summary class="mono" style="cursor:pointer">The ${D.code_lines} lines an engineer writes</summary><pre class="code">${D.code.split('\n').map(l =>
    /cursor=newest if last else None/.test(l) ? `<span class="hl">${esc(l)}</span>` : esc(l)).join('\n')}</pre>`;
  app.append(top, stage, curs, res, lights, code);
  let token = 0, total = 0;
  const wait = ms => new Promise(r => setTimeout(r, reduce ? 0 : ms));
  function setHeld(v) { count(held, v, 'int', total); total = v; }
  async function fly(rows, cls, tag) {
    const c = document.createElement('div'); c.className = 'pg ' + (cls || ''); c.innerHTML = `${F.int(rows)}<small>${tag}</small>`;
    lane.appendChild(c); await wait(30);
    c.style.transition = reduce ? 'none' : 'left .55s cubic-bezier(.4,.1,.2,1)'; c.style.left = 'calc(100% + 10px)';
    await wait(560); c.remove();
  }
  async function play() {
    const my = ++token, key = SC[pick], s = key === 'again' ? D.clean : D[key];
    res.innerHTML = ''; lane.querySelectorAll('.boom').forEach(b => b.remove()); total = 0; held.textContent = '0';
    curs.innerHTML = 'cursor <b>none yet</b>'; go.disabled = true; sub.textContent = 'rows held';
    if (key === 'stale') { await stale(my); go.disabled = false; return; }
    const steps = key === 'again' ? s.steps : s.steps.filter(x => x.label !== 'again');
    for (const st of steps) {
      if (key === 'again' && st.label === 'run') { setHeld(st.loaded); curs.innerHTML = `cursor <b>${esc(st.cursor)}</b>`; await wait(300);
        const b = document.createElement('div'); b.className = 'boom'; b.style.color = 'var(--blue)'; b.style.background = 'rgba(243,245,252,.92)';
        b.textContent = 'cursor lost: read everything again'; lane.appendChild(b); await wait(1100); b.remove(); continue; }
      for (const [i, p] of st.pages.entries()) {
        if (my !== token) return;
        if (p.rows) await fly(p.rows, st.label === 'again' ? 'dup' : '', st.label === 'again' ? 'already held' : `page ${i + 1}`);
        if (p.cursor !== null && p.cursor !== undefined) { curs.innerHTML = `cursor saved <b>${esc(p.cursor)}</b>`; curs.classList.add('flash'); setTimeout(() => curs.classList.remove('flash'), 400); }
        else curs.innerHTML = `cursor <b>kept</b> <span style="color:var(--muted)">(window not finished)</span>`;
      }
      if (st.label === 'crash') { const b = document.createElement('div'); b.className = 'boom'; b.textContent = '💥 crash after page 1';
        lane.appendChild(b); await wait(1200); b.textContent = '↻ resume from the saved cursor'; await wait(900); b.remove(); }
      else setHeld(total + (st.loaded || 0));
    }
    if (my !== token) return;
    const lost = key === 'careless' ? D.lost : 0, h = key === 'careless' ? D.careless.held : D.clean.held;
    sub.textContent = key === 'again' ? 'rows held: 0 added' : 'rows held';
    res.className = 'result ' + (lost ? 'bad' : 'ok');
    res.innerHTML = lost ? `<b>${F.int(h)} of ${F.int(D.events)} held. ${F.int(lost)} lost for good</b>: they were on page 2 or later but
        updated before the cursor saved after page 1. ${D.lost_sample.map(x => `<span class="chip gone">${esc(x)}</span>`).join('')}`
      : key === 'again' ? `<b>${F.int(D.events)} rows read again, 0 added.</b> The loader keeps each row once.`
      : key === 'crash' ? `<b>${F.int(h)} of ${F.int(D.events)} held after the crash.</b> Page 1 was on disk before the cursor moved.`
      : `<b>${F.int(h)} of ${F.int(D.events)} held</b> in ${D.clean.ms} ms of real runs.`;
    go.disabled = false;
  }
  async function boom(text, ms, tone) {
    const b = document.createElement('div'); b.className = 'boom'; b.textContent = text;
    if (tone === 'blue') { b.style.color = 'var(--blue)'; b.style.background = 'rgba(243,245,252,.94)'; }
    lane.appendChild(b); await wait(ms); b.remove();
  }
  async function stale(my) {
    const S = D.stale;
    await fly(0, 'dup', 'stale copy');
    await boom('empty page: the run thinks it is done', 1500);
    if (my !== token) return;
    res.className = 'result bad'; res.innerHTML = `<b>Without <code>stale_when</code>: ${F.int(S.naive.held)} of ${F.int(D.events)} landed.</b> The API answered from a copy a day behind, said so in a header, and the run took the empty page as the end.`;
    await wait(1600); if (my !== token) return;
    await boom(`stale_when set: ${S.careful.stale_answers} stale answer asked again`, 1500, 'blue');
    for (const [i, p] of S.careful.pages.entries()) { if (my !== token) return;
      if (p.rows) { await fly(p.rows, '', `page ${i + 1}`); setHeld(total + p.rows); }
      if (p.cursor) curs.innerHTML = `cursor saved <b>${esc(p.cursor)}</b>`; }
    res.className = 'result ok'; res.innerHTML = `<b>With <code>stale_when</code>: ${F.int(S.careful.held)} of ${F.int(D.events)} held.</b> Found by the third engineer on NYC 311; the kit now asks again, like a 503.`;
  }
  const vcol = (title, rows) => `<div class="card"><div class="mono">${title}</div>` + rows.map(r =>
    `<div class="light"><i class="${r.ok === null ? 's' : r.ok ? 'p' : 'f'}"></i><div><b>${r.ok === null ? 'SKIP' : r.ok ? 'PASS' : 'FAIL'}</b> ${esc(r.check)}
     <div style="color:var(--muted);font-size:12px">${esc(r.detail)}</div></div></div>`).join('') + '</div>';
  lights.innerHTML = vcol('verify · the example source', D.verify.good) + vcol('verify · the careless cursor', D.verify.careless);
  go.onclick = play; play();
};

// The polite scraper, one real run on a simulated clock (app/kit_demo.py scrape): every request the site saw,
// every wait, and the request robots.txt stopped before it was sent. Play moves time along.
R.polite = () => {
  const T = Math.max(1, D.seconds), pos = t => `${5 + 80 * t / T}%`;   // room on the right for the last label
  const head = document.createElement('div'); head.className = 'row';
  const go = document.createElement('button'); go.className = 'btn blue'; go.textContent = '▶ Replay';
  head.innerHTML = `<span class="pill p-info">${esc(D.agent)}</span>`; head.prepend(go);
  const tl = document.createElement('div'); tl.className = 'tl';
  const col = q => q.path === '/robots.txt' ? 'var(--blue)' : q.status === 200 ? 'var(--green)' : q.status === 429 ? 'var(--gold)' : 'var(--red)';
  const stack = {};
  tl.innerHTML = D.waits.map(w => `<div class="w" title="waited ${w.secs} s" style="left:${pos(w.at)};width:calc(${80 * w.secs / T}% )"></div>`).join('') +
    D.requests.map(q => { const k = q.t.toFixed(1), n = stack[k] = (stack[k] || 0) + 1;
      return `<div class="q" data-t="${q.t}" style="left:${pos(q.t)};bottom:${(n - 1) * 46}px;opacity:.15" title="${esc(q.path)} → ${q.status}">
        <b style="background:${col(q)}"></b><span>${esc(q.path.replace(/^\/page\//, 'p').replace(/\/$/, ''))} · ${q.status}</span></div>`; }).join('') +
    D.blocked.map(b => `<div class="q" data-t="${b.t}" style="left:${pos(b.t)};bottom:92px;opacity:.15" title="robots.txt: not allowed, never requested">
        <b style="background:#fff;box-shadow:0 0 0 2px var(--red)"></b><span style="color:var(--red)">✕ ${esc(b.path)} not asked</span></div>`).join('') +
    `<div class="now" style="left:5%"></div>`;
  const now = tl.querySelector('.now');
  const axis = document.createElement('div'); axis.className = 'row'; axis.style.justifyContent = 'space-between'; axis.style.marginTop = '20px';
  axis.innerHTML = `<span class="mono">0 s</span><span class="mono">${D.seconds} s simulated</span>`;
  const t429 = D.requests.find(q => q.status === 429);
  const out = tiles([{label: 'Requests the site saw', value: D.requests.length, fmt: 'int', tone: 'info'},
    {label: 'Time spent waiting', value: D.waits.reduce((a, w) => a + w.secs, 0), fmt: 's', tone: 'warn', sub: t429 ? 'told to slow down once: slower after' : 'the crawl delay between requests'},
    {label: 'Not asked (robots.txt)', value: D.blocked.length, fmt: 'int', tone: D.blocked.length ? 'pass' : 'info', sub: D.outcome}]);
  out.style.marginTop = '14px';
  app.append(head, tl, axis, out);
  let tok = 0;
  async function replay() { const my = ++tok; tl.querySelectorAll('.q').forEach(e => e.style.opacity = .15);
    for (let t = 0; t <= T + .01; t += T / 60) { if (my !== tok) return; now.style.left = pos(t);
      tl.querySelectorAll('.q').forEach(e => { if (+e.dataset.t <= t + 1e-6) e.style.opacity = 1; });
      await new Promise(r => setTimeout(r, reduce ? 0 : 35)); } }
  go.onclick = replay; replay();
};

// Every run of each real feed on one time line: green ran fine, red failed, ringed started by hand. Shaded spans
// are the times a consumer was hurt until a person fixed it; click one for what was done and its postmortem.
R.incidents = () => {
  const ts = D.feeds.flatMap(f => f.runs.map(r => Date.parse(r.t)).concat(f.recoveries.flatMap(x => [Date.parse(x.start), Date.parse(x.end)])));
  const t0 = Math.min(...ts), t1 = Math.max(Date.now(), ...ts), x = t => `${100 * (Date.parse(t) - t0) / (t1 - t0)}%`;
  const card = document.createElement('div'); card.className = 'card'; card.style.marginTop = '12px';
  card.innerHTML = '<div class="cap">Click a shaded span: what broke, how long, what a person did.</div>';
  const sum = tiles(D.feeds.map(f => ({label: f.name, value: f.recoveries.length, fmt: 'int', tone: f.recoveries.length ? 'warn' : 'pass',
    sub: `times a person stepped in · ${f.runs.length} runs · ${f.retries} retries absorbed`})));
  const lanes = document.createElement('div'); lanes.className = 'lanes'; lanes.style.marginTop = '12px';
  D.feeds.forEach((f, fi) => { const ln = document.createElement('div'); ln.className = 'ln';
    ln.innerHTML = `<div><b style="font-size:14px">${esc(f.name)}</b><div class="mono">${f.runs.length} runs</div></div><div class="track2">` +
      f.recoveries.map((r, i) => `<div class="hurt" data-f="${fi}" data-i="${i}" style="left:${x(r.start)};width:max(16px,calc(${x(r.end)} - ${x(r.start)}))"></div>`).join('') +
      f.runs.map(r => `<div class="rd ${r.hand ? 'hand' : r.ok ? 'ok' : 'no'}" style="left:${x(r.t)}" title="${esc(r.t.slice(5, 16).replace('T', ' '))} UTC · ${r.ok ? 'ok' : 'failed'} · ${esc(r.trigger || 'schedule')}"></div>`).join('') + '</div>';
    lanes.appendChild(ln); });
  const legend = document.createElement('div'); legend.className = 'row'; legend.style.marginTop = '8px';
  legend.innerHTML = '<span class="mono"><i style="display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--green)"></i> ran fine</span><span class="mono"><i style="display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--red)"></i> failed</span><span class="mono"><i style="display:inline-block;width:9px;height:9px;border-radius:50%;border:2px solid var(--amber)"></i> started by hand</span><span class="mono"><i style="display:inline-block;width:16px;height:9px;border-radius:3px;background:rgba(194,65,45,.25)"></i> consumers hurt</span>';
  app.append(sum, lanes, legend, card);
  lanes.querySelectorAll('.hurt').forEach(e => e.onclick = () => { lanes.querySelectorAll('.hurt').forEach(h => h.classList.remove('on')); e.classList.add('on');
    const r = D.feeds[+e.dataset.f].recoveries[+e.dataset.i], hrs = (Date.parse(r.end) - Date.parse(r.start)) / 36e5;
    card.innerHTML = `<div class="row" style="justify-content:space-between"><span class="pill p-fail">hurt ${hrs < 2 ? Math.round(hrs * 60) + ' min' : hrs.toFixed(1) + ' h'}</span>
      <span class="mono">${esc(r.start.slice(0, 16).replace('T', ' '))} → ${esc(r.end.slice(0, 16).replace('T', ' '))} UTC</span></div>
      <div style="font-size:18px;font-weight:600;margin:10px 0 4px">${esc(r.title)}</div><div class="cap">A person: ${esc(r.did)}</div>
      ${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener" class="btn" style="display:inline-block;margin-top:10px;text-decoration:none">Read the postmortem ↗</a>` : ''}`; });
  const first = lanes.querySelector('.hurt'); if (first) first.click();
};

// What a run costs, from the jobs that measured themselves. The sliders ask "what if": more runs a day, a private
// repository; the arithmetic is done here, from the measured average.
R.cost = () => {
  const J = D.jobs, avg = D.avg_minutes, rate = D.usd_per_minute;
  const ctl = document.createElement('div'); ctl.className = 'card';
  ctl.innerHTML = `<div class="row" style="justify-content:space-between"><span class="mono">Runs a day</span><b class="num rd">${D.runs_per_day}</b></div>
    <input type="range" min="1" max="48" value="${D.runs_per_day}" class="rpd">
    <div class="row" style="margin-top:8px"></div>`;
  let priv = 'Public repo (this one)';
  ctl.lastElementChild.appendChild(seg(['Public repo (this one)', 'Private repo'], priv, o => { priv = o; calc(); }));
  const out = document.createElement('div'); out.className = 'band'; out.style.marginTop = '12px';
  out.innerHTML = `<div class="mono">For 30 days</div><div class="grid"><div class="k"><div class="mono">List price</div><div class="v num lp"></div><div class="s lps"></div></div>
    <div class="k"><div class="mono">Charged</div><div class="v num ch"></div><div class="s chs"></div></div></div>`;
  const bars = document.createElement('div'); bars.style.marginTop = '14px';
  const mx = Math.max(1, ...J.map(j => j.billed_minutes));
  bars.innerHTML = J.length ? `<div class="mono">Each measured run · billed minutes</div><div class="row" style="align-items:flex-end;gap:6px;height:90px;margin-top:8px">` +
    J.map(j => `<div title="${esc(j.run_at)} · ${j.job_seconds} s · ${j.billed_minutes} min · $${j.list_usd.toFixed(3)} list" style="width:22px;border-radius:5px 5px 2px 2px;background:var(--blue);height:${8 + 76 * j.billed_minutes / mx}px"></div>`).join('') + '</div>'
    : `<div class="cap">No job has measured itself yet; the numbers are a lower bound from the feed steps (${D.steps_seconds} s a run).</div>`;
  app.append(ctl, out, bars);
  const rpd = ctl.querySelector('.rpd'), lab = ctl.querySelector('.rd');
  function calc() { const n = +rpd.value; lab.textContent = n; const list = avg * rate * n * 30;
    out.querySelector('.lp').textContent = '$' + list.toFixed(2); out.querySelector('.lps').textContent = `${avg.toFixed(1)} min a run × $${rate} a minute × ${n} a day`;
    out.querySelector('.ch').textContent = priv.startsWith('Public') ? '$0.00' : '$' + list.toFixed(2);
    out.querySelector('.chs').textContent = priv.startsWith('Public') ? 'standard runners are free for public repositories' : 'before any free minutes on the plan'; }
  rpd.oninput = calc; calc();
};

// The Iceberg copy: one dot per snapshot, sized by the rows it added. Click one to see the table as that run left it.
R.snapshots = () => {
  const S = D.snaps;
  if (!S.length) { app.innerHTML = '<div class="cap">No snapshot yet: the next daily run writes the first.</div>'; return; }
  const mx = Math.max(...S.map(s => s.added || 1));
  const row = document.createElement('div'); row.className = 'snaps';
  row.innerHTML = S.map((s, i) => { const r = 14 + 34 * Math.sqrt((s.added || 1) / mx);
    return `<div class="snap" data-i="${i}"><b style="width:${r}px;height:${r}px;background:${s.seeded ? 'var(--navy)' : 'var(--blue)'}"></b><span>${esc(s.run_at.slice(5, 16).replace('T', ' '))}</span></div>`; }).join('');
  const card = document.createElement('div'); card.className = 'card';
  app.append(row, card);
  row.querySelectorAll('.snap').forEach(e => e.onclick = () => { row.querySelectorAll('.snap').forEach(x => x.classList.remove('on')); e.classList.add('on');
    const s = S[+e.dataset.i];
    card.innerHTML = `<div class="mono">As the run at ${esc(s.run_at.replace('T', ' ').replace('Z', ' UTC'))} left it</div>
      <div class="grid" style="margin-top:10px"></div><div class="cap">${s.seeded ? `The first snapshot: seeded once from the cloud copy, so the table starts with every permit held.` :
      s.added ? `${F.int(s.added)} rows this run did not hold yet, appended; the rows every run re-reads at its cursor added nothing.` : 'Nothing new: no snapshot committed.'}</div>`;
    card.querySelector('.grid').appendChild(tiles([{label: 'Rows in the table', value: s.rows, fmt: 'int', tone: 'info'},
      {label: s.seeded ? 'Seeded' : 'Added this run', value: s.seeded || s.added, fmt: 'int', tone: 'pass'},
      {label: 'Snapshots kept', value: s.snapshots, fmt: 'int', tone: 'info', sub: 'older than 28 days expire'}])); });
  row.lastElementChild.click();
};

// One agent-lab incident as phases you step through: what was injected, how it was caught, what a person approved,
// and the verification. Play walks the phases at their real order; click any phase to open it.
R.incident = () => {
  const P = D.phases, icon = {inject: '!', detect: '◉', inspect: '⌕', propose: '✎', approve: '✓', fix: '⚒', verify: '✔'};
  const head = document.createElement('div'); head.className = 'row'; head.style.justifyContent = 'space-between';
  const go = document.createElement('button'); go.className = 'btn blue'; go.textContent = '▶ Play the incident';
  head.innerHTML = `<div><span class="pill ${D.status === 'verified' ? 'p-pass' : 'p-fail'}">${esc(D.status)}</span>
    <div style="font-size:22px;font-weight:600;margin-top:8px">${esc(D.title)}</div><div class="mono">${esc(D.id)} · ${esc(D.check || '')}</div></div>`;
  head.appendChild(go);
  const rail = document.createElement('div'); rail.className = 'rail';
  rail.innerHTML = P.map((e, i) => `<div class="ph ${e.phase}" data-i="${i}"><b>${icon[e.phase] || i + 1}</b><span>${esc(e.phase)}</span></div>`).join('');
  const card = document.createElement('div'); card.className = 'card'; card.style.minHeight = '150px';
  const risk = document.createElement('div'); risk.className = 'band'; risk.style.marginTop = '12px';
  risk.innerHTML = `<div class="mono">Still possible after the fix</div><div style="margin-top:6px">${esc(D.risk)}</div>`;
  app.append(head, rail, card, risk);
  const kvs = xs => `<div class="kvs">${xs.map(x => `<div><span class="mono">${esc(x.k)}</span><b>${esc(x.v)}</b></div>`).join('')}</div>`;
  function open(i) {
    rail.querySelectorAll('.ph').forEach((n, j) => { n.classList.toggle('lit', j <= i); n.classList.toggle('on', j === i); });
    const e = P[i];
    let body = `<div class="row" style="justify-content:space-between"><span class="mono">${esc(e.at)} · ${esc(e.phase)}</span><span class="mono">${e.seconds} s</span></div>
      <div style="font-size:18px;font-weight:600;margin:8px 0 2px">${esc(e.label)}</div><div class="cap">${esc(e.detail)}</div>`;
    if (e.phase === 'detect' && D.found.length) body += kvs(D.found);
    if (e.phase === 'inspect' && D.evidence.length) body += kvs(D.evidence);
    if (e.phase === 'propose' && D.proposal) body += kvs([{k: 'correction', v: D.proposal.summary}, {k: 'proposed by', v: D.proposal.by}, {k: 'needs the role', v: D.proposal.role}]);
    if (e.phase === 'approve' && D.approval) body += kvs([{k: 'approved by', v: D.approval.by}, {k: 'idempotency key', v: D.approval.key}]) +
      '<div class="cap" style="margin-top:8px">The person who asked cannot approve; the same approval never runs twice.</div>';
    if (e.phase === 'verify') {
      body += D.before_after.map(b => { const mx = Math.max(+b.before || 0, +b.after || 0, 1), pc = v => 100 * (+v || 0) / mx;
        const row = (lab, v, col, w0) => `<div class="row" style="gap:10px;flex-wrap:nowrap;margin-top:6px"><span class="mono" style="width:52px">${lab}</span>
          <div style="flex:1;background:var(--tint);border-radius:8px"><div class="bar2" data-to="${Math.max(pc(v), 0.6)}" style="width:${w0}%;background:${col}"></div></div>
          <b class="num" style="width:64px;text-align:right">${esc(String(v))}</b></div>`;
        return `<div class="ba"><span class="mono">${esc(b.k)}</span>${row('before', b.before, 'var(--red)', pc(b.before))}${row('after', b.after, 'var(--green)', 0)}</div>`; }).join('');
      if (D.checks.length) body += kvs(D.checks);
    }
    card.innerHTML = body;
    card.querySelectorAll('.bar2').forEach(b => setTimeout(() => { b.style.width = b.dataset.to + '%'; }, 250));
  }
  rail.querySelectorAll('.ph').forEach(n => n.onclick = () => { tok++; open(+n.dataset.i); });
  let tok = 0;
  async function play() { const my = ++tok; for (let i = 0; i < P.length; i++) { if (my !== tok) return; open(i);
    await new Promise(r => setTimeout(r, reduce ? 0 : (P[i].phase === 'verify' ? 0 : 1500))); } }
  go.onclick = play; open(P.length - 1);
};

// Every model feature against the rule-based way on the same held-out questions, as bars: grey without a model,
// blue with it. Sort by what the model added; losses stay visible.
R.scorecard = () => {
  const rows = D.rows.map((r, i) => ({...r, i, gain: r.b && r.w ? r.w[0] / r.w[1] - r.b[0] / r.b[1] : null}));
  const top = document.createElement('div'); top.className = 'row'; top.style.justifyContent = 'space-between';
  top.innerHTML = `<div><span class="mono">Model</span><div style="font-size:24px;font-weight:600">${esc(D.model)}</div></div>`;
  let order = 'As measured';
  top.appendChild(seg(['As measured', 'Biggest gain first'], order, o => { order = o; draw(); }));
  const list = document.createElement('div'); list.style.marginTop = '10px';
  const leg = document.createElement('div'); leg.className = 'row'; leg.style.marginTop = '10px';
  leg.innerHTML = '<span class="mono"><i style="display:inline-block;width:14px;height:8px;border-radius:4px;background:#C9CCD6"></i> without a model</span><span class="mono"><i style="display:inline-block;width:14px;height:8px;border-radius:4px;background:var(--blue)"></i> with the model</span>';
  app.append(top, list, leg);
  function draw() {
    const rs = order === 'As measured' ? rows : [...rows].sort((a, b) => (b.gain ?? -9) - (a.gain ?? -9));
    list.innerHTML = rs.map(r => {
      const bar = (f, txt, col) => f ? `<div class="row" style="gap:8px;flex-wrap:nowrap"><div style="flex:1;background:var(--tint);border-radius:6px"><div class="bb" data-w="${100 * f[0] / f[1]}" style="background:${col}"></div></div><span class="lbl" style="width:44px">${f[0]}/${f[1]}</span></div>`
        : `<div class="lbl">${esc(txt)}</div>`;
      const g = r.gain === null ? '' : r.gain > 0 ? `<span class="pill p-pass">+${Math.round(r.gain * 100)} pts</span>` : r.gain < 0 ? `<span class="pill p-fail">${Math.round(r.gain * 100)} pts</span>` : '<span class="pill p-info">same</span>';
      return `<div class="sc-row"><div><b style="font-size:14px">${esc(r.feature)}</b><div class="cap">${esc(r.measure)}</div></div>
        <div class="bars">${bar(r.b, r.without, '#C9CCD6')}${bar(r.w, r.with, 'var(--blue)')}</div><div>${g}</div></div>`; }).join('');
    requestAnimationFrame(() => list.querySelectorAll('.bb').forEach(b => b.style.width = b.dataset.w + '%'));
  }
  draw();
};

// Where every NYC row went: each source on the left splits into what happened to its rows. Bands draw in, dashes
// travel along them; hover a stream to focus it and read the exact count, click it to pin what it means.
R.rowflow = () => {
  const COL = {written: 'var(--blue)', reread: '#9AA3C7', quarantined: 'var(--red)', same: 'var(--green)', changed: 'var(--gold)', gone: '#7E2414'};
  const X0 = 250, X1 = 600, GAP = 16, LAB = 40, W = 1000;
  const lay = (streams, top, band) => { const tot = streams.reduce((t, s) => t + s.n, 0) || 1; let a = top, b = top;
    return streams.map(s => { const w = Math.max(5, band * s.n / tot), y0 = a + w / 2, y1 = b + w / 2; a += w; b += Math.max(w, LAB) + GAP;
      return {s, w, y0, y1, d: `M${X0},${y0} C${(X0 + X1) / 2},${y0} ${(X0 + X1) / 2},${y1} ${X1},${y1}`}; }); };
  const [A, B] = D.sources;
  const ra = lay(A.streams, 20, 170), botA = Math.max(...ra.map(r => r.y1 + r.w / 2), 190);
  const rb = lay(B.streams, botA + 70, 120), H = Math.max(...rb.map(r => r.y1 + r.w / 2), botA + 190) + 16;
  const tot = ss => ss.reduce((t, s) => t + s.n, 0);
  const src = (rows, title, n) => { const y = rows[0].y0 - rows[0].w / 2, h = rows.reduce((t, r) => t + r.w, 0);
    return `<rect x="${X0 - 8}" y="${y}" width="8" height="${h}" rx="3" fill="var(--ink)"/>
      <text x="${X0 - 18}" y="${y + h / 2 - 6}" text-anchor="end" font-size="30" font-weight="300">${F.int(n)}</text>
      <text x="${X0 - 18}" y="${y + h / 2 + 16}" text-anchor="end" font-size="13" fill="var(--muted)">${esc(title)}</text>`; };
  const all = [...ra, ...rb];
  const svg = `<svg class="fl" viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Where every NYC row went">
    ${src(ra, A.title, tot(A.streams))}${src(rb, B.title, tot(B.streams))}
    ${all.map(({s, w, y1, d}, i) => `<g class="st" data-k="${s.key}">
      <path d="${d}" fill="none" stroke="transparent" stroke-width="${Math.max(w, 24)}" pointer-events="stroke"/>
      <rect x="${X1}" y="${y1 - 18}" width="${W - X1}" height="34" fill="transparent"/>
      <path class="bnd" d="${d}" fill="none" stroke="${COL[s.key]}" stroke-opacity=".45" stroke-width="${w}" pathLength="1" stroke-dasharray="1" style="animation-delay:${i * 90}ms"/>
      <path class="dash" d="${d}" fill="none" stroke="${COL[s.key]}" stroke-width="${Math.min(Math.max(2, w * .3), 5)}" stroke-linecap="round" stroke-dasharray="1 23"
        style="animation-duration:${s.key === 'reread' ? 2.6 : s.key === 'written' || s.key === 'changed' ? 0.9 : 1.6}s"/>
      <rect x="${X1}" y="${y1 - w / 2}" width="8" height="${w}" rx="3" fill="${COL[s.key]}"/>
      <text x="${X1 + 20}" y="${y1 - 2}" font-size="17" font-weight="600">${F.int(s.n)}<tspan dx="8" font-size="13.5" font-weight="400" fill="var(--muted)">${esc(s.label)}</tspan></text></g>`).join('')}
  </svg>`;
  const wrap = document.createElement('div'); wrap.className = 'card'; wrap.innerHTML = svg;
  const cap = document.createElement('div'); cap.className = 'cap'; cap.style.minHeight = '42px'; cap.style.marginTop = '8px';
  const def = `${F.int(D.runs)} real runs since ${esc((D.since || '').slice(0, 10))}. Hover a stream for what it is; click to pin it.`;
  cap.textContent = def;
  app.append(wrap, cap);
  if (D.skipped) { const sk = document.createElement('div'); sk.className = 'band'; sk.style.marginTop = '10px';
    sk.innerHTML = `<div class="row" style="justify-content:space-between"><div><div class="mono">Not read at all</div>
      <div style="font-size:28px;font-weight:300">${F.int(D.skipped.rows)} <span style="font-size:15px">rows NYC re-stamped at once</span></div></div>
      <div class="mono">${esc(D.skipped.timestamp.slice(0, 16).replace('T', ' '))} · counted, recorded, skipped</div></div>`;
    app.appendChild(sk); }
  let pinned = null;
  const gs = wrap.querySelectorAll('g.st'), find = k => [...A.streams, ...B.streams].find(s => s.key === k);
  const focus = k => { gs.forEach(g => g.classList.toggle('dim', k !== null && g.dataset.k !== k));
    const s = k && find(k); cap.innerHTML = s ? `<b>${F.int(s.n)} · ${esc(s.label)}.</b> ${esc(s.tell)}` : def; };
  gs.forEach(g => { g.onmouseenter = () => focus(g.dataset.k); g.onmouseleave = () => focus(pinned);
    g.onclick = () => { pinned = pinned === g.dataset.k ? null : g.dataset.k; focus(pinned); }; });
};

// Claims a reviewer will doubt, as cards: the front shows the measure (rings and bars sweep in, misses in red),
// tap to flip for how it was measured and a link to the evidence.
R.proofwall = () => {
  const grid = document.createElement('div'); grid.className = 'pw'; app.appendChild(grid);
  const ring = v => { const r = 46, c = 2 * Math.PI * r, f = v.n ? v.k / v.n : 0, pct = f >= .995 ? (f * 100).toFixed(2) : (f * 100).toFixed(1);
    return `<div class="row" style="gap:16px;flex-wrap:nowrap"><svg viewBox="0 0 112 112" width="104" height="104" aria-hidden="true">
      <circle cx="56" cy="56" r="${r}" fill="none" stroke="var(--line)" stroke-width="11"/>
      <circle class="arc" cx="56" cy="56" r="${r}" fill="none" stroke="${v.miss ? 'var(--red)' : 'var(--blue)'}" stroke-width="11" stroke-linecap="round"
        stroke-dasharray="${c}" stroke-dashoffset="${c}" data-to="${c * (1 - f)}" transform="rotate(-90 56 56)" style="transition:stroke-dashoffset 1.1s cubic-bezier(.2,.7,.2,1)"/>
      <line x1="56" y1="4" x2="56" y2="18" stroke="var(--ink)" stroke-width="2.5" transform="rotate(${v.target * 360} 56 56)"/></svg>
      <div><div class="big ${v.miss ? 'miss' : ''}">${pct}%</div><div class="cap">${F.int(v.k)} of ${F.int(v.n)}</div>
      <span class="pill ${v.miss ? 'p-fail' : 'p-pass'}" style="margin-top:6px;display:inline-block">${esc(v.note)}</span></div></div>`; };
  const versus = v => { const mx = Math.max(v.left, v.right, 1), win = v.lower_better ? v.right < v.left : v.same;
    const head = v.same !== undefined ? v.headline : `${Math.round(100 * (1 - v.right / v.left))}% less`;
    const bar = (n, lab, col, bold) => `<div><div class="row" style="justify-content:space-between;flex-wrap:nowrap"><span class="cap" style="${bold ? 'color:var(--ink);font-weight:600' : ''}">${esc(lab)}</span><b class="num">${F.int(n)}</b></div>
      <div class="b"><i data-w="${100 * n / mx}" style="background:${col}"></i></div></div>`;
    return `<div class="vs"><div class="big" style="color:${win ? 'var(--green)' : 'var(--red)'};font-size:28px;margin-bottom:8px">${esc(head)}</div>
      ${bar(v.left, v.left_label, '#C9CCD6', false)}<div style="height:8px"></div>${bar(v.right, v.right_label, win ? 'var(--green)' : 'var(--red)', true)}</div>`; };
  const dots = v => v.groups.map(g => `<div style="margin-bottom:10px"><div class="dt">${Array.from({length: g.n}, (_, i) =>
      `<i style="background:${i < g.ok ? 'var(--green)' : 'var(--red)'};transition-delay:${i * 60}ms"></i>`).join('')}</div>
      <div class="cap" style="margin-top:4px"><b style="color:var(--ink)">${g.ok} of ${g.n} PASS</b> · ${esc(g.label)}</div></div>`).join('');
  D.cards.forEach((c, i) => {
    const el = document.createElement('div'); el.className = 'pc'; el.style.animation = `rise .4s ${i * 70}ms both`;
    const v = c.visual;
    el.innerHTML = `<button aria-pressed="false" aria-label="${esc(c.title)}: tap for how it was measured">
      <div class="face front"><div class="ttl">${esc(c.title)}</div>${v.type === 'ring' ? ring(v) : v.type === 'dots' ? dots(v) : versus(v)}<div class="hint">tap: how it was measured</div></div>
      <div class="face back"><div class="ttl">${esc(c.title)}</div><p>${esc(c.method)}</p>
        <a class="open" href="${esc(c.href)}" target="_blank" rel="noopener">See the evidence ↗</a></div></button>`;
    const btn = el.querySelector('button');
    btn.onclick = e => { if (e.target.closest('a')) return; el.classList.toggle('flip'); btn.setAttribute('aria-pressed', el.classList.contains('flip')); };
    grid.appendChild(el);
  });
  setTimeout(() => { grid.querySelectorAll('.arc').forEach(a => a.style.strokeDashoffset = a.dataset.to);
    grid.querySelectorAll('.vs i').forEach(b => b.style.width = b.dataset.w + '%');
    grid.querySelectorAll('.dt i').forEach(d => d.style.transform = 'scale(1)'); }, 120);
};

(R[D.kind] || (() => app.textContent = 'unknown visual'))();
setTimeout(fit, 50); setTimeout(fit, 900);
"""


def page(kind: str, data: dict) -> str:
    """One HTML document for a visual. The data rides along as JSON; '</' is escaped so it cannot end the tag."""
    if kind not in KINDS:
        raise ValueError(f"unknown visual {kind!r}; one of {KINDS}")
    blob = json.dumps({"kind": kind, **data}, default=float).replace("</", "<\\/")
    return (f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<style>{_CSS}</style></head><body><div id="app"></div>'
            f'<script id="data" type="application/json">{blob}</script><script>(() => {{{_JS}}})();</script></body></html>')
