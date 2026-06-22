import json
import requests
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask

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

STREAMERS = [s for s in config["streamers"] if s.strip()]
RENDER_SERVICES = config["render_services"]
INTERVAL = config.get("check_interval_seconds", 600)  # default 10 minutes

# ── Twitch live check ─────────────────────────────────────────────────────────
def check_streamer(streamer: str) -> bool:
    try:
        r = requests.get(
            f"https://www.twitch.tv/{streamer}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )
        is_live = '"isLiveBroadcast":true' in r.text or 'isLiveBroadcast":true' in r.text
        log.info(f"{'🟢' if is_live else '⚫'} {streamer} is {'LIVE' if is_live else 'offline'}")
        return is_live
    except Exception as e:
        log.warning(f"Could not check {streamer}: {e}")
        return False

def get_live_streamers() -> list:
    live = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(check_streamer, s): s for s in STREAMERS}
        for future in as_completed(futures):
            if future.result():
                live.append(futures[future])
    return live

# ── Render API ────────────────────────────────────────────────────────────────
def set_service_state(service: dict, action: str):
    name = service["name"]
    service_id = service["service_id"]
    api_key = service["api_key"]
    url = f"https://api.render.com/v1/services/{service_id}/{action}"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
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
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(set_service_state, svc, action) for svc in RENDER_SERVICES]
        for future in as_completed(futures):
            future.result()

# ── Controller loop ───────────────────────────────────────────────────────────
def controller_loop():
    while True:
        try:
            log.info("=" * 50)
            log.info(f"Checking {len(STREAMERS)} streamer(s): {', '.join(STREAMERS)}")
            log.info(f"Managing {len(RENDER_SERVICES)} service(s)")

            live = get_live_streamers()

            if live:
                log.info(f"✅ {len(live)} live: {', '.join(live)} — resuming all services")
                set_all_services("resume")
            else:
                log.info("💤 Nobody live — suspending all services")
                set_all_services("suspend")

            log.info(f"Next check in {INTERVAL}s")
            log.info("=" * 50)
        except Exception as e:
            log.error(f"Controller error: {e}")

        time.sleep(INTERVAL)

# ── Flask health server ───────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/")
def health():
    return "Controller running", 200

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    threading.Thread(target=controller_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
