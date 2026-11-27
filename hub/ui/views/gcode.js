// Top-down G-code layer preview (reused from slicer/index.html): current layer bright, the one below dim.
import { h } from '../util.js';

const FEATURE = { 'Outer wall': '#ff8a3d', 'Inner wall': '#ffd166', 'Sparse infill': '#c86bfa', 'Internal solid infill': '#9b5de5',
  'Top surface': '#ef476f', 'Bottom surface': '#4cc9f0', 'Bridge': '#06d6a0', 'Support': '#8ecae6', 'Support interface': '#219ebc',
  'Brim': '#adb5bd', 'Skirt': '#adb5bd', 'Gap infill': '#f4a261', 'Overhang wall': '#2a9d8f', 'Prime tower': '#6c757d' };

function parse(text) {
  const layers = []; let cur = null, x = 0, y = 0, z = 0, feat = '', relE = true;
  let minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9;
  for (const raw of text.split('\n')) {
    if (raw.startsWith(';')) {
      if (raw.startsWith('; CHANGE_LAYER') || raw.startsWith(';LAYER_CHANGE')) { cur = { z, segs: {} }; layers.push(cur); }
      else if (raw.startsWith('; FEATURE: ') || raw.startsWith(';TYPE:')) feat = raw.slice(raw.indexOf(':') + 1).trim();
      else if (raw.startsWith('; Z_HEIGHT: ') && cur) cur.z = parseFloat(raw.slice(12));
      continue;
    }
    const line = raw.split(';')[0].trim();
    if (!line) continue;
    const cmd = line.split(' ', 1)[0];
    if (cmd === 'M82') { relE = false; continue; }
    if (cmd === 'M83') { relE = true; continue; }
    if (!['G0', 'G1', 'G2', 'G3'].includes(cmd)) continue;
    const p = {};
    for (const w of line.split(' ').slice(1)) if (w) p[w[0]] = parseFloat(w.slice(1));
    const nx = p.X ?? x, ny = p.Y ?? y;
    if (p.Z !== undefined) z = p.Z;
    if (p.E !== undefined && p.E > 0 && relE && cur && feat) {
      const arr = cur.segs[feat] || (cur.segs[feat] = []);
      if ((cmd === 'G2' || cmd === 'G3') && p.I !== undefined) {
        const cx = x + (p.I || 0), cy = y + (p.J || 0), r = Math.hypot(x - cx, y - cy);
        let a0 = Math.atan2(y - cy, x - cx), a1 = Math.atan2(ny - cy, nx - cx);
        if (cmd === 'G3' && a1 <= a0) a1 += 2 * Math.PI;
        if (cmd === 'G2' && a1 >= a0) a1 -= 2 * Math.PI;
        const n = Math.max(2, Math.ceil(Math.abs(a1 - a0) * r / 0.5));
        let px = x, py = y;
        for (let k = 1; k <= n; k++) { const a = a0 + (a1 - a0) * k / n, qx = cx + r * Math.cos(a), qy = cy + r * Math.sin(a); arr.push(px, py, qx, qy); px = qx; py = qy; }
      } else arr.push(x, y, nx, ny);
      minX = Math.min(minX, x, nx); maxX = Math.max(maxX, x, nx); minY = Math.min(minY, y, ny); maxY = Math.max(maxY, y, ny);
    }
    x = nx; y = ny;
  }
  const kept = layers.filter((l) => Object.keys(l.segs).length);
  const span = Math.max(maxX - minX, maxY - minY) + 10;
  return { layers: kept, b: { x: (minX + maxX) / 2 - span / 2, y: (minY + maxY) / 2 - span / 2, span } };
}

// Returns a node; load(text) fills it.
export function layerPreview() {
  const canvas = h('canvas', { class: 'layer', width: 720, height: 720, role: 'img', 'aria-label': 'G-code layer preview' });
  const slider = h('input', { type: 'range', min: 0, max: 0, value: 0, 'aria-label': 'Layer' });
  const label = h('small', { class: 'muted' }, 'Loading G-code…');
  const legend = h('div', { class: 'legend' });
  let G = null;
  const draw = () => {
    if (!G) return;
    const g = canvas.getContext('2d'), n = +slider.value, W = canvas.width, k = W / G.b.span;
    g.clearRect(0, 0, W, W);
    const tx = (v) => (v - G.b.x) * k, ty = (v) => W - (v - G.b.y) * k;
    const stroke = (layer, alpha) => {
      for (const [f, a] of Object.entries(layer.segs)) {
        g.strokeStyle = FEATURE[f] || '#ddd'; g.globalAlpha = alpha; g.lineWidth = Math.max(1, 0.42 * k); g.beginPath();
        for (let i = 0; i < a.length; i += 4) { g.moveTo(tx(a[i]), ty(a[i + 1])); g.lineTo(tx(a[i + 2]), ty(a[i + 3])); }
        g.stroke();
      }
    };
    if (n > 0) stroke(G.layers[n - 1], 0.18);
    stroke(G.layers[n], 1);
    g.globalAlpha = 1;
    label.textContent = `Layer ${n + 1} of ${G.layers.length} · Z ${G.layers[n].z.toFixed(2)} mm`;
  };
  slider.addEventListener('input', draw);
  const node = h('div', { class: 'stack', style: 'gap:8px' }, canvas, slider, label, legend);
  node.load = (text) => {
    G = parse(text);
    if (!G.layers.length) { label.textContent = 'The G-code has no layers to show.'; return; }
    slider.max = G.layers.length - 1; slider.value = G.layers.length - 1;
    const used = new Set(G.layers.flatMap((l) => Object.keys(l.segs)));
    legend.replaceChildren(...[...used].map((f) => h('span', null, h('i', { style: `background:${FEATURE[f] || '#ddd'}` }), f)));
    draw();
  };
  node.fail = (msg) => { label.textContent = msg; };
  return node;
}
