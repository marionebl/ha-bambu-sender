#!/usr/bin/env python3
"""Helpers for the Bambu Studio CLI, which only takes fully merged profile JSON.
  bs.py flatten <resources/profiles dir> <machine|process|filament> "<name>" <out.json>
  bs.py set <profile.json> <key> <json-value>
  bs.py fixup <file.gcode.3mf> <model_id>   # CLI leaves printer_model_id empty
Merge order is Bambu Studio's PresetBundle: `inherits` parent, then each `include`, then own keys."""
import json, os, re, sys, zipfile

def index(root):
    b = json.load(open(os.path.join(root, "BBL.json")))
    return {e["name"]: e["sub_path"] for k in ("machine_list", "process_list", "filament_list") for e in b[k]}

def merged(root, idx, name):
    cur = json.load(open(os.path.join(root, "BBL", idx[name])))
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

def put(path, key, value):
    d = json.load(open(path))
    d[key] = json.loads(value)
    json.dump(d, open(path, "w"), indent=1)

if __name__ == "__main__":
    a = sys.argv[1:]
    {"flatten": lambda: flatten(*a[1:5]), "set": lambda: put(*a[1:4]), "fixup": lambda: fixup(*a[1:3])}[a[0]]()
