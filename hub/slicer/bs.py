#!/usr/bin/env python3
"""Helpers for the slicer CLI, which only takes fully merged profile JSON.
  bs.py flatten <resources/profiles dir> <machine|process|filament> "<name>" <out.json>
  bs.py set <profile.json> <key> <json-value>
  bs.py fixup <file.gcode.3mf> <model_id>   # CLI leaves printer_model_id empty
  bs.py preview <file.gcode.3mf>            # adds Metadata/plate_1.png, which the CLI renders only with Wayland
  bs.py filament-for <profiles dir> "<printer>" <tray_info_idx> <tray_type>   # AMS tray -> filament profile name
Merge order is the slicer's PresetBundle: `inherits` parent, then each `include`, then own keys."""
import json, math, os, re, struct, sys, zipfile, zlib
import xml.etree.ElementTree as ET
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [_HERE, os.path.dirname(_HERE)]  # python -I drops the script dir; the repo keeps externals.py one level up
import externals  # noqa: E402

def index(root):
    b = json.load(open(os.path.join(root, externals.PROFILE_ROOT_FILE)))
    return {e["name"]: e["sub_path"] for k in ("machine_list", "process_list", "filament_list") for e in b[k]}

def merged(root, idx, name):
    cur = json.load(open(os.path.join(root, externals.PROFILE_DIR, idx[name])))
    out = merged(root, idx, cur["inherits"]) if cur.get("inherits") else {}
    inc = cur.get("include") or []
    for i in ([inc] if isinstance(inc, str) else inc):
        out.update({k: v for k, v in merged(root, idx, i).items()
                    if k not in ("name", "inherits", "include", "instantiation", "type", "setting_id", "filament_id")})
    out.update({k: v for k, v in cur.items() if k not in ("inherits", "include")})
    return out

def flatten(root, kind, name, dest):
    idx = index(root)
    if name not in idx:
        sys.exit("FAIL: no %s profile named %r" % (kind, name))
    d = merged(root, idx, name)
    if d.get("type") != kind:
        sys.exit("FAIL: %r is a %s profile, not %s" % (name, d.get("type"), kind))
    d.update(name=name, instantiation="true", **{"from": "system"})
    json.dump(d, open(dest, "w"), indent=1)

def fixup(path, model_id):
    with zipfile.ZipFile(path) as z:
        items = [(i, z.read(i.filename)) for i in z.infolist()]
    with zipfile.ZipFile(path + ".tmp", "w", zipfile.ZIP_DEFLATED) as z:
        for i, data in items:
            if i.filename == "Metadata/slice_info.config":
                data = re.sub(rb'(key="printer_model_id" value=")[^"]*', rb"\g<1>" + model_id.encode(), data)
            z.writestr(i.filename, data)
    os.replace(path + ".tmp", path)

NS = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02",
      "p": "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"}

def _mat(t):
    v = [float(x) for x in (t or "1 0 0 0 1 0 0 0 1 0 0 0").split()]
    return [v[0:3], v[3:6], v[6:9], v[9:12]]  # 3MF: row vector times 3x4

def _apply(m, p):
    return tuple(p[0] * m[0][i] + p[1] * m[1][i] + p[2] * m[2][i] + m[3][i] for i in range(3))

def _mul(a, b):  # a then b
    return [[sum(a[r][k] * b[k][c] for k in range(3)) + (b[3][c] if r == 3 else 0) for c in range(3)] for r in range(4)]

def _triangles(z):
    """World-space triangles of every build item (the vendor's components layout)."""
    root = ET.fromstring(z.read("3D/3dmodel.model"))
    meshes = {}
    def mesh(path, oid):
        if (path, oid) not in meshes:
            r = ET.fromstring(z.read(path.lstrip("/")))
            for o in r.iterfind("m:resources/m:object", NS):
                vs = [(float(v.get("x")), float(v.get("y")), float(v.get("z"))) for v in o.iterfind("m:mesh/m:vertices/m:vertex", NS)]
                ts = [(int(t.get("v1")), int(t.get("v2")), int(t.get("v3"))) for t in o.iterfind("m:mesh/m:triangles/m:triangle", NS)]
                meshes[(path, o.get("id"))] = (vs, ts)
        return meshes.get((path, oid), ([], []))
    objs = {o.get("id"): o for o in root.iterfind("m:resources/m:object", NS)}
    for item in root.iterfind("m:build/m:item", NS):
        top, mi = objs[item.get("objectid")], _mat(item.get("transform"))
        parts = [(c.get("{%s}path" % NS["p"]), c.get("objectid"), _mat(c.get("transform"))) for c in top.iterfind("m:components/m:component", NS)]
        for path, oid, mc in parts:
            vs, ts = mesh(path, oid)
            m = _mul(mc, mi)
            w = [_apply(m, v) for v in vs]
            for a, b, c in ts:
                yield w[a], w[b], w[c]

def _png(w, h, rgb):
    raw = b"".join(b"\0" + bytes(rgb[y * w * 3:(y + 1) * w * 3]) for y in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")

def render(z, size=512):
    """Isometric flat-shaded view of the plate's objects, filament colour on grey."""
    cfg = json.loads(z.read("Metadata/project_settings.config"))
    col = (cfg.get("filament_colour") or ["#C8C8C8"])[0].lstrip("#")[:6]
    base = [int(col[i:i + 2], 16) for i in (0, 2, 4)]
    az, el = math.radians(45), math.radians(35)
    def view(p):  # x right, y up, d toward the camera
        x, y = p[0] * math.cos(az) - p[1] * math.sin(az), p[0] * math.sin(az) + p[1] * math.cos(az)
        return x, p[2] * math.cos(el) + y * math.sin(el), p[2] * math.sin(el) - y * math.cos(el)
    tris = [tuple(view(p) for p in t) for t in _triangles(z)]
    if not tris:
        return None
    xs = [p[0] for t in tris for p in t]; ys = [p[1] for t in tris for p in t]
    k = 0.9 * size / max(max(xs) - min(xs), max(ys) - min(ys), 1e-6)
    ox, oy = (size - k * (max(xs) - min(xs))) / 2 - k * min(xs), (size - k * (max(ys) - min(ys))) / 2 - k * min(ys)
    rgb = bytearray([235, 235, 235] * size * size); depth = [-1e18] * (size * size)
    light = (0.3, 0.5, 0.8)
    for t in tris:
        (x0, y0, d0), (x1, y1, d1), (x2, y2, d2) = [(ox + k * p[0], size - (oy + k * p[1]), p[2]) for p in t]
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if abs(area) < 1e-9:
            continue
        u = [t[1][i] - t[0][i] for i in range(3)]; v = [t[2][i] - t[0][i] for i in range(3)]
        n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        ln = math.sqrt(sum(c * c for c in n)) or 1
        shade = 0.35 + 0.65 * abs(sum(n[i] * light[i] for i in range(3)) / ln)
        c = bytes(min(255, int(b * shade)) for b in base)
        for py in range(max(0, int(min(y0, y1, y2))), min(size, int(max(y0, y1, y2)) + 1)):
            for px in range(max(0, int(min(x0, x1, x2))), min(size, int(max(x0, x1, x2)) + 1)):
                cx, cy = px + 0.5, py + 0.5
                w0 = ((x1 - cx) * (y2 - cy) - (x2 - cx) * (y1 - cy)) / area
                w1 = ((x2 - cx) * (y0 - cy) - (x0 - cx) * (y2 - cy)) / area
                w2 = 1 - w0 - w1
                if w0 < 0 or w1 < 0 or w2 < 0:
                    continue
                d, i = w0 * d0 + w1 * d1 + w2 * d2, py * size + px
                if d > depth[i]:
                    depth[i] = d; rgb[i * 3:i * 3 + 3] = c
    return _png(size, size, rgb)

def preview(path):
    with zipfile.ZipFile(path) as z:
        if "Metadata/plate_1.png" in z.namelist():
            return
        png = render(z)
    if png:
        with zipfile.ZipFile(path, "a", zipfile.ZIP_DEFLATED) as z:
            z.writestr("Metadata/plate_1.png", png)

def filament_for(root, printer, idx, ftype):
    """The system filament profile for an AMS tray: same filament_id, else Generic of the same type."""
    b = json.load(open(os.path.join(root, externals.PROFILE_ROOT_FILE)))
    sub = {e["name"]: e["sub_path"] for e in b["filament_list"]}
    raw = {}
    def load(name):
        if name not in raw:
            raw[name] = json.load(open(os.path.join(root, externals.PROFILE_DIR, sub[name]))) if name in sub else {}
        return raw[name]
    def field(name, key):  # first value up the inherits chain
        while name:
            d = load(name)
            if key in d:
                return d[key]
            name = d.get("inherits")
    best = {}
    for name in sub:
        d = load(name)
        if d.get("instantiation") != "true" or printer not in (d.get("compatible_printers") or []):
            continue
        t = field(name, "filament_type")
        t = t[0] if isinstance(t, list) else t
        rank = 0 if idx and field(name, "filament_id") == idx else 1 if name.startswith("Generic ") and t == ftype else None
        if rank is not None:  # fewest compatible printers = the profile made for this printer
            best.setdefault((rank, len(d["compatible_printers"]), name), name)
    if not best:
        sys.exit("FAIL: no filament profile for %r (%s, %s)" % (printer, idx, ftype))
    return best[min(best)]

def put(path, key, value):
    d = json.load(open(path))
    d[key] = json.loads(value)
    json.dump(d, open(path, "w"), indent=1)

if __name__ == "__main__":
    a = sys.argv[1:]
    {"flatten": lambda: flatten(*a[1:5]), "set": lambda: put(*a[1:4]), "fixup": lambda: fixup(*a[1:3]),
     "preview": lambda: preview(a[1]), "filament-for": lambda: print(filament_for(*a[1:5]))}[a[0]]()
