#!/usr/bin/env python3
"""Validate a sliced .gcode.3mf for Bambu cloud print; fill printer_model_id if empty.
usage: postprocess.py <file.gcode.3mf> <expected_model_id e.g. O1S>"""
import sys, zipfile, hashlib, re, shutil, os

path, model_id = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(path) as z:
    names = z.namelist()
    data = {n: z.read(n) for n in names}
    infos = {i.filename: i for i in z.infolist()}

def fail(msg):
    sys.exit("FAIL: " + msg)

for need in ("Metadata/plate_1.gcode", "Metadata/plate_1.gcode.md5",
             "Metadata/slice_info.config", "Metadata/project_settings.config"):
    if need not in data:
        fail("missing " + need)

orig_si = data["Metadata/slice_info.config"]
si = orig_si.decode()
if "<object " not in si:
    # With --arrange 0 the CLI writes no <object> list; the printer needs it to skip objects.
    g = data["Metadata/plate_1.gcode"].decode("utf-8", "replace")
    objs, cur = {}, None
    for m in re.finditer(r"^; (?:printing object (.+?) id:\d+ copy \d+|start printing object, unique label id: (\d+))$", g, re.M):
        if m.group(1):
            cur = m.group(1)
        elif cur:
            objs.setdefault(m.group(2), cur); cur = None
    if not objs:
        fail("slice_info.config has no objects and the G-code has no object labels")
    rows = "".join('    <object identify_id="%s" name="%s" skipped="false" />\n' % (l, n) for l, n in objs.items())
    si = re.sub(r"(    <filament )", rows.replace("\\", "\\\\") + r"\1", si, count=1)
    data["Metadata/slice_info.config"] = si.encode()
    print("added %d objects to slice_info.config from G-code labels" % len(objs))
m = re.search(r'key="printer_model_id" value="([^"]*)"', si)
if not m:
    fail("slice_info.config has no printer_model_id")
if m.group(1) == "":
    # CLI cannot resolve the model id from flat profiles; set it from the machine model file
    si = si.replace(m.group(0), 'key="printer_model_id" value="%s"' % model_id)
    data["Metadata/slice_info.config"] = si.encode()
    print("patched printer_model_id -> " + model_id)
elif m.group(1) != model_id:
    fail("printer_model_id %r != %r" % (m.group(1), model_id))

if data["Metadata/slice_info.config"] != orig_si:
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        for n in names:
            out.writestr(n, data[n])
    os.replace(tmp, path)

md5 = hashlib.md5(data["Metadata/plate_1.gcode"]).hexdigest()
if md5.lower() != data["Metadata/plate_1.gcode.md5"].decode().strip().lower():
    fail("gcode md5 mismatch")
if b"Bambu Lab H2S" not in data["Metadata/project_settings.config"]:
    fail("project_settings.config does not name H2S")
print("OK: gcode+md5+slice_info+project_settings present, printer_model_id=" + model_id)
