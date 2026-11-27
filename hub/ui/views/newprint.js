// New print: upload, pick printer + filament, slice good/better/best side by side, choose, queue.
import { api } from '../api.js';
import { h, clear, icon, btn, pill, field, swatch, empty, attempt, toast, fail, durSec, money, hex } from '../util.js';
import { layerPreview } from './gcode.js';

const QUALITIES = [['good', 'Good', 'Fastest, visible layers'], ['better', 'Better', 'Balanced'], ['best', 'Best', 'Finest detail, slowest']];
const BEDS = ['Textured PEI Plate', 'Cool Plate', 'Engineering Plate', 'High Temp Plate', 'Supertack Plate'];
const ACCEPT = /\.(stl|3mf|step|stp|obj)$/i;

export async function mount(ctx, root) {
  const [printers, spools] = await Promise.all([api.printers(), api.spools().catch(() => [])]);
  const S = { file: null, target: null, tray: null, bed: BEDS[0], variants: null, pick: null,
    options: { timelapse: false, bed_leveling: true, use_ams: true }, busy: false };
  const box = h('div', { class: 'stack' });
  root.append(h('div', { class: 'page-head' }, h('div', null, h('h1', null, 'New print'), h('div', { class: 'sub' }, 'Upload, compare three slices, then queue'))), box);

  const models = [...new Set(printers.map((p) => p.model))];
  const refPrinter = () => {
    if (!S.target) return null;
    if (S.target.startsWith('any:')) { const m = S.target.slice(4); return printers.find((p) => p.model === m && p.online) || printers.find((p) => p.model === m); }
    return printers.find((p) => p.serial === S.target);
  };
  const trays = () => (refPrinter()?.ams || []).flatMap((u) => u.trays.filter((t) => t.type).map((t) => ({ ...t, unit: u.unit })));
  const trayKey = (t) => `${t.unit}:${t.slot}`;
  const spoolOf = (t) => spools.find((s) => s.id === t.spool_id);

  function pickFile(f) {
    if (!f) return;
    if (!ACCEPT.test(f.name)) return toast('Upload an STL, 3MF or STEP file.', 'error');
    S.file = f; S.variants = null; S.pick = null; draw();
  }

  function stepper(n) {
    return h('div', { class: 'steps' }, ['Model', 'Printer and filament', 'Compare', 'Queue'].map((l, i) =>
      h('div', { class: 'step' + (i + 1 === n ? ' on' : i + 1 < n ? ' done' : '') }, h('i', null, i + 1 < n ? '✓' : i + 1), l)));
  }

  function fileCard() {
    const input = h('input', { type: 'file', accept: '.stl,.3mf,.step,.stp,.obj', hidden: true, onchange: (e) => pickFile(e.target.files[0]) });
    const drop = h('div', { class: 'drop', tabindex: '0', role: 'button', onclick: () => input.click(),
      onkeydown: (e) => (e.key === 'Enter' || e.key === ' ') && input.click(),
      ondragover: (e) => { e.preventDefault(); drop.classList.add('over'); }, ondragleave: () => drop.classList.remove('over'),
      ondrop: (e) => { e.preventDefault(); drop.classList.remove('over'); pickFile(e.dataTransfer.files[0]); } },
      icon('upload', 28),
      S.file ? h('b', null, S.file.name) : h('b', null, 'Drop a model here or tap to choose'),
      h('small', null, S.file ? `${(S.file.size / 1048576).toFixed(1)} MB · tap to replace` : 'STL, 3MF or STEP'), input);
    return h('section', { class: 'card' }, h('h2', null, '1 · Model'), drop);
  }

  function printerCard() {
    const opts = [...models.map((m) => ['any:' + m, `Any compatible ${m}`, (n => n + (n === 1 ? ' printer' : ' printers'))(printers.filter((p) => p.model === m).length)]),
      ...printers.map((p) => [p.serial, p.name, p.model + (p.online ? '' : ' · offline')])];
    const tr = trays();
    return h('section', { class: 'card stack' }, h('h2', { style: 'margin:0' }, '2 · Printer and filament'),
      h('div', null, h('div', { class: 'tag', style: 'margin-bottom:6px' }, 'Target'),
        h('div', { class: 'chips' }, opts.map(([v, l, sub]) =>
          h('button', { class: 'chip', 'aria-pressed': String(S.target === v), onclick: () => { S.target = v; S.tray = null; S.variants = null; draw(); } },
            l, h('small', null, sub))))),
      S.target && h('div', null, h('div', { class: 'tag', style: 'margin-bottom:6px' }, 'Filament tray'),
        tr.length ? h('div', { class: 'chips' }, tr.map((t) => h('button', { class: 'chip', 'aria-pressed': String(S.tray === trayKey(t)),
          onclick: () => { S.tray = trayKey(t); S.variants = null; draw(); } },
          swatch(t.colour, 22), `AMS ${t.unit + 1}·${t.slot + 1} ${t.type}`, h('small', null, t.name))))
          : h('p', { class: 'muted', style: 'margin:0' }, 'No AMS trays reported for this printer.')),
      S.target && field('Build plate', h('select', { onchange: (e) => (S.bed = e.target.value) }, BEDS.map((b) => h('option', { selected: b === S.bed }, b)))),
      btn(S.busy ? [h('span', { class: 'spin' }), ' Slicing three variants…'] : 'Slice good, better and best', sliceAll, 'primary',
        { disabled: !(S.file && S.target && S.tray) || S.busy }));
  }

  async function sliceAll() {
    const ref = refPrinter();
    S.busy = true; S.pick = null;
    S.variants = QUALITIES.map(([q]) => ({ q, state: 'queued' }));
    draw();
    try {
      await Promise.all(S.variants.map(async (v) => {
        const job = await api.upload(S.file);
        v.id = job.id;
        await api.slice(job.id, { serial: ref.serial, quality: v.q, tray: S.tray, bed: S.bed });
      }));
    } catch (e) { fail(e); S.busy = false; S.variants = null; draw(); return; }
    const tick = async () => {
      if (!box.isConnected) return;
      for (const v of S.variants) if (v.state === 'queued' || v.state === 'slicing') Object.assign(v, await api.job(v.id).catch((e) => ({ state: 'failed', error: e.message })));
      S.busy = S.variants.some((v) => v.state === 'queued' || v.state === 'slicing');
      draw();
      if (S.busy) setTimeout(tick, 1500);
      else if (!S.pick) { const ok = S.variants.find((v) => v.q === 'better' && v.state === 'done'); if (ok) { S.pick = ok.q; draw(); } }
    };
    tick();
  }

  const costOf = (v) => {
    if (v.info?.cost != null) return [v.info.cost, false];
    const sp = spoolOf(trays().find((t) => trayKey(t) === S.tray) || {});
    return sp?.price_per_kg ? [(v.info.grams * sp.price_per_kg) / 1000, true] : [null, false];
  };

  function compareCard() {
    const done = S.variants.filter((v) => v.state === 'done');
    const fastest = done.length > 1 && done.reduce((a, b) => (a.info.seconds <= b.info.seconds ? a : b)).q;
    const cards = S.variants.map((v) => {
      const [, name, sub] = QUALITIES.find((x) => x[0] === v.q);
      if (v.state === 'failed') return h('div', { class: 'variant' }, h('b', null, name), h('small', { style: 'color:var(--err)' }, v.error || 'Slicing failed'));
      if (v.state !== 'done') return h('div', { class: 'variant' }, h('b', null, name), h('small', null, h('span', { class: 'spin' }), ' ', v.state === 'slicing' ? `Slicing… ${v.elapsed || 0} s` : 'Waiting for the slicer'));
      const [cost, est] = costOf(v);
      return h('button', { class: 'variant', 'aria-pressed': String(S.pick === v.q), onclick: () => { S.pick = v.q; draw(); } },
        h('div', { class: 'row between' }, h('b', null, name), fastest === v.q ? pill('fastest', 'ok') : null),
        h('div', { class: 'big' }, durSec(v.info.seconds)),
        h('dl', null, h('dt', null, 'Filament'), h('dd', null, v.info.grams.toFixed(1) + ' g'),
          h('dt', null, est ? 'Cost (filament)' : 'Cost'), h('dd', null, cost == null ? '–' : (est ? '≈ ' : '') + money(cost)),
          h('dt', null, 'Layers'), h('dd', null, (v.settings?.process || '').match(/^[\d.]+mm/)?.[0] || '–')),
        h('small', null, sub));
    });
    return h('section', { class: 'card stack' }, h('h2', { style: 'margin:0' }, '3 · Compare'),
      h('p', { class: 'muted', style: 'margin:0' }, 'Pick the trade-off between quality and speed. Prices use your spool and the printer power estimate.'),
      h('div', { class: 'variants' }, cards));
  }

  function queueCard() {
    const v = S.variants.find((x) => x.q === S.pick);
    const prev = layerPreview();
    api.gcode(v.id).then((t) => prev.load(t)).catch((e) => prev.fail(e.message));
    const opt = (key, label, hint) => h('label', { class: 'check' },
      h('input', { type: 'checkbox', checked: S.options[key], onchange: (e) => (S.options[key] = e.target.checked) }),
      h('span', null, h('b', null, label), h('br'), h('small', null, hint)));
    const warn = (v.info.warnings || []).filter(Boolean);
    return h('section', { class: 'card stack' }, h('h2', { style: 'margin:0' }, '4 · Preview and queue'),
      warn.map((w) => h('div', { class: 'hms warn' }, icon('alert', 20), h('div', { class: 'txt' }, w))),
      h('div', { class: 'preview' }, h('img', { src: api.plateUrl(v.id) + '?v=' + v.id, alt: 'Plate preview' }), prev),
      h('div', { class: 'form two' },
        opt('timelapse', 'Timelapse', 'Record a video of the print'),
        opt('bed_leveling', 'Bed leveling', 'Auto-level before printing'),
        opt('use_ams', 'Use AMS', 'Feed filament from the AMS')),
      btn([icon('plus', 18), `Add ${QUALITIES.find((x) => x[0] === S.pick)[1]} to queue`], async () => {
        const any = S.target.startsWith('any:');
        const r = await attempt(() => api.queueJob(v.id, { target: any ? 'any' : S.target, ...(any ? { model: S.target.slice(4) } : {}), options: S.options }));
        if (!r) return;
        toast(r.state === 'pending_approval' ? 'Added. An admin needs to approve it first.' : 'Added to the queue', 'ok');
        ctx.go('#/queue');
      }, 'primary'));
  }

  function draw() {
    const step = S.pick ? 4 : S.variants ? 3 : S.file ? 2 : 1;
    if (!printers.length) return clear(box, empty('You have no printer access yet. Ask an admin for a grant.'));
    clear(box, stepper(step), fileCard(), S.file && printerCard(), S.variants && compareCard(), S.pick && queueCard());
  }
  draw();
}
