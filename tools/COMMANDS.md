# STANDARD COMMAND FORMAT (user se save kiya gaya)

## Command:
User likhega: **"[COIN] BUY AND SELL"**  (e.g. "DOGE BUY AND SELL", "ONT BUY AND SELL")
(Galti se "BY AND SELL" bhi likh sakte hain — matlab same hai)

## Reply MEIN ye 4 cheezein HAMESHA deni hain:
1. **BUY price** — kis price pe buy karna hai (entry zone)
2. **SELL price** — kis price pe sell/exit karna hai (TP1, TP2)
3. **Target hit hone ka CHANCE (%)** — percent mein, estimate ke saath
4. **Stop Loss** — kahan rakhna hai (risk protection)

## Format example:
```
DOGE/USDT — abhi ka data (time)
Price: xxx (24h: +x%)

🟢 BUY zone : a — b
🔴 SELL/exit: TP1 = c | TP2 = d
🛡️  STOP    : s
🎲 CHANCE   : TP1 hit ≈ x% | TP2 hit ≈ y% | SL first ≈ z%

1 line reason + kya tab change hoga (trigger levels)
```

## Rules:
- Hamesha FRESH data uthana hai (signal.py run karke), kabhi purana nahi
- Probability transparent estimate hai (score + R:R se), kabhi 100% claim nahi, max 85%
- Signal WAIT ho toh dono direction ke % dene hain
- Hamesha disclaimer chhota sa: risk apna hai
- Reply short aur clear rakhna — 4 cheezein + 1 line reason, zyada nahi
