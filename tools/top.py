#!/usr/bin/env python3
"""
PUMP TOP DETECTOR — "Finish line aa gayi ya nahi?"
===================================================
6 classic top-signs ko 15m chart pe check karta hai:
  1. Blow-off volume climax (sabse bada volume + price aage nahi badha)
  2. Upper wick rejection (lambi upar wick high volume pe)
  3. Lower high structure (top ke baad neeche ka high)
  4. RSI bearish divergence (price naya high, RSI nahi)
  5. VWAP/EMA20 breakdown (parabolic ke baad structure toota)
  6. CVD/volume fade (price upar, buying volume ghat raha)

Verdict:
  0-2 sign  = race abhi chalu — SHORT MAT KARO
  3-4 sign  = finish line PAAS — tayari karo, trigger ka wait
  5-6 sign  = finish line CONFIRM — short setup ready (lower-high pe)

Usage: python3 top.py SXT
"""

import sys
import signal as S


def ema(vals, n):
    if len(vals) < n:
        return None
    k = 2 / (n + 1)
    e = sum(vals[:n]) / n
    for v in vals[n:]:
        e = v * k + e * (1 - k)
    return e


def main():
    if len(sys.argv) < 2:
        print("usage: top.py COIN")
        return
    coin = sys.argv[1].upper().replace("USDT", "").strip("-_/")

    data = S.fetch_binance(coin) or S.fetch_okx(coin) or S.fetch_mexc(coin) or S.fetch_gate(coin)
    if not data:
        print(f"❌ {coin} nahi mila")
        return

    c15 = data["candles_15m"][-96:]  # last 24h of 15m candles
    price = float(data["ticker"]["last"])
    closes = [c["c"] for c in c15]
    vols = [c["v"] for c in c15]

    signs = []

    # pump context: kitna chadha hai 24h me
    lo24 = min(c["l"] for c in c15)
    hi24 = max(c["h"] for c in c15)
    pump_pct = (hi24 / lo24 - 1) * 100
    hi_idx = max(range(len(c15)), key=lambda i: c15[i]["h"])
    candles_since_top = len(c15) - 1 - hi_idx

    # 1. Blow-off volume climax
    vmax_idx = max(range(len(c15)), key=lambda i: vols[i])
    climax = False
    if vmax_idx >= len(c15) - 12:  # climax pichle 3h me
        vc = c15[vmax_idx]
        avg_vol = sum(vols) / len(vols)
        body_up = (vc["c"] - vc["o"]) / vc["o"] * 100
        if vols[vmax_idx] > 4 * avg_vol and (body_up < 0.5 or vc["c"] < (vc["h"] + vc["l"]) / 2):
            climax = True
    signs.append((climax, "Volume climax (bada volume, price aage nahi badha)"))

    # 2. Upper wick rejection at/near top
    wick_rej = False
    for c in c15[hi_idx:hi_idx + 4]:
        rng = c["h"] - c["l"]
        if rng > 0:
            upper_wick = c["h"] - max(c["o"], c["c"])
            if upper_wick / rng > 0.5 and c["h"] >= hi24 * 0.995:
                wick_rej = True
    signs.append((wick_rej, "Upper wick rejection (top pe bechne wale aa gaye)"))

    # 3. Lower high after top
    lower_high = False
    if candles_since_top >= 4:
        post = c15[hi_idx + 1:]
        if post:
            post_hi = max(c["h"] for c in post)
            if post_hi < hi24 * 0.995:
                bounce_tried = any(
                    c15[i]["h"] > c15[i - 1]["h"] for i in range(hi_idx + 2, len(c15))
                )
                if bounce_tried:
                    lower_high = True
    signs.append((lower_high, "Lower high bana (bounce top tak nahi pahuncha)"))

    # 4. RSI bearish divergence
    r = S.rsi_series(closes, 14)
    div = False
    if len(closes) > 40 and r[-1] is not None:
        half = len(closes) // 2
        old_hi_i = max(range(half), key=lambda i: closes[i])
        new_hi_i = max(range(half, len(closes)), key=lambda i: closes[i])
        if (closes[new_hi_i] > closes[old_hi_i]
                and r[new_hi_i] is not None and r[old_hi_i] is not None
                and r[new_hi_i] < r[old_hi_i] - 3):
            div = True
    signs.append((div, "RSI divergence (price naya high, momentum nahi)"))

    # 5. Structure break: price < 15m EMA20 & session VWAP
    e20 = ema(closes, 20)
    vwap = S.session_vwap(data["candles_15m"])
    struct_break = bool(e20 and price < e20) and bool(vwap and price < vwap)
    signs.append((struct_break, "Structure toota (price EMA20 + VWAP dono ke neeche)"))

    # 6. Buying fade: last 8 candles green-volume vs red-volume
    green_v = sum(c["v"] for c in c15[-8:] if c["c"] >= c["o"])
    red_v = sum(c["v"] for c in c15[-8:] if c["c"] < c["o"])
    fade = red_v > green_v * 1.3
    signs.append((fade, "Selling volume dominant (last 2h)"))

    n = sum(1 for ok, _ in signs if ok)

    print("═" * 58)
    print(f"  {coin} — PUMP TOP CHECK (finish line?)")
    print(f"  Price: {S.fmt_px(price)} | 24h pump: +{pump_pct:.0f}% | top se {candles_since_top*15} min")
    print("═" * 58)
    for ok, label in signs:
        print(f"   {'🔴' if ok else '⚪'} {label}")
    print("─" * 58)

    if n <= 2:
        print(f"  🏃 VERDICT ({n}/6): RACE ABHI CHALU HAI — SHORT MAT KARO")
        print("     Pump zinda hai. Short = squeeze ka khana banoge.")
    elif n <= 4:
        print(f"  🏁 VERDICT ({n}/6): FINISH LINE PAAS HAI — TAYARI KARO")
        print("     Abhi bhi short NAHI — trigger ka wait:")
        print(f"     → lower high bane + wo tut jaye, ya VWAP tut ke retest fail ho")
    else:
        print(f"  🛑 VERDICT ({n}/6): FINISH LINE CONFIRM — SHORT SETUP READY")
        top_px = hi24
        post = c15[hi_idx + 1:]
        lh = max((c["h"] for c in post), default=top_px * 0.98)
        print(f"     🔴 SHORT entry: bounce {S.fmt_px(lh * 0.995)}–{S.fmt_px(lh)} zone pe reject ho tab")
        print(f"     🛡️ SL: {S.fmt_px(top_px * 1.01)} (top ke upar)")
        print(f"     🎯 TP1: {S.fmt_px(vwap if vwap and vwap < price else price * 0.95)} | TP2: pump ka 50% wapas = {S.fmt_px(lo24 + (hi24 - lo24) * 0.5)}")

    print("\n  ⚠️ Top-hunting sabse risky game hai — size chhota, SL pakka.")
    print("═" * 58)


if __name__ == "__main__":
    main()
