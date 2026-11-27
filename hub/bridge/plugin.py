"""Fetches the printer vendor's network plugin at app start instead of shipping it in the image.

The zip is downloaded from the vendor's CDN, checked against a pinned SHA-256, and only the five libraries are kept
under <data>/plugin/<version>/. They are then copied to where the bridge loads them from. State is exposed for
/api/health; failures retry with backoff, so the app never crash-loops on a missing plugin.
"""
import filecmp
import hashlib
import os
import shutil
import sys
import tempfile
import threading
import urllib.request
import zipfile

import externals

env = os.environ.get

VERSION = env("DW_PLUGIN_VERSION", externals.PLUGIN_VERSION)
URL = env("DW_PLUGIN_URL", externals.PLUGIN_URL)
SHA256 = env("DW_PLUGIN_SHA256", externals.PLUGIN_SHA256).lower()
LIBS = externals.PLUGIN_LIBS
HOST_LINKED = externals.PLUGIN_HOST_LINKED
MAX_ZIP = 200 * 1024 * 1024
MAX_LIB = 200 * 1024 * 1024


def log(message):
    print("plugin: " + message, file=sys.stderr, flush=True)


class Plugin:
    def __init__(self, data_dir, link_dir, host_link_dir=None, backoff_min=10.0, backoff_max=600.0):
        self.url, self.sha256 = URL, SHA256  # captured now: a retrying thread must not follow later changes
        self.dir = os.path.join(data_dir, "plugin", VERSION)
        self.stopped = False
        self.link_dir, self.host_link_dir = link_dir, host_link_dir
        self.backoff_min, self.backoff_max = backoff_min, backoff_max
        self.state, self.error, self.attempt, self.retry_in = "checking", None, 0, None
        self.ready = threading.Event()
        self.wake = threading.Event()
        threading.Thread(target=self.run, daemon=True).start()

    def stop(self):
        self.stopped = True
        self.wake.set()

    def status(self):
        return {"state": self.state, "version": VERSION, "error": self.error, "attempt": self.attempt,
                "retry_in": self.retry_in}

    def installed(self):
        paths = [os.path.join(self.dir, n) for n in LIBS]
        return all(os.path.isfile(p) and os.path.getsize(p) > 0 for p in paths)

    def run(self):
        delay = self.backoff_min
        while not self.stopped:
            self.attempt += 1
            try:
                if not self.installed():
                    self.state, self.error = "downloading", None
                    self.download()
                self.place()
            except Exception as e:  # any failure is retried; the message is what health shows
                self.state, self.error = "failed", "%s: %s" % (type(e).__name__, e)
                self.retry_in = delay
                log("%s (retry in %.0fs)" % (self.error, delay))
                self.wake.wait(delay)
                self.wake.clear()
                delay = min(delay * 2, self.backoff_max)
                continue
            self.state, self.error, self.retry_in = "ready", None, None
            log("ready (version %s)" % VERSION)
            self.ready.set()
            return

    def download(self):
        os.makedirs(os.path.dirname(self.dir), exist_ok=True)
        work = tempfile.mkdtemp(prefix=".part-", dir=os.path.dirname(self.dir))
        try:
            zpath = os.path.join(work, "plugin.zip")
            digest, size = hashlib.sha256(), 0
            log("downloading %s" % self.url)
            with urllib.request.urlopen(self.url, timeout=60) as r, open(zpath, "wb") as out:
                while chunk := r.read(1 << 20):
                    size += len(chunk)
                    if size > MAX_ZIP:
                        raise ValueError("download larger than %d bytes" % MAX_ZIP)
                    digest.update(chunk)
                    out.write(chunk)
            if digest.hexdigest() != self.sha256:
                raise ValueError("SHA-256 mismatch: got %s, expected %s" % (digest.hexdigest(), self.sha256))
            stage = os.path.join(work, "libs")
            os.mkdir(stage)
            with zipfile.ZipFile(zpath) as z:
                for name in LIBS:
                    hits = [i for i in z.infolist() if not i.is_dir() and os.path.basename(i.filename) == name]
                    if len(hits) != 1 or hits[0].file_size == 0 or hits[0].file_size > MAX_LIB:
                        raise ValueError("zip does not hold exactly one usable %s" % name)
                    with z.open(hits[0]) as src, open(os.path.join(stage, name), "wb") as dst:
                        shutil.copyfileobj(src, dst)
            if os.path.isdir(self.dir):
                shutil.rmtree(self.dir)
            os.replace(stage, self.dir)  # a half-extracted directory never carries the final name
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def place(self):
        """Copy, not symlink: under qemu -L an absolute link into /data would not resolve like the rootfs paths."""
        os.makedirs(self.link_dir, exist_ok=True)
        for name in LIBS:
            src, dest = os.path.join(self.dir, name), os.path.join(self.link_dir, name)
            if os.path.isfile(dest) and filecmp.cmp(src, dest, shallow=False):
                continue
            tmp = dest + ".tmp"
            shutil.copyfile(src, tmp)
            os.chmod(tmp, 0o755)
            os.replace(tmp, dest)
        for name in HOST_LINKED if self.host_link_dir else ():
            link = os.path.join(self.host_link_dir, name)
            if os.path.lexists(link):
                os.remove(link)
            os.symlink(os.path.join(self.link_dir, name), link)


def from_env():
    """None when the image does not use runtime plugins (tests, the old add-on): the plugin is then assumed present."""
    if not env("DW_PLUGIN_LINK_DIR"):
        return None
    return Plugin(env("DW_DATA", "/data"), env("DW_PLUGIN_LINK_DIR"), env("DW_PLUGIN_HOST_LINK_DIR"),
                  float(env("DW_PLUGIN_BACKOFF_MIN", "10")), float(env("DW_PLUGIN_BACKOFF_MAX", "600")))
