#!/usr/bin/env python3
"""
FULL MARKET SCANNER
===================
Stage 1: OKX + Gate ke SAARE USDT spot pairs ka ticker ek saath uthao
Stage 2: volume + momentum filter se candidates chuno
Stage 3: har candidate pe full analysis (signal.py wala engine) chala ke
         best BUY / SHORT setups nikaalo

Usage: python3 scan.py            # full scan
       python3 scan.py --min-vol 100000   # min 24h volume USD filter
"""

import sys
import argparse
import signal as S


def stage1_okx():
    """All OKX USDT spot tickers."""
    try:
        r = S.SESSION.get("https://www.okx.com/api/v5/market/tickers",
                          params={"instType": "SPOT"}, timeout=20)
        j = r.json()
        if j.get("code") != "0":
            return []
        out = []
        for t in j["data"]:
            inst = t["instId"]
            if not inst.endswith("-USDT"):
                continue
            coin = inst[:-5]
            try:
                last = float(t["last"])
                open24 = float(t["open24h"]) if t["open24h"] else last
                volusd = float(t["volCcy24h"])  # quote ccy volume approx USD
                chg = (last / open24 - 1) * 100 if open24 else 0
                out.append({"coin": coin, "src": "OKX", "last": last,
                            "chg": chg, "vol": volusd})
            except Exception:
                continue
        return out
    except Exception:
        return []


def stage1_gate():
    """All Gate.io USDT spot tickers (for coins not on OKX)."""
    try:
        r = S.SESSION.get("https://api.gateio.ws/api/v4/spot/tickers",
                          timeout=25)
        arr = r.json()
        out = []
        for t in arr:
            cp = t.get("currency_pair", "")
            if not cp.endswith("_USDT"):
                continue
            coin = cp[:-5]
            try:
                last = float(t["last"])
                chg = float(t.get("change_percentage") or 0)
                vol = float(t.get("quote_volume") or 0)
                out.append({"coin": coin, "src": "Gate", "last": last,
                            "chg": chg, "vol": vol})
            except Exception:
                continue
        return out
    except Exception:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-vol", type=float, default=300000,
                    help="min 24h quote volume USD (default 300k)")
    ap.add_argument("--top", type=int, default=25,
                    help="max candidates for deep analysis")
    args = ap.parse_args()

    okx = stage1_okx()
    gate = stage1_gate()
    seen = {t["coin"] for t in okx}
    allt = okx + [t for t in gate if t["coin"] not in seen]
    print(f"Stage 1: {len(okx)} OKX + {len([t for t in gate if t['coin'] not in seen])} Gate-only = {len(allt)} pairs")

    # Stage 2: filter
    liq = [t for t in allt if t["vol"] >= args.min_vol]
    # interesting = strong movers (dono taraf) + strong volume
    movers = sorted(liq, key=lambda t: abs(t["chg"]), reverse=True)[:60]
    by_vol = sorted(liq, key=lambda t: t["vol"], reverse=True)[:25]
    cand, seen2 = [], set()
    for t in movers + by_vol:
        if t["coin"] in seen2:
            continue
        seen2.add(t["coin"])
        cand.append(t)
    cand = cand[:args.top + 15]
    print(f"Stage 2: {len(liq)} liquid -> {len(cand)} candidates")

    # Stage 3: deep analysis
    results = []
    stables = {"USDC", "DAI", "TUSD", "FDUSD", "PYUSD", "USDE", "EURT", "USDG", "XAUT"}

    def is_leveraged(c):
        for suf in ("3L", "3S", "5L", "5S", "BULL", "BEAR", "UP", "DOWN"):
            if c.endswith(suf):
                return True
        return False

    done = 0
    for t in cand:
        if done >= args.top:
            break
        coin = t["coin"]
        if coin in stables or coin.startswith("STETH") or is_leveraged(coin):
            continue
        d = S.fetch_okx(coin) if t["src"] == "OKX" else None
        src = "OKX"
        if d is None:
            d = S.fetch_gate(coin)
            src = "Gate"
        if d is None:
            continue
        try:
            r = S.analyze(coin, d, src, with_cvd=False)
            r["vol24"] = t["vol"]
            results.append(r)
            done += 1
        except Exception:
            continue

    results.sort(key=lambda r: r["score"], reverse=True)
    print(f"Stage 3: {len(results)} analyzed\n")

    longs = [r for r in results if r["score"] >= 3]
    shorts = [r for r in results if r["score"] <= -3]
    waits = [r for r in results if -3 < r["score"] < 3]

    def prow(r):
        if r["prob"][2] is not None:
            ch = f"TP1~{r['prob'][1]:.0f}%"
        else:
            ch = f"B{r['prob'][1]:.0f}%/S{100 - r['prob'][1]:.0f}%"
        vol = r["vol24"]
        vs = f"{vol/1e6:.1f}M" if vol >= 1e6 else f"{vol/1e3:.0f}K"
        return (f"{r['coin']:<10} {S.fmt_px(r['price']):>12} {r['chg24']:>+6.1f}% "
                f"{vs:>10} {r['score']:>+5}/10  {r['signal']:<30} "
                f"{S.fmt_px(r['entry_lo'])} / {S.fmt_px(r['sl'])} / "
                f"{S.fmt_px(r['tp1'])} / {ch}")

    hdr = (f"{'COIN':<10} {'PRICE':>12} {'24h%':>7} {'VOL24($)':>10} "
           f"{'SCORE':>6}  {'SIGNAL':<30} ENTRY / SL / TP1 / CHANCE")

    print("🟢 LONG side (buy setups):")
    print(hdr)
    print("-" * len(hdr))
    for r in longs:
        print(prow(r))
    if not longs:
        print("  (koi strong long setup nahi)")

    # ---------- ELITE FILTER (70% zone wali trades) ----------
    def is_elite(r):
        if r["prob"][2] is None:
            return False
        tp1_chance = r["prob"][1]
        rr = r.get("rr1") or 0
        vol = r.get("vol24") or 0
        chg = abs(r.get("chg24") or 0)
        score_ok = abs(r["score"]) >= 6
        return (score_ok and tp1_chance >= 70 and rr >= 1.8
                and vol >= 800_000 and chg < 50)

    elites = [r for r in longs + shorts if is_elite(r)]
    print("\n" + "⭐" * 20)
    if elites:
        print("⭐ ELITE PICKS (score 7+, chance 70%+, R:R 1.8+, volume OK, no pump-trap):")
        for r in elites:
            side = "LONG" if r["score"] > 0 else "SHORT"
            print(f"   ⭐ {r['coin']} [{side}] score {r['score']:+d} | "
                  f"entry {S.fmt_px(r['entry_lo'])} | SL {S.fmt_px(r['sl'])} | "
                  f"TP1 {S.fmt_px(r['tp1'])} | chance {r['prob'][1]:.0f}% | R:R 1:{r['rr1']:.1f}")
        print("   👉 Sirf YE trades lo — baaki list sirf jaankaari hai.")
    else:
        print("⭐ ELITE PICKS: aaj KOI nahi — sabse profitable action aaj WAIT hai.")
        print("   (Filter: score 6+, TP1 chance 70%+, R:R 1.8+, vol $800K+, 24h move <50%)")
    print("⭐" * 20)

    # ---------- VWAP ZONE (READY-TO-ENTER: price VWAP ke paas hai ABHI) ----------
    ready = []
    for r in results:
        vwap = r.get("vwap")
        if not vwap or not r.get("price"):
            continue
        dist = (r["price"] / vwap - 1) * 100          # +ve = VWAP ke upar
        vol = r.get("vol24") or 0
        chg = abs(r.get("chg24") or 0)
        if abs(dist) <= 1.5 and vol >= 500_000 and chg < 60 and abs(r["score"]) >= 3:
            ready.append((r, dist))

    print("\n" + "🎯" * 20)
    if ready:
        print("🎯 VWAP ZONE — READY TO ENTER (price ABHI vwap ke paas, wait nahi karna):")
        ready.sort(key=lambda x: -abs(x[0]["score"]))
        for r, dist in ready[:6]:
            bull = r["score"] > 0
            side = "🟢 BUY" if bull else "🔴 SHORT"
            if r["prob"][2] is not None:
                ch = f"{r['prob'][1]:.0f}%"
            else:
                ch = "~60%"
            print(f"   {side} {r['coin']} @ {S.fmt_px(r['price'])} "
                  f"(VWAP se {dist:+.1f}%) | score {r['score']:+d} | "
                  f"SL {S.fmt_px(r['sl'])} | TP1 {S.fmt_px(r['tp1'])} | "
                  f"TP1 chance ≈ {ch} | R:R 1:{(r.get('rr1') or 0):.1f}")
        print("   👉 In par ABHI limit/market entry ho sakti hai — VWAP hi entry zone hai.")
        print("      Rule: VWAP hold kare (15m candle bounce) tab entry, tod de toh skip.")
    else:
        print("🎯 VWAP ZONE: abhi koi coin VWAP ke paas strong signal ke saath nahi hai.")
        print("   (Sab ya toh bhaag chuke hain ya kamzor hain — pullback ka wait hi sahi.)")
    print("🎯" * 20)



    print("\n🔴 SHORT side (sell/short setups):")
    print(hdr)
    print("-" * len(hdr))
    for r in sorted(shorts, key=lambda r: r["score"]):
        print(prow(r))
    if not shorts:
        print("  (koi strong short setup nahi)")

    print("\n⚪ WAIT (na long na short — inse door):")
    for r in waits:
        print(f"  {r['coin']:<10} {S.fmt_px(r['price']):>12} {r['chg24']:>+6.1f}%  score {r['score']:+d}")


if __name__ == "__main__":
    main()
