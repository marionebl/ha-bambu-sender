"""Constants for Druckwerk."""

from datetime import timedelta

DOMAIN = "druckwerk"
CONF_URL = "url"
SCAN_INTERVAL = timedelta(seconds=30)
MEDIA_SUBDIR = "druckwerk"
FILE_SUFFIX = ".gcode.3mf"
SHARE_ROOT = "/share"
BUSY_STATUSES = {"RUNNING", "PAUSE"}
OK_RESULTS = {"sent", "success"}
SEND_TIMEOUT = 300  # seconds; up to 50 MB upload plus the bridge's own work
CAMERA_MAX_AGE = 5  # seconds before a viewer triggers a new snapshot
CAMERA_STALE = 60  # older frames are not served; the viewer waits for a new one
CAMERA_TIMEOUT = 120  # the add-on itself waits up to 90 s for the bridge
AMS_SLOTS = 4  # one AMS (ams_id 0) per printer today
CONF_UPLOAD_SECRET = "upload_secret"
UPLOAD_SECRET_HEADER = "X-Druckwerk-Secret"
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MIN_SECRET_LENGTH = 24

# Druckwerk app (this repo's hub/): the bridge stays on 8098, the hub API is on 8099.
CONF_HUB_URL = "hub_url"
CONF_HUB_SECRET = "hub_secret"
CONF_RECIPIENTS = "recipients"  # {person entity_id: notify service}
HUB_PORT = 8099
BRIDGE_PORT = 8098
HUB_SLUG_SUFFIX = "_druckwerk"
# Hostname computed from the repository hash; used only when the Supervisor cannot tell.
DEFAULT_HUB_URL = "http://c9a833d4-druckwerk:8098"
SUPERVISOR_URL = "http://supervisor"
EVENT_INTERVAL = timedelta(seconds=5)
EVENT_PREFIX = "druckwerk_"
ACTION_PREFIX = "DRUCKWERK_START_"
HUB_SECRET_HEADER = "X-Hub-Secret"
