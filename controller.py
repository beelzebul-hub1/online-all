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

STREAMERS        = [s for s in config["streamers"] if s.strip()]
RENDER_SERVICES  = config["render_services"]
INTERVAL         = config.get("check_interval_seconds", 600)
MAX_WORKERS      = max(len(RENDER_SERVICES) + 10, 50)   # scales with your service count
RETRY_ATTEMPTS   = 3
RETRY_DELAY      = 2   # seconds between retries

# ── State tracker (avoids redundant API calls) ────────────────────────────────
_last_action: str | None = None   # "resume" | "suspend" | None
_state_lock = threading.Lock()

# ── Twitch live check ─────────────────────────────────────────────────────────
def check_streamer(streamer: str) -> bool:
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            r = requests.get(
                f"https://www.twitch.tv/{streamer}",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10
            )
            is_live = '"isLiveBroadcast":true' in r.text
            status  = "🟢 LIVE" if is_live else "⚫ offline"
            log.info(f"  {status:<12}  {streamer}")
            return is_live
        except Exception as e:
            if attempt < RETRY_ATTEMPTS:
                log.warning(f"  ⚠️  {streamer}: attempt {attempt} failed ({e}), retrying…")
                time.sleep(RETRY_DELAY)
            else:
                log.warning(f"  ❌ {streamer}: all {RETRY_ATTEMPTS} attempts failed — treating as offline")
                return False

def get_live_streamers() -> list:
    live = []
    with ThreadPoolExecutor(max_workers=len(STREAMERS) or 1) as ex:
        futures = {ex.submit(check_streamer, s): s for s in STREAMERS}
        for future in as_completed(futures):
            if future.result():
                live.append(futures[future])
    return live

# ── Render API ────────────────────────────────────────────────────────────────
def set_service_state(service: dict, action: str) -> tuple[bool, str]:
    """Returns (success, name)."""
    name       = service["name"]
    service_id = service["service_id"]
    api_key    = service["api_key"]
    url        = f"https://api.render.com/v1/services/{service_id}/{action}"
    headers    = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}

    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            r = requests.post(url, headers=headers, timeout=15)
            if r.status_code in (200, 202, 204):
                return True, name
            else:
                if attempt < RETRY_ATTEMPTS:
                    time.sleep(RETRY_DELAY)
                else:
                    log.error(f"  ❌ FAILED   {name:<30}  [{r.status_code}] {r.text[:80]}")
                    return False, name
        except Exception as e:
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_DELAY)
            else:
                log.error(f"  ❌ ERROR    {name:<30}  {e}")
                return False, name

def set_all_services(action: str):
    global _last_action

    with _state_lock:
        if _last_action == action:
            log.info(f"  ↩️  Already {'suspended' if action == 'suspend' else 'resumed'} last cycle — skipping redundant API calls")
            return
        _last_action = action

    icon      = "⏸️ " if action == "suspend" else "▶️ "
    verb      = "Suspending" if action == "suspend" else "Resuming"
    done_verb = "Suspended"  if action == "suspend" else "Resumed"

    log.info(f"  {verb} {len(RENDER_SERVICES)} service(s) with {MAX_WORKERS} workers…")

    ok_names   = []
    fail_names = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(set_service_state, svc, action): svc for svc in RENDER_SERVICES}
        for future in as_completed(futures):
            success, name = future.result()
            if success:
                ok_names.append(name)
            else:
                fail_names.append(name)

    # ── Print results table ───────────────────────────────────────────────────
    log.info(f"")
    log.info(f"  ┌─ {done_verb.upper()} RESULTS {'─' * 30}")
    for name in sorted(ok_names):
        log.info(f"  │  {icon} {name}")
    if fail_names:
        for name in sorted(fail_names):
            log.info(f"  │  ❌ {name}  ← FAILED")
    log.info(f"  └─ {len(ok_names)}/{len(RENDER_SERVICES)} succeeded" +
             (f"  |  {len(fail_names)} FAILED" if fail_names else "  ✅ all good"))
    log.info(f"")

# ── Controller loop ───────────────────────────────────────────────────────────
def controller_loop():
    while True:
        try:
            log.info("━" * 55)
            log.info(f"  🔍 Checking {len(STREAMERS)} streamer(s):")
            log.info(f"")

            live = get_live_streamers()

            log.info(f"")
            if live:
                log.info(f"  ✅ {len(live)}/{len(STREAMERS)} LIVE: {', '.join(live)}")
                log.info(f"  ▶️  RESUMING all {len(RENDER_SERVICES)} service(s)…")
                log.info(f"")
                set_all_services("resume")
            else:
                log.info(f"  💤 Nobody is live")
                log.info(f"  ⏸️  SUSPENDING all {len(RENDER_SERVICES)} service(s)…")
                log.info(f"")
                set_all_services("suspend")

            log.info(f"  ⏱️  Next check in {INTERVAL}s  ({INTERVAL // 60}m)")
            log.info("━" * 55)
        except Exception as e:
            log.error(f"  💥 Controller loop error: {e}")

        time.sleep(INTERVAL)

# ── Flask health server ───────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/")
def health():
    return "Controller running", 200

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("━" * 55)
    log.info(f"  🚀 Controller starting up")
    log.info(f"  👀 Watching : {', '.join(STREAMERS)}")
    log.info(f"  🖥️  Services : {len(RENDER_SERVICES)}")
    log.info(f"  ⏱️  Interval : {INTERVAL}s")
    log.info(f"  🔀 Workers  : {MAX_WORKERS}")
    log.info("━" * 55)

    threading.Thread(target=controller_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
