#!/usr/bin/env python3
"""Telegram — PAKKA pick + CoinTrendz pump-channel auto scan."""
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from html import unescape

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import signal as S
import scan as SC

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT", "")
WHALE_CHAT = os.environ.get("WHALE_CHAT", "@hamid_whales")
IST = timezone(timedelta(hours=5, minutes=30))
HL = "https://api.hyperliquid.xyz/info"
HL_LB = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"


def send_chat(chat_id, text):
    if not TG_TOKEN or not chat_id:
        print("TG secrets missing"); return False
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=20,
    )
    j = r.json()
    ok = j.get("ok")
    print("telegram", chat_id, ok, "" if ok else j)
    return ok


def send(text):
    return send_chat(TG_CHAT, text)


def fmt_usd(v):
    a = abs(v)
    if a >= 1e6:
        return f"${v/1e6:.2f}M"
    if a >= 1e3:
        return f"${v/1e3:.0f}K"
    return f"${v:.0f}"


def collect(top=22, min_vol=400000):
    okx = SC.stage1_okx()
    gate = SC.stage1_gate()
    seen = {t["coin"] for t in okx}
    allt = okx + [t for t in gate if t["coin"] not in seen]
    liq = [t for t in allt if t["vol"] >= min_vol]
    movers = sorted(
        [t for t in liq if 8 <= abs(t["chg"]) < 35],
        key=lambda t: abs(t["chg"]), reverse=True)[:18]
    healthy = sorted(
        [t for t in liq if 2 <= abs(t["chg"]) <= 12],
        key=lambda t: t["vol"], reverse=True)[:28]
    by_vol = sorted(liq, key=lambda t: t["vol"], reverse=True)[:15]
    cand, seen2 = [], set()
    for t in healthy + movers + by_vol:
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
            and abs(r.get("chg24") or 0) < 28)


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
    return abs(px / entry - 1) * 100 <= 3.0


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
    ]
    fd = r.get("field")
    if fd:
        lines.append("")
        if fd.get("ask_w"):
            w = fd["ask_w"][0]
            lines.append(f"🔺 UPAR deewar: {S.fmt_px(w['px'])} pe ${w['usd']:,.0f} — tootne ka chance ≈ {fd['ask_break']:.0f}%")
        if fd.get("bid_w"):
            w = fd["bid_w"][0]
            lines.append(f"🔻 NEECHE deewar: {S.fmt_px(w['px'])} pe ${w['usd']:,.0f} — tootne ka chance ≈ {fd['bid_break']:.0f}%")
        lines.append(fd["crowd_txt"])
    lines += [
        "",
        "📝 Exit sirf TP / SL — beech mein button nahi.",
        "⚠️ SL ke bina mat lagao. Size chhota. Risk aapka.",
    ]
    return "\n".join(lines)


PUMP_CH = "cointrendz_pumpdetector"
PUMP_PAGE = f"https://t.me/s/{PUMP_CH}"
PUMP_WINDOW_MIN = 18  # 15-min cron ke saath naya post catch, duplicate kam


def fetch_recent_pump():
    """Public channel page se sabse naya pump coin, sirf agar abhi-abhi aaya ho."""
    r = requests.get(PUMP_PAGE, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    r.raise_for_status()
    text = r.text
    date_id = re.findall(
        rf'tgme_widget_message_date.*?datetime="([^"]+)".*?t\.me/{PUMP_CH}/(\d+)',
        text, re.S,
    )
    id_coin = []
    seen = set()
    for m in re.finditer(r"Pump</b>\s*-\s*([^/\s<]+)/USDT", text):
        coin = unescape(m.group(1)).upper().strip()
        chunk = text[max(0, m.start() - 2500): m.end() + 80]
        ids = re.findall(rf"t\.me/{PUMP_CH}/(\d+)", chunk)
        if not ids or not re.fullmatch(r"[A-Z0-9]{2,15}", coin):
            continue
        pid = int(ids[-1])
        if pid in seen:
            continue
        seen.add(pid)
        id_coin.append((pid, coin))
    times = {int(i): dt for dt, i in date_id}
    now = datetime.now(timezone.utc)
    fresh = []
    for pid, coin in id_coin:
        raw = times.get(pid)
        if not raw:
            continue
        try:
            ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            continue
        age = (now - ts).total_seconds() / 60
        if 0 <= age <= PUMP_WINDOW_MIN:
            fresh.append((pid, coin, age))
    fresh.sort(key=lambda x: x[0], reverse=True)
    return fresh[0] if fresh else None


def load_coin(coin):
    for fn, src in (
        (S.fetch_okx, "OKX"),
        (S.fetch_gate, "Gate"),
        (S.fetch_binance, "Binance"),
        (S.fetch_mexc, "MEXC"),
    ):
        try:
            d = fn(coin)
        except Exception:
            d = None
        if d:
            return d, src
    return None, None


def scan_one(coin):
    d, src = load_coin(coin)
    if not d:
        return None
    r = S.analyze(coin, d, src, with_cvd=False)
    r["_src"] = src
    enrich(r)
    try:
        cvd_t = (r.get("cvd") or (None, 0))[1]
        r["field"] = S.battlefield(coin, src, r["price"], r["score"], cvd_t, None, None)
    except Exception:
        r["field"] = None
    return r


def fmt_channel_scan(r):
    """Wahi 4 cheezein jo normal coin signal mein hoti hain."""
    side = "🟢 BUY" if r["score"] > 0 else "🔴 SHORT" if r["score"] < 0 else "⚪ WAIT"
    lines = [
        f"📡 PUMP CHANNEL  {datetime.now(IST).strftime('%d %b %H:%M IST')}",
        f"{side}  {r['coin']}/USDT  ·  {r['signal']}",
        f"Price: {S.fmt_px(r['price'])}  ({r['chg24']:+.1f}%)",
        f"🎖️ Confidence: {r.get('conf', 0):.0f}%  |  Score {r['score']:+d}",
        "",
        f"💰 Entry: {S.fmt_px(r['entry_lo'])} – {S.fmt_px(r['entry_hi'])}",
        f"🛡️ SL: {S.fmt_px(r['sl'])}",
        f"🎯 TP1: {S.fmt_px(r['tp1'])}  |  TP2: {S.fmt_px(r['tp2'])}",
        f"🎲 CHANCE: TP1 ≈ {r['prob'][1]:.0f}%  |  TP2 ≈ {r['prob'][2]:.0f}%",
    ]
    fd = r.get("field")
    if fd:
        lines.append("")
        if fd.get("ask_w"):
            w = fd["ask_w"][0]
            lines.append(
                f"🔺 UPAR deewar: {S.fmt_px(w['px'])} pe ${w['usd']:,.0f} — toot ≈ {fd['ask_break']:.0f}%"
            )
        if fd.get("bid_w"):
            w = fd["bid_w"][0]
            lines.append(
                f"🔻 NEECHE deewar: {S.fmt_px(w['px'])} pe ${w['usd']:,.0f} — toot ≈ {fd['bid_break']:.0f}%"
            )
        if fd.get("crowd_txt"):
            lines.append(fd["crowd_txt"])
    if abs(r.get("chg24") or 0) >= 6:
        lines += ["", "⚠️ Channel ka PUMP alert — price hil chuka. Conf 72%+ na ho to WAIT."]
    lines += ["", "📝 Exit sirf TP / SL. Size chhota. Risk aapka."]
    return "\n".join(lines)


def _hl_vol30(row):
    for w, p in row.get("windowPerformances", []):
        if w == "month":
            return float(p.get("vlm", 0))
    return 0.0


def _whale_remaining(addr, coin):
    try:
        d = requests.post(
            HL, json={"type": "clearinghouseState", "user": addr},
            headers={"Content-Type": "application/json"}, timeout=12,
        ).json()
        for p in d.get("assetPositions") or []:
            q = p.get("position") or {}
            if (q.get("coin") or "").upper() != coin.upper():
                continue
            sz = float(q.get("szi") or 0)
            if sz == 0:
                return 0.0, 0.0, "FLAT"
            side = "LONG" if sz > 0 else "SHORT"
            return float(q.get("positionValue") or 0), float(q.get("unrealizedPnl") or 0), side
    except Exception:
        pass
    return None, None, None


def whale_watch():
    """Hyperliquid top whales ke naye fill — @hamid_whales pe."""
    if not TG_TOKEN:
        return
    try:
        rows = requests.get(HL_LB, timeout=20).json().get("leaderboardRows", [])
    except Exception as e:
        print("whale lb fail", e)
        return
    rows.sort(key=_hl_vol30, reverse=True)
    addrs = [r["ethAddress"] for r in rows[:18]]
    now_ms = time.time() * 1000
    window = 18 * 60 * 1000
    min_usd = 80_000

    def pulls(addr):
        try:
            fills = requests.post(
                HL, json={"type": "userFills", "user": addr},
                headers={"Content-Type": "application/json"}, timeout=12,
            ).json()
        except Exception:
            return []
        if not isinstance(fills, list):
            return []
        out = []
        for f in fills:
            if now_ms - float(f.get("time") or 0) > window:
                continue
            coin = f.get("coin") or ""
            if ":" in coin:
                continue
            px = float(f.get("px") or 0)
            sz = float(f.get("sz") or 0)
            usd = px * sz
            if usd < min_usd:
                continue
            out.append({
                "addr": addr, "coin": coin, "px": px, "sz": sz, "usd": usd,
                "dir": f.get("dir") or "", "pnl": float(f.get("closedPnl") or 0),
                "tid": str(f.get("tid") or f.get("hash") or ""),
            })
        return out

    events = []
    with ThreadPoolExecutor(10) as ex:
        futs = [ex.submit(pulls, a) for a in addrs]
        for fut in as_completed(futs):
            events.extend(fut.result() or [])
    events.sort(key=lambda x: x["usd"], reverse=True)
    print("whale events", len(events))
    sent = 0
    seen = set()
    for e in events:
        if sent >= 4:
            break
        key = (e["addr"], e["coin"], e["dir"], round(e["usd"], -3))
        if key in seen:
            continue
        seen.add(key)
        d = (e["dir"] or "").lower()
        if "close" in d:
            emoji = "💸"
        elif "short" in d:
            emoji = "🔴"
        else:
            emoji = "🟢"
        rem_v, rem_pnl, rem_side = _whale_remaining(e["addr"], e["coin"])
        lines = [
            f"🐋 WHALE  {datetime.now(IST).strftime('%d %b %H:%M IST')}",
            f"{emoji} {e['coin']}  ·  {e['dir']}",
            f"Price: {e['px']}",
            f"Paisa: {fmt_usd(e['usd'])}",
        ]
        if "close" in d:
            sign = "+" if e["pnl"] >= 0 else ""
            lines.append(f"PnL (is trade): {sign}{fmt_usd(e['pnl'])}")
        if rem_side and rem_side != "FLAT" and rem_v is not None:
            lines.append(f"Abhi open: {rem_side} {fmt_usd(rem_v)}  (uPnL {fmt_usd(rem_pnl or 0)})")
        elif rem_side == "FLAT":
            lines.append("Abhi open: kuch nahi (flat)")
        lines += [
            f"Wallet: {e['addr'][:8]}…{e['addr'][-4:]}",
            "",
            "Hyperliquid whale · copy mat karna andha. Risk aapka.",
        ]
        if send_chat(WHALE_CHAT, "\n".join(lines)):
            sent += 1
    print("whale sent", sent)


def pump_watch():
    try:
        hit = fetch_recent_pump()
    except Exception as e:
        print("pump fetch fail", e)
        return
    if not hit:
        print("pump: no fresh post")
        return
    pid, coin, age = hit
    print(f"pump fresh {coin} id={pid} age={age:.1f}m")
    r = scan_one(coin)
    if not r:
        send(f"{coin}: data nahi mila.")
        return
    send(fmt_channel_scan(r))
    print("pump sent", coin)


def main():
    try:
        pump_watch()
    except Exception as e:
        print("pump fail", e)
    try:
        whale_watch()
    except Exception as e:
        print("whale fail", e)
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
    try:
        cvd_t = (best.get("cvd") or (None, 0))[1]
        best["field"] = S.battlefield(
            best["coin"], best.get("_src") or "OKX", best["price"],
            best["score"], cvd_t, None, best.get("vol24"))
    except Exception:
        best["field"] = None
    send(fmt_pakka(best))
    print("sent", best["coin"], best["conf"])


if __name__ == "__main__":
    main()
