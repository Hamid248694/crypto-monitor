#!/usr/bin/env python3
"""
WHALE POSITIONS TRACKER
=======================
Executed paisa kahan laga hai (futures) — OKX public stats se:
  - Open Interest (total laga paisa) + trend
  - Long/Short account ratio + trend
  - Taker buy/sell flow (aggressive paisa kis taraf)
  - Funding rate
  - OI-spike zones = whale entry price zones (estimate)

Usage: python3 positions.py BTC
"""

import sys
from datetime import datetime, timezone, timedelta
import requests

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0"})
OKX = "https://www.okx.com/api/v5"


def jget(url, params=None):
    try:
        r = S.get(url, params=params, timeout=15)
        j = r.json()
        if j.get("code") == "0":
            return j["data"]
    except Exception:
        pass
    return None


def fmt_usd(v):
    if v >= 1e9: return f"${v/1e9:.2f}B"
    if v >= 1e6: return f"${v/1e6:.1f}M"
    return f"${v/1e3:.0f}K"


def main():
    if len(sys.argv) < 2:
        print("usage: positions.py COIN"); return
    coin = sys.argv[1].upper().replace("USDT", "").strip("-_/")
    inst = f"{coin}-USDT-SWAP"

    ist = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist).strftime("%d %b %H:%M IST")
    print("═" * 60)
    print(f"  {coin} — WHALE POSITIONS REPORT (futures) | {now}")
    print("═" * 60)

    # current OI
    oi = jget(f"{OKX}/public/open-interest", {"instType": "SWAP", "instId": inst})
    oi_usd = float(oi[0]["oiUsd"]) if oi else None

    # OI history (1H, contracts stats)
    oih = jget(f"{OKX}/rubik/stat/contracts/open-interest-volume",
               {"ccy": coin, "period": "1H"})
    # long/short ratio
    ls = jget(f"{OKX}/rubik/stat/contracts/long-short-account-ratio",
              {"ccy": coin, "period": "1H"})
    # taker volume
    tv = jget(f"{OKX}/rubik/stat/taker-volume",
              {"ccy": coin, "instType": "CONTRACTS", "period": "1H"})
    # funding
    fr = jget(f"{OKX}/public/funding-rate", {"instId": inst})
    # price candles for OI-spike zone mapping
    cd = jget(f"{OKX}/market/candles", {"instId": f"{coin}-USDT", "bar": "1H", "limit": "48"})

    if oi_usd:
        print(f"\n  💰 TOTAL PAISA LAGA HUA (Open Interest): {fmt_usd(oi_usd)}")

    if oih and len(oih) >= 25:
        # rows newest-first: [ts, oi, vol]
        oi_now = float(oih[0][1]); oi_6h = float(oih[6][1]); oi_24h = float(oih[24][1])
        ch6 = (oi_now/oi_6h - 1) * 100
        ch24 = (oi_now/oi_24h - 1) * 100
        print(f"     OI change: 6h {ch6:+.1f}% | 24h {ch24:+.1f}%")
        px_now = float(cd[0][4]) if cd else None
        px_6h = float(cd[6][4]) if cd and len(cd) > 6 else None
        if px_now and px_6h:
            px_up = px_now > px_6h
            oi_up = ch6 > 1
            oi_dn = ch6 < -1
            if oi_up and px_up:
                verdict = "🟢 NAYA PAISA LONG mein ghus raha — whales upar ka soch rahe (strong)"
            elif oi_up and not px_up:
                verdict = "🔴 NAYA PAISA SHORT mein ghus raha — whales neeche ka soch rahe"
            elif oi_dn and px_up:
                verdict = "🟡 Shorts band ho rahe (squeeze rally) — naya paisa nahi, bharosa kam"
            elif oi_dn and not px_up:
                verdict = "🟡 Longs bhaag rahe hain — girawat mein positions kat rahe"
            else:
                verdict = "⚪ OI flat — koi bada naya commitment nahi"
            print(f"     👉 {verdict}")

    if ls:
        r_now = float(ls[0][1]); r_6h = float(ls[6][1]) if len(ls) > 6 else r_now
        side = "LONG zyada" if r_now > 1.05 else ("SHORT zyada" if r_now < 0.95 else "barabar")
        trend = "badh raha" if r_now > r_6h + 0.02 else ("ghat raha" if r_now < r_6h - 0.02 else "stable")
        print(f"\n  ⚖️  LONG/SHORT RATIO: {r_now:.2f} ({side}, {trend})")
        if r_now > 1.5:
            print("     ⚠️ Bahut zyada longs — crowd ek taraf = squeeze ka khatra NEECHE")
        elif r_now < 0.7:
            print("     ⚠️ Bahut zyada shorts — squeeze ka fuel UPAR (bullish contrarian)")

    if tv and len(tv) >= 7:
        # rows: [ts, sellVol, buyVol]
        buy6 = sum(float(r[2]) for r in tv[:6])
        sell6 = sum(float(r[1]) for r in tv[:6])
        net = buy6 - sell6
        pct = net / (buy6 + sell6) * 100 if (buy6 + sell6) else 0
        arrow = "🟢 BUYERS aggressive" if pct > 3 else ("🔴 SELLERS aggressive" if pct < -3 else "⚪ balanced")
        print(f"\n  ⚔️  TAKER FLOW (6h): buy {fmt_usd(buy6)} vs sell {fmt_usd(sell6)} → {arrow} ({pct:+.1f}%)")

    if fr:
        f = float(fr[0]["fundingRate"]) * 100
        note = "normal" if -0.01 <= f <= 0.03 else ("longs overheated ⚠️" if f > 0.03 else "shorts bhare hain (squeeze fuel) 🚀")
        print(f"\n  ⚡ FUNDING: {f:+.4f}% — {note}")

    # OI spike zones = whale entry estimate
    if oih and cd and len(oih) >= 24:
        rows = oih[:24]
        deltas = []
        for i in range(len(rows) - 1):
            d = float(rows[i][1]) - float(rows[i+1][1])
            deltas.append((rows[i][0], d))
        deltas.sort(key=lambda x: abs(x[1]), reverse=True)
        top = [d for d in deltas[:3] if abs(d[1]) > 0]
        if top:
            print(f"\n  🎯 WHALE ENTRY ZONES (24h ke OI-spike, estimate):")
            cdmap = {r[0]: r for r in cd}
            for ts, d in top:
                c = cdmap.get(ts)
                if not c:
                    continue
                px_lo, px_hi = float(c[3]), float(c[2])
                t = datetime.fromtimestamp(int(ts)/1000, ist).strftime("%H:%M")
                side = "LONGS/SHORTS ghuse" if d > 0 else "positions band hue"
                print(f"     {t} IST: {px_lo:,.6g} – {px_hi:,.6g} zone pe bada OI move ({side})")
            print("     👉 In zones ko whale DEFEND karega — support/resistance jaisa treat karo")

    print("\n" + "─" * 60)
    print("  ℹ️  CEX pe kisi EK whale ka personal position public nahi hota.")
    print("     Ye aggregate hai — sab positions ka joda. Estimate = estimate.")
    print("═" * 60)


if __name__ == "__main__":
    main()
