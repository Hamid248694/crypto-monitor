#!/usr/bin/env python3
"""Telegram ELITE scanner — GitHub Actions se 24/7 chalta hai."""
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
            continue
        try:
            r = S.analyze(coin, d, src, with_cvd=False)
            r["vol24"] = t["vol"]
            results.append(r); done += 1
        except Exception:
            continue
    return results, len(allt)


def is_elite(r):
    if r["prob"][2] is None:
        return False
    return (abs(r["score"]) >= 6 and r["prob"][1] >= 70 and (r.get("rr1") or 0) >= 1.8
            and (r.get("vol24") or 0) >= 800_000 and abs(r.get("chg24") or 0) < 50)


def fmt_pick(r, tag="⭐ ELITE"):
    side = "🟢 BUY" if r["score"] > 0 else "🔴 SHORT"
    vol = r["vol24"]
    vs = f"{vol/1e6:.1f}M" if vol >= 1e6 else f"{vol/1e3:.0f}K"
    return (
        f"{tag} {side} {r['coin']}/USDT\n"
        f"Price: {S.fmt_px(r['price'])}  ({r['chg24']:+.1f}% 24h)  vol {vs}\n"
        f"Score: {r['score']:+d}/{r['max_score']}\n"
        f"💰 Entry: {S.fmt_px(r['entry_lo'])} – {S.fmt_px(r['entry_hi'])}\n"
        f"🛡️ SL: {S.fmt_px(r['sl'])}\n"
        f"🎯 TP1: {S.fmt_px(r['tp1'])}   TP2: {S.fmt_px(r['tp2'])}\n"
        f"🎲 TP1 chance ≈ {r['prob'][1]:.0f}%   R:R 1:{r['rr1']:.1f}\n"
        f"👉 Order se pehle chat me '{r['coin']} buy and sell' likh ke confirm karo."
    )


def main():
    now = datetime.now(IST).strftime("%d %b %H:%M IST")
    results, n = collect()
    elites = [r for r in results if is_elite(r)]
    print(f"scanned {n} pairs, analyzed {len(results)}, elite {len(elites)}")

    if elites:
        parts = [f"🤖 AUTO-SCAN  {now}\n{len(elites)} ELITE pick(s) — 2180+ coins se:"]
        for r in elites[:4]:
            parts.append("\n" + fmt_pick(r))
        parts.append("\n⚠️ Educational — SL ke bina mat lagao. Risk aapka.")
        send("\n".join(parts))
    else:
        # silent wait — spam nahi. heartbeat sirf FORCE=1 pe
        if os.environ.get("FORCE"):
            send(f"🤖 AUTO-SCAN  {now}\n⭐ ELITE: koi nahi — WAIT hi profit.\n"
                 f"(Filter: score 6+, chance 70%+, R:R 1.8+)")
        print("no elite — silent")


if __name__ == "__main__":
    main()
