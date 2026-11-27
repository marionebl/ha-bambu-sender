"""Mock of the boocloud-bridge daemon: `python3 mock_daemon.py <credentials.toml> <port>`.
Serves the printers listed in that credentials file, reports it (and HOME) in /health, records /print bodies."""
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import tomllib
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AMS = {"ams": {"ams": [{"id": "0", "tray": [{"id": "0", "tray_type": "PLA", "tray_color": "FF0000FF"}]}]}, "vt_tray": {}}

H264_SERIAL, JPEG_SERIAL = "SERIAL001", "SERIAL002"  # which serial answers /live with which format
SAMPLE = os.path.join(tempfile.gettempdir(), "mock_testsrc_320x240_g15.h264")
FFMPEG = ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15"]


def sample_h264():
    """Two seconds of real H.264 (two GOPs, SPS+PPS before each keyframe), made once and reused.
    No B-frames and 4:2:0, like a camera feed: ffmpeg cannot stream-copy B-frames from a raw stream."""
    if not os.path.exists(SAMPLE):
        tmp = SAMPLE + ".%d" % os.getpid()
        subprocess.run(FFMPEG + ["-t", "2", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-g", "15", "-bf", "0", "-bsf:v", "h264_mp4toannexb", "-f", "h264", tmp],
                       check=True)
        os.replace(tmp, SAMPLE)  # atomic: parallel mocks may race here
    with open(SAMPLE, "rb") as f:
        return f.read()


def sample_jpegs():
    out = subprocess.run(FFMPEG + ["-frames:v", "5", "-c:v", "mjpeg", "-f", "image2pipe", "-"],
                         check=True, capture_output=True).stdout
    starts = [i for i in range(len(out) - 2) if out[i:i + 3] == b"\xff\xd8\xff"]
    return [out[a:b] for a, b in zip(starts, starts[1:] + [len(out)])]


class Handler(BaseHTTPRequestHandler):
    def live(self, serial):
        """Stream like the bridge: chunked, at real time, until the client leaves."""
        s = self.server
        if serial in s.live_active:  # the bridge allows one tunnel per printer
            return self.reply(409, {"stage": "busy", "code": -1, "error": "a live stream is running"})
        h264 = serial == H264_SERIAL
        if h264:
            data = sample_h264()
            step, pause = len(data) // 40, 0.05  # about 2 s of video in 40 chunks
            frames = [data[i:i + step] for i in range(0, len(data), step)]
            ctype, rate = "video/h264", 15
        else:
            frames = sample_jpegs()
            step, pause, ctype, rate = 0, 0.2, "multipart/x-mixed-replace; boundary=frame", 5
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("x-stream-info", json.dumps({"sub_type": 1, "format_type": 1, "width": 320, "height": 240,
                                                        "frame_rate": rate}))
        self.end_headers()
        s.live_log.append(["connect", serial])
        s.live_active.add(serial)
        try:
            i = 0
            while True:
                piece = frames[i % len(frames)]
                if not h264:
                    piece = b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(piece) + piece + b"\r\n"
                self.wfile.write(b"%x\r\n" % len(piece) + piece + b"\r\n")
                self.wfile.flush()
                i += 1
                time.sleep(pause)
        except OSError:
            s.live_active.discard(serial)
            s.live_log.append(["disconnect", serial])

    def reply(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        s = self.server
        if self.path == "/health":
            self.reply(200, {"status": "ok", "mqtt_connected": True, "cached_devices": len(s.serials),
                             "credentials": s.creds, "home": os.environ.get("HOME"), "pid": os.getpid()})
        elif self.path == "/printers":
            self.reply(200, {"printers": [{"name": "p", "serial": x, "gcode_state": "IDLE"} for x in s.serials[:1]]})
        elif self.path == "/commands":  # mock only: what /command received
            self.reply(200, s.commands)
        elif self.path == "/prints":  # mock only: what /print received
            self.reply(200, [{"file_b64": base64.b64encode(p["file"]).decode(), "params": p["params"]} for p in s.prints])
        elif self.path.startswith("/status/") and self.path[8:] in s.serials:
            # SERIALB01 (the H2S) answers like a new-protocol printer, the others like a P1S.
            np = {"cfg": "0", "fun": "0", "aux": "0", "stat": "0"} if self.path.endswith("SERIALB01") else {}
            self.reply(200, {"print": dict({"gcode_state": "IDLE", "nozzle_temper": 21.5}, **np), "served_by": s.creds})
        elif self.path == "/live-log":  # mock only: stream connects and disconnects
            self.reply(200, s.live_log)
        elif self.path.startswith("/camera/") and "/live" in self.path and self.path.split("/")[2] in s.serials:
            self.live(self.path.split("/")[2])
        elif self.path.startswith("/camera/") and "/snapshot" in self.path and self.path.split("/")[2] in s.live_active:
            s.live_log.append(["snapshot", self.path.split("/")[2]])  # a snapshot that reached the bridge during live
            self.reply(409, {"stage": "busy", "code": -1, "error": "a live stream is running"})
        elif self.path.startswith("/camera/") and "/snapshot" in self.path:
            # Seen live: the plugin never calls back, and the daemon stops serving status until restarted.
            self.reply(502, {"error": "timeout after 20s waiting for camera URL callback", "stage": "mint"})
        elif self.path.startswith("/camera/") and "/url" in self.path:
            self.reply(200, {"ok": True, "path": self.path})
        elif self.path.startswith("/ams/") and self.path[5:] in s.serials:
            self.reply(200, dict(AMS, served_by=s.creds))
        else:
            self.reply(404, {"error": "unknown"})

    def do_POST(self):
        if self.path.startswith("/command/") and self.path[9:] in self.server.serials:
            self.server.commands.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            return self.reply(200, {"sent": True})
        if self.path != "/print":
            return self.reply(404, {"error": "unknown"})
        body = self.rfile.read(int(self.headers["Content-Length"]))
        msg = BytesParser(policy=default).parsebytes(
            b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body)
        parts = {p.get_param("name", header="content-disposition"): p.get_payload(decode=True) for p in msg.iter_parts()}
        if "file" not in parts or "params" not in parts:
            return self.reply(400, {"error": "missing file or params"})
        params = json.loads(parts["params"])
        self.server.prints.append({"file": parts["file"], "params": params})
        if params["filename"] == "fail.gcode.3mf":
            return self.reply(502, {"result": "error", "return_code": -3140})
        self.reply(200, {"result": "sent", "return_code": -1, "print_result": -999,
                         "device_id": params["device_id"], "file": params["filename"]})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    creds, port = sys.argv[1], int(sys.argv[2])
    with open(creds, "rb") as f:
        serials = [p["serial"] for p in tomllib.load(f).get("printers", {}).values()]
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.creds, server.serials, server.prints, server.commands, server.live_log = creds, serials, [], [], []
    server.live_active = set()
    print("mock daemon up for " + creds, flush=True)
    server.serve_forever()
