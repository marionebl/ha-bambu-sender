// DOM helpers, formatting, toast and modal. No framework.
const BOOL_PROPS = new Set(['value', 'checked', 'disabled', 'selected', 'hidden', 'open']);

export function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = v;
    else if (BOOL_PROPS.has(k)) el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  add(el, kids);
  return el;
}
function add(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k == null || k === false) continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
}
export const clear = (el, ...kids) => { el.replaceChildren(); add(el, kids); return el; };

// Static trusted SVG paths (24x24, stroke icons).
const ICONS = {
  printer: 'M7 9V4h10v5M7 17H5a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2M7 14h10v6H7z',
  queue: 'M4 6h16M4 12h16M4 18h10',
  plus: 'M12 5v14M5 12h14',
  spool: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zm0 6a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
  key: 'M14 10a4 4 0 1 0-3.9 4L11 15h2v2h2v2h3v-3.2l-4.9-4.9',
  gear: 'M4 7h10M18 7h2M4 17h2M10 17h10M16 4v6M8 14v6',
  up: 'M6 15l6-6 6 6',
  down: 'M6 9l6 6 6-6',
  grip: 'M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01',
  check: 'M5 12l5 5 9-10',
  x: 'M6 6l12 12M18 6L6 18',
  back: 'M15 6l-6 6 6 6',
  alert: 'M12 4l9 16H3zM12 10v4M12 17h.01',
  clock: 'M12 7v5l3 2M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z',
  camera: 'M4 8h3l2-3h6l2 3h3v11H4zM12 16a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  upload: 'M12 16V5M7 9l5-5 5 5M5 19h14',
  trash: 'M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13',
  edit: 'M4 20h4L19 9l-4-4L4 16zM13 7l4 4',
  link: 'M10 14a4 4 0 0 0 5.700 0l3-3a4 4 0 0 0-5.700-5.700l-1 1M14 10a4 4 0 0 0-5.700 0l-3 3a4 4 0 0 0 5.700 5.700l1-1',
  user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 20a8 8 0 0 1 16 0',
};
export function icon(name, size = 20) {
  const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('width', size); s.setAttribute('height', size);
  s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor'); s.setAttribute('stroke-width', '1.8');
  s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round'); s.setAttribute('aria-hidden', 'true');
  const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  p.setAttribute('d', ICONS[name] || ''); s.append(p);
  return s;
}

export const hex = (c) => (/^[0-9a-f]{6}$/i.test(c || '') ? '#' + c : '#9aa0a6');
export const pct = (v) => (v == null || v < 0 ? '–' : Math.round(v) + '%');
export const temp = (v) => (v == null ? '–' : Math.round(v) + '°');
export const money = (v) => (v == null ? '–' : new Intl.NumberFormat(undefined, { style: 'currency', currency: 'EUR' }).format(v));
export function dur(min) {
  if (min == null) return '–';
  const m = Math.round(min), hh = Math.floor(m / 60);
  return hh ? `${hh} h ${String(m % 60).padStart(2, '0')} min` : `${m} min`;
}
export const durSec = (s) => dur(s / 60);
// Accepts ISO strings or epoch seconds/milliseconds.
export function toDate(v) {
  if (v == null || v === '') return null;
  const d = typeof v === 'number' ? new Date(v < 1e12 ? v * 1000 : v) : new Date(v);
  return isNaN(d) ? null : d;
}
export const clock = (v) => { const d = toDate(v); return d ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '–'; };
export const dateTime = (v) => { const d = toDate(v); return d ? d.toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '–'; };
export const date = (v) => { const d = toDate(v); return d ? d.toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' }) : '–'; };

// Toast. 403 texts start with "rule:"; show the rule itself.
let toastBox;
export function toast(msg, kind = 'info') {
  toastBox ||= document.body.appendChild(h('div', { class: 'toasts', role: 'status', 'aria-live': 'polite' }));
  const t = h('div', { class: 'toast ' + kind }, String(msg).replace(/^rule:\s*/, ''));
  toastBox.append(t);
  setTimeout(() => t.remove(), kind === 'error' ? 6500 : 3500);
}
export function fail(e) {
  toast(e.status === 403 ? 'Not allowed: ' + e.message : e.message, 'error');
}
// Run an async action, toast failures. Returns undefined on failure.
export async function attempt(fn, ok) {
  try { const r = await fn(); if (ok) toast(ok, 'ok'); return r ?? true; } catch (e) { fail(e); }
}

// Modal built on <dialog>. build(close) returns the body node; resolves when closed.
export function modal(title, build) {
  return new Promise((resolve) => {
    const dlg = h('dialog', { class: 'modal' });
    const close = (v) => { dlg.close(); dlg.remove(); resolve(v); };
    dlg.append(h('header', null, h('h2', null, title),
      h('button', { class: 'icon-btn', 'aria-label': 'Close', onclick: () => close(null) }, icon('x'))), build(close));
    dlg.addEventListener('cancel', () => close(null));
    document.body.append(dlg);
    dlg.showModal();
  });
}

export const field = (label, input, hint) => h('label', { class: 'field' }, h('span', null, label), input, hint && h('small', null, hint));
export const btn = (label, onclick, cls = '', extra = {}) => h('button', { type: 'button', class: 'btn ' + cls, onclick, ...extra }, label);
export const pill = (text, cls = '') => h('span', { class: 'pill ' + cls }, text);
export const empty = (text) => h('div', { class: 'empty' }, text);

export function swatch(colour, size = 28) {
  return h('span', { class: 'swatch', style: `--c:${hex(colour)};--s:${size}px` });
}

export function ring(percent, label, sub) {
  const r = 44, c = 2 * Math.PI * r, v = Math.max(0, Math.min(100, percent || 0));
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 100 100'); svg.setAttribute('class', 'ring');
  svg.innerHTML = `<circle class="trk" cx="50" cy="50" r="${r}"/><circle class="val" cx="50" cy="50" r="${r}" stroke-dasharray="${(c * v) / 100} ${c}" transform="rotate(-90 50 50)"/>`;
  return h('div', { class: 'ring-wrap', role: 'img', 'aria-label': `${Math.round(v)} percent` }, svg,
    h('div', { class: 'ring-txt' }, h('b', null, label ?? Math.round(v) + '%'), sub && h('small', null, sub)));
}
