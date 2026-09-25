#!/usr/bin/env python3
"""Telegram — sirf PAKKA HIGH-confidence pick. Spam / weak setups nahi."""
import os
import sys
from datetime import datetime, timezone, timedelta

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import signal as S
import scan as SC

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT", "")
IST = timezone(timedelta(hours=5, minutes=30))


def send(text):
    if not TG_TOKEN or not TG_CHAT:
        print("TG secrets missing"); return False
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True},
        timeout=20,
    )
    ok = r.json().get("ok")
    print("telegram:", ok)
    return ok


def collect(top=22, min_vol=400000):
    okx = SC.stage1_okx()
    gate = SC.stage1_gate()
    seen = {t["coin"] for t in okx}
    allt = okx + [t for t in gate if t["coin"] not in seen]
    liq = [t for t in allt if t["vol"] >= min_vol]
    movers = sorted(liq, key=lambda t: abs(t["chg"]), reverse=True)[:50]
    by_vol = sorted(liq, key=lambda t: t["vol"], reverse=True)[:20]
    cand, seen2 = [], set()
    for t in movers + by_vol:
        if t["coin"] in seen2:
            continue
        seen2.add(t["coin"]); cand.append(t)
    stables = {"USDC", "DAI", "TUSD", "FDUSD", "PYUSD", "USDE", "EURT", "USDG", "XAUT"}

    def lev(c):
        return any(c.endswith(s) for s in ("3L", "3S", "5L", "5S", "BULL", "BEAR"))

    results, done = [], 0
    for t in cand:
        if done >= top:
            break
        coin = t["coin"]
        if coin in stables or coin.startswith("STETH") or lev(coin):
            continue
        d = S.fetch_okx(coin) if t["src"] == "OKX" else None
        src = "OKX"
        if d is None:
            d = S.fetch_gate(coin); src = "Gate"
        if d is None:
            d = S.fetch_binance(coin) if hasattr(S, "fetch_binance") else None
            src = "Binance"
        if d is None:
            continue
        try:
            r = S.analyze(coin, d, src, with_cvd=False)
            r["vol24"] = t["vol"]
            r["_src"] = src
            results.append(r); done += 1
        except Exception:
            continue
    return results, len(allt)


def is_elite(r):
    if r["prob"][2] is None:
        return False
    return (abs(r["score"]) >= 6 and r["prob"][1] >= 70
            and (r.get("rr1") or 0) >= 1.8
            and (r.get("vol24") or 0) >= 800_000
            and abs(r.get("chg24") or 0) < 45)


def enrich(r):
    """4H + BTC confidence — sirf ye extra layers pakka banaati hain."""
    try:
        htf = S.htf_trend(r["coin"], r.get("_src") or "OKX")
    except Exception:
        htf = None
    try:
        btc = S.btc_bias() if r["coin"] != "BTC" else None
    except Exception:
        btc = None
    conf, label, reasons = S.confidence_engine(r, htf, btc, None)
    r["conf"] = conf
    r["conf_label"] = label
    r["conf_why"] = reasons
    return r


def near_entry(r):
    """Abhi entry possible? VWAP/entry se 2.5% ke andar."""
    px = r["price"]
    entry = (r["entry_lo"] + r["entry_hi"]) / 2
    return abs(px / entry - 1) * 100 <= 2.5


def fmt_pakka(r):
    side = "🟢 BUY" if r["score"] > 0 else "🔴 SHORT"
    split = r["conf"] >= 72 and r["score"] >= 6
    lines = [
        f"🎯 PAKKA SETUP  {datetime.now(IST).strftime('%d %b %H:%M IST')}",
        f"{side}  {r['coin']}/USDT",
        f"Price: {S.fmt_px(r['price'])}  ({r['chg24']:+.1f}%)",
        f"🎖️ Confidence: {r['conf']:.0f}%  |  Score {r['score']:+d}",
        "",
    ]
    if split and r["score"] > 0:
        lines += [
            "📋 SPLIT ENTRY (miss na ho):",
            f"1️⃣ AADHA ABHI market/limit @ {S.fmt_px(r['price'])}",
            f"2️⃣ AADHA LIMIT @ {S.fmt_px(r['entry_lo'])} (VWAP)",
            f"🛡️ SL dono ka: {S.fmt_px(r['sl'])}",
        ]
    else:
        lines += [
            f"💰 Entry: {S.fmt_px(r['entry_lo'])} – {S.fmt_px(r['entry_hi'])}",
            f"🛡️ SL: {S.fmt_px(r['sl'])}",
        ]
    lines += [
        f"🎯 TP1: {S.fmt_px(r['tp1'])}  (50% book, SL entry pe utha dena)",
        f"🎯 TP2: {S.fmt_px(r['tp2'])}",
        f"🎲 TP1 ≈ {r['prob'][1]:.0f}%   R:R 1:{r['rr1']:.1f}",
        "",
        "📝 Exit sirf TP / SL — beech mein button nahi.",
        "⚠️ SL ke bina mat lagao. Size chhota. Risk aapka.",
    ]
    return "\n".join(lines)


def main():
    results, n = collect()
    elites = [r for r in results if is_elite(r)]
    print(f"scanned {n}, analyzed {len(results)}, elite-raw {len(elites)}")

    pakka = []
    for r in elites:
        enrich(r)
        print(f"  {r['coin']} conf={r['conf']:.0f} near={near_entry(r)}")
        # PAKKA = HIGH confidence 72%+ AND (near entry OR strong long with split)
        if r["conf"] < 72:
            continue
        if abs(r.get("chg24") or 0) >= 40:
            continue  # pump-trap
        pakka.append(r)

    # sabse high confidence pehle, near-entry ko bonus
    pakka.sort(key=lambda r: (r["conf"] + (8 if near_entry(r) else 0)), reverse=True)

    if not pakka:
        if os.environ.get("FORCE"):
            send("🤖 Scan complete — aaj PAKKA setup nahi. WAIT.\n(Filter: conf 72%+, ELITE, no pump-trap)")
        print("no pakka — silent")
        return

    # SIRF 1 coin — jo sabse pakka ho
    best = pakka[0]
    send(fmt_pakka(best))
    print("sent", best["coin"], best["conf"])


if __name__ == "__main__":
    main()
