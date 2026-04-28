#!/usr/bin/env python3
"""
HyperWait Admin Monitor
Polls active waitlists, detects guests waiting too long without notification,
and pings the ops team on Telegram.

Run:
  python3 monitor.py                 # one pass, print + alert
  python3 monitor.py --loop          # poll every POLL_INTERVAL_SEC seconds
  python3 monitor.py --dry           # check + print, no Telegram alerts
"""

import os
import sys
import time
import json
import argparse
import requests
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────────
BASE_URL  = os.environ["HYPERWAIT_API_BASE_URL"]
TOKEN     = os.environ["HYPERWAIT_API_TOKEN"]
HEADERS   = {"Authorization": f"Bearer {TOKEN}"}

TG_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT   = os.environ.get("TELEGRAM_CHAT_ID")

# Alert thresholds (minutes since signup with notifiedAt = null)
WARN_MIN   = int(os.environ.get("HW_WARN_MIN",   "15"))   # first nudge
URGENT_MIN = int(os.environ.get("HW_URGENT_MIN", "30"))   # escalate

POLL_INTERVAL_SEC = int(os.environ.get("HW_POLL_SEC", "300"))  # 5 min
STATE_FILE        = Path(__file__).parent / ".monitor_state.json"

# Daily report (yesterday's recap) — local hour to send at, 24h
DAILY_REPORT_HOUR  = int(os.environ.get("HW_DAILY_HOUR", "9"))
TZ_OFFSET_HOURS    = int(os.environ.get("HW_TZ_OFFSET", "3"))   # GST = UTC+3


# ── HyperWait API ───────────────────────────────────────────────────────
def get(path, params=None):
    r = requests.get(f"{BASE_URL}/{path.lstrip('/')}",
                     headers=HEADERS, params=params, timeout=15)
    r.raise_for_status()
    return r.json()

def fetch_active_restaurants():
    rs = get("restaurants")["restaurants"]
    return [r for r in rs if r["status"] == "approved" and r.get("isOpen")]

def fetch_waitlist(rid):
    return get(f"restaurants/{rid}/waitlist", {"status": "active", "limit": 200})["entries"]


# ── State (so we don't spam the same alert) ─────────────────────────────
def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── Telegram ────────────────────────────────────────────────────────────
def tg_send(text):
    if not (TG_TOKEN and TG_CHAT):
        print("⚠️  Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing)")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "parse_mode": "Markdown",
                  "disable_web_page_preview": True},
            timeout=10,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


# ── Detection ───────────────────────────────────────────────────────────
def minutes_since(iso):
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - dt).total_seconds() / 60

def classify(entry):
    """Return ('urgent'|'warn'|None, age_min) for a single entry."""
    if entry.get("notifiedAt"):
        return None, 0
    if entry["status"] != "active":
        return None, 0
    age = minutes_since(entry["createdAt"])
    if age >= URGENT_MIN:
        return "urgent", age
    if age >= WARN_MIN:
        return "warn", age
    return None, age


def check_restaurant(restaurant, state, dry=False):
    rid   = restaurant["id"]
    name  = restaurant["name"]
    slug  = restaurant["slug"]
    entries = fetch_waitlist(rid)

    # Total live queue + un-notified bucket
    waiting = [e for e in entries if e["status"] == "active"]
    unnotified = [e for e in waiting if not e.get("notifiedAt")]

    flagged = []
    for e in waiting:
        level, age = classify(e)
        if level:
            flagged.append((level, age, e))

    if not flagged:
        print(f"  ✓ {name}: {len(waiting)} waiting, all notified or fresh")
        return

    # Sort urgent first, oldest first
    flagged.sort(key=lambda x: (0 if x[0] == "urgent" else 1, -x[1]))

    # Build a single grouped alert per restaurant per polling pass
    urgent_count = sum(1 for f in flagged if f[0] == "urgent")
    warn_count   = len(flagged) - urgent_count

    icon = "🚨" if urgent_count else "⏰"
    lines = [
        f"{icon} *{name}* — admin attention needed",
        f"_{len(waiting)} waiting · {len(unnotified)} un-notified_",
        "",
    ]
    for level, age, e in flagged[:10]:
        tag = "🚨" if level == "urgent" else "⏰"
        nm  = (e.get("name") or "—").replace("*", "")
        ps  = e.get("partySize", "?")
        pos = e.get("position", "?")
        calls = e.get("callAttempts", 0)
        lines.append(f"{tag} `#{pos}` {nm}  ·  party {ps}  ·  {age:.0f} min  ·  calls {calls}")

    if len(flagged) > 10:
        lines.append(f"_… and {len(flagged) - 10} more_")

    lines += [
        "",
        f"👉 https://hyperwait.com/host/{slug}",
    ]
    msg = "\n".join(lines)
    print(f"\n{msg}\n")

    # De-dupe: only alert if (a) a new urgent appeared, or (b) >25min since last alert
    key = f"r{rid}"
    last = state.get(key, {})
    last_urgent_ids = set(last.get("urgent_ids", []))
    cur_urgent_ids  = {e["id"] for lvl, _, e in flagged if lvl == "urgent"}
    new_urgent      = bool(cur_urgent_ids - last_urgent_ids)
    age_since_last  = (time.time() - last.get("ts", 0)) / 60

    should_alert = (new_urgent or age_since_last > 25 or not last)
    if should_alert and not dry:
        if tg_send(msg):
            state[key] = {"ts": time.time(), "urgent_ids": list(cur_urgent_ids)}


# ── Daily report scheduling ─────────────────────────────────────────────
def maybe_send_daily_report(state, dry=False):
    """Trigger daily_report.run() once per local day at DAILY_REPORT_HOUR."""
    from datetime import timedelta
    now_local = datetime.now(timezone.utc) + timedelta(hours=TZ_OFFSET_HOURS)
    today_str = now_local.date().isoformat()
    if state.get("last_daily_report") == today_str:
        return
    if now_local.hour < DAILY_REPORT_HOUR:
        return
    print(f"  → sending daily report for {today_str}")
    if dry:
        state["last_daily_report"] = today_str
        return
    try:
        import daily_report
        daily_report.run("yesterday")
        state["last_daily_report"] = today_str
    except Exception as e:
        print(f"  ✗ daily report failed: {e}")


# ── Main loop ───────────────────────────────────────────────────────────
def run_once(dry=False):
    state = load_state()
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] HyperWait monitor — checking…")
    rs = fetch_active_restaurants()
    for r in rs:
        try:
            check_restaurant(r, state, dry=dry)
        except Exception as e:
            print(f"  ✗ {r['name']}: {e}")
    maybe_send_daily_report(state, dry=dry)
    save_state(state)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--loop", action="store_true", help="poll forever")
    p.add_argument("--dry",  action="store_true", help="print only, no Telegram")
    p.add_argument("--test", action="store_true", help="send a test Telegram message and exit")
    p.add_argument("--report", choices=["yesterday","today"], help="send daily report and exit")
    args = p.parse_args()

    if args.report:
        import daily_report
        daily_report.run(args.report)
        sys.exit(0)

    if args.test:
        ok = tg_send("✅ HyperWait monitor — test message. Bot is connected.")
        sys.exit(0 if ok else 1)

    if args.loop:
        print(f"Looping every {POLL_INTERVAL_SEC}s. Warn ≥ {WARN_MIN}min, urgent ≥ {URGENT_MIN}min.")
        while True:
            try:
                run_once(dry=args.dry)
            except Exception as e:
                print(f"loop error: {e}")
            time.sleep(POLL_INTERVAL_SEC)
    else:
        run_once(dry=args.dry)


if __name__ == "__main__":
    main()
