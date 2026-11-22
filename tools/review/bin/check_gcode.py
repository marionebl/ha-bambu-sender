#!/usr/bin/env python3
"""Run the print-path G-code guard on a .gcode.3mf before upload.
Usage: check_gcode.py FILE --printer H2S [--no-ams]   Exit 0 only if no problems.
Loads the module by path: importing the package would pull in Home Assistant."""
import argparse, importlib.util, os, sys

ap = argparse.ArgumentParser()
ap.add_argument("file"); ap.add_argument("--printer", required=True, help="cloud model name, e.g. H2S, P1S")
ap.add_argument("--no-ams", action="store_true", help="file is meant for the external spool")
a = ap.parse_args()
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "custom_components", "bambu_sender", "gcode_guard.py")
spec = importlib.util.spec_from_file_location("gcode_guard", path)
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
problems = mod.check_gcode_3mf(a.file, a.printer, use_ams=not a.no_ams)
for p in problems:
    print("FAIL  " + p)
print("RESULT: " + ("GUARD PASSED" if not problems else "%d PROBLEM(S), the integration would refuse this file" % len(problems)))
sys.exit(1 if problems else 0)
