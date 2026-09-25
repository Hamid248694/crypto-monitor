#!/usr/bin/env python3
"""
Crypto Live Signal Tool
=======================
Cryexc wala hi public exchange data (OKX primary, MEXC fallback) leta hai
aur analyze karke BUY / SELL / WAIT signal + entry/SL/target levels deta hai.

Usage:
  python3 signal.py ONT              # ONT/USDT (default: 1H bias + 15m timing)
  python3 signal.py BTC --perp       # futures funding/OI bhi dekhna ho toh
  python3 signal.py ETH --no-cvd     # bina CVD ke (fast)

NOTE: Ye educational analysis tool hai, financial advice nahi.
"""

import sys
import math
import json
import argparse
from datetime import datetime, timezone

import requests

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (signal-tool)"})

# ---------------- data fetching ----------------

def okx_symbol(coin):
    return f"{coin.upper()}-USDT"

def okx_candles(inst, bar, want=400):
    """Fetch candles with pagination (recent 300 + history pages)."""
    r = SESSION.get("https://www.okx.com/api/v5/market/candles",
                    params={"instId": inst, "bar": bar, "limit": 300},
                    timeout=15)
    j = r.json()
    if j.get("code") != "0":
        return None
    rows = list(j["data"])
    while len(rows) < want:
        oldest = rows[-1][0]
        r = SESSION.get("https://www.okx.com/api/v5/market/history-candles",
                        params={"instId": inst, "bar": bar,
                                "after": oldest, "limit": 100},
                        timeout=15)
        j = r.json()
        if j.get("code") != "0" or not j["data"]:
            break
        rows = j["data"] + rows
        if len(j["data"]) < 100:
            break
    return rows[-want:]


def rows_to_candles(rows):
    """OKX rows (newest-first) -> oldest-first candle dicts."""
    candles = []
    for row in reversed(rows):  # -> oldest-first
        candles.append({
            "ts": int(row[0]),
            "o": float(row[1]), "h": float(row[2]),
            "c": float(row[3]), "l": float(row[4]),
            "v": float(row[5]),
        })
    return candles


def fetch_okx(coin):
    """Returns dict with candles_1h, candles_15m, trades, or None on failure."""
    inst = okx_symbol(coin)
    out = {}
    try:
        r = SESSION.get("https://www.okx.com/api/v5/market/ticker",
                        params={"instId": inst}, timeout=12)
        j = r.json()
        if j.get("code") != "0":
            return None
        out["ticker"] = j["data"][0]

        for bar, key, want in [("1H", "candles_1h", 400), ("15m", "candles_15m", 300)]:
            rows = okx_candles(inst, bar, want)
            if rows is None or len(rows) < 250:
                return None
            out[key] = rows_to_candles(rows)

        r = SESSION.get("https://www.okx.com/api/v5/market/trades",
                        params={"instId": inst, "limit": 1000}, timeout=15)
        j = r.json()
        if j.get("code") == "0":
            out["trades"] = [
                {"ts": int(t["ts"]), "px": float(t["px"]),
                 "sz": float(t["sz"]), "side": t["side"]}
                for t in j["data"]
            ]
        return out
    except Exception:
        return None


def fetch_mexc(coin):
    """Fallback: MEXC klines include taker-buy volume -> CVD possible."""
    sym = coin.upper() + "USDT"
    out = {}
    try:
        r = SESSION.get("https://api.mexc.com/api/v3/ticker/24hr",
                        params={"symbol": sym}, timeout=12)
        t = r.json()
        if "lastPrice" not in t:
            return None
        out["ticker"] = {
            "last": t["lastPrice"],
            "open24h": t["openPrice"],
            "high24h": t["highPrice"], "low24h": t["lowPrice"],
            "volCcy24h": t["quoteVolume"],
        }
        for interval, key in [("1h", "candles_1h"), ("15m", "candles_15m")]:
            r = SESSION.get("https://api.mexc.com/api/v3/klines",
                            params={"symbol": sym, "interval": interval,
                                    "limit": 500}, timeout=15)
            rows = r.json()
            if not isinstance(rows, list):
                return None
            out[key] = [{
                "ts": int(row[0]), "o": float(row[1]), "h": float(row[2]),
                "l": float(row[3]), "c": float(row[4]), "v": float(row[5]),
                "tb": float(row[8]) if len(row) > 8 and row[8] is not None else None,
            } for row in rows]
        return out
    except Exception:
        return None


def fetch_gate(coin):
    """Fallback 2: Gate.io spot (ticker, candles, trades -> CVD possible)."""
    pair = coin.upper() + "_USDT"
    out = {}
    try:
        r = SESSION.get("https://api.gateio.ws/api/v4/spot/tickers",
                        params={"currency_pair": pair}, timeout=12)
        arr = r.json()
        if not isinstance(arr, list) or not arr:
            return None
        t = arr[0]
        last = float(t["last"])
        chg = float(t.get("change_percentage") or 0)
        out["ticker"] = {
            "last": last,
            "open24h": last / (1 + chg / 100) if chg > -100 else 0,
            "high24h": t.get("high_24h") or last,
            "low24h": t.get("low_24h") or last,
        }
        for interval, key in [("1h", "candles_1h"), ("15m", "candles_15m")]:
            r = SESSION.get("https://api.gateio.ws/api/v4/spot/candlesticks",
                            params={"currency_pair": pair, "interval": interval,
                                    "limit": 400}, timeout=15)
            rows = r.json()
            if not isinstance(rows, list) or len(rows) < 250:
                return None
            out[key] = [{
                "ts": int(row[0]) * 1000,
                "o": float(row[5]), "h": float(row[3]),
                "l": float(row[4]), "c": float(row[2]),
                "v": float(row[6]),
            } for row in rows]
        r = SESSION.get("https://api.gateio.ws/api/v4/spot/trades",
                        params={"currency_pair": pair, "limit": 1000},
                        timeout=15)
        trows = r.json()
        if isinstance(trows, list) and trows:
            out["trades"] = [{
                "ts": int(t.get("create_time", 0)) * 1000,
                "px": float(t["price"]), "sz": float(t["amount"]),
                "side": "buy" if t.get("side") == "buyer" else "sell",
            } for t in trows]
        return out
    except Exception:
        return None


def fetch_binance(coin):
    """Binance public data mirror (data-api.binance.vision) — sabse deep liquidity.
    Taker-buy volume bhi deta hai -> real CVD possible."""
    sym = coin.upper() + "USDT"
    base = "https://data-api.binance.vision/api/v3"
    out = {}
    try:
        r = SESSION.get(f"{base}/ticker/24hr", params={"symbol": sym}, timeout=12)
        t = r.json()
        if "lastPrice" not in t:
            return None
        out["ticker"] = {
            "last": t["lastPrice"], "open24h": t["openPrice"],
            "high24h": t["highPrice"], "low24h": t["lowPrice"],
            "volCcy24h": t["quoteVolume"],
        }
        for interval, key in [("1h", "candles_1h"), ("15m", "candles_15m")]:
            r = SESSION.get(f"{base}/klines",
                            params={"symbol": sym, "interval": interval,
                                    "limit": 500}, timeout=15)
            rows = r.json()
            if not isinstance(rows, list) or len(rows) < 250:
                return None
            out[key] = [{
                "ts": int(row[0]), "o": float(row[1]), "h": float(row[2]),
                "l": float(row[3]), "c": float(row[4]), "v": float(row[5]),
                "tb": float(row[9]) if len(row) > 9 else None,
            } for row in rows]
        return out
    except Exception:
        return None


def fetch_fear_greed():
    """Crypto Fear & Greed Index (alternative.me) — market ka mood."""
    try:
        r = SESSION.get("https://api.alternative.me/fng/", params={"limit": 2},
                        timeout=10)
        d = r.json()["data"]
        now = int(d[0]["value"]); prev = int(d[1]["value"]) if len(d) > 1 else now
        return {"value": now, "label": d[0]["value_classification"],
                "prev": prev}
    except Exception:
        return None


def fetch_whales_for(coin, topn=20):
    """Hyperliquid: top active wallets me se kaun is coin me kis side hai."""
    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        r = SESSION.get("https://stats-data.hyperliquid.xyz/Mainnet/leaderboard",
                        timeout=20)
        rows = r.json().get("leaderboardRows", [])

        def vol30(row):
            for w, p in row.get("windowPerformances", []):
                if w == "month":
                    return float(p.get("vlm", 0))
            return 0.0

        def pnl30(row):
            for w, p in row.get("windowPerformances", []):
                if w == "month":
                    return float(p.get("pnl", 0))
            return 0.0

        rows.sort(key=vol30, reverse=True)
        whales = [{"addr": x["ethAddress"], "pnl30": pnl30(x)} for x in rows[:topn]]

        def getpos(w):
            try:
                rr = SESSION.post("https://api.hyperliquid.xyz/info",
                                  json={"type": "clearinghouseState",
                                        "user": w["addr"]}, timeout=12)
                out = []
                for p in rr.json().get("assetPositions", []):
                    q = p.get("position", {})
                    if q.get("coin") != coin:
                        continue
                    sz = float(q.get("szi", 0))
                    if sz == 0:
                        continue
                    out.append({"side": "LONG" if sz > 0 else "SHORT",
                                "value": float(q.get("positionValue") or 0),
                                "upnl": float(q.get("unrealizedPnl") or 0),
                                "pnl30": w["pnl30"]})
                return out
            except Exception:
                return []

        long_usd = short_usd = 0.0
        win_long = win_short = 0.0
        nl = ns = 0
        with ThreadPoolExecutor(max_workers=8) as ex:
            for f in as_completed([ex.submit(getpos, w) for w in whales]):
                for p in f.result():
                    if p["side"] == "LONG":
                        long_usd += p["value"]; nl += 1
                        if p["pnl30"] > 0: win_long += p["value"]
                    else:
                        short_usd += p["value"]; ns += 1
                        if p["pnl30"] > 0: win_short += p["value"]
        total = long_usd + short_usd
        if total <= 0:
            return {"found": False}
        wt = win_long + win_short
        return {"found": True, "total": total,
                "long_pct": long_usd / total * 100,
                "nl": nl, "ns": ns,
                "win_long_pct": (win_long / wt * 100) if wt > 0 else None}
    except Exception:
        return None


# ---------------- indicators ----------------

def ema_series(vals, n):
    out = [None] * len(vals)
    if len(vals) < n:
        return out
    k = 2.0 / (n + 1)
    prev = sum(vals[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(vals)):
        prev = vals[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def dema(vals, n):
    e1 = ema_series(vals, n)
    start = n - 1
    if start >= len(vals):
        return [None] * len(vals)
    e2 = [None] * start + ema_series(e1[start:], n)
    return [(2 * e1[i] - e2[i]) if (e1[i] is not None and e2[i] is not None)
            else None for i in range(len(vals))]


def rsi_series(vals, n=14):
    out = [None] * len(vals)
    if len(vals) <= n:
        return out
    gains, losses = [], []
    for i in range(1, len(vals)):
        ch = vals[i] - vals[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n

    def calc(g, l):
        return 100.0 if l == 0 else 100.0 - 100.0 / (1.0 + g / l)

    out[n] = calc(avg_g, avg_l)
    for i in range(n + 1, len(vals)):
        avg_g = (avg_g * (n - 1) + gains[i - 1]) / n
        avg_l = (avg_l * (n - 1) + losses[i - 1]) / n
        out[i] = calc(avg_g, avg_l)
    return out


def sma_last(vals, m):
    out = [None] * len(vals)
    for i in range(len(vals)):
        w = vals[i - m + 1:i + 1]
        if all(x is not None for x in w):
            out[i] = sum(w) / m
    return out


def stoch_rsi(closes, n=14, k_s=3, d_s=3):
    r = rsi_series(closes, n)
    stoch = [None] * len(closes)
    for i in range(n - 1 + n, len(closes)):
        w = r[i - n + 1:i + 1]
        if any(x is None for x in w):
            continue
        lo, hi = min(w), max(w)
        stoch[i] = 50.0 if hi == lo else (r[i] - lo) / (hi - lo) * 100
    k = sma_last(stoch, k_s)
    d = sma_last(k, d_s)
    return k, d


def mfi_series(h, l, c, v, n=14):
    out = [None] * len(c)
    tp = [(a + b + x) / 3 for a, b, x in zip(h, l, c)]
    mf = [tp[i] * v[i] for i in range(len(tp))]
    for i in range(n, len(tp)):
        pos = neg = 0.0
        for j in range(i - n + 1, i + 1):
            if tp[j] > tp[j - 1]:
                pos += mf[j]
            elif tp[j] < tp[j - 1]:
                neg += mf[j]
        out[i] = 100.0 if neg == 0 else 100.0 - 100.0 / (1 + pos / neg)
    return out


def atr_series(h, l, c, n=14):
    trs = [h[0] - l[0]]
    for i in range(1, len(c)):
        trs.append(max(h[i] - l[i], abs(h[i] - c[i - 1]),
                       abs(l[i] - c[i - 1])))
    out = [None] * len(c)
    if len(trs) < n:
        return out
    a = sum(trs[:n]) / n
    out[n - 1] = a
    for i in range(n, len(c)):
        a = (a * (n - 1) + trs[i]) / n
        out[i] = a
    return out


def obv_series(c, v):
    out = [0.0]
    for i in range(1, len(c)):
        p = out[-1]
        if c[i] > c[i - 1]:
            p += v[i]
        elif c[i] < c[i - 1]:
            p -= v[i]
        out.append(p)
    return out


def session_vwap(candles):
    """VWAP of current UTC day (hlc3 * vol)."""
    now = datetime.now(timezone.utc)
    day_start = datetime(now.year, now.month, now.day,
                         tzinfo=timezone.utc).timestamp() * 1000
    num = den = 0.0
    for cd in candles:
        if cd["ts"] >= day_start:
            tp = (cd["h"] + cd["l"] + cd["c"]) / 3
            num += tp * cd["v"]
            den += cd["v"]
    return (num / den) if den > 0 else None


def cvd_from_trades(trades):
    """Returns (total_cvd_usdt-ish, trend: +1 rising / -1 falling / 0)."""
    if not trades or len(trades) < 200:
        return None, 0
    cvd = sum(t["sz"] if t["side"] == "buy" else -t["sz"] for t in trades)
    total_sz = sum(t["sz"] for t in trades)
    if abs(cvd) < 0.05 * total_sz:  # ~5% se kam net flow = neutral
        return cvd, 0
    half = len(trades) // 2
    first = sum(t["sz"] if t["side"] == "buy" else -t["sz"]
                for t in trades[:half])
    second = sum(t["sz"] if t["side"] == "buy" else -t["sz"]
                 for t in trades[half:])
    trend = 1 if second > first else (-1 if second < first else 0)
    return cvd, trend


def cvd_from_klines(candles):
    """Approx CVD from taker-buy volume: 2*tb - vol per candle, cumulated."""
    rows = [c for c in candles if c.get("tb") is not None][-50:]
    if len(rows) < 20:
        return None, 0
    vals = [2 * c["tb"] - c["v"] for c in rows]
    total = sum(vals)
    half = len(vals) // 2
    trend = 1 if sum(vals[half:]) > sum(vals[:half]) else \
            (-1 if sum(vals[half:]) < sum(vals[:half]) else 0)
    return total, trend


# ---------------- analysis ----------------

def fmt_px(p):
    if p >= 1000:
        return f"{p:,.2f}"
    if p >= 10:
        return f"{p:,.4f}"
    return f"{p:.5f}"


def analyze(coin, data, source, with_cvd=True, perp=False):
    c1h = data["candles_1h"]
    c15 = data["candles_15m"]
    tkr = data["ticker"]
    price = float(tkr["last"])
    chg24 = (price / float(tkr["open24h"]) - 1) * 100 if tkr.get("open24h") else 0.0

    closes = [c["c"] for c in c1h]
    highs = [c["h"] for c in c1h]
    lows = [c["l"] for c in c1h]
    vols = [c["v"] for c in c1h]

    d20 = dema(closes, 20)[-1]
    d200 = dema(closes, 200)[-1]
    rsi = rsi_series(closes, 14)[-1]
    kser, dser = stoch_rsi(closes, 14)
    k, d = kser[-1], dser[-1]
    mfi = mfi_series(highs, lows, closes, vols, 14)[-1]
    obv = obv_series(closes, vols)
    obv_sma = sma_last(obv, 9)[-1]
    vwap = session_vwap(c1h)

    # stoch rsi cross in last 3 bars
    cross = 0
    for i in range(len(closes) - 3, len(closes)):
        if kser[i] is None or dser[i] is None or kser[i - 1] is None:
            continue
        if kser[i] > dser[i] and kser[i - 1] <= dser[i - 1]:
            cross = 1
        elif kser[i] < dser[i] and kser[i - 1] >= dser[i - 1]:
            cross = -1

    # CVD
    cvd_total, cvd_trend = None, 0
    if with_cvd:
        if "trades" in data:
            cvd_total, cvd_trend = cvd_from_trades(data["trades"])
        else:
            cvd_total, cvd_trend = cvd_from_klines(c15)

    # 15m timing
    closes15 = [c["c"] for c in c15]
    vwap15 = session_vwap(c15)
    mom15 = 0
    if len(closes15) > 4:
        mom15 = 1 if closes15[-1] > closes15[-5] else -1

    # ---------------- scoring ----------------
    checks = []   # (ok_bool_or_None, label, detail)
    score = 0

    def add(val, label, detail):
        nonlocal score
        score += val
        checks.append((val, label, detail))

    if d20:
        add(1 if price > d20 else -1, "DEMA 20 (short trend)",
            f"price {fmt_px(price)} vs {fmt_px(d20)}")
    if d200:
        add(1 if price > d200 else -1, "DEMA 200 (main trend)",
            f"price {fmt_px(price)} vs {fmt_px(d200)}")
    if vwap:
        add(1 if price > vwap else -1, "Session VWAP (control)",
            f"price {fmt_px(price)} vs {fmt_px(vwap)}")
    if rsi is not None:
        if rsi >= 75:
            add(-1, "RSI 14", f"{rsi:.1f} — OVERBOUGHT (chase mat karo)")
        elif rsi <= 25:
            add(0, "RSI 14", f"{rsi:.1f} — oversold, bounce possible but wait")
        elif rsi >= 55:
            add(1, "RSI 14", f"{rsi:.1f} — healthy up momentum")
        elif rsi <= 45:
            add(-1, "RSI 14", f"{rsi:.1f} — weak, sellers active")
        else:
            add(0, "RSI 14", f"{rsi:.1f} — neutral")
    if k is not None and d is not None:
        if cross == 1:
            add(1, "Stoch RSI", f"K {k:.1f} cross up D {d:.1f} — momentum on")
        elif cross == -1:
            add(-1, "Stoch RSI", f"K {k:.1f} cross down D {d:.1f} — momentum off")
        elif k > 85:
            add(-1, "Stoch RSI", f"K {k:.1f} — overbought zone")
        elif k < 15:
            add(1, "Stoch RSI", f"K {k:.1f} — oversold, bounce zone")
        else:
            add(0, "Stoch RSI", f"K {k:.1f} / D {d:.1f} — no fresh signal")
    if mfi is not None:
        if mfi >= 80:
            add(-1, "MFI 14 (money flow)", f"{mfi:.1f} — overbought flow")
        elif mfi <= 20:
            add(1, "MFI 14 (money flow)", f"{mfi:.1f} — oversold flow, bounce")
        else:
            add(1 if mfi > 50 else -1, "MFI 14 (money flow)",
                f"{mfi:.1f} — {'buyers' if mfi > 50 else 'sellers'} flow")
    if obv[-1] != 0 or obv_sma:
        add(1 if obv[-1] > obv_sma else -1, "OBV vs SMA9 (paisa)",
            f"{'OBV upar — inflow' if obv[-1] > obv_sma else 'OBV neeche — outflow'}")
    if cvd_total is not None:
        add(cvd_trend, "CVD (trade flow)",
            f"cvd {'+' if cvd_total > 0 else ''}{cvd_total:,.0f} — "
            f"{'rising' if cvd_trend > 0 else 'falling' if cvd_trend < 0 else 'flat'}")
    if vwap15:
        add(1 if price > vwap15 else -1, "15m VWAP (timing)",
            f"price vs {fmt_px(vwap15)}")
    add(mom15, "15m momentum (last 1h)",
        f"{'upar' if mom15 > 0 else 'neeche'}")

    max_score = len(checks)
    if score >= max(5, max_score * 0.55):
        signal, emoji = "BUY", "🟢"
    elif score <= -max(5, max_score * 0.55):
        signal, emoji = "SELL / SHORT", "🔴"
    elif score >= 3:
        signal, emoji = "BULLISH BIAS (pullback pe BUY)", "🟡"
    elif score <= -3:
        signal, emoji = "BEARISH BIAS (breakdown pe SELL)", "🟠"
    else:
        signal, emoji = "WAIT (neutal)", "⚪"

    # ---------------- levels ----------------
    recent_hi20 = max(highs[-20:])
    recent_lo20 = min(lows[-20:])
    recent_hi48 = max(highs[-48:]) if len(highs) >= 48 else recent_hi20

    below = [x for x in [vwap, d200, recent_lo20] if x and x < price]
    above = [x for x in [vwap, d20, recent_hi20] if x and x > price]
    support = max(below) if below else price * 0.97
    resistance = min(above) if above else price * 1.03

    atr = atr_series(highs, lows, closes, 14)[-1] or price * 0.01
    sl_buf = 1.2 * atr  # stop = level se 1.2 ATR door (noise se bachne ke liye)

    if signal in ("BUY", "BULLISH BIAS (pullback pe BUY)"):
        entry = min(price, vwap) if vwap and vwap < price else price
        entry_lo = entry * 0.998
        entry_hi = entry * 1.002
        sl = support - sl_buf
        tp1 = resistance
        tp2 = max(recent_hi48, tp1 * 1.004)
        risk = entry - sl
        rr1 = (tp1 - entry) / risk if risk > 0 else 0
    else:
        entry = max(price, vwap) if vwap and vwap > price else price
        entry_lo = entry * 0.998
        entry_hi = entry * 1.002
        sl = resistance + sl_buf
        tp1 = support
        tp2 = min(recent_lo20, price * 0.996)
        risk = sl - entry
        rr1 = (entry - tp1) / risk if risk > 0 else 0
        if tp2 >= tp1:
            tp2 = tp1 * 0.996

    # ---------------- probability estimate (transparent) ----------------
    p_rr = (rr1 / (rr1 + 1)) * 100 if rr1 > 0 else 50
    if signal in ("BUY", "BULLISH BIAS (pullback pe BUY)"):
        p_win = min(80, 50 + 4 * score)
        p_tp1 = min(85, max(10, 0.5 * p_win + 0.5 * p_rr))
        p_tp2 = min(80, max(5, 0.62 * p_tp1))
        prob = ("BUY side", p_tp1, p_tp2)
    elif signal in ("SELL / SHORT", "BEARISH BIAS (breakdown pe SELL)"):
        p_win = min(80, 50 + 4 * abs(score))
        p_tp1 = min(85, max(10, 0.5 * p_win + 0.5 * p_rr))
        p_tp2 = min(80, max(5, 0.62 * p_tp1))
        prob = ("SELL side", p_tp1, p_tp2)
    else:
        p_buy = min(70, max(30, 50 + 2 * score))
        prob = ("WAIT", p_buy, None)

    # ---------- order-fill probability (dono taraf) ----------
    # ATR ke hisaab se: level jitna door, fill ka chance utna kam
    atr_val = atr if atr else price * 0.01

    def fill_chance(level):
        if level is None:
            return None
        dist = abs(price - level)
        atrs = dist / atr_val if atr_val > 0 else 99
        # 24h horizon estimate: 0 ATR door = ~95%, 1 ATR = ~75%, 2 = ~55%,
        # 3 = ~40%, 5 = ~25%, 8+ = ~10%
        p = 95 - 20 * atrs + 1.2 * atrs * atrs
        return max(8, min(95, p))

    # buy-side zone (neeche wala entry) aur short-side zone (upar wala entry)
    buy_zone_px = min(price, vwap) if vwap and vwap < price else support
    short_zone_px = max(price, vwap) if vwap and vwap > price else resistance
    fill_buy = fill_chance(buy_zone_px)
    fill_short = fill_chance(short_zone_px)

    # ---------- ABHI SE (live price se direction estimate) ----------
    # score + momentum se up/down bias, ATR se expected time
    up_bias = 50 + 3.5 * score
    up_bias = max(20, min(80, up_bias))
    dist_up = abs(resistance - price)
    dist_dn = abs(price - support)
    atrs_up = dist_up / atr_val if atr_val > 0 else 9
    atrs_dn = dist_dn / atr_val if atr_val > 0 else 9
    eta_up = max(1, round(atrs_up * 3))   # rough: 1 ATR ~ 3 ghante 1H chart pe
    eta_dn = max(1, round(atrs_dn * 3))

    abhi = {
        "up_target": resistance, "dn_target": support,
        "up_pct": (resistance / price - 1) * 100,
        "dn_pct": (support / price - 1) * 100,
        "up_chance": up_bias, "dn_chance": 100 - up_bias,
        "eta_up": eta_up, "eta_dn": eta_dn,
    }

    # ---------- DO PLANS: A = VWAP pullback, B = Breakout (fill guarantee side) ----------
    # Plan B: recent consolidation high (last 12 x 1H candles ka high) tode toh entry
    recent12_hi = max(highs[-12:])
    brk_entry = recent12_hi * 1.002
    brk_sl = max(min(lows[-6:]), brk_entry - 1.5 * atr_val)
    brk_tp1 = brk_entry + 1.5 * (brk_entry - brk_sl)
    brk_tp2 = brk_entry + 2.5 * (brk_entry - brk_sl)
    brk_chance = max(30, min(72, 40 + 3 * score))  # breakout thoda kam reliable
    plan_b = {"entry": brk_entry, "sl": brk_sl, "tp1": brk_tp1,
              "tp2": brk_tp2, "chance": brk_chance,
              "trigger_hi": recent12_hi}

    return {
        "coin": coin, "source": source, "price": price, "chg24": chg24,
        "signal": signal, "emoji": emoji, "score": score,
        "max_score": max_score, "checks": checks,
        "rsi": rsi, "k": k, "d": d, "mfi": mfi,
        "vwap": vwap, "d20": d20, "d200": d200,
        "cvd": (cvd_total, cvd_trend),
        "support": support, "resistance": resistance,
        "entry_lo": entry_lo, "entry_hi": entry_hi,
        "sl": sl, "tp1": tp1, "tp2": tp2, "rr1": rr1,
        "prob": prob,
        "fill_buy": fill_buy, "fill_short": fill_short,
        "buy_zone_px": buy_zone_px, "short_zone_px": short_zone_px,
        "abhi": abhi,
        "plan_b": plan_b,
    }


# ---------------- CONFIDENCE ENGINE (multi-layer confirmation) ----------------

def htf_trend(coin, source):
    """4H trend check: bada timeframe kya bol raha hai."""
    try:
        if source.startswith("Binance"):
            r = SESSION.get("https://data-api.binance.vision/api/v3/klines",
                            params={"symbol": f"{coin}USDT", "interval": "4h",
                                    "limit": 120}, timeout=12)
            rows = r.json()
            closes = [float(x[4]) for x in rows]
        elif source.startswith("OKX"):
            r = SESSION.get("https://www.okx.com/api/v5/market/candles",
                            params={"instId": f"{coin}-USDT", "bar": "4H",
                                    "limit": 120}, timeout=12)
            j = r.json()
            closes = [float(x[4]) for x in reversed(j["data"])]
        else:
            r = SESSION.get("https://api.gateio.ws/api/v4/spot/candlesticks",
                            params={"currency_pair": f"{coin}_USDT",
                                    "interval": "4h", "limit": 120}, timeout=12)
            closes = [float(x[2]) for x in r.json()]
        if len(closes) < 60:
            return None
        e20 = ema_series(closes, 20)[-1]
        e50 = ema_series(closes, 50)[-1]
        px = closes[-1]
        if e20 and e50:
            if px > e20 > e50:
                return 1      # strong uptrend
            if px < e20 < e50:
                return -1     # strong downtrend
        return 0              # mixed
    except Exception:
        return None


def btc_bias():
    """BTC ka quick bias: alts ka mausam."""
    try:
        r = SESSION.get("https://data-api.binance.vision/api/v3/klines",
                        params={"symbol": "BTCUSDT", "interval": "1h",
                                "limit": 60}, timeout=12)
        closes = [float(x[4]) for x in r.json()]
        e20 = ema_series(closes, 20)[-1]
        px = closes[-1]
        mom = closes[-1] > closes[-7]
        if e20:
            if px > e20 and mom:
                return 1
            if px < e20 and not mom:
                return -1
        return 0
    except Exception:
        return None


def confidence_engine(res, htf, btc, wh):
    """Sab layers jodkar final confidence % + reasons.
    NOTE: base ABS(score) se banta hai — short mein bhi strong score = strong confidence."""
    base = 50 + 3.5 * abs(res["score"])     # direction-neutral strength
    reasons = []
    bull = res["score"] > 0

    if htf is not None:
        if (htf > 0 and bull) or (htf < 0 and not bull):
            base += 12; reasons.append("✅ 4H trend SAATH hai (+12)")
        elif htf == 0:
            reasons.append("➖ 4H mixed (0)")
        else:
            base -= 15; reasons.append("❌ 4H trend KHILAF hai (-15)")

    if btc is not None and res["coin"] != "BTC":
        if (btc > 0 and bull) or (btc < 0 and not bull):
            base += 8; reasons.append("✅ BTC saath hai (+8)")
        elif btc == 0:
            reasons.append("➖ BTC neutral (0)")
        else:
            base -= 10; reasons.append("❌ BTC khilaf hai (-10)")

    if wh and wh.get("found"):
        lp = wh["long_pct"]
        if (lp >= 65 and bull) or (lp <= 35 and not bull):
            base += 8; reasons.append("✅ Whales saath hain (+8)")
        elif (lp <= 35 and bull) or (lp >= 65 and not bull):
            base -= 10; reasons.append("❌ Whales khilaf hain (-10)")
        else:
            reasons.append("➖ Whales mixed (0)")

    fng = res.get("fng")
    if fng:
        v = fng["value"]
        if v >= 75 and bull:
            base -= 6; reasons.append("⚠️ Extreme Greed — top zone (-6)")
        elif v <= 25 and bull:
            base += 6; reasons.append("✅ Extreme Fear — contrarian edge (+6)")

    chg = abs(res.get("chg24") or 0)
    if chg >= 50:
        base -= 12; reasons.append(f"⚠️ 24h ±{chg:.0f}% pump/dump zone (-12)")

    conf = max(15, min(88, base))
    if conf >= 70:
        label = "🟢 HIGH — poora size theek (SL ke saath)"
    elif conf >= 55:
        label = "🟡 MEDIUM — aadha size"
    elif conf >= 40:
        label = "🟠 LOW — chhota size ya skip"
    else:
        label = "🔴 VERY LOW — skip karo"
    return conf, label, reasons

def position_map(candles, price):
    """Volume profile: kis price pe sabse zyada volume hua + buy/sell split."""
    if not candles or len(candles) < 60:
        return None
    lo = min(c["l"] for c in candles)
    hi = max(c["h"] for c in candles)
    if hi <= lo:
        return None
    BINS = 30
    w = (hi - lo) / BINS
    vol = [0.0] * BINS
    buyv = [0.0] * BINS
    for c in candles:
        tp = (c["h"] + c["l"] + c["c"]) / 3
        b = int((tp - lo) / w)
        b = max(0, min(BINS - 1, b))
        vol[b] += c["v"]
        # green candle ka volume ~ buyers ne uthaya, red ~ sellers ne
        if c["c"] >= c["o"]:
            buyv[b] += c["v"]
    total = sum(vol)
    if total <= 0:
        return None
    center = lambda b: lo + (b + 0.5) * w
    nodes = sorted(range(BINS), key=lambda b: vol[b], reverse=True)[:4]
    out = []
    for b in nodes:
        if vol[b] <= 0:
            continue
        bpct = buyv[b] / vol[b] * 100
        out.append({
            "px": center(b), "lo": lo + b * w, "hi": lo + (b + 1) * w,
            "vol_pct": vol[b] / total * 100,
            "buy_pct": bpct,
            "above": center(b) > price,
        })
    return out


def orderbook_balance(coin, source, zones):
    """Har zone pe ABHI orderbook mein kitna buy vs sell paisa baitha hai."""
    try:
        if source.startswith("Binance"):
            r = SESSION.get("https://data-api.binance.vision/api/v3/depth",
                            params={"symbol": f"{coin}USDT", "limit": 500},
                            timeout=12)
            d = r.json()
            if "bids" not in d:
                return None
            bids = [(float(x[0]), float(x[1])) for x in d["bids"]]
            asks = [(float(x[0]), float(x[1])) for x in d["asks"]]
        elif source.startswith("OKX"):
            r = SESSION.get("https://www.okx.com/api/v5/market/books",
                            params={"instId": f"{coin}-USDT", "sz": 400}, timeout=12)
            j = r.json()
            if j.get("code") != "0" or not j["data"]:
                return None
            d = j["data"][0]
            bids = [(float(x[0]), float(x[1])) for x in d["bids"]]
            asks = [(float(x[0]), float(x[1])) for x in d["asks"]]
        else:
            r = SESSION.get("https://api.gateio.ws/api/v4/spot/order_book",
                            params={"currency_pair": f"{coin}_USDT", "limit": 100},
                            timeout=12)
            d = r.json()
            if "bids" not in d:
                return None
            bids = [(float(x[0]), float(x[1])) for x in d["bids"]]
            asks = [(float(x[0]), float(x[1])) for x in d["asks"]]
    except Exception:
        return None

    res = []
    for z in zones:
        blo, bhi = z["lo"], z["hi"]
        bid_usd = sum(p * s for p, s in bids if blo <= p <= bhi)
        ask_usd = sum(p * s for p, s in asks if blo <= p <= bhi)
        res.append({**z, "bid_usd": bid_usd, "ask_usd": ask_usd})
    return res


def perp_extras(coin):
    try:
        inst = f"{coin.upper()}-USDT-SWAP"
        r = SESSION.get("https://www.okx.com/api/v5/public/funding-rate",
                        params={"instId": inst}, timeout=10)
        j = r.json()
        if j.get("code") == "0":
            fr = float(j["data"][0]["fundingRate"]) * 100
            r = SESSION.get("https://www.okx.com/api/v5/public/open-interest",
                            params={"instType": "SWAP", "instId": inst},
                            timeout=10)
            j2 = r.json()
            oi = None
            if j2.get("code") == "0" and j2["data"]:
                oi = float(j2["data"][0]["oi"]) * float(j2["data"][0]["oiCcy"])
            return fr, oi
    except Exception:
        pass
    return None, None


# ---------------- report ----------------

def print_report(res, fr, oi):
    now = datetime.now(timezone.utc)
    ist = now.timestamp() + 5.5 * 3600
    ist_str = datetime.fromtimestamp(ist, tz=timezone.utc).strftime("%d %b %Y %H:%M IST")

    W = 62
    print("═" * W)
    print(f"  {res['coin']}/USDT  —  LIVE SIGNAL REPORT")
    print(f"  Source: {res['source']}   |   {ist_str}")
    print(f"  Price: {fmt_px(res['price'])}   (24h: {res['chg24']:+.2f}%)")
    print("═" * W)
    print(f"\n  {res['emoji']}  SIGNAL: {res['signal']}")
    print(f"     Score: {res['score']:+d} / {res['max_score']}")

    cf = res.get("confidence")
    if cf:
        conf, label, reasons = cf
        print(f"\n  🎖️  CONFIDENCE: {conf:.0f}% — {label}")
        for rr_ in reasons:
            print(f"      {rr_}")

    ab = res.get("abhi")
    if ab:
        print(f"\n  ⚡ ABHI SE (live price {fmt_px(res['price'])} se seedha):")
        print(f"     🔺 UPAR jaane ka chance ≈ {ab['up_chance']:.0f}%  → target {fmt_px(ab['up_target'])} ({ab['up_pct']:+.1f}%), andaza ~{ab['eta_up']}h")
        print(f"     🔻 NEECHE aane ka chance ≈ {ab['dn_chance']:.0f}%  → target {fmt_px(ab['dn_target'])} ({ab['dn_pct']:+.1f}%), andaza ~{ab['eta_dn']}h")
        if ab['up_chance'] >= 62:
            print(f"     👉 ABHI market entry theek hai (aadha size) — SL {fmt_px(res['price'] * 0.985 if res['price'] > res['support'] else res['sl'])} ke paas rakho")
        elif ab['dn_chance'] >= 62:
            print(f"     👉 ABHI entry mat lo — neeche ka jhukav hai. Coins hain toh exit/short socho")
        else:
            print(f"     👉 50-50 zone — ABHI entry = coin toss. Pullback wala hi better hai")

    fng = res.get("fng")
    if fng:
        v, lbl, pv = fng["value"], fng["label"], fng["prev"]
        trend = "badh raha" if v > pv else ("ghat raha" if v < pv else "same")
        warn = ""
        if v >= 75:
            warn = " ⚠️ EXTREME GREED — top ka zone, chase mat karo, profit book karte chalo"
        elif v <= 25:
            warn = " 💎 EXTREME FEAR — historically accha buy zone (contrarian)"
        print(f"     Market Mood (Fear&Greed): {v}/100 ({lbl}, {trend}){warn}")
    print()

    print("  CHECKS (1H bias + 15m timing):")
    for val, label, detail in res["checks"]:
        mark = "✅" if val > 0 else ("❌" if val < 0 else "➖")
        print(f"    {mark} {label:<26} {detail}")

    if res["cvd"][0] is not None:
        cvd, tr = res["cvd"]
        who = ("buyers dominant" if tr > 0
               else "sellers dominant" if tr < 0
               else "neutral / mixed")
        print(f"\n  💰 Trade flow (CVD, last ~1000 trades): "
              f"{'+' if cvd > 0 else ''}{cvd:,.0f} ({who})")

    if fr is not None:
        print(f"  ⚡ Funding rate: {fr:+.4f}%  |  "
              f"{'overheated longs ⚠️' if fr > 0.05 else 'extreme fear shorts ⚠️' if fr < -0.01 else 'normal zone'}"
              + (f"  |  OI: ${oi/1e6:,.1f}M" if oi else ""))

    pd_, p1, p2 = res["prob"]
    print(f"\n  🎲 CHANCE (estimate):")
    if p2 is not None:
        print(f"     {pd_}: Target 1 hit ≈ {p1:.0f}%  |  Target 2 hit ≈ {p2:.0f}%")
        print(f"     Stop Loss pehle = ≈ {100 - p1:.0f}%")
    else:
        print(f"     Buy trigger ho ≈ {p1:.0f}%  |  Sell trigger ho ≈ {100 - p1:.0f}%")

    if res.get("fill_buy") is not None or res.get("fill_short") is not None:
        print(f"\n  📩 ORDER FILL CHANCE (24h mein price wahan aane ka):")
        if res.get("fill_buy") is not None:
            print(f"     🟢 BUY order @ {fmt_px(res['buy_zone_px'])} tak price aaye ≈ {res['fill_buy']:.0f}%")
        if res.get("fill_short") is not None:
            print(f"     🔴 SHORT/SELL order @ {fmt_px(res['short_zone_px'])} tak price jaaye ≈ {res['fill_short']:.0f}%")

    wh = res.get("whales")
    if wh is not None:
        print(f"\n  🐋 WHALES (Hyperliquid on-chain, top active wallets):")
        if not wh or not wh.get("found"):
            print(f"     Top whales me se koi bhi {res['coin']} me nahi baitha — "
                  f"smart money ka focus yahan nahi (na bullish na bearish).")
        else:
            lp = wh["long_pct"]
            v = ("🟢 whales UPAR ka soch rahe" if lp >= 65 else
                 "🔴 whales NEECHE ka soch rahe" if lp <= 35 else
                 "⚪ whales bate hue — clear side nahi")
            print(f"     Paisa: {'$%.1fM' % (wh['total']/1e6) if wh['total'] >= 1e6 else '$%.0fK' % (wh['total']/1e3)} "
                  f"| LONG {lp:.0f}% ({wh['nl']}) vs SHORT {100-lp:.0f}% ({wh['ns']}) → {v}")
            if wh.get("win_long_pct") is not None:
                wl = wh["win_long_pct"]
                print(f"     🏆 Jeetne-wale whales ka paisa: {wl:.0f}% LONG / {100-wl:.0f}% SHORT"
                      + (" ← inka weight zyada do" if abs(wl - 50) > 20 else ""))
            # conflict warning
            if res["score"] >= 3 and lp <= 35:
                print(f"     ⚠️ CONFLICT: indicators bullish PAR whales short — size aadha rakho")
            elif res["score"] <= -3 and lp >= 65:
                print(f"     ⚠️ CONFLICT: indicators bearish PAR whales long — short jaldi mat karo")

    pm = res.get("posmap")
    if pm:
        print(f"\n  🗺️  POSITION MAP (kahan sabse zyada log ghuse the + ab wahan kaun baitha hai):")
        for z in pm:
            side = "UPAR" if z["above"] else "NEECHE"
            who = ("zyada log BUY karke ghuse the" if z["buy_pct"] >= 55
                   else "zyada log SELL/SHORT karke ghuse the" if z["buy_pct"] <= 45
                   else "buy/sell barabar ghuse the")
            line = (f"     {'🔺' if z['above'] else '🔻'} {fmt_px(z['px'])} ({side}, "
                    f"{z['vol_pct']:.0f}% ka volume yahan) — {who} ({z['buy_pct']:.0f}% buy)")
            print(line)
            if z.get("bid_usd") is not None:
                b, a = z["bid_usd"], z["ask_usd"]
                if b + a > 0:
                    if b > a * 1.5:
                        now_who = f"ABHI yahan BUYERS ka paisa zyada baitha hai (${b:,.0f} vs ${a:,.0f}) → support"
                    elif a > b * 1.5:
                        now_who = f"ABHI yahan SELLERS ka paisa zyada baitha hai (${a:,.0f} vs ${b:,.0f}) → resistance"
                    else:
                        now_who = f"ABHI dono barabar baithe hain (buy ${b:,.0f} / sell ${a:,.0f}) → jang ka zone"
                    print(f"        └ {now_who}")
        print(f"     ℹ️  Jahan log BUY karke ghuse par price neeche hai = wo log phase hain (breakeven pe bechenge = resistance)")

    print(f"\n  🎯 LEVELS:")
    print(f"     Support   : {fmt_px(res['support'])}")
    print(f"     Resistance: {fmt_px(res['resistance'])}")

    print(f"\n  📋 DO PLANS (jo pehle trigger ho wahi lo):")
    print(f"     🅰️ VWAP PULLBACK (sasta, high-chance, par fill ka intezar):")
    print(f"        Entry {fmt_px(res['entry_lo'])} – {fmt_px(res['entry_hi'])} | SL {fmt_px(res['sl'])} | TP1 {fmt_px(res['tp1'])} | TP2 {fmt_px(res['tp2'])} (R:R 1:{res['rr1']:.1f})")
    pb = res.get("plan_b")
    if pb and res["score"] >= 3:
        print(f"     🅱️ BREAKOUT (coin wapas na aaye toh bhi entry mile):")
        print(f"        Trigger: {fmt_px(pb['trigger_hi'])} ke UPAR 1H close + volume")
        print(f"        Entry {fmt_px(pb['entry'])} | SL {fmt_px(pb['sl'])} | TP1 {fmt_px(pb['tp1'])} | TP2 {fmt_px(pb['tp2'])} | chance ≈ {pb['chance']:.0f}%")
        print(f"        (size: Plan A ka aadha — breakout thoda risky hota hai)")
    elif pb:
        print(f"     🅱️ BREAKOUT: signal bullish nahi — Plan B inactive")

    print("\n" + "─" * W)
    print("  ⚠️  Educational analysis — financial advice nahi.")
    print("      Risk aapka apna hai. Always stop-loss ke saath trade karo.")
    print("═" * W)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("coin", help="e.g. ONT, BTC, ETH, SOL, DOGE")
    ap.add_argument("--perp", action="store_true", help="funding rate + OI bhi dikhao")
    ap.add_argument("--no-cvd", action="store_true", help="CVD skip karo (fast)")
    args = ap.parse_args()
    coin = args.coin.upper().replace("USDT", "").strip("-")

    data = fetch_binance(coin)
    source = "Binance (public data mirror)"
    if data is None:
        data = fetch_okx(coin)
        source = "OKX (spot public feed)"
    if data is None:
        data = fetch_mexc(coin)
        source = "MEXC (spot public feed)"
    if data is None:
        data = fetch_gate(coin)
        source = "Gate.io (spot public feed)"
    if data is None:
        print(f"❌ {coin}/USDT kisi bhi connected exchange pe mila nahi.")
        sys.exit(1)

    res = analyze(coin, data, source, with_cvd=not args.no_cvd, perp=args.perp)
    # position map: kahan log ghuse the + ab wahan orderbook mein kaun baitha hai
    try:
        zones = position_map(data["candles_1h"], res["price"])
        if zones:
            enriched = orderbook_balance(coin, source, zones)
            res["posmap"] = enriched if enriched else zones
    except Exception:
        res["posmap"] = None
    res["fng"] = fetch_fear_greed()
    res["whales"] = fetch_whales_for(coin)
    res["htf"] = htf_trend(coin, source)
    res["btc_bias"] = btc_bias() if coin != "BTC" else None
    res["confidence"] = confidence_engine(res, res["htf"], res["btc_bias"],
                                          res["whales"])
    fr, oi = (perp_extras(coin) if args.perp else (None, None))
    print_report(res, fr, oi)


if __name__ == "__main__":
    main()
