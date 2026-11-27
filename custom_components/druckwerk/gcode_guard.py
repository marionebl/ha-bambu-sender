"""Refuse sliced files that would make a printer "print air".

Slicer independent: it reads only the executed G-code and the `; printer_model`
header that the supported slicers write. Standard library only.

Background: a file sliced with a generic start G-code (profile `include` ignored)
never loaded filament from the AMS and ran an hour of toolpaths with a dry nozzle.
Every real start G-code of these printers (slicer v02.09.01.70 and OrcaSlicer v2.5.2,
H2S and P1S) has the `M1002 gcode_claim_action` stage markers and the AMS load
`M620 S<k>A` / `T<k>` / `M621 S<k>A`.
"""

from __future__ import annotations

import re
import zipfile

from .externals import GCODE_MODEL_PREFIX

_PLATE = re.compile(r"Metadata/plate_(\d+)\.gcode")
_MODEL = re.compile(r"^;\s*printer_model\s*=\s*(.*?)\s*$")
_E = re.compile(r"\bE(-?\d*\.?\d+)")
_S = re.compile(r"\bS(-?\d*\.?\d+)")
_LOAD = re.compile(r"^M620\s+S(\d+)A\b")
_UNLOAD_DONE = re.compile(r"^M621\s+S(\d+)A\b")
_TOOL = re.compile(r"^T(\d+)\b")
_MOVE = re.compile(r"^G[0-3]\b")
_EXTERNAL_SPOOL = 255  # the printer's slot number for "no AMS"


def _norm(model: str) -> str:
    """The vendor-prefixed model ('<vendor> H2S') and 'H2S' both become 'h2s'."""
    m = re.sub(r"[^a-z0-9]", "", model.lower())
    return m.removeprefix(GCODE_MODEL_PREFIX)


def check_gcode_3mf(path: str, printer_model: str | None, use_ams: bool = True) -> list[str]:
    """Return problems found in the first plate of a .gcode.3mf (empty list = safe).

    printer_model is the cloud name of the target printer ('H2S', 'P1S').
    Rule for use_ams: True requires the AMS load before the first extrusion; False
    (external spool, no AMS) skips only that load check.
    """
    try:
        with zipfile.ZipFile(path) as z:
            plates = sorted(
                (n for n in z.namelist() if _PLATE.fullmatch(n)),
                key=lambda n: int(_PLATE.fullmatch(n).group(1)),
            )
            if not plates:
                return ["The 3MF has no sliced plate (Metadata/plate_N.gcode)."]
            with z.open(plates[0]) as fh:
                return _check_lines(
                    (raw.decode("utf-8", "replace") for raw in fh), printer_model, use_ams
                )
    except (zipfile.BadZipFile, OSError) as err:
        return [f"Cannot read the file as a 3MF: {err}"]


def _check_lines(lines, printer_model, use_ams) -> list[str]:
    problems: list[str] = []
    file_model = None
    executing = False
    absolute_e = True  # G-code default until M83
    last_e = 0.0
    nozzle = 0.0
    claims = 0
    started: set[int] = set()  # M620 S<k>A seen
    tooled: set[int] = set()  # ... then T<k>
    loaded = False  # ... then M621 S<k>A, with k < 255
    heated = False
    first_extrusion = False

    for raw in lines:
        if not executing:
            m = _MODEL.match(raw)
            if m and file_model is None:
                file_model = m.group(1)
            if raw.startswith("; EXECUTABLE_BLOCK_START"):
                executing = True
            continue
        code = raw.split(";", 1)[0].strip()
        if not code:
            continue
        if code.startswith("M1002") and "gcode_claim_action" in code:
            claims += 1
        elif code.startswith("M82"):
            absolute_e = True
        elif code.startswith("M83"):
            absolute_e = False
        elif code.startswith("G92"):
            e = _E.search(code)
            if e:
                last_e = float(e.group(1))
        elif code.startswith(("M104", "M109")):
            s = _S.search(code)
            if s:
                nozzle = float(s.group(1))
        elif m := _LOAD.match(code):
            started.add(int(m.group(1)))
        elif m := _TOOL.match(code):
            if int(m.group(1)) in started:
                tooled.add(int(m.group(1)))
        elif m := _UNLOAD_DONE.match(code):
            k = int(m.group(1))
            if k in tooled and k < _EXTERNAL_SPOOL:
                loaded = True
        elif _MOVE.match(code):
            e = _E.search(code)
            if e:
                value = float(e.group(1))
                extruding = value > (last_e if absolute_e else 0.0)
                if absolute_e:
                    last_e = value
                if extruding:
                    first_extrusion = True
                    heated = nozzle > 0
                    break

    if file_model is None:
        problems.append("The G-code header has no printer_model line.")
    elif not printer_model:
        problems.append("The target printer model is unknown, cannot compare it with the file.")
    elif _norm(file_model) != _norm(printer_model):
        problems.append(
            f"File is sliced for '{file_model}' but the target printer is '{printer_model}'."
        )
    if not executing:
        problems.append("The G-code has no EXECUTABLE_BLOCK_START marker.")
        return problems
    if not first_extrusion:
        problems.append("The G-code has no extruding move.")
        return problems
    if use_ams and not loaded:
        problems.append(
            "No AMS filament load (M620 S<n>A, T<n>, M621 S<n>A with n < 255) before the first extrusion."
        )
    if claims == 0:
        problems.append(
            "The start G-code has no 'M1002 gcode_claim_action' stage markers, so it is not a supported start G-code."
        )
    if not heated:
        problems.append("The nozzle is not heated (M104/M109 with S > 0) before the first extrusion.")
    return problems
