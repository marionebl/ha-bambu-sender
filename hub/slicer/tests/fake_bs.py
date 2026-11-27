"""Test stand-in for bs.py: answers filament-for without the slicer's profiles."""
import sys
if sys.argv[1] == "filament-for":
    print("Acme PETG HF @BBL H2S" if sys.argv[4] == "GFG02" else "Generic %s @BBL H2S" % sys.argv[5])
