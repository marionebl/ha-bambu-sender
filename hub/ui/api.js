// Every network call of the UI lives here. Paths are relative (ingress adds a path prefix).
export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function call(method, path, body, raw) {
  if (path.startsWith('/')) throw new Error('absolute path not allowed: ' + path);
  const init = { method, headers: {} };
  if (body !== undefined) {
    if (raw) init.body = body;
    else { init.body = JSON.stringify(body); init.headers['Content-Type'] = 'application/json'; }
  }
  let r;
  try { r = await fetch(path, init); } catch (e) { throw new ApiError(0, 'Network error: ' + e.message); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(r.status, data.error || 'HTTP ' + r.status);
  return data;
}
const get = (p) => call('GET', p);
const post = (p, b) => call('POST', p, b ?? {});
const put = (p, b) => call('PUT', p, b);
const del = (p) => call('DELETE', p);
const q = encodeURIComponent;

export const api = {
  me: () => get('api/me'),
  // setup
  setup: () => get('api/setup'),
  loginStart: (email, password) => post('api/setup/login/start', { email, password }),
  loginCode: (email, code) => post('api/setup/login/code', { email, code }),
  removeAccount: (id) => del('api/setup/accounts/' + q(id)),
  savePrinter: (serial, body) => put('api/setup/printers/' + q(serial), body),
  removePrinter: (serial) => del('api/setup/printers/' + q(serial)),
  integration: () => get('api/integration'),
  // printers
  printers: () => get('api/printers'),
  startNext: (serial) => post(`api/printers/${q(serial)}/start-next`, { plate_clear: true }),
  startJob: (serial, job_id) => post(`api/printers/${q(serial)}/start`, { job_id, plate_clear: true }),
  // queue
  queue: () => get('api/queue'),
  jobAction: (id, action, body) => post(`api/queue/${q(id)}/${action}`, body),
  // slicing: one uploaded job per slice (the slicer keeps one result per job)
  upload: (file) => call('POST', 'api/jobs?name=' + q(file.name), file, true),
  slice: (id, body) => post(`api/jobs/${q(id)}/slice`, body),
  job: (id) => get(`api/jobs/${q(id)}`),
  plateUrl: (id) => `api/jobs/${q(id)}/plate.png`,
  gcode: async (id) => {
    const r = await fetch(`api/jobs/${q(id)}/plate.gcode`);
    if (!r.ok) throw new ApiError(r.status, 'No G-code preview');
    return r.text();
  },
  queueJob: (id, body) => post(`api/jobs/${q(id)}/queue`, body),
  // filament
  spools: () => get('api/spools'),
  addSpool: (s) => post('api/spools', s),
  saveSpool: (id, s) => put('api/spools/' + q(id), s),
  deleteSpool: (id) => del('api/spools/' + q(id)),
  // access
  grants: () => get('api/grants'),
  addGrant: (g) => post('api/grants', g),
  deleteGrant: (id) => del('api/grants/' + q(id)),
  haUsers: () => get('api/ha/users'),
  haEntities: (domains) => get('api/ha/entities?domains=' + q(domains.join(','))),
};
