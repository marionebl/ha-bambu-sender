// Printer grid and printer detail.
import { api } from '../api.js';
import { h, clear, icon, ring, btn, empty, dur, clock, attempt, pill, swatch } from '../util.js';
import { statusPill, isPrinting, hmsBanners, amsBlock, temps, timeLeft, finishAt } from './common.js';

export async function mount(ctx, root, [serial]) {
  if (!ctx.me.setup_done && !ctx.isAdmin()) {
    root.append(empty('The hub has not been set up yet. Ask a Home Assistant admin to open the setup wizard.'));
    return;
  }
  return serial ? detail(ctx, root, decodeURIComponent(serial)) : grid(ctx, root);
}

function camera(p, big) {
  const box = h('div', { class: 'cam' + (big ? ' big' : '') });
  if (p.online && p.camera_url) box.append(h('img', { src: p.camera_url, alt: `Camera of ${p.name}`, loading: 'lazy' }));
  else box.append(h('div', { class: 'off' }, icon('camera', 28), p.online ? 'No camera image' : 'Printer offline'));
  box.append(h('span', { class: 'badge' }, statusPill(p)));
  if (p.task_name && isPrinting(p)) box.append(h('div', { class: 'name' }, p.task_name));
  return box;
}

function card(p) {
  const printing = isPrinting(p);
  return h('a', { class: 'card pcard', href: '#/printers/' + encodeURIComponent(p.serial), 'aria-label': p.name },
    camera(p),
    h('div', { class: 'info' },
      h('div', { class: 'row between' }, h('div', null, h('h2', { style: 'margin:0' }, p.name), h('small', null, p.model)),
        p.queue_length ? pill(`${p.queue_length} queued`) : null),
      p.hms?.length > 0 && h('div', { class: 'hms', role: 'alert' }, icon('alert', 18), h('div', { class: 'txt' }, h('span', null, h('code', null, p.hms[0].code), ' ', p.hms[0].text))),
      printing && h('div', { class: 'pbar' },
        h('div', { class: 'top' }, h('b', null, Math.round(p.progress) + '%'), h('small', null, [timeLeft(p), finishAt(p)].filter(Boolean).join(' · '))),
        h('div', { class: 'bar' }, h('i', { style: `width:${p.progress}%` })),
        h('small', null, `Layer ${p.layer}/${p.total_layers}`)),
      p.online && h('div', { class: 'row between' },
        h('small', null, `Nozzle ${Math.round(p.nozzle_temp)}° · Bed ${Math.round(p.bed_temp)}°`)),
      (p.ams?.length || p.external) && amsBlock(p, true)));
}

async function grid(ctx, root) {
  const list = h('div', { class: 'grid' });
  root.append(h('div', { class: 'page-head' }, h('div', null, h('h1', null, 'Printers'), h('div', { class: 'sub' }, 'Live status from Home Assistant')),
    btn([icon('plus', 18), 'New print'], () => ctx.go('#/new'), 'primary')), list);
  let last = '';
  ctx.every(5000, async () => {
    try {
      const ps = await api.printers();
      const sig = JSON.stringify(ps);
      if (sig === last) return;
      last = sig;
      clear(list, ps.length ? ps.map(card) : empty('No printers yet. An admin can grant you access, or add printers in the setup wizard.'));
    } catch (e) { if (!last) clear(list, empty(e.message)); }
  });
}

async function detail(ctx, root, serial) {
  const body = h('div');
  const camHost = h('div');
  root.append(h('a', { class: 'btn sm', href: '#/printers', style: 'margin-bottom:12px' }, icon('back', 16), 'Printers'), body);
  let camUrl = '', last = '';
  ctx.every(5000, async () => {
    let ps, queue;
    try { [ps, queue] = await Promise.all([api.printers(), api.queue()]); } catch (e) { if (!last) clear(body, empty(e.message)); return; }
    const p = ps.find((x) => x.serial === serial);
    if (!p) { clear(body, empty('This printer was not found, or you have no access to it.')); return; }
    const sig = JSON.stringify([p, queue.jobs]);
    if (sig === last) return;
    last = sig;
    if (p.camera_url !== camUrl || !p.online) { camUrl = p.camera_url; clear(camHost, camera(p, true)); }
    const mine = queue.jobs.filter((j) => j.printer === serial || j.target === serial || (j.target === 'any' && j.model === p.model));
    draw(ctx, body, camHost, p, mine, () => { last = ''; });
  });
}

function jobCard(p) {
  const j = p.current_job;
  if (!isPrinting(p)) {
    return h('section', { class: 'card' }, h('h2', null, 'Current job'),
      p.online ? h('p', { class: 'muted' }, p.status === 'finish' ? 'The last print is finished. Clear the plate before starting the next one.' : 'Nothing is printing.') : h('p', { class: 'muted' }, 'The printer is offline.'));
  }
  return h('section', { class: 'card' }, h('h2', null, 'Current job'),
    h('div', { class: 'jobbox' },
      ring(p.progress, null, `Layer ${p.layer}/${p.total_layers}`),
      h('div', { class: 'meta' },
        h('div', { class: 't' }, p.task_name || 'Print'),
        h('div', { class: 'big' }, timeLeft(p)),
        h('small', null, p.end_time ? 'Finishes at ' + clock(p.end_time) : ''),
        j && h('small', null, `Started by ${j.owner_name}`),
        h('small', null, p.stage && p.stage !== 'printing' ? p.stage : ''))));
}

const plateOk = new Set(); // survives the 5 s redraw
function startNext(ctx, p, mine, refresh) {
  const next = mine.find((j) => j.state === 'queued');
  const sure = h('input', { type: 'checkbox', id: 'plate-clear', checked: plateOk.has(p.serial) });
  const go = btn([icon('check', 18), next ? `Start “${next.source}”` : 'Start next'], async () => {
    if (!sure.checked) return;
    if (await attempt(() => api.startNext(p.serial), 'Starting the next job')) { sure.checked = false; plateOk.delete(p.serial); refresh(); }
  }, 'primary', { disabled: !sure.checked || !next || isPrinting(p) });
  sure.addEventListener('change', () => {
    sure.checked ? plateOk.add(p.serial) : plateOk.delete(p.serial);
    go.disabled = !sure.checked || !next || isPrinting(p);
  });
  return h('section', { class: 'card' }, h('h2', null, 'Start next print'),
    next ? h('p', { class: 'muted', style: 'margin-top:0' }, `Next in line: ${next.source} (${next.owner_name})`)
      : h('p', { class: 'muted', style: 'margin-top:0' }, 'The queue for this printer is empty.'),
    h('div', { class: 'form' },
      h('label', { class: 'check' }, sure, h('span', null, h('b', null, 'Plate is clear'), h('br'), h('small', null, 'I removed the last print and the right filament is loaded.'))),
      go,
      isPrinting(p) && h('small', null, 'The printer is busy.')));
}

function draw(ctx, body, camHost, p, mine, refresh) {
  const queueCard = h('section', { class: 'card' }, h('div', { class: 'row between' }, h('h2', { style: 'margin:0' }, 'Queue'), h('a', { href: '#/queue' }, 'Open queue')),
    mine.length ? h('div', { class: 'stack', style: 'margin-top:12px;gap:8px' }, mine.map((j, i) =>
      h('div', { class: 'row between' }, h('span', null, h('b', null, `${i + 1}. `), j.source, h('small', null, ' · ' + j.owner_name)),
        pill(j.state === 'pending_approval' ? 'needs approval' : j.state, j.state === 'pending_approval' ? 'warn' : j.state === 'printing' ? 'ok' : ''))))
      : h('p', { class: 'muted' }, 'Nothing waiting.'));
  clear(body,
    h('div', { class: 'page-head' }, h('div', null, h('h1', null, p.name), h('div', { class: 'sub' }, `${p.model} · ${p.serial.slice(0, 4)}…${p.serial.slice(-3)}`)), statusPill(p)),
    h('div', { class: 'stack', style: 'margin-bottom:16px' }, hmsBanners(p.hms)),
    h('div', { class: 'cols' },
      h('div', { class: 'stack' }, camHost, jobCard(p), ctx.canManage(p.serial) && startNext(ctx, p, mine, refresh)),
      h('div', { class: 'stack' },
        h('section', { class: 'card' }, h('h2', null, 'Temperatures'), p.online ? temps(p) : h('p', { class: 'muted' }, 'No data while offline.')),
        (p.ams?.length || p.external) && h('section', { class: 'card' }, h('h2', null, 'Filament'), amsBlock(p)),
        queueCard)));
}
