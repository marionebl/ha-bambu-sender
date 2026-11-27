// Spool list, edit, link to AMS trays.
import { api } from '../api.js';
import { h, clear, icon, btn, field, swatch, empty, modal, attempt, money, pill, hex } from '../util.js';

export async function mount(ctx, root) {
  const list = h('div', { class: 'grid' });
  let printers = [], spools = [];
  const canEdit = (s) => ctx.me.is_admin || (s?.printer && ctx.canManage(s.printer));
  const pname = (serial) => printers.find((p) => p.serial === serial)?.name || serial;
  root.append(h('div', { class: 'page-head' }, h('div', null, h('h1', null, 'Filament'), h('div', { class: 'sub' }, 'Spools, remaining grams and prices')),
    ctx.managesAny() && btn([icon('plus', 18), 'Add spool'], () => edit(null), 'primary')), list);

  async function load() {
    try { [spools, printers] = await Promise.all([api.spools(), api.printers()]); } catch (e) { clear(list, empty(e.message)); return; }
    draw();
  }

  function card(s) {
    const frac = s.grams_total ? s.grams_left / s.grams_total : 0, low = frac < 0.15;
    return h('section', { class: 'card spool' },
      swatch(s.colour, 44),
      h('div', null, h('div', { class: 'row between' }, h('b', null, `${s.brand} ${s.type}`), s.printer ? pill(`${pname(s.printer)} · AMS ${s.unit + 1}·${s.slot + 1}`, 'ok') : pill('not loaded')),
        h('small', null, s.name)),
      h('div', { class: 'bar' + (low ? ' low' : '') }, h('i', { style: `width:${Math.round(frac * 100)}%` })),
      h('div', { class: 'row between', style: 'grid-column:1/-1' }, h('span', null, h('b', null, Math.round(s.grams_left) + ' g'), h('small', null, ` of ${s.grams_total} g`)),
        h('small', null, s.price_per_kg != null ? money(s.price_per_kg) + ' / kg' : 'no price')),
      canEdit(s) && h('div', { class: 'acts' }, btn([icon('edit', 16), 'Edit'], () => edit(s), 'sm'),
        btn([icon('trash', 16), 'Delete'], async () => { if (confirm(`Delete ${s.brand} ${s.type} (${s.name})?`) && await attempt(() => api.deleteSpool(s.id), 'Deleted')) load(); }, 'sm danger')));
  }
  function draw() { clear(list, spools.length ? spools.map(card) : empty('No spools yet.')); }

  function edit(s) {
    return modal(s ? 'Edit spool' : 'Add spool', (close) => {
      const v = s || { brand: '', type: 'PLA', colour: '1D1D1D', name: '', grams_left: 1000, grams_total: 1000, price_per_kg: 20 };
      const f = {
        brand: h('input', { type: 'text', value: v.brand }), type: h('input', { type: 'text', value: v.type }), name: h('input', { type: 'text', value: v.name }),
        colour: h('input', { type: 'color', value: hex(v.colour), style: 'width:100%;height:42px;padding:2px' }),
        left: h('input', { type: 'number', min: 0, value: v.grams_left }), total: h('input', { type: 'number', min: 1, value: v.grams_total }),
        price: h('input', { type: 'number', min: 0, step: '0.01', value: v.price_per_kg ?? '' }),
      };
      const trayOpts = [['', 'Not loaded'], ...printers.flatMap((p) => (p.ams || []).flatMap((u) => u.trays.map((t) =>
        [`${p.serial}|${u.unit}|${t.slot}`, `${p.name} · AMS ${u.unit + 1}·${t.slot + 1}`])))];
      const cur = s?.printer ? `${s.printer}|${s.unit}|${s.slot}` : '';
      const link = h('select', null, trayOpts.map(([val, l]) => h('option', { value: val, selected: val === cur }, l)));
      return h('div', { class: 'body' },
        h('div', { class: 'form two' }, field('Brand', f.brand), field('Material', f.type)),
        h('div', { class: 'form two' }, field('Name', f.name), field('Colour', f.colour)),
        h('div', { class: 'form two' }, field('Grams left', f.left), field('Spool size (g)', f.total)),
        field('Price per kg (EUR)', f.price),
        field('Loaded in', link, 'Links this spool to an AMS tray so prints take their grams off it.'),
        btn('Save', async () => {
          const [printer, unit, slot] = link.value ? link.value.split('|') : [];
          const body = { brand: f.brand.value.trim(), type: f.type.value.trim(), name: f.name.value.trim(), colour: f.colour.value.slice(1).toUpperCase(),
            grams_left: +f.left.value, grams_total: +f.total.value, price_per_kg: f.price.value === '' ? null : +f.price.value,
            printer: printer || null, unit: printer ? +unit : null, slot: printer ? +slot : null };
          if (await attempt(() => (s ? api.saveSpool(s.id, body) : api.addSpool(body)), 'Saved')) { close(true); load(); }
        }, 'primary'));
    });
  }
  load();
}
