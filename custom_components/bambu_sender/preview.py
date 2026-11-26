"""Plate preview for a sliced .gcode.3mf (no Home Assistant imports, blocking)."""

from __future__ import annotations

import io
import logging
import re
import zipfile

_LOGGER = logging.getLogger(__name__)

_PLATE_GCODE = re.compile(r"Metadata/plate_(\d+)\.gcode")
_WORD = re.compile(r"([XYZEIJ])\s*(-?\d*\.?\d+)")
_SIZE = 640
_PAD = 24
_MAX_GCODE = 200 * 1024 * 1024


def plate_png(path: str) -> bytes | None:
    """The slicer's own preview of the first sliced plate, else one rendered from its G-code."""
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            plates = sorted(int(m.group(1)) for n in names if (m := _PLATE_GCODE.fullmatch(n)))
            if not plates:
                _LOGGER.warning("No sliced plate in %s (entries: %s)", path, sorted(names)[:20])
                return None
            for name in ("plate_{}.png", "plate_no_light_{}.png", "top_{}.png"):
                name = "Metadata/" + name.format(plates[0])
                if name in names:
                    return z.read(name)
            info = z.getinfo(f"Metadata/plate_{plates[0]}.gcode")
            if info.file_size > _MAX_GCODE:
                _LOGGER.warning("G-code in %s is too big to render (%d bytes)", path, info.file_size)
                return None
            _LOGGER.info("No preview image in %s, rendering it from the G-code", path)
            return render_gcode(z.read(info).decode("utf-8", "replace"))
    except (zipfile.BadZipFile, OSError, KeyError) as err:
        _LOGGER.warning("Cannot read the plate preview from %s: %s", path, err)
    except Exception:  # noqa: BLE001 - a broken render must not take the entity down
        _LOGGER.exception("Cannot render the plate preview from %s", path)
    return None


def _segments(text: str):
    """Extruding XY moves as (x0, y0, x1, y1, layer), arcs as chords."""
    x = y = e = 0.0
    rel_e = False
    layer = -1
    segs = []
    for raw in text.split("\n"):
        if not raw:
            continue
        if raw[0] == ";":
            if raw.startswith("; CHANGE_LAYER"):
                layer += 1
            continue
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        cmd = line.split(None, 1)[0].upper()
        if cmd == "M82":
            rel_e = False
            continue
        if cmd == "M83":
            rel_e = True
            continue
        if cmd == "G92":
            w = {a: float(b) for a, b in _WORD.findall(line[3:].upper())}
            x, y, e = w.get("X", x), w.get("Y", y), w.get("E", e)
            continue
        if cmd not in ("G0", "G1", "G00", "G01", "G2", "G3", "G02", "G03"):
            continue
        w = {a: float(b) for a, b in _WORD.findall(line[len(cmd):].upper())}
        nx, ny = w.get("X", x), w.get("Y", y)
        de = 0.0
        if "E" in w:
            de = w["E"] if rel_e else w["E"] - e
            e = e + w["E"] if rel_e else w["E"]
        if de > 1e-9 and layer >= 0 and abs(nx - x) + abs(ny - y) > 1e-9:
            segs.append((x, y, nx, ny, layer))
        x, y = nx, ny
    return segs


def render_gcode(text: str) -> bytes | None:
    """Top view of all extruded paths, colour by layer height, as PNG."""
    from PIL import Image, ImageDraw

    segs = _segments(text)
    if not segs:
        return None
    xs = [v for s in segs for v in (s[0], s[2])]
    ys = [v for s in segs for v in (s[1], s[3])]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    span = max(x1 - x0, y1 - y0, 1.0)
    scale = (_SIZE - 2 * _PAD) / span
    top = max(s[4] for s in segs) or 1
    img = Image.new("RGB", (_SIZE, _SIZE), (30, 30, 34))
    draw = ImageDraw.Draw(img)
    ox = (_SIZE - (x1 - x0) * scale) / 2
    oy = (_SIZE + (y1 - y0) * scale) / 2  # G-code Y points up
    width = max(1, round(0.42 * scale))
    for ax, ay, bx, by, ly in sorted(segs, key=lambda s: s[4]):
        t = ly / top
        colour = (int(40 + 60 * t), int(140 + 90 * t), int(255 - 120 * t))
        draw.line(
            [(ox + (ax - x0) * scale, oy - (ay - y0) * scale), (ox + (bx - x0) * scale, oy - (by - y0) * scale)],
            fill=colour,
            width=width,
        )
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()
