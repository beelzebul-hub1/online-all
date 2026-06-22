import json
import requests
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Logging setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

# ── Load config ───────────────────────────────────────────────────────────────
with open("config.json", "r") as f:
    config = json.load(f)

STREAMERS = [s for s in config["streamers"] if s.strip()]  # ignore empty entries
RENDER_SERVICES = config["render_services"]

# ── Twitch live check ─────────────────────────────────────────────────────────
def check_streamer(streamer: str) -> bool:
    """Returns True if streamer is live."""
    try:
        r = requests.get(
            f"https://www.twitch.tv/{streamer}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )
        is_live = '"isLiveBroadcast":true' in r.text or 'isLiveBroadcast":true' in r.text
        if is_live:
            log.info(f"🟢 {streamer} is LIVE")
        else:
            log.info(f"⚫ {streamer} is offline")
        return is_live
    except Exception as e:
        log.warning(f"Could not check {streamer}: {e}")
        return False

def get_live_streamers() -> list:
    """Check all streamers in parallel."""
    live = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(check_streamer, s): s for s in STREAMERS}
        for future in as_completed(futures):
            streamer = futures[future]
            if future.result():
                live.append(streamer)
    return live

# ── Render API ────────────────────────────────────────────────────────────────
def set_service_state(service: dict, action: str):
    """Suspend or resume a single Render service."""
    name = service["name"]
    service_id = service["service_id"]
    api_key = service["api_key"]

    url = f"https://api.render.com/v1/services/{service_id}/{action}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json"
    }
    try:
        r = requests.post(url, headers=headers, timeout=10)
        if r.status_code in (200, 202, 204):
            icon = "⏸️" if action == "suspend" else "▶️"
            log.info(f"{icon} {name}: {action}d OK")
        else:
            log.error(f"❌ {name}: failed to {action} — {r.status_code} {r.text}")
    except Exception as e:
        log.error(f"❌ {name}: request error — {e}")

def set_all_services(action: str):
    """Suspend or resume all services in parallel."""
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(set_service_state, svc, action) for svc in RENDER_SERVICES]
        for future in as_completed(futures):
            future.result()

# ── Main logic ────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 50)
    log.info(f"Run at {datetime.now(datetime.UTC if hasattr(datetime, 'UTC') else __import__('datetime').timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    log.info(f"Checking {len(STREAMERS)} streamer(s): {', '.join(STREAMERS)}")
    log.info(f"Managing {len(RENDER_SERVICES)} service(s)")

    live_streamers = get_live_streamers()

    if live_streamers:
        log.info(f"✅ {len(live_streamers)} live: {', '.join(live_streamers)} — resuming all services")
        set_all_services("resume")
    else:
        log.info("💤 Nobody live — suspending all services")
        set_all_services("suspend")

    log.info("=" * 50)

if __name__ == "__main__":
    main()
