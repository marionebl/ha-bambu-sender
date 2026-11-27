// Queue: jobs grouped by printer, approvals separate, drag or buttons to reorder, history tab.
import { api } from '../api.js';
import { h, clear, icon, btn, pill, empty, modal, field, attempt, dateTime, money, toast } from '../util.js';

const WAITING = ['queued', 'pending_approval'];
const STATE = { pending_approval: ['Needs approval', 'warn'], queued: ['Queued', ''], starting: ['Starting', 'ok'], printing: ['Printing', 'ok'], done: ['Done', 'ok'], failed: ['Failed', 'err'], cancelled: ['Cancelled', ''] };

export async function mount(ctx, root) {
  const state = { tab: 'queue', dragging: null };
  const list = h('div', { class: 'stack' });
  const seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'View' });
  let data = null, names = {}, models = {}, last = '';

  const setTab = (t) => { state.tab = t; last = ''; draw(); };
  const drawSeg = () => clear(seg, [['queue', 'Queue'], ['history', 'History']].map(([id, l]) =>
    h('button', { 'aria-pressed': String(state.tab === id), onclick: () => setTab(id) }, l)));
  root.append(h('div', { class: 'page-head' }, h('div', null, h('h1', null, 'Queue'), h('div', { class: 'sub' }, ctx.isAdmin() ? 'All jobs' : 'Jobs on your printers')),
    h('div', { class: 'row' }, seg, btn([icon('plus', 18), 'New print'], () => ctx.go('#/new'), 'primary'))), list);

  const isAdminFor = (j) => ctx.me.is_admin || (j.target === 'any' ? ctx.managesAny() : ctx.canManage(j.target));
  const isOwn = (j) => j.owner_user_id === ctx.me.user_id;
  const label = (target) => (target === 'any' ? 'Any compatible printer' : names[target] || target);

  async function act(j, action, body, ok) {
    if (await attempt(() => api.jobAction(j.id, action, body), ok)) refresh();
  }

  // Reorder: position is 0-based among the waiting jobs of the same printer (scope), or of all printers for "any" jobs;
  // a user's pool holds only their own jobs.
  const pool = (j) => data.jobs.filter((x) => WAITING.includes(x.state) && (j.target === 'any' || x.target === j.target) && (isAdminFor(j) || isOwn(x)));
  function moveTo(j, neighbour) {
    const at = pool(j).findIndex((x) => x.id === neighbour.id);
    if (at < 0 || neighbour.id === j.id) return;
    return act(j, 'reorder', j.target === 'any' ? { position: at } : { position: at, scope: j.target });
  }
  function step(j, group, dir) {
    const scope = group.filter((x) => isAdminFor(j) || isOwn(x));
    const n = scope[scope.findIndex((x) => x.id === j.id) + dir];
    if (n) moveTo(j, n);
  }

  function startDialog(j) {
    const candidates = Object.entries(names).filter(([s]) => ctx.canManage(s) && (j.target === 'any' ? models[s] === j.model : s === j.target));
    return modal('Start this job now', (close) => {
      const sure = h('input', { type: 'checkbox' });
      const sel = h('select', null, candidates.map(([s, n]) => h('option', { value: s }, n)));
      const go = btn('Start print', async () => {
        if (await attempt(() => api.startJob(sel.value, j.id), 'Starting')) { close(true); refresh(); }
      }, 'primary', { disabled: true });
      sure.addEventListener('change', () => (go.disabled = !sure.checked));
      return h('div', { class: 'body' }, h('p', { class: 'muted', style: 'margin:0' }, j.source),
        j.target === 'any' && field('Printer', sel),
        j.target !== 'any' && h('p', { style: 'margin:0' }, 'Printer: ', h('b', null, label(j.target)), h('span', { hidden: true }, sel)),
        h('label', { class: 'check' }, sure, h('span', null, h('b', null, 'Plate is clear'), h('br'), h('small', null, 'The last print is removed and the right filament is loaded.'))), go);
    });
  }
  function moveDialog(j) {
    return modal('Move job', (close) => {
      const opts = [['any:' + j.model, 'Any compatible ' + (j.model || 'printer')], ...Object.entries(names).map(([s, n]) => [s, n])];
      const sel = h('select', null, opts.map(([v, n]) => h('option', { value: v, selected: v === j.target }, n)));
      return h('div', { class: 'body' }, field('Target printer', sel),
        btn('Move', async () => {
          const v = sel.value, any = v.startsWith('any:');
          if (await attempt(() => api.jobAction(j.id, 'move', any ? { target: 'any', model: v.slice(4) } : { target: v, model: models[v] }), 'Job moved')) { close(true); refresh(); }
        }, 'primary'));
    });
  }

  function row(j, group, idx) {
    const [stl, cls] = STATE[j.state] || [j.state, ''];
    const waiting = j.state === 'queued', pending = j.state === 'pending_approval';
    const admin = isAdminFor(j), own = isOwn(j);
    const canOrder = (waiting || pending) && (admin || own) && state.tab === 'queue';
    const acts = [];
    if (canOrder && waiting) acts.push(
      h('button', { class: 'icon-btn', 'aria-label': 'Move up', disabled: idx === 0, onclick: () => step(j, group, -1) }, icon('up')),
      h('button', { class: 'icon-btn', 'aria-label': 'Move down', disabled: idx === group.length - 1, onclick: () => step(j, group, 1) }, icon('down')));
    if (pending && admin) acts.push(btn('Approve', () => act(j, 'approve', undefined, 'Approved'), 'sm primary'), btn('Reject', () => act(j, 'reject', undefined, 'Rejected'), 'sm danger'));
    if (waiting && admin) acts.push(btn('Start', () => startDialog(j), 'sm'), btn('Skip', () => act(j, 'skip', undefined, 'Skipped'), 'sm'), btn('Move…', () => moveDialog(j), 'sm'));
    if (['queued', 'pending_approval', 'starting', 'printing'].includes(j.state) && (admin || own)) acts.push(btn('Cancel', () => act(j, 'cancel', undefined, 'Cancelled'), 'sm danger'));
    if (['failed', 'cancelled'].includes(j.state) && (admin || own)) acts.push(btn('Retry', () => act(j, 'retry', undefined, 'Requeued'), 'sm'));
    const el = h('div', { class: 'job' + (pending ? ' pending' : ''), draggable: canOrder && waiting ? 'true' : null, 'data-id': j.id },
      canOrder && waiting ? h('div', { class: 'grip', title: 'Drag to reorder', 'aria-hidden': 'true' }, icon('grip')) : h('div', { class: 'pos' }, waiting ? idx + 1 : ''),
      j.plate_png ? h('img', { class: 'thumb', src: j.plate_png, alt: '', loading: 'lazy' }) : h('div', { class: 'thumb' }),
      h('div', { class: 'main' },
        h('div', { class: 'row between' }, h('span', { class: 'title' }, j.source || j.file), pill(stl, cls)),
        h('div', { class: 'sub' },
          h('span', { class: 'row', style: 'gap:6px' }, h('span', { class: 'avatar' }, (j.owner_name || '?')[0].toUpperCase()), j.owner_name, own && ' (you)'),
          state.tab === 'history' && h('span', null, label(j.printer || j.target)),
          j.grams != null && h('span', null, j.grams.toFixed(0) + ' g'),
          j.cost != null && h('span', null, money(j.cost)),
          j.options?.timelapse && h('span', null, 'timelapse'),
          h('span', null, dateTime(j.finished_at || j.created_at))),
        j.error && h('small', { style: 'color:var(--err)' }, j.error)),
      acts.length ? h('div', { class: 'acts' }, acts) : null);
    if (canOrder && waiting) {
      el.addEventListener('dragstart', (e) => { state.dragging = j; el.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', j.id); });
      el.addEventListener('dragend', () => { state.dragging = null; el.classList.remove('dragging'); list.querySelectorAll('.over').forEach((x) => x.classList.remove('over')); });
    }
    el.addEventListener('dragover', (e) => { if (state.dragging && state.dragging.id !== j.id && state.dragging.target === j.target && waiting) { e.preventDefault(); el.classList.add('over'); } });
    el.addEventListener('dragleave', () => el.classList.remove('over'));
    el.addEventListener('drop', (e) => { e.preventDefault(); el.classList.remove('over'); const d = state.dragging; state.dragging = null; if (d) moveTo(d, j); });
    return el;
  }

  function draw() {
    drawSeg();
    if (!data) return;
    if (state.tab === 'history') {
      clear(list, data.history.length ? h('div', { class: 'stack', style: 'gap:8px' }, data.history.map((j, i) => row(j, data.history, i))) : empty('No finished jobs yet.'));
      return;
    }
    const pending = data.jobs.filter((j) => j.state === 'pending_approval');
    const groups = new Map();
    for (const j of data.jobs) {
      if (j.state === 'pending_approval') continue;
      (groups.get(j.target) || groups.set(j.target, []).get(j.target)).push(j);
    }
    const out = [];
    if (pending.length) out.push(h('section', { class: 'group' },
      h('div', { class: 'section-note' }, `${pending.length} job${pending.length > 1 ? 's' : ''} waiting for approval`),
      pending.map((j, i) => row(j, pending, i))));
    const order = [...groups.keys()].sort((a, b) => (a === 'any') - (b === 'any'));
    for (const t of order) {
      const g = groups.get(t), waitingOnly = g.filter((j) => j.state === 'queued');
      out.push(h('section', { class: 'group' }, h('h3', null, label(t), pill(String(g.length))),
        g.map((j) => row(j, waitingOnly, waitingOnly.indexOf(j)))));
    }
    clear(list, out.length ? out : empty('The queue is empty. Slice something under New print.'));
  }

  async function refresh() {
    try {
      const [q, ps] = await Promise.all([api.queue(), api.printers()]);
      names = Object.fromEntries(ps.map((p) => [p.serial, p.name]));
      models = Object.fromEntries(ps.map((p) => [p.serial, p.model]));
      const sig = JSON.stringify([q.jobs, q.history, names]);
      if (sig === last || state.dragging || document.querySelector('dialog[open]')) return;
      last = sig; data = q; draw();
    } catch (e) { if (!data) clear(list, empty(e.message)); }
  }
  drawSeg();
  ctx.every(8000, refresh);
}
