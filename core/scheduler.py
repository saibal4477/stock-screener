import datetime
import os
import pytz
import threading
import time
from typing import Dict, Any, Optional

import config

# Global state for internal scheduler
_SCHEDULER_STATE = {
    "is_running": False,
    "last_10am_scan": None,
    "last_10am_status": "Idle",
    "last_430pm_sync": None,
    "last_430pm_status": "Idle",
    "last_gcs_result": None
}

_SCHEDULER_THREAD: Optional[threading.Thread] = None

def get_scheduler_status() -> Dict[str, Any]:
    ny_tz = pytz.timezone('America/New_York')
    now = datetime.datetime.now(ny_tz)
    
    # Calculate next 10:00 AM EST (next trading day)
    target_10am = now.replace(hour=10, minute=0, second=0, microsecond=0)
    if now >= target_10am or now.weekday() >= 5:
        # Move forward to next weekday
        days_ahead = 1
        candidate = now + datetime.timedelta(days=days_ahead)
        while candidate.weekday() >= 5: # Skip Sat/Sun
            days_ahead += 1
            candidate = now + datetime.timedelta(days=days_ahead)
        target_10am = candidate.replace(hour=10, minute=0, second=0, microsecond=0)
    elif target_10am.weekday() >= 5:
        while target_10am.weekday() >= 5:
            target_10am += datetime.timedelta(days=1)

    # Calculate next 4:30 PM EST
    target_430pm = now.replace(hour=16, minute=30, second=0, microsecond=0)
    if now >= target_430pm or now.weekday() >= 5:
        days_ahead = 1
        candidate = now + datetime.timedelta(days=days_ahead)
        while candidate.weekday() >= 5:
            days_ahead += 1
            candidate = now + datetime.timedelta(days=days_ahead)
        target_430pm = candidate.replace(hour=16, minute=30, second=0, microsecond=0)
    elif target_430pm.weekday() >= 5:
        while target_430pm.weekday() >= 5:
            target_430pm += datetime.timedelta(days=1)

    diff_10am = max(0, int((target_10am - now).total_seconds()))
    diff_430pm = max(0, int((target_430pm - now).total_seconds()))

    def fmt_seconds(s):
        h = s // 3600
        m = (s % 3600) // 60
        sec = s % 60
        if h > 0:
            return f"{h}h {m}m {sec}s"
        return f"{m}m {sec}s"

    return {
        "is_running": _SCHEDULER_STATE["is_running"],
        "current_time_est": now.strftime("%Y-%m-%d %H:%M:%S EST"),
        "is_weekend": now.weekday() >= 5,
        "next_10am_scan_est": target_10am.strftime("%Y-%m-%d 10:00:00 EST"),
        "countdown_10am": fmt_seconds(diff_10am),
        "countdown_10am_seconds": diff_10am,
        "next_430pm_gcs_sync_est": target_430pm.strftime("%Y-%m-%d 16:30:00 EST"),
        "countdown_430pm": fmt_seconds(diff_430pm),
        "countdown_430pm_seconds": diff_430pm,
        "last_10am_scan": _SCHEDULER_STATE["last_10am_scan"],
        "last_10am_status": _SCHEDULER_STATE["last_10am_status"],
        "last_430pm_sync": _SCHEDULER_STATE["last_430pm_sync"],
        "last_430pm_status": _SCHEDULER_STATE["last_430pm_status"],
        "last_gcs_result": _SCHEDULER_STATE["last_gcs_result"],
        "gcs_bucket_configured": bool(os.environ.get("GCS_BUCKET_NAME"))
    }

def _scheduler_loop():
    ny_tz = pytz.timezone('America/New_York')
    print("[SCHEDULER] Internal Web Server Scheduler started.")
    _SCHEDULER_STATE["is_running"] = True
    
    last_10am_done_day = None
    last_430pm_done_day = None

    while True:
        try:
            now = datetime.datetime.now(ny_tz)
            today_str = now.strftime('%Y-%m-%d')
            is_weekday = (now.weekday() < 5) # Mon-Fri

            if is_weekday:
                # 1. Check 10:00 AM EST Intraday Scan
                # Trigger window: 10:00:05 to 10:01:00
                if now.hour == 10 and now.minute == 0 and now.second >= 5 and last_10am_done_day != today_str:
                    last_10am_done_day = today_str
                    _SCHEDULER_STATE["last_10am_scan"] = now.strftime('%Y-%m-%d %H:%M:%S EST')
                    _SCHEDULER_STATE["last_10am_status"] = "Running 10:00 AM Scan..."
                    print(f"[{now.strftime('%H:%M:%S EST')}] [SCHEDULER] ⏰ Triggering automated 10:00 AM Intraday Scan...")
                    try:
                        from core.db import get_universe_tickers
                        from core.live_screener import scan_live_market
                        tickers = get_universe_tickers("scope_all")
                        res = scan_live_market(tickers=tickers, direction_filter="ALL", min_planned_rr=1.5)
                        ops_count = res.get('opportunities_count', 0)
                        _SCHEDULER_STATE["last_10am_status"] = f"Success ({ops_count} setups found)"
                        print(f"[{now.strftime('%H:%M:%S EST')}] [SCHEDULER] ✅ 10:00 AM Scan completed! Found {ops_count} setups.")
                    except Exception as e:
                        _SCHEDULER_STATE["last_10am_status"] = f"Failed: {str(e)}"
                        print(f"[SCHEDULER ERROR] 10:00 AM scan failed: {e}")

                # 2. Check 4:30 PM EST Closing Data DB & GCS Sync
                # Trigger window: 16:30:05 to 16:31:00
                if now.hour == 16 and now.minute == 30 and now.second >= 5 and last_430pm_done_day != today_str:
                    last_430pm_done_day = today_str
                    _SCHEDULER_STATE["last_430pm_sync"] = now.strftime('%Y-%m-%d %H:%M:%S EST')
                    _SCHEDULER_STATE["last_430pm_status"] = "Running 4:30 PM EOD GCS Sync..."
                    print(f"[{now.strftime('%H:%M:%S EST')}] [SCHEDULER] ☁️ Triggering automated 4:30 PM EOD DB & GCS Sync...")
                    try:
                        from core.daily_updater import update_daily_closing_data
                        res = update_daily_closing_data(universe_id="scope_all", upload_gcs=True)
                        _SCHEDULER_STATE["last_430pm_status"] = "Success"
                        _SCHEDULER_STATE["last_gcs_result"] = res.get("gcs_status")
                        print(f"[{now.strftime('%H:%M:%S EST')}] [SCHEDULER] ✅ 4:30 PM DB & GCS Sync completed! Result: {res.get('gcs_status')}")
                    except Exception as e:
                        _SCHEDULER_STATE["last_430pm_status"] = f"Failed: {str(e)}"
                        print(f"[SCHEDULER ERROR] 4:30 PM sync failed: {e}")

            time.sleep(1)
        except Exception as e:
            time.sleep(5)

def start_internal_scheduler():
    """Starts the internal background scheduler daemon inside the web server process."""
    global _SCHEDULER_THREAD
    if _SCHEDULER_THREAD is None or not _SCHEDULER_THREAD.is_alive():
        _SCHEDULER_THREAD = threading.Thread(target=_scheduler_loop, daemon=True, name="WebServerScheduler")
        _SCHEDULER_THREAD.start()
