"""Interactive visuals for the app: animated SVG and plain JavaScript, computed in the browser.

Streamlit reruns the whole page on every click, so a slider that waits on the server feels slow. These
visuals carry their (already measured) numbers into the page and do the arithmetic there: a slider, a
size picker or a card answers at once. Nothing here measures anything; every number comes from the
caller, which reads it from the warehouse or a saved result, so a test can check the two agree.

    page(kind, data) -> one self-contained HTML document (no outside scripts; fonts only)

Kinds: tiles, flow, sizes, worth, scale, game, gate, onboard, history, city.
"""
from __future__ import annotations

import json

KINDS = ("tiles", "flow", "sizes", "worth", "scale", "game", "gate", "onboard", "history", "city")

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
.chip{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:3px 11px;font-size:12px;margin:3px 4px 0 0}
.chip.gone{text-decoration:line-through;color:var(--red);border-color:#F2C9C0;background:#FDF5F3}
.chip.new{color:var(--blue);border-color:#C9D1F2;background:var(--tint)}
@media (max-width:560px){.recs{grid-template-columns:1fr}.bars .b{grid-template-columns:1fr 70px}.bars .track{grid-column:1/3;grid-row:2}.tile .v{font-size:28px}}
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
  const step = t => { const p = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - p, 3);
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
      <text x="${X0 - 16}" y="${a.y + a.h / 2 + 14}" text-anchor="end" font-size="${FL}" fill="#6B6B6B">${esc(n.label)}</text></g>`; });
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
    r.innerHTML = `<div class="lab">${esc(s.label)}<small>${esc(s.cost)} · AUC ${F.plus4(s.auc_change)} [${F.plus4(s.lo)}, ${F.plus4(s.hi)}]</small></div>
      <div class="track"><div class="fill" style="background:${s.measured ? s.color : '#C9C9C9'};width:0"></div></div><div class="val num">$0.000</div>`;
    r.onmouseenter = () => w.querySelector('#cap').textContent = s.why; r.onmouseleave = () => w.querySelector('#cap').textContent = D.hint;
    bars.appendChild(r); s.el = r; });
  let prev = {};
  const upd = v => { w.querySelector('#vv').textContent = F.usd0(v);
    D.sources.forEach(s => { const p = s.measured ? s.lost_per_person * v : 0;
      s.el.querySelector('.fill').style.width = (100 * p / (max * D.max_value / 10000)) + '%';
      count(s.el.querySelector('.val'), p, 'usd3', prev[s.label] || 0); prev[s.label] = p; });
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
    ${Object.entries(r.seconds).map(([k, v], n) => `<div class="grow" title="${k}: ${v.toFixed(2)} s" style="width:${Math.max(3, 100 * v / tot)}%;
      background:${['var(--navy)', 'var(--blue)', 'var(--gold)', 'var(--green)'][n % 4]};animation-delay:${n * 160}ms;color:#fff;
      font:500 12px Geist,sans-serif;display:flex;align-items:center;padding-left:8px;white-space:nowrap;overflow:hidden">${k}</div>`).join('')}</div>
    <div class="row" style="gap:16px;margin-top:6px">${Object.entries(r.seconds).map(([k, v], n) => `<span class="cap" style="margin:0">
      <span style="display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px;background:${['var(--navy)', 'var(--blue)', 'var(--gold)', 'var(--green)'][n % 4]}"></span>${k} ${v.toFixed(2)} s</span>`).join('')}</div>
    <div style="margin-top:14px"><div class="mono">Columns that name a person, never written</div>${r.dropped_personal.map(c => `<span class="chip gone">${esc(c)}</span>`).join('') || '<span class="cap">none</span>'}</div>
    ${r.drift.length ? `<div style="margin-top:10px"><div class="mono">New columns, reported and held back</div>${r.drift.map(c => `<span class="chip new">${esc(c)}</span>`).join('')}</div>` : ''}`;
  app.appendChild(t);
};

// Every scheduled run against the live API: rows pulled, and the whole record on hover. Failed runs stay red.
R.history = () => {
  const runs = D.runs, S = D.summary;
  if (!runs.length) {
    app.innerHTML = `<div class="card fade"><div class="mono">Scheduled sync</div>
      <div style="font-size:22px;font-weight:600;margin:8px 0">The first daily run has not happened yet</div>
      <div class="cap" style="margin:0">Each morning the job pulls what NYC changed since its last cursor and records the run here, failures included.</div></div>`;
    return;
  }
  const last = S.last, days = Math.max(1, Math.round((Date.parse(last.run_at) - Date.parse(S.first)) / 864e5) + 1);
  app.appendChild(tiles([
    {label: 'On schedule since', value: S.first.slice(0, 10), fmt: 'raw', sub: `${days} day${days > 1 ? 's' : ''}`, tone: 'info'},
    {label: 'Runs that succeeded', value: S.succeeded, fmt: 'int', sub: `of ${S.runs}`, tone: S.succeeded === S.runs ? 'pass' : 'warn'},
    {label: 'Rows pulled from NYC', value: S.rows, fmt: 'int', sub: `${F.int(S.retries)} requests retried`, tone: 'info'},
    {label: 'Behind the source', value: last.hours_behind_source ?? 0, fmt: 'raw', sub: 'hours, at the last run', tone: (last.hours_behind_source ?? 99) <= 30 ? 'pass' : 'warn'}]));
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
  const tell = r => r.ok ? `${r.run_at.slice(0, 16).replace('T', ' ')} UTC: ${F.int(r.rows)} rows in ${r.pages} pages, ${r.retries} retried`
      + (Object.keys(r.retries_by_cause || {}).length ? ` (${Object.entries(r.retries_by_cause).map(([k, v]) => v + '×' + k).join(', ')})` : '')
      + (r.rows_loaded !== undefined ? `; ${F.int(r.rows_loaded)} loaded, ${F.int(r.rows_quarantined)} quarantined` : '') + `; ${r.seconds} s`
      + (r.drift && r.drift.length ? `; new columns held back: ${r.drift.join(', ')}` : '')
    : `${r.run_at.slice(0, 16).replace('T', ' ')} UTC: failed. ${r.error || ''}`;
  cap.textContent = tell(last); app.appendChild(cap);
  box.querySelectorAll('.hr').forEach(e => { e.onmouseenter = () => cap.textContent = tell(runs[+e.dataset.i]); e.onmouseleave = () => cap.textContent = tell(last); });
};

// The city's data, live: rows leave the sources, pass the contract gate (or drop into quarantine, or hit
// the wall of people who asked to be forgotten), and the records of one person snap together into one node.
// The animation plays a sample of real people; the counters are the whole city's totals.
R.city = () => {
  const T = D.totals, P = D.people, SRC = D.sources;
  const order = ['crm', 'vendor', 'store', 'email', 'privacy'].filter(k => SRC[k]);
  const head = document.createElement('div'); head.className = 'grid'; head.style.gridTemplateColumns = 'repeat(auto-fit,minmax(118px,1fr))';
  const SHORT = {crm: 'CRM', vendor: 'Vendor', store: 'Store', email: 'Email', privacy: 'Privacy'};
  const counters = [['Rows in', () => Object.values(T.rows_in).reduce((a, b) => a + b, 0), 'info'], ['Loaded', () => T.loaded, 'pass'],
    ['Already had', () => T.already, 'info'], ['Quarantined', () => T.quarantined, 'warn'], ['Blocked: forgotten', () => T.suppressed, 'fail'],
    ['People', () => T.people, 'pass']];
  const cEls = counters.map(([l, , tone]) => { const d = document.createElement('div'); d.className = 'tile tone-' + tone; d.style.padding = '12px 14px';
    d.innerHTML = `<span class="bar"></span><div class="mono">${l}</div><div class="v num" style="font-size:26px">0</div>`; head.appendChild(d); return d.querySelector('.v'); });
  const ctl = document.createElement('div'); ctl.className = 'row'; ctl.style.margin = '12px 0 6px';
  const play = document.createElement('button'); play.className = 'btn blue'; play.style.padding = '7px 16px'; play.textContent = 'Pause';
  const again = document.createElement('button'); again.className = 'btn'; again.style.padding = '7px 16px'; again.textContent = 'Replay';
  let speed = 1; const sp = seg(['1×', '3×', '10×'], '1×', v => speed = parseFloat(v));
  const note = document.createElement('span'); note.className = 'cap'; note.style.margin = '0';
  note.textContent = `Animated: ${P.length} real people of ${F.int(T.people)}. Counters: the whole city. Click a person.`;
  ctl.append(play, again, sp, note);
  const cv = document.createElement('canvas'); cv.style.width = '100%'; cv.style.display = 'block'; cv.style.borderRadius = '18px';
  cv.style.background = 'linear-gradient(180deg,#FBFCFF 0%,#F3F5FC 100%)'; cv.style.border = '1px solid var(--line)'; cv.style.cursor = 'pointer';
  const panel = document.createElement('div'); panel.className = 'cap'; panel.style.minHeight = '22px';
  app.append(head, ctl, cv, panel);
  const ctx = cv.getContext('2d'); let W = 0, H = 0, narrow = false;
  let nodes = [], parts = [], t = 0, running = true, last = performance.now(), pick = null, hover = null;
  const DUR = 26;                                  // seconds of story at 1x
  function layout() {
    W = Math.max(320, cv.clientWidth); narrow = W < 620; H = narrow ? 520 : 470;
    const dpr = window.devicePixelRatio || 1; cv.width = W * dpr; cv.height = H * dpr; cv.style.height = H + 'px'; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const gx = narrow ? W * 0.30 : W * 0.25, zx0 = gx + (narrow ? 26 : 50), zx1 = W - 16, zy0 = 44, zy1 = H - (narrow ? 110 : 96);
    const cols = Math.max(6, Math.round(Math.sqrt(P.length * (zx1 - zx0) / (zy1 - zy0)))), rows = Math.ceil(P.length / cols);
    const dx = (zx1 - zx0) / cols, dy = (zy1 - zy0) / rows;
    const rnd = mulberry(7), slot = P.map((_, i) => i);
    for (let i = slot.length - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [slot[i], slot[j]] = [slot[j], slot[i]]; }   // organic, not sorted by type
    nodes = P.map((p, k) => ({p, i: slot[k], x: 0, y: 0, got: [], r: 0})).map(n => ({...n, x: zx0 + dx * (n.i % cols + .5) + (rnd() - .5) * dx * .45, y: zy0 + dy * (Math.floor(n.i / cols) + .5) + (rnd() - .5) * dy * .45}));
    const srcY = {}; order.forEach((k, i) => srcY[k] = 54 + i * ((H - 150) / Math.max(1, order.length - 1)));
    geo = {gx, srcX: narrow ? 12 : 24, srcY, zy1, binX: narrow ? 14 : 24, binY: H - 52, wallX: gx + 6};
    build();
  }
  let geo = {};
  function mulberry(a) { return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
  function build() {
    const rnd = mulberry(11); parts = [];
    nodes.forEach(n => n.p.records.forEach((r, j) => parts.push({kind: 'rec', src: r.source, node: n, rec: r, t0: rnd() * 0.72 + j * 0.01})));
    const q = Math.min(T.quarantined, 10), sup = Math.min(T.suppressed, 10), em = Math.min(60, Math.round(P.length * 0.6));
    for (let i = 0; i < q; i++) parts.push({kind: 'q', src: (D.reasons[i % Math.max(1, D.reasons.length)] || {}).source || 'email', t0: 0.1 + rnd() * 0.7});
    for (let i = 0; i < sup; i++) parts.push({kind: 'sup', src: order[Math.floor(rnd() * Math.min(3, order.length))], t0: 0.1 + rnd() * 0.7});
    if (SRC.privacy) for (let i = 0; i < Math.min(8, T.rows_in.privacy || 0); i++) parts.push({kind: 'sup', req: true, src: 'privacy', t0: 0.02 + rnd() * 0.6});
    for (let i = 0; i < em && SRC.email; i++) parts.push({kind: 'email', src: 'email', node: nodes[Math.floor(rnd() * nodes.length)], t0: 0.05 + rnd() * 0.8});
    parts.sort((a, b) => a.t0 - b.t0); nodes.forEach(n => { n.got = []; n.r = 0; });
  }
  const ease = x => x < .5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2;
  const col = k => (SRC[k] || {color: '#9A9A9A'}).color;
  function draw() {
    ctx.clearRect(0, 0, W, H);
    // sources
    ctx.font = `500 ${narrow ? 11 : 12.5}px Geist, sans-serif`; ctx.textBaseline = 'middle';
    order.forEach(k => { const y = geo.srcY[k]; ctx.fillStyle = col(k); ctx.beginPath(); ctx.roundRect(geo.srcX, y - 15, narrow ? 58 : 116, 30, 9); ctx.fill();
      ctx.fillStyle = '#fff'; ctx.fillText(narrow ? SHORT[k] || k : SRC[k].label, geo.srcX + 8, y); });
    // the contract gate
    ctx.strokeStyle = 'rgba(41,65,185,.35)'; ctx.setLineDash([4, 6]); ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(geo.gx, 26); ctx.lineTo(geo.gx, geo.zy1 + 8); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = '#2941B9'; ctx.font = `500 10.5px 'Geist Mono', monospace`; ctx.fillText('CONTRACTS', geo.gx - 30, 16);
    // quarantine and forgotten bins
    ctx.fillStyle = 'rgba(229,183,77,.16)'; ctx.strokeStyle = 'rgba(229,183,77,.7)'; ctx.beginPath(); ctx.roundRect(geo.binX, geo.binY - 18, narrow ? 70 : 104, 40, 10); ctx.fill(); ctx.stroke();
    ctx.fillStyle = '#7A5A06'; ctx.font = `500 10px 'Geist Mono', monospace`; ctx.fillText('QUARANTINE', geo.binX + 8, geo.binY - 6);
    const wx = geo.binX + (narrow ? 80 : 118);
    ctx.fillStyle = 'rgba(194,65,45,.12)'; ctx.strokeStyle = 'rgba(194,65,45,.6)'; ctx.beginPath(); ctx.roundRect(wx, geo.binY - 18, narrow ? 70 : 104, 40, 10); ctx.fill(); ctx.stroke();
    ctx.fillStyle = '#8E2A1B'; ctx.fillText('FORGOTTEN', wx + 8, geo.binY - 6);
    ctx.fillStyle = '#6B6B6B'; ctx.font = `500 10.5px 'Geist Mono', monospace`; ctx.fillText('PEOPLE, RESOLVED', geo.gx + 18, 30);
    if (!narrow) { ctx.font = `400 11.5px Geist, sans-serif`; ctx.textAlign = 'right';
      ctx.fillText('ring: each record, colored by its source  ·  red center: a stranger\'s record was joined (answer key)', W - 18, H - 18); ctx.textAlign = 'left'; }
    // people
    nodes.forEach(n => { if (!n.got.length) { ctx.fillStyle = 'rgba(17,25,111,.07)'; ctx.beginPath(); ctx.arc(n.x, n.y, 2.2, 0, 7); ctx.fill(); return; }
      const R = 3.5 + 1.8 * Math.sqrt(n.got.length) * (narrow ? .8 : 1.15); n.r += (R - n.r) * .15;
      const per = (Math.PI * 2) / n.got.length; n.got.forEach((s, j) => { ctx.strokeStyle = col(s); ctx.lineWidth = narrow ? 2.5 : 3.2;
        ctx.beginPath(); ctx.arc(n.x, n.y, n.r, j * per - Math.PI / 2, (j + 1) * per - Math.PI / 2 - .12); ctx.stroke(); });
      ctx.fillStyle = n.p.mixed && n.got.length === n.p.records.length ? 'rgba(194,65,45,.85)' : (n === pick ? '#2941B9' : '#fff');
      ctx.beginPath(); ctx.arc(n.x, n.y, Math.max(1.5, n.r - 2.6), 0, 7); ctx.fill();
      if (n === hover || n === pick) { ctx.strokeStyle = '#050505'; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.arc(n.x, n.y, n.r + 4, 0, 7); ctx.stroke(); } });
    // moving rows
    parts.forEach(p => { const age = t - p.t0; if (age < 0) return;
      const sx = geo.srcX + (narrow ? 58 : 116), sy = geo.srcY[p.src] ?? geo.srcY[order[0]];
      if (p.kind === 'rec' || p.kind === 'email') {
        const a1 = .07, a2 = .11, tx = p.node.x, ty = p.node.y;
        let x, y; if (age < a1) { const e = ease(age / a1); x = sx + (geo.gx - sx) * e; y = sy; }
        else if (age < a1 + a2) { const e = ease((age - a1) / a2); x = geo.gx + (tx - geo.gx) * e; y = sy + (ty - sy) * e - Math.sin(e * Math.PI) * 22; }
        else { if (!p.done) { p.done = true; if (p.kind === 'rec') p.node.got.push(p.src); else p.node.pulse = 1; } return; }
        ctx.fillStyle = col(p.src); ctx.globalAlpha = p.kind === 'email' ? .5 : .95; ctx.beginPath(); ctx.arc(x, y, p.kind === 'email' ? 2 : 3.2, 0, 7); ctx.fill(); ctx.globalAlpha = 1;
      } else {
        const a1 = .07, a2 = .09, tx = p.kind === 'q' ? geo.binX + 20 + (p.t0 * 997 % 60) : wx + 20 + (p.t0 * 991 % 60), ty = geo.binY + 4;
        let x, y, alpha = 1; if (age < a1) { const e = ease(age / a1); x = sx + (geo.gx - 6 - sx) * e; y = sy; }
        else if (age < a1 + a2) { const e = ease((age - a1) / a2); x = geo.gx - 6 + (tx - geo.gx + 6) * e; y = sy + (ty - sy) * e * e; }
        else { x = tx; y = ty; alpha = p.kind === 'sup' ? Math.max(.25, 1 - (age - a1 - a2) * 4) : 1; }
        ctx.globalAlpha = alpha; ctx.fillStyle = p.kind === 'q' ? '#E5B74D' : p.req ? '#9A9A9A' : '#C2412D'; ctx.beginPath(); ctx.arc(x, y, 3.4, 0, 7); ctx.fill(); ctx.globalAlpha = 1;
        if (age > a1 - .004 && age < a1 + .012) { ctx.strokeStyle = p.kind === 'q' ? '#E5B74D' : '#C2412D'; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(geo.gx, sy, 10, 0, 7); ctx.stroke(); }
      } });
    nodes.forEach(n => { if (n.pulse > 0) { ctx.strokeStyle = `rgba(124,143,224,${n.pulse})`; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(n.x, n.y, n.r + 6 * (1 - n.pulse) + 2, 0, 7); ctx.stroke(); n.pulse -= .04; } });
    // counters follow the story's progress; the totals are the whole city's
    const prog = Math.min(1, t / 0.92);
    counters.forEach(([, f], i) => cEls[i].textContent = F.int(f() * prog));
  }
  function loop(now) { const dt = Math.min(.05, (now - last) / 1000); last = now;
    if (running && !reduce) t = Math.min(1.25, t + dt * speed / DUR); draw(); requestAnimationFrame(loop); }
  function at(e) { const b = cv.getBoundingClientRect(), x = e.clientX - b.left, y = e.clientY - b.top; let best = null, d = 1e9;
    nodes.forEach(n => { if (!n.got.length) return; const dd = Math.hypot(n.x - x, n.y - y); if (dd < d && dd < n.r + 8) { d = dd; best = n; } }); return best; }
  function show(n) { if (!n) { panel.textContent = 'Click a person to see the records they were built from.'; return; }
    panel.innerHTML = `<b>${esc(n.p.name)}</b> · ${n.p.records.length} records${n.p.mixed ? ' · <span class="differ">the answer key says one of these belongs to someone else: a matcher mistake, shown on purpose</span>' : ''}<br>` +
      n.p.records.map(r => `<span class="chip" style="border-color:${col(r.source)}"><b style="color:${col(r.source)}">${esc(SRC[r.source] ? SRC[r.source].label : r.source)}</b> ${esc(r.name)} · ${esc(r.email || 'no email')} · ${esc(r.address)}</span>`).join(''); }
  cv.onmousemove = e => { hover = at(e); cv.title = hover ? `${hover.p.name}: ${hover.got.length} of ${hover.p.records.length} records joined` : ''; };
  cv.onmouseleave = () => hover = null;
  cv.onclick = e => { pick = at(e); show(pick); };
  play.onclick = () => { running = !running; play.textContent = running ? 'Pause' : 'Play'; };
  again.onclick = () => { t = 0; build(); pick = null; show(null); running = true; play.textContent = 'Pause'; };
  layout(); show(null); if (reduce) t = 1.25;
  let lw = W; new ResizeObserver(() => { if (Math.abs(cv.clientWidth - lw) > 20) { lw = cv.clientWidth; const keep = t; layout(); t = 0; build(); t = keep;
    parts.forEach(p => { if (t - p.t0 > .18 && p.kind === 'rec') { p.done = true; p.node.got.push(p.src); } }); } }).observe(cv);
  requestAnimationFrame(loop);
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
