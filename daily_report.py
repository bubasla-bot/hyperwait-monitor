#!/usr/bin/env python3
"""
HyperWait Daily Detailed Report → Telegram OPS group.

Builds a rich digest covering yesterday's performance per restaurant:
  • Funnel (signups → notified → seated; no-show count)
  • Conversion rate vs 7-day baseline
  • Notification speed (avg minutes to notify)
  • Top cancellation reasons
  • Live queue at report time
  • Cross-restaurant ranking
  • A single, actionable callout based on the worst KPI of the day

Designed to be called from main.py at a scheduled hour (default 09:00 local).
Standalone usage:
  python3 daily_report.py            # send for "yesterday"
  python3 daily_report.py --today    # send for today so far
"""
import os, sys, argparse, requests
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()
BASE   = os.environ["HYPERWAIT_API_BASE_URL"]
TOKEN  = os.environ["HYPERWAIT_API_TOKEN"]
TG_TOK = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHT = os.environ["TELEGRAM_CHAT_ID"]
TZ_OFFSET_HOURS = int(os.environ.get("HW_TZ_OFFSET", "3"))   # GST = UTC+3
H = {"Authorization": f"Bearer {TOKEN}"}


# ── API ─────────────────────────────────────────────────────────────────
def g(p, params=None):
    r = requests.get(f"{BASE}/{p.lstrip('/')}", headers=H, params=params, timeout=20)
    r.raise_for_status(); return r.json()

def fetch_restaurants():
    return [r for r in g("restaurants")["restaurants"] if r["status"] == "approved"]

def fetch_waitlist(rid):
    return g(f"restaurants/{rid}/waitlist", {"status":"all", "limit": 1000})["entries"]


# ── Helpers ─────────────────────────────────────────────────────────────
def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

def local_date(iso):
    """Return the local-date (date only) of an ISO timestamp, using TZ_OFFSET_HOURS."""
    dt = parse_iso(iso) + timedelta(hours=TZ_OFFSET_HOURS)
    return dt.date()

def pct(n, d): return f"{n/d:.0%}" if d else "—"

def fmt_min(v):
    if v is None: return "—"
    return f"{v:.0f}m" if v < 60 else f"{v/60:.1f}h"


# ── Per-restaurant analysis for a target local date ─────────────────────
def analyze(restaurant, target_date):
    entries = fetch_waitlist(restaurant["id"])

    # Filter to entries created on the target local date
    day = [e for e in entries if local_date(e["createdAt"]) == target_date]
    seated    = [e for e in day if e["status"] == "completed"]
    cancelled = [e for e in day if e["status"] == "cancelled"]
    notified  = [e for e in day if e.get("notifiedAt")]
    no_shows  = [e for e in cancelled if e.get("removalReason") == "no_show"]
    reasons   = Counter(e.get("removalReason") or "unknown" for e in cancelled)

    # Notification speed: minutes between createdAt and notifiedAt
    speeds = []
    for e in notified:
        try:
            d = (parse_iso(e["notifiedAt"]) - parse_iso(e["createdAt"])).total_seconds() / 60
            if 0 <= d < 60 * 12:
                speeds.append(d)
        except Exception:
            pass
    avg_speed = sum(speeds) / len(speeds) if speeds else None

    # 7-day baseline conversion (excluding target day)
    base = []
    for off in range(1, 8):
        d = target_date - timedelta(days=off)
        chunk = [e for e in entries if local_date(e["createdAt"]) == d]
        if not chunk: continue
        s = sum(1 for e in chunk if e["status"] == "completed")
        base.append((s, len(chunk)))
    base_conv = sum(s for s, _ in base) / sum(t for _, t in base) if base else None

    # Live queue right now
    live = [e for e in entries if e["status"] == "active"]
    live_unnotif = [e for e in live if not e.get("notifiedAt")]

    return {
        "name": restaurant["name"],
        "country": restaurant["country"],
        "slug": restaurant["slug"],
        "is_open": restaurant.get("isOpen"),
        "signups": len(day),
        "seated": len(seated),
        "cancelled": len(cancelled),
        "no_shows": len(no_shows),
        "notified": len(notified),
        "conv": (len(seated) / len(day)) if day else None,
        "base_conv": base_conv,
        "avg_speed_min": avg_speed,
        "reasons": dict(reasons),
        "live": len(live),
        "live_unnotif": len(live_unnotif),
    }


# ── Insight selector — pick THE most actionable callout ─────────────────
def pick_callout(rows):
    issues = []
    for r in rows:
        # 1. High no-show rate
        if r["cancelled"] >= 3 and r["no_shows"] / r["cancelled"] >= 0.5:
            issues.append((90 + r["no_shows"],
                f"⚠️ *{r['name']}* — no-shows are {r['no_shows']}/{r['cancelled']} of cancellations. Add a 30-min reminder + tap-to-confirm."))
        # 2. Slow notification
        if r["avg_speed_min"] and r["avg_speed_min"] > 90:
            issues.append((80 + int(r["avg_speed_min"]),
                f"⏱  *{r['name']}* — avg {fmt_min(r['avg_speed_min'])} from signup to notify. Aim for under 30 min; check host workflow."))
        # 3. Conversion sliding vs baseline
        if r["conv"] is not None and r["base_conv"] and r["conv"] < r["base_conv"] * 0.7 and r["signups"] >= 5:
            issues.append((70,
                f"📉 *{r['name']}* — conversion {pct(r['seated'], r['signups'])} vs 7-day avg {pct(int(r['base_conv']*100),100)}. Investigate."))
        # 4. Live queue with un-notified at report time
        if r["live_unnotif"] >= 3:
            issues.append((100,
                f"🚨 *{r['name']}* — `{r['live_unnotif']}` un-notified guests waiting *right now*. Open dashboard."))
    if not issues:
        return "✅ All restaurants healthy yesterday. Keep notifying inside 30 min and chasing reminders."
    issues.sort(reverse=True)
    return issues[0][1]


# ── Render ──────────────────────────────────────────────────────────────
DAYS_AR = {0:"Mon",1:"Tue",2:"Wed",3:"Thu",4:"Fri",5:"Sat",6:"Sun"}

def build_message(rows, target_date, label):
    lines = []
    weekday = DAYS_AR[target_date.weekday()]
    lines.append(f"📊 *HyperWait Daily Report* — {label}")
    lines.append(f"_{weekday} {target_date.strftime('%d %b %Y')}  ·  {len(rows)} restaurants_")
    lines.append("")

    # Aggregate
    tot_sign = sum(r["signups"] for r in rows)
    tot_seat = sum(r["seated"] for r in rows)
    tot_canc = sum(r["cancelled"] for r in rows)
    tot_ns   = sum(r["no_shows"] for r in rows)
    tot_live = sum(r["live"] for r in rows)
    tot_unot = sum(r["live_unnotif"] for r in rows)

    lines.append(f"*Group totals*")
    lines.append(f"  Signups: `{tot_sign}`  ·  Seated: `{tot_seat}` ({pct(tot_seat, tot_sign)})")
    lines.append(f"  Cancelled: `{tot_canc}`  ·  No-shows: `{tot_ns}`")
    lines.append(f"  Live now: `{tot_live}`  ·  un-notified: `{tot_unot}`")
    lines.append("")

    # Per-restaurant block
    # Sort by signups desc, closed at the bottom
    rows_sorted = sorted(rows, key=lambda r: (-int(bool(r["is_open"])), -r["signups"]))
    for r in rows_sorted:
        flag = "🟢" if r["is_open"] else "⚪"
        lines.append(f"{flag} *{r['name']}*  ({r['country']})")
        if r["signups"] == 0:
            lines.append(f"   No signups.  Live now `{r['live']}`")
        else:
            base = f"  vs 7d {pct(int(r['base_conv']*100) if r['base_conv'] else 0,100) if r['base_conv'] else '—'}"
            lines.append(
                f"   Funnel: `{r['signups']}` → `{r['notified']}` notified → `{r['seated']}` seated  ({pct(r['seated'], r['signups'])}{base})"
            )
            lines.append(
                f"   Cancel `{r['cancelled']}`  ·  no-show `{r['no_shows']}`  ·  notify-speed `{fmt_min(r['avg_speed_min'])}`"
            )
            if r["reasons"]:
                top = sorted(r["reasons"].items(), key=lambda x: -x[1])[:3]
                lines.append("   Reasons: " + "  ".join(f"{k} `{v}`" for k, v in top))
            if r["live"]:
                lines.append(f"   Live now: `{r['live']}` waiting · `{r['live_unnotif']}` un-notified")
        lines.append("")

    # Callout
    lines.append("*Today's priority*")
    lines.append(pick_callout(rows))
    lines.append("")
    lines.append(f"_Generated {datetime.utcnow().strftime('%H:%M')} UTC · monitor every 5 min_")
    return "\n".join(lines)


def send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOK}/sendMessage",
        json={"chat_id": TG_CHT, "text": text, "parse_mode": "Markdown",
              "disable_web_page_preview": True},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def send_document(pdf_path, caption=""):
    with open(pdf_path, "rb") as f:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOK}/sendDocument",
            data={"chat_id": TG_CHT, "caption": caption, "parse_mode": "Markdown"},
            files={"document": (pdf_path.name, f, "application/pdf")},
            timeout=60,
        )
    r.raise_for_status()
    return r.json()


# ── Main ────────────────────────────────────────────────────────────────
def run(target="yesterday"):
    today_local = (datetime.now(timezone.utc) + timedelta(hours=TZ_OFFSET_HOURS)).date()
    if target == "today":
        d = today_local
        label = "today so far"
    else:
        d = today_local - timedelta(days=1)
        label = "yesterday"

    rows = [analyze(r, d) for r in fetch_restaurants()]
    msg = build_message(rows, d, label)
    callout = pick_callout(rows)

    # Build + send the PDF (primary deliverable)
    try:
        from pdf_report import build_pdf
        pdf = build_pdf(rows, d, label, callout)
        caption = (
            f"📊 *HyperWait Daily Report* — {label}\n"
            f"_{d.strftime('%a %d %b %Y')}  ·  {len(rows)} restaurants_\n\n"
            f"{callout}"
        )
        send_document(pdf, caption=caption[:1000])
        print(f"PDF sent: {pdf}")
    except Exception as e:
        # Fallback to text-only if PDF generation fails
        print(f"PDF failed, sending text instead: {e}")
        for c in [msg[i:i+3800] for i in range(0, len(msg), 3800)]:
            send(c)
    return msg


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--today", action="store_true", help="report for today so far instead of yesterday")
    args = p.parse_args()
    out = run("today" if args.today else "yesterday")
    print(out)
