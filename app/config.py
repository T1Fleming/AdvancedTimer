"""Environment/config loading shared by the server and BUSY Bar client."""
import os
from pathlib import Path


ROOT = Path(__file__).parent.parent


def load_env_file():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_env_file()

BUSY_BAR_IP = os.getenv("BUSY_BAR_IP", "10.0.4.20")
BASE = f"http://{BUSY_BAR_IP}/api"
WS_URL = f"ws://{BUSY_BAR_IP}/api/status/ws"
APP_NAME = "time_tracker"
PRIORITY = 90
LOG_PATH = ROOT / "sessions.jsonl"
STATE_PATH = ROOT / "state.json"
LABELS_PATH = ROOT / "labels.json"
MAX_EVENT_AGE_MS = 2000
# Coalesce a fast scroll-wheel flick into one display redraw instead of one per detent.
ENCODER_REDRAW_DEBOUNCE_S = 0.15

WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "8765"))
