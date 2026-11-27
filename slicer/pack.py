"""Packs G-code sliced in the browser into a minimal .gcode.3mf, the shape the app and Bambu Sender read.

It has the G-code with its md5, slice_info.config (model id, print time, weight) and project_settings.config (the
flattened presets). Bambu Studio's thumbnails and 3D model are not made.
"""
import hashlib
import json
import zipfile
from xml.sax.saxutils import quoteattr

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
    ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
    ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
    ' <Default Extension="gcode" ContentType="text/x.gcode"/>\n'
    '</Types>')
RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
    ' <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
    '</Relationships>')
MODEL = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
    ' <resources/><build/>\n</model>')


def pack(dest, gcode, presets, filament, bed, model_id, tray_colour, seconds, grams, metres, object_name):
    """presets: the app's flattened presets for the printer (machine, process of the quality); filament: its entry in presets/<model>.json (name, type, config)."""
    settings = dict(presets["machine"])
    settings.update(presets["process"])
    settings["curr_bed_type"] = bed
    settings["printer_settings_id"] = presets["machine"]["name"]
    settings["print_settings_id"] = presets["process"]["name"]
    settings["filament_settings_id"] = [filament["name"]]
    settings["filament_colour"] = [tray_colour]
    settings["flattened_presets"] = {"machine": presets["machine"], "process": presets["process"], "filaments": [filament["config"]]}
    info = ('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n <plate>\n'
            '  <metadata key="index" value="1"/>\n'
            '  <metadata key="printer_model_id" value=%s/>\n'
            '  <metadata key="nozzle_diameters" value="0.4"/>\n'
            '  <metadata key="prediction" value="%d"/>\n'
            '  <metadata key="weight" value="%.2f"/>\n'
            '  <object identify_id="1" name=%s skipped="false"/>\n'
            '  <filament id="1" type=%s color=%s used_m="%.2f" used_g="%.2f"/>\n'
            ' </plate>\n</config>\n' % (quoteattr(model_id), seconds, grams, quoteattr(object_name),
                                         quoteattr(filament["type"]), quoteattr(tray_colour), metres, grams))
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("3D/3dmodel.model", MODEL)
        z.writestr("Metadata/plate_1.gcode", gcode)
        z.writestr("Metadata/plate_1.gcode.md5", hashlib.md5(gcode).hexdigest())
        z.writestr("Metadata/slice_info.config", info)
        z.writestr("Metadata/project_settings.config", json.dumps(settings, indent=1))
