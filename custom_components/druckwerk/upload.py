"""PUT /api/druckwerk/upload/<name>.gcode.3mf: drop a sliced file into the print file list.

HA's own auth is off for this view; a shared secret from the integration options guards it,
so agents without an HA account can upload. A wrong secret counts as a failed login (IP ban).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import tempfile
import zipfile

from aiohttp import web
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.components.http.ban import process_wrong_login

from . import api
from .const import CONF_UPLOAD_SECRET, DOMAIN, MAX_UPLOAD_BYTES, UPLOAD_SECRET_HEADER

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,118}\.gcode\.3mf")


def _check_and_store(base: str, name: str, tmp: str, overwrite: bool) -> tuple[int, str]:
    """Validate the 3MF and move it into place (blocking). Returns (status, message)."""
    try:
        with zipfile.ZipFile(tmp) as z:
            plates = [n for n in z.namelist() if re.fullmatch(r"Metadata/plate_\d+\.gcode", n)]
    except zipfile.BadZipFile:
        return 400, "Body is not a 3MF (zip) file."
    if not plates:
        return 400, "The 3MF has no sliced plate (Metadata/plate_N.gcode); export it as .gcode.3mf."
    os.makedirs(base, exist_ok=True)
    dest = os.path.join(base, name)
    if os.path.exists(dest) and not overwrite:
        return 409, "File exists; add ?overwrite=1 to replace it."
    os.replace(tmp, dest)
    os.chmod(dest, 0o644)
    return 200, dest


class UploadView(HomeAssistantView):
    url = "/api/druckwerk/upload/{name}"
    name = "api:druckwerk:upload"
    requires_auth = False

    async def put(self, request: web.Request, name: str) -> web.Response:
        hass = request.app[KEY_HASS]
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        secret = entries[0].options.get(CONF_UPLOAD_SECRET) if entries else None
        if not secret:
            return self.json_message("Uploads are off: set an upload secret in the Druckwerk options.", 403)
        given = request.headers.get(UPLOAD_SECRET_HEADER, "")
        if not hmac.compare_digest(given.encode(), secret.encode()):
            await process_wrong_login(request)
            return self.json_message("Wrong or missing upload secret.", 401)
        if not NAME_RE.fullmatch(name):
            return self.json_message("Name must be letters, digits, '._ -' and end in .gcode.3mf.", 400)

        base = api.base_dir(hass)
        fd, tmp = await hass.async_add_executor_job(lambda: tempfile.mkstemp(dir=os.path.dirname(base) or "/tmp", suffix=".part"))
        digest, size = hashlib.sha256(), 0
        try:
            with os.fdopen(fd, "wb") as out:
                async for chunk in request.content.iter_chunked(1 << 16):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        return self.json_message("File too large (limit %d bytes)." % MAX_UPLOAD_BYTES, 413)
                    digest.update(chunk)
                    await hass.async_add_executor_job(out.write, chunk)
            status, msg = await hass.async_add_executor_job(
                _check_and_store, base, name, tmp, request.query.get("overwrite") == "1"
            )
        finally:
            if os.path.exists(tmp):
                await hass.async_add_executor_job(os.remove, tmp)
        if status != 200:
            return self.json_message(msg, status)
        await entries[0].runtime_data.async_request_refresh()  # the select lists the new file
        return self.json({"file": name, "bytes": size, "sha256": digest.hexdigest()})

    post = put
