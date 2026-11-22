#!/usr/bin/env python3
"""Extract plate thumbnail from .gcode.3mf and print slice_info summary."""
import sys, zipfile, re, shutil
src, out = sys.argv[1:3]
z = zipfile.ZipFile(src)
png = next((n for n in ("Metadata/plate_1.png", "Metadata/plate_no_light_1.png", "Metadata/top_1.png") if n in z.namelist()), None)
if png:
    open(out, "wb").write(z.read(png)); print("thumbnail:", png, "->", out)
else:
    sys.exit("no thumbnail in 3mf (render from G-code not implemented)")
si = z.read("Metadata/slice_info.config").decode()
g = lambda k: (re.search(r'key="%s" value="([^"]*)"' % k, si) or [None, "?"])[1]
print("printer_model_id:", g("printer_model_id"))
s = int(g("prediction")); print("estimated time: %dh %02dm %02ds (%ds)" % (s//3600, s%3600//60, s%60, s))
print("total weight g:", g("weight"))
for f in re.finditer(r'<filament id="(\d+)"[^>]*?type="([^"]*)" color="([^"]*)" used_m="([^"]*)" used_g="([^"]*)"', si):
    print("slot %s: %s %s %s m %s g" % (f.group(1), f.group(2), f.group(3), f.group(4), f.group(5)))
