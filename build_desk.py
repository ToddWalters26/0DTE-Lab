#!/usr/bin/env python3
"""
0DTE Desk generator — pulls SPY/AAPL data, runs rules, writes index.html.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
OUT = Path(__file__).resolve().parent / "index.html"
UA = {"User-Agent": "Mozilla/5.0 (compatible; DeskBot/1.0)"}

DOW = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


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
            "now_et": datetime.now(ET),
        }

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
    trail_large = sum(1 for x in trail if x > 7) / len(trail) if trail else 0.0

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


def next_weekday(from_dt: datetime) -> datetime:
    d = from_dt
    for _ in range(1, 8):
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            return d
    return from_dt + timedelta(days=1)


def remaining_weekdays(from_dt: datetime) -> list[datetime]:
    """Upcoming weekdays in the current calendar week (Mon–Fri), plus next week if weekend."""
    out = []
    # If weekend or after Friday close conceptually, show next Mon–Wed-ish
    start = from_dt
    if from_dt.weekday() >= 5:
        start = next_weekday(from_dt)
    elif from_dt.hour >= 16:
        start = next_weekday(from_dt)

    # Collect next 5 weekdays from start (covers rest of week + spill)
    d = start
    while len(out) < 5:
        if d.weekday() < 5:
            out.append(d)
        d = d + timedelta(days=1)
    return out


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


def entry_stack(side: str, s: dict, after_10: bool) -> dict:
    m, o, bv, av = s.get("move_30"), s.get("or_30"), s.get("below_vwap_30"), s.get("above_vwap_30")
    if not after_10:
        return {"met": False, "reason": "Wait until 10:00 ET to judge the entry stack."}
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


def esc(t: str) -> str:
    return (
        t.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def px(x) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x):.2f}"
    except Exception:
        return "—"


def build_html(spy: dict, aapl: dict) -> str:
    now = spy.get("now_et") or datetime.now(ET)
    hm = now.hour * 60 + now.minute
    after_open = hm >= 570
    after_10 = hm >= 600
    after_close = hm >= 960 or now.weekday() >= 5

    # Focus day: if after close or weekend, brief the NEXT trading day
    if after_close or now.weekday() >= 5:
        focus = next_weekday(now)
        mode = "next_day"
    else:
        focus = now
        mode = "today"

    focus_name = DOW[focus.weekday()]
    focus_date = focus.strftime("%b %-d")

    vp = vol_persist_score(spy.get("prev_rng"), spy.get("trail_large"), spy.get("trail_avg"))
    put = entry_stack("put", spy, after_10 and mode == "today")
    call = entry_stack("call", spy, after_10 and mode == "today")

    # Levels from latest session
    last = spy.get("last") or spy.get("price")
    high = spy.get("high")
    low = spy.get("low")
    day_range = spy.get("range")

    # Confidence for next/today setup
    conf = 55
    if vp >= 4:
        conf += 10
    if vp >= 6:
        conf += 5
    if day_range and day_range > 7:
        conf += 5
    # Monday gap-hold reduced pure bear continuation
    if spy.get("open") and spy.get("prev") and spy["open"] > spy["prev"] + 1:
        conf = max(45, conf - 8)
    conf = max(40, min(78, conf))

    # Always 3 trade options for remaining week — independent of whether user traded
    days = remaining_weekdays(now)
    # Prefer: first upcoming = primary SPY, second = SPY alternate, third = AAPL
    d1 = days[0]
    d2 = days[1] if len(days) > 1 else days[0]
    d3 = days[2] if len(days) > 2 else days[-1]

    trade1 = {
        "title": f"Trade 1 · SPY · {DOW[d1.weekday()]} {d1.strftime('%b %-d')}",
        "conf": conf,
        "body": (
            f"Primary slot. At 10:00 ET, check three things: how far price has moved from the open, "
            f"how wide the first 30 minutes were, and whether price is above or below VWAP. "
            f"If SPY has dropped about 1.50 or more from the open, the opening range is about 2.00 or more, "
            f"and price is below VWAP, take the put side toward roughly {px((low or 760) - 5)}–{px((low or 760) - 3)} "
            f"if the drive continues. If SPY has risen about 1.50 or more, the range is wide, and price is above VWAP, "
            f"take the call side toward roughly {px((high or 770) + 4)}–{px((high or 770) + 6)}. "
            f"If the first 30 minutes are quiet, skip this day and keep the slot."
        ),
    }
    trade2 = {
        "title": f"Trade 2 · SPY · {DOW[d2.weekday()]} {d2.strftime('%b %-d')}",
        "conf": max(40, conf - 10),
        "body": (
            f"Second SPY slot. Use the same 10:00 rules as Trade 1. "
            f"This is available whether or not you took Trade 1 — the desk always keeps three options listed "
            f"so you can choose based on your own trade count. Prefer this day if {DOW[d1.weekday()]} was quiet "
            f"or the drive never confirmed."
        ),
    }
    trade3 = {
        "title": f"Trade 3 · AAPL · {DOW[d3.weekday()]} {d3.strftime('%b %-d')}",
        "conf": 45,
        "body": (
            "Secondary ticker. Only use if you still want a third name or SPY is not clear. "
            "Watch a failed hold near the mid-340s with a clear open drive and VWAP agreement. "
            "If the open is choppy, skip AAPL entirely."
        ),
    }

    if mode == "next_day":
        headline = (
            f"Session is closed. Here is the briefing for {focus_name}, {focus_date}."
        )
        sub = (
            f"SPY last finished around {px(last)} with a day range of about {px(day_range)}. "
            f"Vol-persist score is {vp}. The three trade options below are for the rest of the week; "
            f"use whichever fit your own three-trade limit."
        )
        stack_title = f"{focus_name} plan (decide at 10:00)"
        stack_status = "WAITING FOR 10:00"
        stack_class = "watch"
        stack_body = (
            f"Do not enter before 10:00 ET on {focus_name}. "
            "After the first 30 minutes, take a trade only if the drive, opening range, and VWAP side all agree. "
            "Otherwise sit out."
        )
        put_line = "Put stack will be judged at 10:00 from that morning’s open."
        call_line = "Call stack will be judged at 10:00 from that morning’s open."
    else:
        if put["met"]:
            headline = "SPY put entry conditions are met as of this update."
            stack_status = "PUT CONDITIONS MET"
            stack_class = "met"
        elif call["met"]:
            headline = "SPY call entry conditions are met as of this update."
            stack_status = "CALL CONDITIONS MET"
            stack_class = "met"
        elif after_10:
            headline = "The 10:00 entry stack is not met for a clean SPY drive trade."
            stack_status = "CONDITIONS NOT MET"
            stack_class = "notmet"
        elif after_open:
            headline = "The session is open. Wait for 10:00 before deciding."
            stack_status = "WAITING FOR 10:00"
            stack_class = "watch"
        else:
            headline = f"Pre-market briefing for {focus_name}, {focus_date}."
            stack_status = "PRE-MARKET"
            stack_class = "watch"
        sub = (
            f"SPY is around {px(spy.get('price'))}. "
            f"Day range so far is about {px(day_range)}. Vol-persist score is {vp}."
        )
        stack_title = "Today’s entry stack"
        stack_body = "Use the put and call checks below. Only act if one side fully qualifies."
        put_line = put["reason"]
        call_line = call["reason"]

    stamp = now.strftime("%a %b %-d · %-I:%M %p ET")

    spy_prices = (
        f"Last <strong>{px(spy.get('price'))}</strong> · Prev close {px(spy.get('prev'))}<br>"
        f"Session open {px(spy.get('open'))} · High {px(spy.get('high'))} · Low {px(spy.get('low'))} · "
        f"Range {px(spy.get('range'))}"
    )
    aapl_prices = (
        f"Last <strong>{px(aapl.get('price'))}</strong> · Prev close {px(aapl.get('prev'))}<br>"
        f"Session open {px(aapl.get('open'))} · High {px(aapl.get('high'))} · Low {px(aapl.get('low'))} · "
        f"Range {px(aapl.get('range'))}"
    )

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
.updated{{font-size:11px;color:var(--dim);margin-bottom:12px;}}
.call{{font-size:17px;font-weight:600;line-height:1.4;margin-bottom:8px;}}
.meta{{font-size:13px;color:var(--muted);margin-bottom:18px;}}
.section-title{{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:0.09em;color:var(--dim);margin:14px 0 10px;}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;margin-bottom:12px;}}
.card.ok{{border-color:rgba(0,230,118,0.3);}}
.card.warn{{border-color:rgba(255,196,0,0.3);}}
.card.bad{{border-color:rgba(255,23,68,0.25);}}
.card h3{{font-size:15px;font-weight:600;margin-bottom:6px;}}
.card p{{font-size:13px;color:var(--muted);margin-bottom:8px;}}
.card p:last-child{{margin-bottom:0;}}
.card strong{{color:var(--text);}}
.price-line{{font-size:13px;font-variant-numeric:tabular-nums;color:var(--text);margin:8px 0;padding:8px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border);}}
.status{{font-size:13px;font-weight:600;margin:8px 0;}}
.status.met{{color:var(--green);}}
.status.notmet{{color:var(--red);}}
.status.watch{{color:var(--amber);}}
.conf{{font-weight:600;color:var(--amber);}}
.footer{{text-align:center;color:var(--dim);font-size:11px;margin-top:28px;line-height:1.5;}}
</style>
</head>
<body>
<div class="top"><h1>0DTE Desk</h1><span style="font-size:11px;color:var(--dim);">Auto-built</span></div>
<div class="updated">Built {esc(stamp)} · next auto rebuild on the weekday schedule</div>

<div class="call">{esc(headline)}</div>
<div class="meta">{esc(sub)}</div>

<div class="section-title">Daily briefing · {esc(focus_name)} {esc(focus_date)}</div>
<div class="card warn">
  <h3>What to watch</h3>
  <p>SPY reference levels from the latest session: high {px(high)}, low {px(low)}, last {px(last)}.</p>
  <p>At 10:00 ET on {esc(focus_name)}, decide with the drive + opening-range + VWAP rule. Do not force a trade if the first 30 minutes are quiet.</p>
  <p>You can take up to three trades this week. The list below always shows three options; pick based on what you have already done.</p>
</div>

<div class="section-title">Where prices are now</div>
<div class="card">
  <h3>SPY</h3>
  <div class="price-line">{spy_prices}</div>
  <p>First-30-minute move: {px(spy.get('move_30'))}. Opening range: {px(spy.get('or_30'))}.</p>
</div>
<div class="card">
  <h3>AAPL</h3>
  <div class="price-line">{aapl_prices}</div>
  <p>Secondary name this week.</p>
</div>

<div class="section-title">{esc(stack_title)}</div>
<div class="card {'ok' if stack_class=='met' else 'bad' if stack_class=='notmet' else 'warn'}">
  <p class="status {stack_class}">{esc(stack_status)}</p>
  <p>{esc(stack_body)}</p>
  <p><strong>Put check:</strong> {esc(put_line)}</p>
  <p><strong>Call check:</strong> {esc(call_line)}</p>
</div>

<div class="section-title">Three trade options (always listed)</div>
<div class="card ok">
  <h3>{esc(trade1['title'])}</h3>
  <p><span class="conf">Confidence about {trade1['conf']}%</span></p>
  <p>{esc(trade1['body'])}</p>
</div>
<div class="card">
  <h3>{esc(trade2['title'])}</h3>
  <p><span class="conf">Confidence about {trade2['conf']}%</span></p>
  <p>{esc(trade2['body'])}</p>
</div>
<div class="card">
  <h3>{esc(trade3['title'])}</h3>
  <p><span class="conf">Confidence about {trade3['conf']}%</span></p>
  <p>{esc(trade3['body'])}</p>
</div>

<div class="section-title">Simple rules</div>
<div class="card">
  <p>1. After the close, this page shifts the briefing to the next trading day.</p>
  <p>2. At 10:00, take a side only if move size, opening range, and VWAP all agree.</p>
  <p>3. Three options stay listed all week so you can choose without reporting fills to the model.</p>
</div>

<div class="footer">Not financial advice. Generated by build_desk.py · research only.</div>
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
        f"move30={spy.get('move_30')} mode_time={spy.get('now_et')}"
    )


if __name__ == "__main__":
    main()
