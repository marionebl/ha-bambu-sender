// Pieces shared by several screens.
import { h, icon, pill, swatch, pct, temp, clock, dur, hex } from '../util.js';

const STATUS = {
  running: ['Printing', 'ok'], pause: ['Paused', 'warn'], idle: ['Idle', ''], finish: ['Finished', 'ok'],
  failed: ['Failed', 'err'], offline: ['Offline', 'err'], prepare: ['Preparing', 'warn'],
};
export function statusPill(p) {
  const [label, cls] = !p.online ? STATUS.offline : STATUS[p.status] || [p.status || 'Unknown', ''];
  return pill(label, cls);
}
export const isPrinting = (p) => p.online && (p.status === 'running' || p.status === 'pause');

export function hmsBanners(list) {
  return (list || []).map((m) => h('div', { class: 'hms' + (m.severity === 'fatal' || m.severity === 'serious' ? '' : ' warn'), role: 'alert' },
    icon('alert', 22),
    h('div', { class: 'txt' },
      h('span', null, h('code', null, m.code), ' · ', m.severity),
      h('span', null, m.text),
      m.url && h('a', { href: m.url, target: '_blank', rel: 'noopener noreferrer' }, 'What does this mean?'))));
}

export function trayView(t, active) {
  return h('div', { class: 'tray' + (active ? ' active' : ''), title: t.name || t.type, 'aria-current': active ? 'true' : null },
    swatch(t.colour, 30), h('span', { class: 'ty' }, t.type || 'Empty'), h('span', { class: 'pc' }, t.type ? pct(t.remain_pct) : ''));
}

// AMS units as rows of round swatches; the active tray is highlighted.
export function amsBlock(p, compact) {
  const act = p.active_tray;
  if (compact) {
    const all = (p.ams || []).flatMap((u) => u.trays.map((t) => trayView(t, act && act.unit === u.unit && act.slot === t.slot)));
    if (p.external) all.push(trayView({ ...p.external, remain_pct: -1 }, !act));
    return h('div', { class: 'trays flat' }, all);
  }
  const units = (p.ams || []).map((u) => h('div', { class: 'unit' },
    !compact && h('div', { class: 'row between' }, h('b', null, 'AMS ' + (u.unit + 1)),
      h('small', null, `Humidity ${u.humidity_index ?? '–'}/5 · ${temp(u.temp)}C`)),
    h('div', { class: 'trays' }, u.trays.map((t) => trayView(t, act && act.unit === u.unit && act.slot === t.slot)))));
  if (p.external) {
    units.push(h('div', { class: 'unit' }, !compact && h('b', null, 'External spool'),
      h('div', { class: 'trays' }, trayView({ ...p.external, remain_pct: -1 }, !act && p.external.type))));
  }
  return h('div', { class: 'ams' }, units);
}

export function temps(p) {
  const t = (label, v, target) => h('div', { class: 'temp' }, h('small', null, label),
    h('b', null, temp(v)), h('small', null, target != null ? 'target ' + temp(target) : ' '));
  return h('div', { class: 'temps' }, t('Nozzle', p.nozzle_temp, p.nozzle_target), t('Bed', p.bed_temp, p.bed_target),
    t('Chamber', p.chamber_temp, null));
}

export const timeLeft = (p) => (p.remaining_min != null ? `${dur(p.remaining_min)} left` : '');
export const finishAt = (p) => (p.end_time ? `done ${clock(p.end_time)}` : '');
export { hex };
