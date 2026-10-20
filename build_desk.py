#!/usr/bin/env python3
"""
0DTE Desk generator — pulls SPY/AAPL data, runs the combined model rules,
and writes index.html next to this script (GitHub Pages entry point).

Usage:
  python3 build_desk.py

Optional schedule (Mac/Linux cron example, every weekday 9:45 and 10:05 ET):
  45 9 * * 1-5 cd /path/to/folder && python3 build_desk.py
  5 10 * * 1-5 cd /path/to/folder && python3 build_desk.py
"""

from __future__ import annotations

import json
import math
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
OUT = Path(__file__).resolve().parent / "index.html"
UA = {"User-Agent": "Mozilla/5.0 (compatible; DeskBot/1.0)"}


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())


def chart(symbol: str, interval: str, range_: str) -> dict:
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?interval={interval}&range={range_}"
    )
    return fetch_json(url)["chart"]["result"][0]


def session_from_5m(symbol: str) -> dict:
    """Regular-session summary from 5m bars (ET)."""
    res = chart(symbol, "5m", "5d")
    meta = res["meta"]
    ts = res["timestamp"]
    q = res["indicators"]["quote"][0]
    bars = []
    for i, t in enumerate(ts):
        if q["close"][i] is None or q["open"][i] is None:
            continue
        et = datetime.fromtimestamp(t, tz=timezone.utc).astimezone(ET)
        hm = et.hour * 60 + et.minute
        # RTH 9:30–16:00 ET
        if hm < 570 or hm > 960:
            continue
        bars.append(
            {
                "date": et.strftime("%Y-%m-%d"),
                "hm": hm,
                "o": float(q["open"][i]),
                "h": float(q["high"][i]),
                "l": float(q["low"][i]),
                "c": float(q["close"][i]),
                "v": float(q["volume"][i] or 0),
            }
        )
    if not bars:
        return {
            "symbol": symbol,
            "price": meta.get("regularMarketPrice"),
            "prev": meta.get("previousClose") or meta.get("chartPreviousClose"),
            "error": "no RTH bars",
        }

    # last trading date in feed
    last_date = bars[-1]["date"]
    day = [b for b in bars if b["date"] == last_date]
    day_o = day[0]["o"]
    day_h = max(b["h"] for b in day)
    day_l = min(b["l"] for b in day)
    day_c = day[-1]["c"]
    b30 = [b for b in day if b["hm"] <= 600]
    move_30 = or_30 = vwap_30 = None
    below_vwap_30 = above_vwap_30 = None
    if len(b30) >= 3:
        move_30 = b30[-1]["c"] - day_o
        or_30 = max(b["h"] for b in b30) - min(b["l"] for b in b30)
        cum_pv = cum_v = 0.0
        for b in b30:
            typ = (b["h"] + b["l"] + b["c"]) / 3
            cum_pv += typ * b["v"]
            cum_v += b["v"]
        vwap_30 = cum_pv / cum_v if cum_v else b30[-1]["c"]
        below_vwap_30 = b30[-1]["c"] < vwap_30 - 0.05
        above_vwap_30 = b30[-1]["c"] > vwap_30 + 0.05

    # prior day range
    dates = sorted({b["date"] for b in bars})
    prev_rng = None
    if len(dates) >= 2:
        prev = [b for b in bars if b["date"] == dates[-2]]
        if prev:
            prev_rng = max(b["h"] for b in prev) - min(b["l"] for b in prev)

    trail = []
    for d in dates[-11:-1]:
        dd = [b for b in bars if b["date"] == d]
        if dd:
            trail.append(max(b["h"] for b in dd) - min(b["l"] for b in dd))
    trail_avg = sum(trail) / len(trail) if trail else None
    trail_large = sum(1 for x in trail if x > 7) / len(trail) if trail else 0

    return {
        "symbol": symbol,
        "date": last_date,
        "price": float(meta.get("regularMarketPrice") or day_c),
        "prev": float(meta.get("previousClose") or meta.get("chartPreviousClose") or day_o),
        "open": day_o,
        "high": day_h,
        "low": day_l,
        "last": day_c,
        "range": day_h - day_l,
        "move_from_open": day_c - day_o,
        "move_30": move_30,
        "or_30": or_30,
        "vwap_30": vwap_30,
        "below_vwap_30": below_vwap_30,
        "above_vwap_30": above_vwap_30,
        "prev_rng": prev_rng,
        "trail_avg": trail_avg,
        "trail_large": trail_large,
        "now_et": datetime.now(ET),
    }


def vol_persist_score(prev_rng, trail_large, trail_avg) -> int:
    s = 0
    if prev_rng is not None:
        if prev_rng > 10:
            s += 3
        elif prev_rng > 7:
            s += 2
        elif prev_rng > 5:
            s += 1
    if trail_large is not None:
        if trail_large >= 0.4:
            s += 2
        elif trail_large >= 0.3:
            s += 1
    if trail_avg is not None and trail_avg >= 7:
        s += 1
    return s


def entry_stack(side: str, s: dict) -> dict:
    """Return whether drive+OR+VWAP stack is met for put/call."""
    m, o, bv, av = s.get("move_30"), s.get("or_30"), s.get("below_vwap_30"), s.get("above_vwap_30")
    if m is None or o is None:
        return {"met": False, "reason": "Not enough first-30-minute data yet."}
    if side == "put":
        ok = m <= -1.5 and o >= 2.0 and bool(bv)
        parts = [
            f"move from open in first 30 minutes was {m:+.2f} (need about -1.50 or more)",
            f"opening range was {o:.2f} (need about 2.00 or more)",
            "price was below VWAP" if bv else "price was not below VWAP",
        ]
        return {"met": ok, "reason": "; ".join(parts) + "."}
    ok = m >= 1.5 and o >= 2.0 and bool(av)
    parts = [
        f"move from open in first 30 minutes was {m:+.2f} (need about +1.50 or more)",
        f"opening range was {o:.2f} (need about 2.00 or more)",
        "price was above VWAP" if av else "price was not above VWAP",
    ]
    return {"met": ok, "reason": "; ".join(parts) + "."}


def html_escape(t: str) -> str:
    return (
        t.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_html(spy: dict, aapl: dict) -> str:
    now = spy.get("now_et") or datetime.now(ET)
    hm = now.hour * 60 + now.minute
    weekday = now.weekday()  # 0=Mon
    after_open = hm >= 570
    after_10 = hm >= 600
    session_date = spy.get("date") or now.strftime("%Y-%m-%d")

    vp = vol_persist_score(spy.get("prev_rng"), spy.get("trail_large"), spy.get("trail_avg"))
    put = entry_stack("put", spy) if after_10 else {"met": False, "reason": "Wait until 10:00 ET to judge the entry stack."}
    call = entry_stack("call", spy) if after_10 else {"met": False, "reason": "Wait until 10:00 ET to judge the entry stack."}

    # Monday optional put was the weekend plan; evaluate if today is that Monday session
    monday_put_closed = session_date >= "2026-10-19" and (weekday > 0 or (weekday == 0 and after_10 and not put["met"]))

    # Confidence heuristics
    tue_conf = 55
    if vp >= 4:
        tue_conf += 8
    if spy.get("range") and spy["range"] > 7 and weekday == 0:
        tue_conf += 5
    # If Monday held gap up, lean less on pure puts
    if spy.get("open") and spy.get("prev") and spy["open"] > spy["prev"] + 1:
        tue_conf = max(45, tue_conf - 10)
    tue_conf = max(35, min(78, tue_conf))

    if put["met"]:
        action = "PUT CONDITIONS MET"
        action_class = "met"
        headline = (
            f"SPY put entry stack is met as of {now.strftime('%I:%M %p')} ET. "
            "Only take the trade if you still have a free weekly slot and risk is acceptable."
        )
        conf_note = f"Intraday confirmation raised confidence on the downside path for today."
    elif call["met"]:
        action = "CALL CONDITIONS MET"
        action_class = "met"
        headline = (
            f"SPY call entry stack is met as of {now.strftime('%I:%M %p')} ET. "
            "Only take the trade if you still have a free weekly slot and risk is acceptable."
        )
        conf_note = "Intraday confirmation favors the upside path for today."
    elif after_10:
        action = "CONDITIONS NOT MET"
        action_class = "notmet"
        headline = (
            "The 10:00 entry stack is not met for a clean SPY drive trade. "
            "Default action is to sit out unless a later rebuild shows a new setup."
        )
        conf_note = "No drive-and-VWAP confirmation yet, so direction confidence stays moderate."
    elif after_open:
        action = "WAITING FOR 10:00"
        action_class = "watch"
        headline = (
            "The regular session has started. Wait for the first 30 minutes to finish, "
            "then refresh this page to see whether put or call conditions are met."
        )
        conf_note = "Pre-10:00 — no entry decision yet."
    else:
        action = "PRE-MARKET / CLOSED"
        action_class = "watch"
        headline = (
            "Outside the decision window. When the next regular session reaches 10:00 ET, "
            "run this script again (or use your schedule) for a conditions met / not met update."
        )
        conf_note = "Model levels update when you rebuild during RTH."

    def px(x):
        return "—" if x is None else f"{x:.2f}"

    spy_price_block = (
        f"Last <strong>{px(spy.get('price'))}</strong> · Prev close {px(spy.get('prev'))}<br>"
        f"Session open {px(spy.get('open'))} · High {px(spy.get('high'))} · Low {px(spy.get('low'))} · "
        f"Range {px(spy.get('range'))}"
    )
    aapl_price_block = (
        f"Last <strong>{px(aapl.get('price'))}</strong> · Prev close {px(aapl.get('prev'))}<br>"
        f"Session open {px(aapl.get('open'))} · High {px(aapl.get('high'))} · Low {px(aapl.get('low'))} · "
        f"Range {px(aapl.get('range'))}"
    )

    mon_status = (
        "<p class=\"status notmet\">Conditions not met by 10:00 — Monday put slot is closed.</p>"
        if monday_put_closed
        else f"<p class=\"status {action_class}\">{html_escape(action)}</p>"
    )

    stamp = now.strftime("%a %b %-d · %-I:%M %p ET") if hasattr(now, "strftime") else str(now)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="0DTE Desk">
<meta name="theme-color" content="#090a0c">
<title>0DTE Desk</title>
<style>
:root{{--bg:#090a0c;--card:#12141a;--border:#1e2229;--text:#f1f3f5;--muted:#8b929e;--dim:#5a616c;--green:#00e676;--red:#ff1744;--amber:#ffc400;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;background:var(--bg);color:var(--text);line-height:1.5;padding:20px 16px 56px;max-width:460px;margin:0 auto;}}
.top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;}}
.top h1{{font-size:12px;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:var(--muted);}}
.btn{{background:var(--card);border:1px solid var(--border);color:var(--text);font-size:12px;padding:6px 12px;border-radius:6px;}}
.tabs{{display:flex;gap:8px;margin-bottom:16px;}}
.tab{{flex:1;text-align:center;padding:8px;font-size:12px;font-weight:600;border-radius:8px;border:1px solid var(--border);background:var(--card);color:var(--muted);cursor:pointer;}}
.tab.active{{color:var(--text);border-color:rgba(0,230,118,0.35);}}
.panel{{display:none}}.panel.active{{display:block}}
.updated{{font-size:11px;color:var(--dim);margin-bottom:12px;}}
.call{{font-size:17px;font-weight:600;line-height:1.4;margin-bottom:8px;}}
.meta{{font-size:12px;color:var(--muted);margin-bottom:18px;}}
.section-title{{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:0.09em;color:var(--dim);margin:12px 0 10px;}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;margin-bottom:12px;}}
.card.ok{{border-color:rgba(0,230,118,0.3);}}
.card.warn{{border-color:rgba(255,196,0,0.3);}}
.card.bad{{border-color:rgba(255,23,68,0.25);}}
.card h3{{font-size:15px;font-weight:600;margin-bottom:6px;}}
.card p{{font-size:13px;color:var(--muted);margin-bottom:8px;}}
.card strong{{color:var(--text);}}
.price-line{{font-size:13px;font-variant-numeric:tabular-nums;color:var(--text);margin:8px 0;padding:8px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border);}}
.status{{font-size:13px;font-weight:600;margin:8px 0;}}
.status.met{{color:var(--green);}}
.status.notmet{{color:var(--red);}}
.status.watch{{color:var(--amber);}}
.row{{display:flex;justify-content:space-between;font-size:12px;padding:6px 0;border-bottom:1px solid var(--border);color:var(--muted);}}
.row:last-child{{border-bottom:none}}
.row .r{{color:var(--text);text-align:right;}}
.pill{{display:inline-block;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:600;}}
.pill-win{{background:rgba(0,230,118,0.12);color:var(--green);}}
.pill-skip{{background:rgba(90,97,108,0.2);color:var(--muted);}}
.pill-miss{{background:rgba(255,196,0,0.12);color:var(--amber);}}
.footer{{text-align:center;color:var(--dim);font-size:11px;margin-top:28px;}}
</style>
</head>
<body>
<div class="top">
  <h1>0DTE Desk</h1>
  <span style="font-size:11px;color:var(--dim);">Auto-built</span>
</div>
<div class="tabs">
  <div class="tab active" onclick="showTab('live')">Live</div>
  <div class="tab" onclick="showTab('perf')">Performance</div>
</div>

<div id="tab-live" class="panel active">
<div class="updated">Built {html_escape(stamp)} · run build_desk.py anytime for a fresh write-up</div>
<div class="call">{html_escape(headline)}</div>
<div class="meta">{html_escape(conf_note)} Vol-persist score (prior range + trail): {vp}.</div>

<div class="section-title">Where prices are now</div>
<div class="card">
  <h3>SPY</h3>
  <div class="price-line">{spy_price_block}</div>
  <p>Move from open: {px(spy.get('move_from_open'))}. First-30-minute move: {px(spy.get('move_30'))}. Opening range: {px(spy.get('or_30'))}.</p>
</div>
<div class="card">
  <h3>AAPL</h3>
  <div class="price-line">{aapl_price_block}</div>
  <p>Secondary ticker this week. Use only if SPY does not offer a clear slot.</p>
</div>

<div class="section-title">Entry stack status</div>
<div class="card {'ok' if put['met'] or call['met'] else 'bad' if after_10 else 'warn'}">
  <h3>SPY drive + VWAP check</h3>
  <p class="status {action_class}">{html_escape(action)}</p>
  <p><strong>Put stack:</strong> {html_escape(put['reason'])}</p>
  <p><strong>Call stack:</strong> {html_escape(call['reason'])}</p>
</div>

<div class="section-title">Monday slot</div>
<div class="card bad">
  <h3>Monday SPY put</h3>
  {mon_status}
  <p>That optional trade required a clear downside drive by 10:00. If those conditions were not met, the slot is closed and should not be chased.</p>
</div>

<div class="section-title">Rest of week (updates when you rebuild)</div>
<div class="card ok">
  <h3>SPY Tuesday / next session (primary)</h3>
  <p><strong>Confidence about {tue_conf}%</strong> for a tradeable expansion setup if the 10:00 stack confirms.</p>
  <p>Put path: wait for about 1.50 or more below the open by 10:00, opening range about 2.00 or more, and price below VWAP. Target area near 760–762 if the drive is down.</p>
  <p>Call path: about 1.50 or more above the open, wide opening range, price above VWAP. Target area near 774–776 if the drive is up.</p>
  <p>If the first 30 minutes are quiet, sit out and keep the weekly slot.</p>
</div>
<div class="card">
  <h3>AAPL Wed/Thu (secondary)</h3>
  <p><strong>Confidence about 45%</strong>. Only if SPY is already used or skipped. Watch the mid-340s; a failed hold with a clear drive is the idea, not a forced trade.</p>
</div>

<div class="section-title">How to refresh without chatting</div>
<div class="card">
  <p>On a computer, run <strong>python3 build_desk.py</strong> in this folder. It pulls prices, applies the rules, and overwrites this HTML.</p>
  <p>You can schedule that command on weekdays (for example 9:50 and 10:05 ET) so the file is ready when you open it on your phone via iCloud/Files or a local server.</p>
</div>
</div>

<div id="tab-perf" class="panel">
<div class="section-title">Running totals</div>
<div class="card">
  <p>Seed week Oct 13–16: 2 taken shorts scored as wins; Oct 15 upside missed (no 10:00 drive); quiet days skipped.</p>
  <p>Oct 19: Monday put conditions not met — recorded as a correct skip under the entry rule.</p>
  <p>Live sample is still small. Thresholds should not be changed until more weeks are logged.</p>
</div>
<div class="card">
  <div class="row"><span>Oct 14</span><span class="r"><span class="pill pill-win">WIN</span> short stack</span></div>
  <div class="row"><span>Oct 15</span><span class="r"><span class="pill pill-miss">MISS</span> no drive</span></div>
  <div class="row"><span>Oct 16</span><span class="r"><span class="pill pill-win">WIN</span> short stack</span></div>
  <div class="row"><span>Oct 19</span><span class="r"><span class="pill pill-skip">SKIP</span> conditions not met</span></div>
</div>
</div>

<div class="footer">Not financial advice. Generated locally by build_desk.py.</div>
<script>
function showTab(id){{
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('tab-'+id).classList.add('active');
  document.querySelectorAll('.tab')[id==='live'?0:1].classList.add('active');
}}
</script>
</body>
</html>
"""


def main():
    print("Fetching SPY…")
    spy = session_from_5m("SPY")
    print("Fetching AAPL…")
    aapl = session_from_5m("AAPL")
    html = build_html(spy, aapl)
    OUT.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT}")
    print(
        f"SPY last={spy.get('price')} range={spy.get('range')} "
        f"move30={spy.get('move_30')} or30={spy.get('or_30')} vp_prev={spy.get('prev_rng')}"
    )


if __name__ == "__main__":
    main()
