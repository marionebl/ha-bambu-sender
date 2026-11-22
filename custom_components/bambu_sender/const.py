"""Constants for Bambu Sender."""

from datetime import timedelta

DOMAIN = "bambu_sender"
BAMBU_LAB_DOMAIN = "bambu_lab"  # ha-bambulab's device identifier domain
DEFAULT_URL = "http://local-bambu-sender:8098"
CONF_URL = "url"
SCAN_INTERVAL = timedelta(seconds=30)
MEDIA_SUBDIR = "bambu_sender"
FILE_SUFFIX = ".gcode.3mf"
SHARE_ROOT = "/share"
BUSY_STATUSES = {"RUNNING", "PAUSE", "PREPARE"}
OK_RESULTS = {"sent", "success"}
SEND_TIMEOUT = 300  # seconds; up to 50 MB upload plus the bridge's own work
CAMERA_MAX_AGE = 5  # seconds before a viewer triggers a new snapshot
CAMERA_STALE = 60  # older frames are not served; the viewer waits for a new one
CAMERA_FRAME_INTERVAL = 2  # seconds between stills when the live stream is not used
CAMERA_TIMEOUT = 120  # the add-on itself waits up to 90 s for the bridge
AMS_SLOTS = 4  # one AMS (ams_id 0) per printer today
CONF_UPLOAD_SECRET = "upload_secret"
UPLOAD_SECRET_HEADER = "X-Bambu-Sender-Secret"
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MIN_SECRET_LENGTH = 24
