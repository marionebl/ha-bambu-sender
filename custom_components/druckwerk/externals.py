"""Identifiers owned by the printer vendor's Home Assistant integration (ha-bambulab): the only place that names them.

The integration ships separately from the app (hub/externals.py has the app's side), so it carries its own copy.
Two literals cannot live here because Home Assistant reads them from static files: `after_dependencies` in manifest.json
and the device manufacturer filter in services.yaml.
"""

VENDOR_DOMAIN = "bambu_lab"  # device and entity registry domain of the vendor integration
VENDOR_MANUFACTURER = "Bambu Lab"  # manufacturer on its devices
GCODE_MODEL_PREFIX = "bambulab"  # `; printer_model = Bambu Lab H2S`, lower-cased with spaces removed
EXAMPLE_GCODE_MODEL = "Bambu Lab H2S"  # a real `; printer_model` value, for tests
