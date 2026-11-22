#!/usr/bin/env python3
"""Convert a sliced .gcode.3mf (OrcaSlicer / Bambu) into plate.json + plate.bin.txt
for the Plate Review viewer.  Usage:
  gcode2plate.py <file.gcode.3mf> <outdir> [--plate-name NAME] [--filament-colour #RRGGBB] [--filament NAME]
Exit codes: 0 ok, 2 viewer limits exceeded / bad input.
"""
import argparse, base64, json, math, os, re, sys, zipfile
import xml.etree.ElementTree as ET
import numpy as np

Q = 100.0
MAX_OBJ = 16
MAX_FEAT = 16
ARC_CHORD = 1.0          # mm, max chord length when approximating G2/G3
ARC_SAGITTA = 0.02       # mm, max deviation of chord from true arc
OVL_CELL = 0.25          # mm raster cell for overlap check
OVL_LAYERS = 3
OVL_HALFWIDTH = 0.21     # half of a 0.42 mm extrusion line
WORD = re.compile(r'([A-Za-z])\s*(-?\d*\.?\d+)')


def die(msg):
    print("ERROR: " + msg, file=sys.stderr)
    sys.exit(2)


def arc_n(angle, r):
    """Number of chords for an arc of |angle| rad and radius r (mm)."""
    n = math.ceil(angle * r / ARC_CHORD)
    if r > ARC_SAGITTA:
        n = max(n, math.ceil(angle / (2 * math.acos(1 - ARC_SAGITTA / r))))
    return max(1, int(n))


def parse_gcode(text):
    """Return (segments, layers). segments: list of tuples
    (x0,y0,x1,y1,objlabel,feature,layer_idx); layers: list of z."""
    x = y = z = 0.0
    e = 0.0
    rel_e = False
    layers = []
    layer = -1
    feature = None
    obj = -1
    segs = []
    pending_z = False
    for raw in text.split('\n'):
        if not raw:
            continue
        if raw[0] == ';':
            if raw.startswith('; CHANGE_LAYER'):
                layer += 1
                pending_z = True
            elif raw.startswith('; Z_HEIGHT:') and pending_z:
                layers.append(float(raw.split(':')[1]))
                pending_z = False
            elif raw.startswith('; FEATURE:'):
                feature = raw.split(':', 1)[1].strip()
            elif raw.startswith('; start printing object, unique label id:'):
                obj = int(raw.rsplit(':', 1)[1])
            elif raw.startswith('; stop printing object'):
                obj = -1
            continue
        line = raw.split(';', 1)[0].strip()
        if not line:
            continue
        cmd = line.split(None, 1)[0].upper()
        if cmd == 'M82':
            rel_e = False; continue
        if cmd == 'M83':
            rel_e = True; continue
        if cmd == 'G92':
            w = dict((a.upper(), float(b)) for a, b in WORD.findall(line[3:]))
            if 'E' in w: e = w['E']
            if 'X' in w: x = w['X']
            if 'Y' in w: y = w['Y']
            if 'Z' in w: z = w['Z']
            continue
        if cmd not in ('G0', 'G1', 'G2', 'G3', 'G00', 'G01', 'G02', 'G03'):
            continue
        w = dict((a.upper(), float(b)) for a, b in WORD.findall(line[len(cmd):]))
        nx = w.get('X', x); ny = w.get('Y', y)
        de = 0.0
        if 'E' in w:
            de = w['E'] if rel_e else w['E'] - e
            e = w['E'] if not rel_e else e + w['E']
        if 'Z' in w:
            z = w['Z']
        extruding = de > 1e-9 and (abs(nx - x) + abs(ny - y)) > 1e-9
        if extruding and layer >= 0 and layer < len(layers) and feature != 'Custom':
            c = int(cmd[1:])
            if c in (0, 1):
                pts = [(x, y), (nx, ny)]
            else:
                cx = x + w.get('I', 0.0); cy = y + w.get('J', 0.0)
                r = math.hypot(x - cx, y - cy)
                a0 = math.atan2(y - cy, x - cx)
                a1 = math.atan2(ny - cy, nx - cx)
                da = a1 - a0
                if c == 2:   # clockwise
                    if da >= -1e-9: da -= 2 * math.pi
                else:        # counter-clockwise
                    if da <= 1e-9: da += 2 * math.pi
                n = arc_n(abs(da), r)
                pts = [(cx + r * math.cos(a0 + da * i / n), cy + r * math.sin(a0 + da * i / n))
                       for i in range(n + 1)]
                pts[-1] = (nx, ny)
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                segs.append((ax, ay, bx, by, obj, feature, layer))
        x, y = nx, ny
    return segs, layers


def read_inputs(path):
    zf = zipfile.ZipFile(path)
    names = zf.namelist()
    def rd(n):
        return zf.read(n).decode('utf-8', 'replace') if n in names else None
    gcode = rd('Metadata/plate_1.gcode')
    if gcode is None:
        die("Metadata/plate_1.gcode not found in " + path)
    info = rd('Metadata/slice_info.config') or ''
    ps = rd('Metadata/project_settings.config')
    settings = json.loads(ps) if ps else {}
    return gcode, info, settings


def first(v):
    return v[0] if isinstance(v, list) and v else v


def overlap_check(segs, layers, labels, names):
    """Raster first OVL_LAYERS layers per object; return (bbox_pairs, raster_pairs)."""
    cells = {}
    for (x0, y0, x1, y1, ob, ft, ly) in segs:
        if ob < 0 or ly >= OVL_LAYERS:
            continue
        L = math.hypot(x1 - x0, y1 - y0)
        n = max(1, int(L / (OVL_CELL / 2)))
        nx_, ny_ = (-(y1 - y0) / L, (x1 - x0) / L) if L > 0 else (0, 0)
        s = cells.setdefault(ob, set())
        for i in range(n + 1):
            t = i / n
            px = x0 + (x1 - x0) * t; py = y0 + (y1 - y0) * t
            for o in (-OVL_HALFWIDTH, 0.0, OVL_HALFWIDTH):
                s.add((int((px + nx_ * o) // OVL_CELL), int((py + ny_ * o) // OVL_CELL)))
    res = []
    keys = sorted(cells)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            shared = len(cells[a] & cells[b])
            if shared:
                res.append((a, b, shared * OVL_CELL * OVL_CELL))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('file'); ap.add_argument('outdir')
    ap.add_argument('--plate-name'); ap.add_argument('--filament-colour'); ap.add_argument('--filament')
    a = ap.parse_args()

    gcode, info, st = read_inputs(a.file)
    segs, layers = parse_gcode(gcode)
    if not segs:
        die("no extruding segments found")

    # object names from slice_info
    nm = {}
    for o in re.findall(r'<object identify_id="(\d+)" name="([^"]*)"', info):
        nm[int(o[0])] = o[1]
    labels = sorted({s[4] for s in segs if s[4] >= 0})
    objects = [{"label": -1, "name": "Brim and purge (no object)"}] + \
              [{"label": l, "name": nm.get(l, "object_%d" % l)} for l in labels]
    if len(objects) > MAX_OBJ:
        die("%d objects (incl. brim/purge) exceed the viewer limit of %d" % (len(objects), MAX_OBJ))
    oidx = {o["label"]: i for i, o in enumerate(objects)}
    features = []
    for s in segs:
        if s[5] not in features:
            features.append(s[5])
    if len(features) > MAX_FEAT:
        die("%d features exceed the viewer limit of %d" % (len(features), MAX_FEAT))
    fidx = {f: i for i, f in enumerate(features)}

    # order is already layer by layer; verify
    N = len(segs)
    arr = np.array([(s[0], s[1], s[2], s[3]) for s in segs])
    co = np.rint(arr * Q)
    if co.min() < 0 or co.max() > 65535:
        die("coordinate outside Uint16 range at q=%g (min %g max %g)" % (Q, co.min() / Q, co.max() / Q))
    co = co.astype('<u2')
    ob = np.array([oidx[s[4]] for s in segs], dtype='u1')
    ft = np.array([fidx[s[5]] for s in segs], dtype='u1')
    lay = np.array([s[6] for s in segs])
    assert (np.diff(lay) >= 0).all()
    counts = np.bincount(lay, minlength=len(layers))
    layer_list = [{"z": layers[i], "n": int(counts[i])} for i in range(len(layers))]

    # bboxes from toolpaths
    zs = np.array(layers)[lay]
    for i, o in enumerate(objects):
        m = ob == i
        if not m.any():
            o["bbox"] = [0, 0, 0, 0, 0, 0]; continue
        xs = np.concatenate([arr[m, 0], arr[m, 2]]); ys = np.concatenate([arr[m, 1], arr[m, 3]])
        o["bbox"] = [round(float(xs.min()), 2), round(float(ys.min()), 2), round(float(zs[m].min()), 2),
                     round(float(xs.max()), 2), round(float(ys.max()), 2), round(float(zs[m].max()), 2)]

    # meta
    hdr = re.search(r'total estimated time:\s*([^\n]+)', gcode[:20000])
    ver = re.search(r'OrcaSlicer-Version" value="([^"]+)"', info)
    wt = re.search(r'key="weight" value="([^"]+)"', info)
    pa = first(st.get('printable_area')) and st.get('printable_area')
    bed = [0.0, 0.0, 256.0, 256.0]
    if pa:
        pts = [tuple(float(v) for v in p.split('x')) for p in pa]
        bed = [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)]
    meta = {
        "plate": a.plate_name or "Plate 1",
        "source": os.path.basename(a.file),
        "slicer": "OrcaSlicer " + (ver.group(1) if ver else "?"),
        "printer": first(st.get('printer_settings_id')) or "?",
        "filament": a.filament or first(st.get('filament_settings_id')) or "?",
        "filament_colour": a.filament_colour or first(st.get('filament_colour')) or "#808080",
        "bed_type": first(st.get('curr_bed_type')) or "?",
        "layer_height": str(first(st.get('layer_height')) or "?"),
        "wall_loops": str(first(st.get('wall_loops')) or "?"),
        "infill": str(first(st.get('sparse_infill_density')) or "?"),
        "time": hdr.group(1).strip() if hdr else "?",
        "grams": wt.group(1) if wt else "?",
        "bed": bed,
    }

    # overlap check
    real = objects[1:]
    name_of = {o["label"]: o["name"] for o in real}
    print("Objects: %d real, %d segments, %d layers" % (len(real), N, len(layers)))
    bb = []
    for i, p in enumerate(real):
        for q in real[i + 1:]:
            ox = min(p["bbox"][3], q["bbox"][3]) - max(p["bbox"][0], q["bbox"][0])
            oy = min(p["bbox"][4], q["bbox"][4]) - max(p["bbox"][1], q["bbox"][1])
            if ox > 0 and oy > 0:
                bb.append((p["name"], q["name"], ox, oy))
    print("BBox XY overlaps (%d):" % len(bb))
    for p, q, ox, oy in bb:
        print("  %s x %s: %.1f x %.1f mm" % (p, q, ox, oy))
    rp = overlap_check(segs, layers, labels, name_of)
    print("Toolpath footprint overlaps, first %d layers, %.2f mm cells (%d):" % (OVL_LAYERS, OVL_CELL, len(rp)))
    for x, y, area in rp:
        print("  %s x %s: %.1f mm2" % (name_of[x], name_of[y], area))
    meta["overlaps"] = "none" if not rp else "; ".join(
        "%s × %s: %.1f mm²" % (name_of[x], name_of[y], ar) for x, y, ar in rp)
    outside = [o["name"] for o in real if o["bbox"][0] < bed[0] or o["bbox"][1] < bed[1]
               or o["bbox"][3] > bed[2] or o["bbox"][4] > bed[3]]
    if outside:
        meta["objects_note"] = "outside bed area: " + ", ".join(outside)
        print("WARNING " + meta["objects_note"])

    plate = {"version": 1, "q": Q, "meta": meta, "objects": objects, "features": features,
             "layers": layer_list, "segments": N,
             "bin": {"file": "plate.bin", "coords": 0, "obj": N * 8, "feat": N * 9}}
    buf = co.tobytes() + ob.tobytes() + ft.tobytes()
    assert len(buf) == N * 10
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, 'plate.json'), 'w') as f:
        json.dump(plate, f, separators=(',', ':'), ensure_ascii=False)
    with open(os.path.join(a.outdir, 'plate.bin.txt'), 'w') as f:
        f.write(base64.b64encode(buf).decode('ascii'))
    print("wrote %s (%d bytes raw bin, %d bytes b64)" % (a.outdir, len(buf), (len(buf) + 2) // 3 * 4))


if __name__ == '__main__':
    main()
