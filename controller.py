import json
import requests
import logging
from datetime import datetime

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

STREAMERS = config["streamers"]
RENDER_SERVICES = config["render_services"]

# ── Twitch live check (no app/credentials needed) ────────────────────────────
def get_live_streamers(streamers: list[str]) -> list[str]:
    """Returns list of streamers that are currently live."""
    live = []
    headers = {
        "Client-ID": "kimne78kx3ncx6brgo4mv6wki5h1ko",  # public Twitch web client ID
        "User-Agent": "Mozilla/5.0"
    }
    for streamer in streamers:
        try:
            url = f"https://gql.twitch.tv/gql"
            payload = [{
                "operationName": "StreamMetadata",
                "variables": {"channelLogin": streamer.lower()},
                "extensions": {
                    "persistedQuery": {
                        "version": 1,
                        "sha256Hash": "a647c2a13599e5991e175155f798ca7f1ecddde73f7f341f39009c14dbf59AA"
                    }
                }
            }]
            # Simpler approach: use the undocumented stream endpoint
            r = requests.get(
                f"https://www.twitch.tv/{streamer}",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10
            )
            # Check for isLiveBroadcast in the page source (structured data Twitch embeds)
            if '"isLiveBroadcast":true' in r.text or 'isLiveBroadcast":true' in r.text:
                live.append(streamer)
                log.info(f"🟢 {streamer} is LIVE")
            else:
                log.info(f"⚫ {streamer} is offline")
        except Exception as e:
            log.warning(f"Could not check {streamer}: {e}")
    return live

# ── Render API ────────────────────────────────────────────────────────────────
def set_service_state(service: dict, action: str):
    """Suspend or resume a Render service. action = 'suspend' or 'resume'"""
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
        if r.status_code in (200, 204):
            icon = "⏸️" if action == "suspend" else "▶️"
            log.info(f"{icon} {name} ({service_id}): {action}d successfully")
        else:
            log.error(f"❌ {name} ({service_id}): failed to {action} — {r.status_code} {r.text}")
    except Exception as e:
        log.error(f"❌ {name} ({service_id}): request error — {e}")

# ── Main logic ────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 50)
    log.info(f"Controller run at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    log.info(f"Checking {len(STREAMERS)} streamer(s): {', '.join(STREAMERS)}")

    live_streamers = get_live_streamers(STREAMERS)

    if live_streamers:
        log.info(f"✅ {len(live_streamers)} streamer(s) live: {', '.join(live_streamers)} — resuming services")
        for service in RENDER_SERVICES:
            set_service_state(service, "resume")
    else:
        log.info("💤 Nobody is live — suspending services")
        for service in RENDER_SERVICES:
            set_service_state(service, "suspend")

    log.info("=" * 50)

if __name__ == "__main__":
    main()
