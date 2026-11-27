// Flatten H2S / P1S presets from a Muon3D profile set into presets.json for slice.mjs.
// usage: node flatten.mjs <engine repo checkout> <profiles/<set> dir> <h2s|p1s> <out.json>
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import { pathToFileURL } from 'node:url';

const [repo, setDir, which, out] = process.argv.slice(2);
const { flattenPreset } = await import(pathToFileURL(path.join(repo, 'packages/protocol/src/profileSet.ts')).href);

const WANT = {
  h2s: {
    machine: ['Bambu Lab H2S 0.4 nozzle'],
    process: ['0.20mm Standard @BBL H2S'],
    filament: ['Bambu PETG HF @BBL H2S', 'Bambu PETG HF @BBL H2S 0.4 nozzle', 'Generic PETG @BBL H2S'],
    bed: 'High Temp Plate',
  },
  p1s: {
    machine: ['Bambu Lab P1S 0.4 nozzle'],
    process: ['0.20mm Standard @BBL X1C'],
    filament: ['Generic PETG @BBL P1S', 'Generic PETG @BBL X1C', 'Generic PETG'],
    bed: 'Textured PEI Plate',
  },
}[which];

const readJson = (f) => {
  let b = fs.readFileSync(f);
  if (b[0] === 0x1f && b[1] === 0x8b) b = zlib.gunzipSync(b);
  return JSON.parse(b.toString('utf8'));
};
const first = (re) => {
  const f = fs.readdirSync(setDir).find((n) => re.test(n));
  if (!f) throw new Error(`no ${re} in ${setDir}: ${fs.readdirSync(setDir)}`);
  return path.join(setDir, f);
};
const index = readJson(first(/^index\..*\.json$/));
console.log(`profile set ${index.set}, engine ${index.engine.version}, Orca ${index.engine.orca.version}`);
const bundleOf = (id) => {
  const v = index.vendors.find((x) => x.id === id);
  return readJson(path.join(setDir, v.bundle.path));
};
const vendor = bundleOf('BBL');
const library = index.vendors.some((v) => v.id === index.library) ? bundleOf(index.library) : null;
const sources = { vendor, library, defaults: index.defaults };

const names = (t) => Object.keys(vendor.presets[t]);
const libNames = library ? Object.keys(library.presets.filament) : [];
const keyword = which === 'h2s' ? /H2S/ : /P1S|X1C/;
console.log('machines:', names('machine').filter((n) => keyword.test(n)).join(' | '));
console.log('processes (0.20 Standard):', names('process').filter((n) => keyword.test(n) && /0\.20mm Standard/.test(n)).join(' | '));
console.log('PETG filaments:', [...names('filament'), ...libNames].filter((n) => /PETG/.test(n) && (which === 'h2s' ? /H2S/.test(n) : /P1S|X1C|Generic/.test(n))).join(' | '));

const pick = (type, cands) => {
  for (const n of cands) {
    try { return flattenPreset(type, n, sources); } catch { /* try next */ }
  }
  throw new Error(`FAIL: no ${type} preset resolves from ${JSON.stringify(cands)}`);
};
const machine = pick('machine', WANT.machine);
const process_ = pick('process', WANT.process);
const filament = pick('filament', WANT.filament);
console.log(`USED machine="${machine.name}" process="${process_.name}" filament="${filament.name}"`);
// curr_bed_type is not in the presets; the CLI default (Cool Plate) refuses PETG, so set it on both.
machine.curr_bed_type = process_.curr_bed_type = WANT.bed;
console.log(`curr_bed_type set to ${WANT.bed} (machine and process)`);
fs.writeFileSync(out, JSON.stringify({ machine, process: process_, filaments: [filament] }));
