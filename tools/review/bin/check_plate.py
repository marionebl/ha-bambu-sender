#!/usr/bin/env python3
"""Validate plate.json + plate.bin.txt the way the viewer reads them.
Usage: check_plate.py <outdir> [--gcode3mf FILE]   (with --gcode3mf: independent recount from the G-code)
Exit 0 only if every check passes."""
import argparse, base64, json, math, os, re, sys, zipfile
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument('outdir'); ap.add_argument('--gcode3mf')
a = ap.parse_args()
fails = []
def check(ok, msg):
    print(("PASS  " if ok else "FAIL  ") + msg)
    if not ok: fails.append(msg)

P = json.load(open(os.path.join(a.outdir, 'plate.json')))
raw = open(os.path.join(a.outdir, 'plate.bin.txt')).read().strip()
buf = base64.b64decode(raw, validate=True)
N = P['segments']; b = P['bin']; q = P['q']
check('\n' not in open(os.path.join(a.outdir, 'plate.bin.txt')).read().strip(), 'plate.bin.txt is a single line')
check(b['coords'] == 0 and b['obj'] == N * 8 and b['feat'] == N * 9, 'offsets: coords=0 obj=N*8 feat=N*9 (N=%d)' % N)
check(len(buf) == N * 10, 'buffer length %d == N*10' % len(buf))
check(b['coords'] % 2 == 0, 'Uint16 offset aligned')
# viewer-style typed-array views (little endian)
co = np.frombuffer(buf, dtype='<u2', count=N * 4, offset=b['coords']).reshape(N, 4)
ob = np.frombuffer(buf, dtype='u1', count=N, offset=b['obj'])
ft = np.frombuffer(buf, dtype='u1', count=N, offset=b['feat'])
objs = P['objects']; feats = P['features']; layers = P['layers']
check(len(objs) <= 16 and len(feats) <= 16, 'viewer limits: %d objects, %d features (<=16)' % (len(objs), len(feats)))
check(objs[0]['label'] == -1 and objs[0]['name'] == 'Brim and purge (no object)', 'objects[0] is brim/purge')
check(int(ob.max()) < len(objs), 'object indices in range (max %d < %d)' % (ob.max(), len(objs)))
check(int(ft.max()) < len(feats), 'feature indices in range (max %d < %d)' % (ft.max(), len(feats)))
check(sum(l['n'] for l in layers) == N, 'layer sums == N (%d layers)' % len(layers))
zs = np.repeat([l['z'] for l in layers], [l['n'] for l in layers])
mm = co / q
bad = []
print('\nPer-object segments and bbox containment (+-0.5 mm):')
for i, o in enumerate(objs):
    m = ob == i
    c = int(m.sum())
    if c == 0:
        print('  [%2d] %-30s 0 segments' % (i, o['name'])); continue
    x0, y0, z0, x1, y1, z1 = o['bbox']; t = 0.5
    inx = ((mm[m][:, [0, 2]] >= x0 - t) & (mm[m][:, [0, 2]] <= x1 + t)).all()
    iny = ((mm[m][:, [1, 3]] >= y0 - t) & (mm[m][:, [1, 3]] <= y1 + t)).all()
    inz = ((zs[m] >= z0 - 0.01) & (zs[m] <= z1 + 0.01)).all()
    ok = bool(inx and iny and inz)
    if not ok: bad.append(o['name'])
    print('  [%2d] %-30s %7d segs  bbox %s  inside=%s' % (i, o['name'], c, o['bbox'], ok))
check(not bad, 'all objects inside their bbox' + (' (bad: %s)' % bad if bad else ''))

if a.gcode3mf:
    print('\nIndependent recount from G-code (regex scan, arc chords from cross/dot angle):')
    text = zipfile.ZipFile(a.gcode3mf).read('Metadata/plate_1.gcode').decode()
    num = re.compile(r'([XYZEIJ])(-?\d*\.?\d+)')
    X = Y = 0.0
    layer = -1; obj = -1; feat = None; per_layer = []; per_obj = {}
    lines_n = arcs_n = 0
    for ln in text.splitlines():
        if ln.startswith('; CHANGE_LAYER'): layer += 1; per_layer.append(0); continue
        if ln.startswith('; FEATURE:'): feat = ln[10:].strip(); continue
        if ln.startswith('; start printing object, unique label id:'): obj = int(ln.split(':')[-1]); continue
        if ln.startswith('; stop printing object'): obj = -1; continue
        m = re.match(r'(G[0123])\s', ln)
        if not m: continue
        w = {k: float(v) for k, v in num.findall(ln.split(';')[0])}
        nx, ny = w.get('X', X), w.get('Y', Y)
        if w.get('E', 0) > 0 and (nx != X or ny != Y) and layer >= 0 and feat != 'Custom':
            if m.group(1) in ('G0', 'G1'): k = 1; lines_n += 1
            else:
                cx, cy = X + w.get('I', 0), Y + w.get('J', 0); r = math.hypot(X - cx, Y - cy)
                cr = (X - cx) * (ny - cy) - (Y - cy) * (nx - cx); dt = (X - cx) * (nx - cx) + (Y - cy) * (ny - cy)
                ang = math.atan2(cr, dt)
                if m.group(1) == 'G2':  # clockwise: angle must be negative
                    ang = ang if ang < -1e-9 else ang - 2 * math.pi
                else:
                    ang = ang if ang > 1e-9 else ang + 2 * math.pi
                k = max(1, math.ceil(abs(ang) * r / 1.0))
                if r > 0.02: k = max(k, math.ceil(abs(ang) / (2 * math.acos(1 - 0.02 / r))))
                arcs_n += 1
            per_layer[layer] += k; per_obj[obj] = per_obj.get(obj, 0) + k
        X, Y = nx, ny
    tot = sum(per_layer)
    print('  linear extruding moves %d, arcs %d, expanded segments %d, CHANGE_LAYER count %d' % (lines_n, arcs_n, tot, len(per_layer)))
    check(tot == N, 'segment count: viewer %d vs independent %d' % (N, tot))
    check(len(per_layer) == len(layers), 'layer count: viewer %d vs independent %d' % (len(layers), len(per_layer)))
    check(per_layer == [l['n'] for l in layers], 'per-layer segment counts identical')
    lab = {o['label']: i for i, o in enumerate(objs)}
    check(all(per_obj.get(o['label'], 0) == int((ob == i).sum()) for i, o in enumerate(objs)), 'per-object segment counts identical')
    info = zipfile.ZipFile(a.gcode3mf).read('Metadata/slice_info.config').decode()
    nm = {int(i): n for i, n in re.findall(r'<object identify_id="(\d+)" name="([^"]*)"', info)}
    check(all(nm.get(o['label']) == o['name'] for o in objs[1:]), 'object names match slice_info.config')
    check(set(nm) == {o['label'] for o in objs[1:]}, 'object set equals slice_info objects (%d)' % len(nm))

print('\nRESULT: ' + ('ALL CHECKS PASSED' if not fails else '%d CHECK(S) FAILED' % len(fails)))
sys.exit(1 if fails else 0)
