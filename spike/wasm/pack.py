#!/usr/bin/env python3
"""Pack engine G-code + flattened presets into a minimal .gcode.3mf.
usage: pack.py <in.gcode> <presets.json> <model_id> <out.gcode.3mf>"""
import hashlib, json, sys, zipfile

gcode_path, presets_path, model_id, out = sys.argv[1:5]
gcode = open(gcode_path, "rb").read()
p = json.load(open(presets_path))
settings = dict(p["machine"]); settings.update(p["process"])
settings["filament_settings_id"] = [f["name"] for f in p["filaments"]]
settings["flattened_presets"] = {"machine": p["machine"], "process": p["process"], "filaments": p["filaments"]}

ct = ('<?xml version="1.0" encoding="UTF-8"?>\n'
      '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
      ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
      ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
      ' <Default Extension="gcode" ContentType="text/x.gcode"/>\n'
      '</Types>')
rels = ('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        ' <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
        '</Relationships>')
model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
         '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
         ' <resources/><build/>\n</model>')
info = ('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n <plate>\n'
        '  <metadata key="index" value="1"/>\n'
        '  <metadata key="printer_model_id" value="%s"/>\n'
        '  <metadata key="nozzle_diameters" value="0.4"/>\n'
        ' </plate>\n</config>\n' % model_id)

with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("[Content_Types].xml", ct)
    z.writestr("_rels/.rels", rels)
    z.writestr("3D/3dmodel.model", model)
    z.writestr("Metadata/plate_1.gcode", gcode)
    z.writestr("Metadata/plate_1.gcode.md5", hashlib.md5(gcode).hexdigest())
    z.writestr("Metadata/slice_info.config", info)
    z.writestr("Metadata/project_settings.config", json.dumps(settings, indent=1))
print("packed", out)
