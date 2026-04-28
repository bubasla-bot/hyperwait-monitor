#!/usr/bin/env python3
"""
HyperWait Daily Report — PDF renderer.

Pure-Python (reportlab) so it runs on Replit with no system deps.
Produces a single A4 page in dark-luxury style:
  • Title bar with date
  • Group totals
  • One card per restaurant with funnel + KPIs + cancellation reasons
  • Priority callout panel
  • Footer

Usage (from daily_report.run):
  pdf_path = build_pdf(rows, target_date, label, callout)
"""
from datetime import datetime
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ── Palette (dark Levantine luxury) ─────────────────────────────────────
BG       = HexColor("#09090E")
INK      = HexColor("#F5F2EA")
INK_DIM  = HexColor("#9C9A92")
INK_MUTE = HexColor("#5A5852")
GOLD     = HexColor("#C49A3C")
LINE     = HexColor("#1F1F26")
CARD     = HexColor("#11111A")
OK       = HexColor("#2A9D74")
DANGER   = HexColor("#B54C4C")
WARN     = HexColor("#C4832A")

PW, PH = A4    # 210 × 297 mm
M = 15 * mm

# Fonts: stay with built-ins (Helvetica). Reportlab is portable; a custom font
# would require shipping TTF files in the repo. Helvetica looks crisp.
SANS  = "Helvetica"
SANSB = "Helvetica-Bold"
MONO  = "Courier-Bold"


# ── Helpers ─────────────────────────────────────────────────────────────
def pct(n, d): return f"{n/d*100:.0f}%" if d else "—"
def fmt_min(v):
    if v is None: return "—"
    return f"{v:.0f}m" if v < 60 else f"{v/60:.1f}h"


# ── Drawing primitives ──────────────────────────────────────────────────
def fill_rect(c, x, y, w, h, color):
    c.setFillColor(color); c.rect(x, y, w, h, stroke=0, fill=1)

def draw_text(c, x, y, txt, font=SANS, size=9, color=INK):
    c.setFont(font, size); c.setFillColor(color); c.drawString(x, y, str(txt))

def draw_text_right(c, x, y, txt, font=SANS, size=9, color=INK):
    c.setFont(font, size); c.setFillColor(color); c.drawRightString(x, y, str(txt))

def hline(c, x1, x2, y, color=LINE, w=0.4):
    c.setStrokeColor(color); c.setLineWidth(w); c.line(x1, y, x2, y)


# ── Layout ──────────────────────────────────────────────────────────────
def header(c, target_date, label):
    fill_rect(c, 0, 0, PW, PH, BG)

    # Top brand strip
    fill_rect(c, M, PH - M - 0.6 * mm, 8 * mm, 0.6 * mm, GOLD)
    draw_text(c, M, PH - M - 5 * mm, "HYPERWAIT", SANSB, 9, GOLD)
    draw_text_right(c, PW - M, PH - M - 5 * mm,
                    datetime.utcnow().strftime("Generated %d %b %Y · %H:%M UTC"),
                    SANS, 8, INK_DIM)

    # Title
    draw_text(c, M, PH - M - 18 * mm, "Daily Operations Report", SANSB, 22, INK)
    draw_text(c, M, PH - M - 25 * mm,
              f"{label.capitalize()} · {target_date.strftime('%A, %d %B %Y')}",
              SANS, 11, INK_DIM)
    hline(c, M, PW - M, PH - M - 30 * mm, LINE, 0.6)


def totals_card(c, y, rows):
    tot_sign = sum(r["signups"] for r in rows)
    tot_seat = sum(r["seated"] for r in rows)
    tot_canc = sum(r["cancelled"] for r in rows)
    tot_ns   = sum(r["no_shows"] for r in rows)
    tot_live = sum(r["live"] for r in rows)
    tot_unot = sum(r["live_unnotif"] for r in rows)

    H = 26 * mm
    fill_rect(c, M, y - H, PW - 2 * M, H, CARD)
    fill_rect(c, M, y - H, 1.5 * mm, H, GOLD)  # accent

    draw_text(c, M + 6 * mm, y - 6 * mm, "GROUP TOTALS", SANSB, 8, INK_DIM)

    # Stats row
    cols = [
        ("Signups",   f"{tot_sign}",      INK),
        ("Seated",    f"{tot_seat}",      OK if tot_sign and tot_seat/tot_sign >= .25 else INK),
        ("Conv.",     pct(tot_seat, tot_sign), GOLD),
        ("Cancelled", f"{tot_canc}",      INK),
        ("No-shows",  f"{tot_ns}",        DANGER if tot_ns >= 5 else INK),
        ("Live now",  f"{tot_live}",      INK),
        ("Un-notified", f"{tot_unot}",    DANGER if tot_unot else INK),
    ]
    col_w = (PW - 2 * M - 12 * mm) / len(cols)
    cx = M + 6 * mm
    for label, val, color in cols:
        draw_text(c, cx, y - 13 * mm, label.upper(), SANS, 7, INK_MUTE)
        draw_text(c, cx, y - 21 * mm, val, MONO, 16, color)
        cx += col_w

    return y - H - 6 * mm


def reason_pill(c, x, y, label, count, w=22 * mm, h=4.6 * mm):
    fill_rect(c, x, y, w, h, HexColor("#1A1A22"))
    draw_text(c, x + 1.6 * mm, y + 1.4 * mm, label[:14], SANS, 6.5, INK_DIM)
    draw_text_right(c, x + w - 1.6 * mm, y + 1.4 * mm, str(count), MONO, 7.5, GOLD)


def restaurant_card(c, y, r):
    H = 36 * mm
    x = M
    w = PW - 2 * M
    fill_rect(c, x, y - H, w, H, CARD)

    # Status dot
    dot = OK if r["is_open"] else INK_MUTE
    c.setFillColor(dot); c.circle(x + 5 * mm, y - 5.5 * mm, 1.2 * mm, fill=1, stroke=0)

    # Name + country
    draw_text(c, x + 9 * mm, y - 6 * mm, r["name"], SANSB, 12, INK)
    draw_text(c, x + 9 * mm + len(r["name"]) * 2.4 + 4 * mm, y - 6 * mm,
              f"· {r['country']}", SANS, 10, INK_DIM)

    # No-data shortcut
    if r["signups"] == 0:
        draw_text(c, x + 9 * mm, y - 14 * mm, "No signups yesterday.", SANS, 9, INK_DIM)
        draw_text_right(c, x + w - 6 * mm, y - 6 * mm,
                        f"Live now {r['live']}", MONO, 9, INK_DIM)
        return y - H - 4 * mm

    # KPI grid
    kpis = [
        ("Signups",  f"{r['signups']}",                            INK),
        ("Notified", f"{r['notified']}",                            INK),
        ("Seated",   f"{r['seated']}",                              OK),
        ("Conv.",    pct(r["seated"], r["signups"]),                GOLD),
        ("Cancel",   f"{r['cancelled']}",                           INK),
        ("No-show",  f"{r['no_shows']}",                            DANGER if r["no_shows"] else INK),
        ("Notify-speed", fmt_min(r["avg_speed_min"]),
            WARN if (r["avg_speed_min"] or 0) > 60 else INK),
    ]
    col_w = (w - 14 * mm) / len(kpis)
    cx = x + 9 * mm
    for label, val, color in kpis:
        draw_text(c, cx, y - 14 * mm, label.upper(), SANS, 6.5, INK_MUTE)
        draw_text(c, cx, y - 20 * mm, val, MONO, 12, color)
        cx += col_w

    # Baseline conv chip
    if r["base_conv"] is not None and r["signups"]:
        cur = r["seated"] / r["signups"]
        delta = cur - r["base_conv"]
        sign = "+" if delta >= 0 else "−"
        col = OK if delta >= 0 else DANGER
        draw_text_right(c, x + w - 6 * mm, y - 6 * mm,
                        f"{sign}{abs(delta)*100:.0f} pts vs 7d avg ({r['base_conv']*100:.0f}%)",
                        SANS, 8, col)

    # Cancellation reasons
    if r["reasons"]:
        draw_text(c, x + 9 * mm, y - 26 * mm, "TOP CANCEL REASONS",
                  SANSB, 6.5, INK_MUTE)
        cx = x + 9 * mm
        for label, count in sorted(r["reasons"].items(), key=lambda kv: -kv[1])[:5]:
            reason_pill(c, cx, y - 33 * mm, label.replace("_", " "), count)
            cx += 24 * mm

    # Live now indicator (right edge)
    if r["live"]:
        col = DANGER if r["live_unnotif"] >= 3 else INK_DIM
        draw_text_right(c, x + w - 6 * mm, y - 32 * mm,
                        f"LIVE: {r['live']} waiting · {r['live_unnotif']} un-notified",
                        SANSB, 7.5, col)

    return y - H - 4 * mm


def callout_panel(c, y, callout_text):
    H = 18 * mm
    x, w = M, PW - 2 * M
    fill_rect(c, x, y - H, w, H, HexColor("#16110A"))
    fill_rect(c, x, y - H, 1.5 * mm, H, GOLD)
    draw_text(c, x + 6 * mm, y - 6 * mm, "TODAY'S PRIORITY", SANSB, 8, GOLD)

    # wrap callout (no markdown). simple wrap:
    body = callout_text
    for ch in ("*", "_", "`"):
        body = body.replace(ch, "")
    # naive wrap to 3 lines
    max_w = w - 12 * mm
    c.setFont(SANS, 10)
    words = body.split()
    line, lines = "", []
    for word in words:
        trial = (line + " " + word).strip()
        if c.stringWidth(trial, SANS, 10) > max_w:
            lines.append(line); line = word
        else:
            line = trial
    if line: lines.append(line)
    for i, ln in enumerate(lines[:2]):
        draw_text(c, x + 6 * mm, y - 11 * mm - i * 4.6 * mm, ln, SANS, 10, INK)

    return y - H - 4 * mm


def footer(c):
    hline(c, M, PW - M, M + 6 * mm, LINE, 0.4)
    draw_text(c, M, M + 2 * mm, "HYPERWAIT · OPS", SANSB, 7, INK_MUTE)
    draw_text_right(c, PW - M, M + 2 * mm,
                    "Confidential · Gastronomica Group", SANS, 7, INK_MUTE)


# ── Public API ──────────────────────────────────────────────────────────
def build_pdf(rows, target_date, label, callout, out_path=None):
    if out_path is None:
        out_path = Path(__file__).parent / f"hyperwait_daily_{target_date.isoformat()}.pdf"
    out_path = Path(out_path)

    c = canvas.Canvas(str(out_path), pagesize=A4)
    header(c, target_date, label)

    y = PH - M - 36 * mm
    y = totals_card(c, y, rows)

    # Restaurants — open first, then by signups desc
    rs = sorted(rows, key=lambda r: (-int(bool(r["is_open"])), -r["signups"]))
    for r in rs:
        if y < M + 50 * mm:  # leave room for callout + footer; break to new page
            footer(c); c.showPage(); header(c, target_date, label)
            y = PH - M - 36 * mm
        y = restaurant_card(c, y, r)

    if y < M + 30 * mm:
        footer(c); c.showPage(); header(c, target_date, label)
        y = PH - M - 36 * mm

    callout_panel(c, y, callout)
    footer(c)
    c.save()
    return out_path
