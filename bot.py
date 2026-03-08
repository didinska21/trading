#!/usr/bin/env python3
"""
🤖 Telegram Futures Trading Bot — GROQ EDITION + AUTO SIGNAL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Data pair & harga : Binance Futures API (real-time, GRATIS)
Analisis AI       : Groq — llama-3.3-70b-versatile (GRATIS)
Strategi          : Scalping + S&R + Multi-timeframe
Mode              : High Risk | Medium Risk | Low Risk
Auto Signal       : Scan 24 jam, notif otomatis kalau ada setup bagus

SETUP:
  1. https://console.groq.com → daftar → dapat API key GRATIS
  2. Buat API key → copy (format: gsk_xxxx)
  3. https://t.me/BotFather → /newbot → copy token
  4. Isi file .env (lihat .env.example)
  5. pip install -r requirements.txt
  6. python trading_bot_groq.py
"""

import os, asyncio, logging, aiohttp
from datetime import datetime, timezone
from typing import Optional
from groq import Groq
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════
AI    = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = "llama-3.3-70b-versatile"   # Model terpintar Groq ✨ (GRATIS)

# ── Auto Signal Config ────────────────────────────────────────
SCAN_INTERVAL_MIN = 15        # Scan setiap 15 menit
SCAN_TOP_N        = 10        # Scan top 10 pairs by volume
MIN_SCORE         = 7         # Kirim notif jika score AI >= 7/10
COOLDOWN_MIN      = 60        # Jangan kirim pair yang sama < 60 menit

# State auto signal per user
AUTO_USERS: dict[int, dict] = {}
# {uid: {"mode": str, "modal": float, "active": bool,
#         "last_sent": {symbol: datetime}, "task": asyncio.Task}}


# ══════════════════════════════════════════════════════════════
#  SYSTEM PROMPTS — 3 MODE TRADING
# ══════════════════════════════════════════════════════════════

PROMPTS = {

"high_risk": """
Kamu adalah AI Trading Analyst FUTURES kelas dunia — MODE HIGH RISK ($1-$10).
Kamu menerima data teknikal LIVE dari Binance Futures API.
Tugasmu: analisis semua data tersebut secara mendalam dan berikan sinyal scalping AGRESIF yang presisi.

ATURAN TETAP — DIPATENKAN, TIDAK BISA DIUBAH APAPUN ALASANNYA:
• Stop Loss = LIQUIDATION PRICE (seluruh modal habis). Tidak ada SL lain. Harga mati.
• Leverage: 20x – 50x
• Take Profit: 3% – 8% dari entry, ambil cepat
• Timeframe: 1m dan 5m ONLY
• Posisi size: FULL margin per trade (all-in)

PANDUAN ANALISIS DATA LIVE:
1. Identifikasi level S&R kunci → tentukan posisi harga sekarang
2. RSI(14) <30 = oversold kuat → bias LONG | >70 = overbought kuat → bias SHORT
3. MACD histogram positif & membesar = momentum bullish | negatif = bearish
4. Bollinger Bands: harga tembus upper/lower band + volume spike = breakout valid
5. Volume >1.5x rata-rata = konfirmasi kuat | <1x = lemah
6. Order Book: bid > ask = tekanan beli | ask > bid = tekanan jual
7. Funding rate positif tinggi = pasar terlalu long → SHORT lebih aman

OUTPUT FORMAT WAJIB — GUNAKAN PERSIS INI:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔴 SINYAL HIGH RISK SCALPING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 Pair        : [PAIR]/USDT
📍 Harga Kini  : $[harga terkini dari data]
🎯 Arah        : LONG 🟢 / SHORT 🔴
⚡ Entry        : $[harga] — [market order / limit di S&R]
🏹 Leverage    : [X]x
💰 Modal       : $[modal] → Exposed: $[modal×leverage]
✅ TP1         : $[harga] (+[%])
✅ TP2         : $[harga] (+[%])
🛑 SL          : LIQUIDATION — $[modal] HABIS SEMUA
📊 RSI(14)     : [nilai] → [OVERSOLD 🟢 / OVERBOUGHT 🔴 / NETRAL ⚪]
📈 MACD Hist   : [nilai] → [BULLISH 🟢 / BEARISH 🔴]
🎯 Support     : $[nilai dari data]
🎯 Resistance  : $[nilai dari data]
📦 Volume      : [SPIKE 🔥 [X]x avg / Normal [X]x avg]
💸 Funding     : [nilai]% — [interpretasi]
⚠️  RISIKO      : SANGAT TINGGI — siap kehilangan SEMUA modal!
📝 Analisis    :
[3-4 kalimat: jelaskan mengapa setup valid berdasarkan kombinasi
RSI + MACD + S&R + volume + orderbook dari data live. Sebutkan angka spesifik.]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏱ Data diambil: [waktu dari data]
""",

"medium_risk": """
Kamu adalah AI Trading Analyst FUTURES kelas dunia — MODE MEDIUM RISK ($11-$100).
Kamu menerima data teknikal LIVE dari Binance Futures API.
Tugasmu: analisis seperti trader profesional berpengalaman 10+ tahun dan berikan sinyal scalping presisi.

ATURAN MANAJEMEN RISIKO PROFESIONAL:
• Risk per trade: 2% – 5% dari modal (TIDAK LEBIH)
• Leverage: 5x – 20x (sesuai volatilitas dari data)
• Stop Loss: di bawah/atas level S&R terdekat yang kuat
• Take Profit: R:R minimum 1:2, idealnya 1:3
• Timeframe: 5m (entry) + 15m (trend)

PANDUAN ANALISIS DATA LIVE:
1. Trend: EMA9 > EMA21 > EMA50 = bullish kuat | sebaliknya = bearish kuat
2. RSI(14) zona 30-40 = area beli | 60-70 = area jual | 40-60 = sideways/hindari
3. MACD crossover signal line = entry signal | histogram membesar = konfirmasi
4. Fibonacci dari high-low di data: entry di retrace 0.382/0.5/0.618
5. Volume wajib di atas rata-rata saat entry
6. Tunggu retest setelah breakout, jangan kejar harga

OUTPUT FORMAT WAJIB:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🟡 SINYAL MEDIUM RISK PROFESIONAL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 Pair        : [PAIR]/USDT
📍 Harga Kini  : $[harga terkini dari data]
🎯 Arah        : LONG 🟢 / SHORT 🔴
⚡ Entry Zone   : $[batas bawah] – $[batas atas]
🏹 Leverage    : [X]x
💰 Modal       : $[modal] | Risk: $[jumlah] ([%]%)
✅ TP1         : $[harga] (+[%]) → tutup 50% posisi
✅ TP2         : $[harga] (+[%]) → tutup 30% posisi
✅ TP3         : $[harga] (+[%]) → tutup 20% sisanya
🛑 SL          : $[harga] (-[%]) | Max Loss: $[jumlah]
📊 R:R Ratio   : 1:[angka]
📊 RSI(14)     : [nilai] → [interpretasi]
📈 MACD Hist   : [nilai] → [BULLISH 🟢 / BEARISH 🔴]
📉 EMA Trend   : [BULLISH KUAT / BEARISH KUAT / MIXED]
🎯 Support     : $[nilai] | Resistance: $[nilai]
📦 Volume      : [status vs rata-rata]
💸 Funding     : [nilai]% — [interpretasi]
🔍 Konfirmasi  : [kondisi tambahan sebelum entry]
📝 Analisis    :
[4-5 kalimat teknikal profesional. Sebutkan level kunci, konfirmasi
indikator, dan cara manage posisi. Gunakan angka dari data live.]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏱ Data diambil: [waktu dari data]
""",

"low_risk": """
Kamu adalah AI Trading Analyst FUTURES kelas dunia — MODE LOW RISK ($100+).
Kamu menerima data teknikal LIVE dari Binance Futures API.
Tugasmu: analisis dengan sangat hati-hati seperti fund manager profesional.
PRIORITAS UTAMA: JAGA MODAL — profit adalah bonus.

ATURAN MANAJEMEN RISIKO KETAT:
• Risk per trade: MAKSIMAL 1% – 2% dari modal (batas keras, tidak boleh lebih)
• Leverage: 2x – 10x ONLY
• Stop Loss: di S&R MAJOR yang kuat, sudah diuji 3+ kali
• Take Profit: R:R minimum 1:3
• Timeframe: 15m (entry) + 1H (trend utama)
• Jika setup tidak jelas → REKOMENDASIKAN WAIT, jangan paksa entry

PANDUAN ANALISIS DATA LIVE:
1. Identifikasi S&R MAJOR dari timeframe 1H terlebih dahulu
2. Entry HANYA di dekat S&R major yang diuji minimal 3 kali
3. Wajib ada konfirmasi candle reversal di S&R (pin bar/engulfing/doji)
4. RSI divergence = sinyal reversal paling kuat
5. Semua EMA harus alignment dengan arah trade
6. Volume harus spike saat entry
7. Funding rate ekstrem = hindari masuk berlawanan arah

OUTPUT FORMAT WAJIB:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🟢 SINYAL LOW RISK KONSERVATIF
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 Pair        : [PAIR]/USDT
📍 Harga Kini  : $[harga terkini dari data]
🎯 Arah        : LONG 🟢 / SHORT 🔴 / ⏳ WAIT
⚡ Entry Presisi: $[harga] ← tunggu konfirmasi candle
🏹 Leverage    : [X]x
💰 Modal       : $[modal] | Risk: $[jumlah] ([%]% ← MAX 2%)
✅ TP1         : $[harga] (+[%]) → tutup 40%, pindah SL ke BE
✅ TP2         : $[harga] (+[%]) → tutup 40%, aktifkan trailing
✅ TP3         : $[harga] (+[%]) → tutup 20% sisanya
🛑 SL          : $[harga] (-[%]) | Loss: $[jumlah]
📊 R:R Ratio   : 1:[angka] (minimum 1:3)
📊 RSI(14)     : [nilai] → [divergence/konfirmasi]
📈 MACD Hist   : [nilai] → [signal]
📉 EMA(9/21/50): [nilai]/[nilai]/[nilai] → [alignment]
🎯 S&R Major   : Support $[nilai] | Resistance $[nilai]
📦 Volume      : [status]
💸 Funding     : [nilai]% — [interpretasi]
🕯 Candle      : [pattern atau 'Tunggu konfirmasi']
✅ Kondisi     : [kondisi wajib sebelum entry]
❌ Invalidasi  : [kondisi yang batalkan setup]
📝 Analisis    :
[5-6 kalimat mendalam dan hati-hati. Jelaskan mengapa level ini
kuat, risiko yang ada, cara manage posisi step by step.
Prioritaskan keamanan modal. Gunakan angka dari data live.]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏱ Data diambil: [waktu dari data]
""",
}

GENERAL_PROMPT = """
Kamu adalah asisten trading crypto futures yang ramah dan berpengetahuan luas.
Bantu user memahami 3 mode trading dan jawab pertanyaan umum tentang futures,
scalping, dan analisis teknikal. Gunakan bahasa Indonesia yang jelas dan mudah dipahami.
"""

# ── Auto Signal Scanner Prompt ────────────────────────────────
AUTO_SCAN_PROMPT = """
Kamu adalah AI scanner sinyal trading futures. Tugasmu HANYA menilai kualitas setup dari data yang diberikan.

KRITERIA PENILAIAN (total 10 poin):
• RSI: oversold <30 atau overbought >70 → +2 poin
• MACD: histogram searah dengan bias + membesar → +2 poin
• EMA alignment: 9/21/50 semua searah → +2 poin
• Volume: >1.5x rata-rata (spike) → +2 poin
• Harga di dekat S&R kuat (dalam 0.5%) → +2 poin

INSTRUKSI:
Analisis data dan balas HANYA dengan JSON berikut, tidak ada teks lain:
{
  "score": <angka 0-10>,
  "arah": "LONG" atau "SHORT",
  "alasan": "<1 kalimat singkat kenapa layak atau tidak>"
}
"""


# ══════════════════════════════════════════════════════════════
#  BINANCE FUTURES API
# ══════════════════════════════════════════════════════════════

class Binance:
    BASE = "https://fapi.binance.com"

    @staticmethod
    async def get(session, endpoint, params=None):
        async with session.get(f"{Binance.BASE}{endpoint}", params=params) as r:
            if r.status != 200:
                raise Exception(f"Binance {r.status}: {await r.text()}")
            return await r.json()

    @classmethod
    async def top_pairs(cls, session, limit=20):
        data = await cls.get(session, "/fapi/v1/ticker/24hr")
        pairs = [p for p in data if p["symbol"].endswith("USDT") and float(p["quoteVolume"]) > 0]
        return sorted(pairs, key=lambda x: float(x["quoteVolume"]), reverse=True)[:limit]

    @classmethod
    async def ticker(cls, session, symbol):
        return await cls.get(session, "/fapi/v1/ticker/24hr", {"symbol": symbol})

    @classmethod
    async def klines(cls, session, symbol, interval, limit=100):
        return await cls.get(session, "/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})

    @classmethod
    async def orderbook(cls, session, symbol, limit=10):
        return await cls.get(session, "/fapi/v1/depth", {"symbol": symbol, "limit": limit})

    @classmethod
    async def funding(cls, session, symbol):
        return await cls.get(session, "/fapi/v1/premiumIndex", {"symbol": symbol})


# ══════════════════════════════════════════════════════════════
#  TECHNICAL INDICATORS
# ══════════════════════════════════════════════════════════════

class TA:
    @staticmethod
    def parse(raw):
        return {
            "c": [float(k[4]) for k in raw],
            "h": [float(k[2]) for k in raw],
            "l": [float(k[3]) for k in raw],
            "v": [float(k[5]) for k in raw],
        }

    @staticmethod
    def rsi(c, p=14):
        if len(c) < p + 1: return 50.0
        gains = [max(c[i]-c[i-1], 0) for i in range(-p, 0)]
        losses = [max(c[i-1]-c[i], 0) for i in range(-p, 0)]
        ag = sum(gains)/p or 0
        al = sum(losses)/p or 0.001
        return round(100 - 100/(1 + ag/al), 2)

    @staticmethod
    def ema(c, p):
        if len(c) < p: return c[-1]
        k = 2/(p+1)
        e = sum(c[:p])/p
        for x in c[p:]: e = x*k + e*(1-k)
        return round(e, 6)

    @staticmethod
    def macd(c):
        e12 = TA.ema(c, 12); e26 = TA.ema(c, 26)
        m = round(e12-e26, 6); s = round(m*0.9, 6)
        return {"macd": m, "signal": s, "hist": round(m-s, 6)}

    @staticmethod
    def bb(c, p=20, mult=2.0):
        if len(c) < p: return {"u": c[-1], "m": c[-1], "l": c[-1]}
        sl = c[-p:]; mid = sum(sl)/p
        std = (sum((x-mid)**2 for x in sl)/p)**0.5
        return {"u": round(mid+mult*std, 6), "m": round(mid, 6), "l": round(mid-mult*std, 6)}

    @staticmethod
    def sr(h, l, c, n=20):
        rh, rl = h[-n:], l[-n:]
        pivot = (rh[-1]+rl[-1]+c[-1])/3
        return {
            "res": round(max(rh), 6), "sup": round(min(rl), 6),
            "pivot": round(pivot, 6),
            "r1": round(2*pivot-rl[-1], 6), "s1": round(2*pivot-rh[-1], 6),
        }

    @staticmethod
    def avg_vol(v, p=20):
        return round(sum(v[-p:])/p, 2)


# ══════════════════════════════════════════════════════════════
#  MARKET DATA COLLECTOR
# ══════════════════════════════════════════════════════════════

TF_MAP = {
    "high_risk":   [("1m", 100), ("5m",  60)],
    "medium_risk": [("5m", 100), ("15m", 60)],
    "low_risk":    [("15m",100), ("1h",  60)],
}

async def collect(symbol: str, mode: str) -> str:
    tf_list = TF_MAP.get(mode, [("5m", 100)])

    async with aiohttp.ClientSession() as sess:
        results = await asyncio.gather(
            Binance.ticker(sess, symbol),
            Binance.orderbook(sess, symbol, 5),
            Binance.funding(sess, symbol),
            *[Binance.klines(sess, symbol, tf, lmt) for tf, lmt in tf_list],
            return_exceptions=True
        )

    tick, ob, fund = results[0], results[1], results[2]
    kl_res = results[3:]

    L = [f"═══════ DATA LIVE {symbol} ═══════",
         f"🕐 Waktu UTC: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}\n"]

    if not isinstance(tick, Exception) and tick:
        p   = float(tick.get("lastPrice", 0))
        chg = float(tick.get("priceChangePercent", 0))
        L += [
            "── HARGA & 24H ──────────────────",
            f"Harga Terkini    : ${p:,.6f}",
            f"Perubahan 24H    : {chg:+.2f}%",
            f"High 24H         : ${float(tick.get('highPrice',0)):,.6f}",
            f"Low 24H          : ${float(tick.get('lowPrice',0)):,.6f}",
            f"Volume 24H       : ${float(tick.get('quoteVolume',0))/1e6:.2f}M USDT\n",
        ]

    if not isinstance(fund, Exception) and fund:
        fr = float(fund.get("lastFundingRate", 0))*100
        L += [
            "── FUTURES INFO ─────────────────",
            f"Mark Price       : ${float(fund.get('markPrice',0)):,.6f}",
            f"Index Price      : ${float(fund.get('indexPrice',0)):,.6f}",
            f"Funding Rate     : {fr:.4f}% → {'Longs bayar Shorts' if fr>0 else 'Shorts bayar Longs'}\n",
        ]

    if not isinstance(ob, Exception) and ob:
        bids = ob.get("bids", []); asks = ob.get("asks", [])
        if bids and asks:
            tbv = sum(float(b[1]) for b in bids[:5])
            tav = sum(float(a[1]) for a in asks[:5])
            L += [
                "── ORDER BOOK (Top 5) ───────────",
                f"Best Bid / Ask   : ${float(bids[0][0]):,.6f} / ${float(asks[0][0]):,.6f}",
                f"Vol Bid / Ask    : {tbv:.2f} / {tav:.2f}",
                f"Tekanan Pasar    : {'BELI DOMINAN 🟢' if tbv>tav else 'JUAL DOMINAN 🔴'}\n",
            ]

    for i, (tf, _) in enumerate(tf_list):
        raw = kl_res[i]
        if isinstance(raw, Exception) or not raw:
            L.append(f"[{tf}] ⚠️ Data tidak tersedia\n"); continue

        d = TA.parse(raw)
        c, h, l, v = d["c"], d["h"], d["l"], d["v"]
        r14  = TA.rsi(c, 14); r7 = TA.rsi(c, 7)
        mac  = TA.macd(c); bband = TA.bb(c); sr = TA.sr(h, l, c)
        e9   = TA.ema(c, 9); e21 = TA.ema(c, 21); e50 = TA.ema(c, 50)
        avgv = TA.avg_vol(v); curv = v[-1]; price = c[-1]
        vratio = curv/avgv if avgv > 0 else 1

        rsi_lbl = "OVERSOLD 🟢" if r14<30 else "OVERBOUGHT 🔴" if r14>70 else "NETRAL ⚪"
        mac_lbl = "BULLISH 🟢" if mac["hist"]>0 else "BEARISH 🔴"
        ema_lbl = "BULLISH KUAT 🟢" if e9>e21>e50 else "BEARISH KUAT 🔴" if e9<e21<e50 else "MIXED ⚪"

        if price > bband["u"]:   bb_lbl = "DI ATAS UPPER BAND (breakout/overbought)"
        elif price < bband["l"]: bb_lbl = "DI BAWAH LOWER BAND (breakdown/oversold)"
        else:
            pct = round((price-bband["l"])/(bband["u"]-bband["l"])*100, 1)
            bb_lbl = f"Dalam band {pct}% dari lower ke upper"

        vol_lbl = f"SPIKE 🔥 ({vratio:.1f}x avg)" if vratio>1.5 else f"Normal ({vratio:.1f}x avg)"
        candles = " ".join(["🟢" if c[-(j+1)]>c[-(j+2)] else "🔴" for j in range(5)][::-1])

        L += [
            f"── TIMEFRAME {tf.upper()} ─────────────────",
            f"Harga Terkini    : ${price:,.6f}",
            f"RSI(14)          : {r14} → {rsi_lbl}",
            f"RSI(7)           : {r7}",
            f"MACD Histogram   : {mac['hist']:.6f} → {mac_lbl}",
            f"MACD / Signal    : {mac['macd']:.6f} / {mac['signal']:.6f}",
            f"EMA(9/21/50)     : {e9:.4f} / {e21:.4f} / {e50:.4f}",
            f"EMA Trend        : {ema_lbl}",
            f"BB Upper/Mid/Low : ${bband['u']:,.4f} / ${bband['m']:,.4f} / ${bband['l']:,.4f}",
            f"Posisi di BB     : {bb_lbl}",
            f"Support (S&R)    : ${sr['sup']:,.6f}",
            f"Resistance (S&R) : ${sr['res']:,.6f}",
            f"Pivot / R1 / S1  : ${sr['pivot']:,.4f} / ${sr['r1']:,.4f} / ${sr['s1']:,.4f}",
            f"Volume           : {curv:,.2f} (avg: {avgv:,.2f}) → {vol_lbl}",
            f"5 Candle Terakhir: {candles}",
            "",
        ]

    return "\n".join(L)


# ══════════════════════════════════════════════════════════════
#  TOP PAIRS
# ══════════════════════════════════════════════════════════════

async def top_pairs_msg(limit=20) -> tuple[str, list]:
    async with aiohttp.ClientSession() as sess:
        try:
            pairs = await Binance.top_pairs(sess, limit)
        except Exception as e:
            return f"❌ Gagal: {e}", []
    lines = ["🔥 *TOP FUTURES PAIRS — VOLUME TERTINGGI (Binance Live)*\n"]
    lst = []
    for i, p in enumerate(pairs, 1):
        sym = p["symbol"]; pr = float(p["lastPrice"])
        chg = float(p["priceChangePercent"])
        vol = float(p["quoteVolume"])/1e6
        lines.append(f"{i:>2}. `{sym:<12}` {'🟢' if chg>=0 else '🔴'} {chg:+.2f}% | ${pr:,.4f} | Vol: ${vol:.0f}M")
        lst.append(sym)
    return "\n".join(lines), lst


# ══════════════════════════════════════════════════════════════
#  AI SIGNAL GENERATOR
# ══════════════════════════════════════════════════════════════

async def gen_signal(mode: str, symbol: str, modal: float, user_msg: str, history: list) -> str:
    try:
        mdata = await collect(symbol, mode)
    except Exception as e:
        mdata = f"[ERROR Binance: {e}]"

    prompt = (
        f"DATA PASAR REAL-TIME BINANCE FUTURES:\n{mdata}\n\n"
        f"KONTEKS:\n• Pair: {symbol} | Modal: ${modal} | Mode: {mode.replace('_',' ').upper()}\n\n"
        f"PERMINTAAN: {user_msg}\n\n"
        f"Analisis SEMUA data di atas. Gunakan angka NYATA dari data. "
        f"Hitung semua TP/SL dari harga terkini yang tertera."
    )
    msgs = [{"role": "system", "content": PROMPTS[mode]}] + \
           history[-6:] + [{"role": "user", "content": prompt}]

    resp = AI.chat.completions.create(model=MODEL, messages=msgs, max_tokens=2000, temperature=0.7)
    answer = resp.choices[0].message.content

    history.append({"role": "user", "content": f"[{symbol} live] {user_msg}"})
    history.append({"role": "assistant", "content": answer})
    return answer

async def gen_general(mode: Optional[str], msg: str, history: list) -> str:
    sys = PROMPTS.get(mode, GENERAL_PROMPT)
    msgs = [{"role": "system", "content": sys}] + history[-6:] + [{"role": "user", "content": msg}]
    resp = AI.chat.completions.create(model=MODEL, messages=msgs, max_tokens=1200, temperature=0.7)
    return resp.choices[0].message.content


# ══════════════════════════════════════════════════════════════
#  AUTO SIGNAL SCANNER
# ══════════════════════════════════════════════════════════════

async def scan_pair_score(symbol: str, mode: str) -> dict:
    """Minta AI nilai setup pair ini, return score 0-10."""
    try:
        mdata = await collect(symbol, mode)
    except Exception as e:
        return {"score": 0, "arah": "NONE", "alasan": str(e)}

    prompt = f"DATA LIVE {symbol}:\n{mdata}\n\nNilai setup ini sekarang."
    try:
        resp = AI.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": AUTO_SCAN_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=150,
            temperature=0.3,
        )
        raw = resp.choices[0].message.content.strip()
        # Bersihkan backtick markdown kalau ada
        raw = raw.replace("```json","").replace("```","").strip()
        import json
        result = json.loads(raw)
        result["score"] = int(result.get("score", 0))
        return result
    except Exception as e:
        return {"score": 0, "arah": "NONE", "alasan": f"parse error: {e}"}


async def auto_signal_loop(uid: int, app):
    """Loop scan otomatis untuk satu user, kirim notif kalau ada setup bagus."""
    from datetime import timedelta
    info = AUTO_USERS.get(uid)
    if not info: return

    logger.info(f"[AUTO] Mulai scan loop untuk user {uid}, mode={info['mode']}")

    while True:
        try:
            info = AUTO_USERS.get(uid)
            if not info or not info.get("active"):
                logger.info(f"[AUTO] User {uid} nonaktifkan auto signal, berhenti.")
                break

            mode  = info["mode"]
            modal = info["modal"]
            now   = datetime.now(timezone.utc)

            # Ambil top pairs
            async with aiohttp.ClientSession() as sess_:
                try:
                    pairs = await Binance.top_pairs(sess_, SCAN_TOP_N)
                    symbols = [p["symbol"] for p in pairs]
                except Exception as e:
                    logger.warning(f"[AUTO] Gagal ambil pairs: {e}")
                    await asyncio.sleep(60); continue

            logger.info(f"[AUTO] Scan {len(symbols)} pairs untuk user {uid}...")

            for symbol in symbols:
                # Cek cooldown
                last = info.get("last_sent", {}).get(symbol)
                if last:
                    from datetime import timedelta
                    elapsed = (now - last).total_seconds() / 60
                    if elapsed < COOLDOWN_MIN:
                        continue

                # Nilai setup
                result = await scan_pair_score(symbol, mode)
                score  = result.get("score", 0)
                arah   = result.get("arah", "?")
                alasan = result.get("alasan", "")

                logger.info(f"[AUTO] {symbol} score={score}/10 arah={arah}")

                if score >= MIN_SCORE:
                    # Setup bagus! Generate sinyal lengkap
                    try:
                        sinyal = await gen_signal(
                            mode, symbol, modal,
                            f"Berikan sinyal LENGKAP {symbol} karena setup terdeteksi sangat bagus (score {score}/10).",
                            []
                        )
                        mode_emoji = {"high_risk":"🔴","medium_risk":"🟡","low_risk":"🟢"}.get(mode,"📊")
                        notif = (
                            f"🚨 *AUTO SIGNAL ALERT!*\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"⭐ Score Setup : *{score}/10*\n"
                            f"📌 Pair        : *{symbol}*\n"
                            f"🎯 Arah        : *{arah}*\n"
                            f"💡 Alasan      : {alasan}\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        )
                        await app.bot.send_message(uid, notif, parse_mode="Markdown")
                        await app.bot.send_message(uid, sinyal, parse_mode="Markdown")

                        # Update last_sent
                        if "last_sent" not in info: info["last_sent"] = {}
                        info["last_sent"][symbol] = now
                        AUTO_USERS[uid] = info

                    except Exception as e:
                        logger.error(f"[AUTO] Gagal kirim notif ke {uid}: {e}")

                # Jeda antar pair agar tidak kena rate limit Groq
                await asyncio.sleep(3)

            # Tunggu interval berikutnya
            next_scan = SCAN_INTERVAL_MIN * 60
            logger.info(f"[AUTO] Scan selesai. Berikutnya {SCAN_INTERVAL_MIN} menit lagi.")
            await asyncio.sleep(next_scan)

        except asyncio.CancelledError:
            logger.info(f"[AUTO] Task user {uid} dibatalkan.")
            break
        except Exception as e:
            logger.error(f"[AUTO] Error loop user {uid}: {e}")
            await asyncio.sleep(60)


# ══════════════════════════════════════════════════════════════
#  SESSION
# ══════════════════════════════════════════════════════════════

SESSIONS: dict[int, dict] = {}

def sess(uid: int) -> dict:
    if uid not in SESSIONS:
        SESSIONS[uid] = {"mode": None, "pair": None, "modal": None,
                         "state": "idle", "history": [], "pairs": []}
    return SESSIONS[uid]


# ══════════════════════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════════════════════

def main_kb(auto_active=False):
    auto_label = "🤖 AUTO SIGNAL ✅ ON" if auto_active else "🤖 AUTO SIGNAL ⭕ OFF"
    return ReplyKeyboardMarkup([
        [KeyboardButton("🔴 HIGH RISK ($1-$10)"),  KeyboardButton("🟡 MEDIUM RISK ($11-$100)")],
        [KeyboardButton("🟢 LOW RISK ($100+)"),     KeyboardButton("📊 Top Pairs Live")],
        [KeyboardButton(auto_label),                KeyboardButton("📈 Analisis Pasar")],
        [KeyboardButton("⚙️ Auto Signal Settings"), KeyboardButton("❓ Bantuan")],
    ], resize_keyboard=True,
       input_field_placeholder="Pilih mode atau ketik pertanyaan...")

def pairs_kb(pair_list: list, page=0, per=9):
    start = page*per; chunk = pair_list[start:start+per]
    rows, row = [], []
    for p in chunk:
        row.append(InlineKeyboardButton(p, callback_data=f"pair_{p}"))
        if len(row) == 3: rows.append(row); row = []
    if row: rows.append(row)
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"page_{page-1}"))
    if start+per < len(pair_list): nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"page_{page+1}"))
    if nav: rows.append(nav)
    rows.append([InlineKeyboardButton("✍️ Ketik Pair Manual", callback_data="pair_custom")])
    return InlineKeyboardMarkup(rows)


# ══════════════════════════════════════════════════════════════
#  HANDLERS
# ══════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    s = sess(u.id)
    s.update({"mode": None, "pair": None, "modal": None, "state": "idle", "history": []})
    auto_on = AUTO_USERS.get(u.id, {}).get("active", False)
    await update.message.reply_text(
        f"🤖 *FUTURES TRADING BOT*\n\n"
        f"Halo *{u.first_name}*!\n\n"
        f"🔴 *HIGH RISK* ($1-$10)\n"
        f"└ SL = Liquidation Price (dipatenkan)\n"
        f"└ Leverage 20x-50x, scalping 1m-5m\n\n"
        f"🟡 *MEDIUM RISK* ($11-$100)\n"
        f"└ Risk 2-5% per trade\n"
        f"└ Leverage 5x-20x, scalping 5m-15m\n\n"
        f"🟢 *LOW RISK* ($100+)\n"
        f"└ Risk max 1-2% per trade\n"
        f"└ Leverage 2x-10x, R:R min 1:3\n\n"
        f"🤖 *AUTO SIGNAL*\n"
        f"└ Scan 24 jam, notif otomatis setup bagus\n\n"
        f"📡 Data  : *Binance Futures API (real-time)*\n"
        f"🧠 AI    : *Groq llama-3.3-70b (GRATIS)*\n\n"
        f"⚠️ Hanya alat bantu analisis, bukan jaminan profit.",
        parse_mode="Markdown", reply_markup=main_kb(auto_on)
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ *BANTUAN*\n\n"
        "1️⃣ Pilih mode sesuai modal\n"
        "2️⃣ Pilih pair dari daftar Binance live\n"
        "3️⃣ Masukkan nominal modal\n"
        "4️⃣ Sinyal otomatis di-generate!\n"
        "5️⃣ Tanya apapun tentang pair tersebut\n\n"
        "*🤖 AUTO SIGNAL 24 JAM:*\n"
        "• Klik tombol 🤖 AUTO SIGNAL ⭕ OFF untuk aktifkan\n"
        "• Bot scan top 10 pairs setiap 15 menit\n"
        "• AI nilai setup 0-10, kirim notif jika ≥ 7/10\n"
        "• Cooldown 60 menit per pair (tidak spam)\n"
        "• Bisa atur interval & sensitivity di Settings\n\n"
        "*Data real-time dari Binance:*\n"
        "• Harga, Volume, Order Book, Funding Rate\n"
        "• RSI, MACD, EMA, Bollinger Bands, S&R\n\n"
        "/start → reset bot",
        parse_mode="Markdown"
    )

async def handle_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    txt = update.message.text.strip()
    s = sess(u.id)

    MODE_BTN = {
        "🔴 HIGH RISK ($1-$10)":    ("high_risk",  "🔴 HIGH RISK",  "$1–$10"),
        "🟡 MEDIUM RISK ($11-$100)":("medium_risk","🟡 MEDIUM RISK","$11–$100"),
        "🟢 LOW RISK ($100+)":      ("low_risk",   "🟢 LOW RISK",  "$100+"),
    }
    if txt in MODE_BTN:
        key, label, rng = MODE_BTN[txt]
        s.update({"mode": key, "pair": None, "modal": None, "history": [], "state": "selecting_pair"})
        sl_note = "\n⚠️ *SL = LIQUIDATION PRICE (dipatenkan)*" if key == "high_risk" else ""
        wait = await update.message.reply_text("⏳ Mengambil top pairs dari Binance...")
        ptxt, plst = await top_pairs_msg(20)
        s["pairs"] = plst
        await wait.delete()
        await update.message.reply_text(
            f"*{label}* dipilih — Modal {rng}{sl_note}\n\nPilih pair:",
            parse_mode="Markdown", reply_markup=pairs_kb(plst)
        )
        return

    if txt == "📊 Top Pairs Live":
        wait = await update.message.reply_text("⏳ Mengambil data Binance...")
        ptxt, _ = await top_pairs_msg(20)
        await wait.delete()
        await update.message.reply_text(ptxt, parse_mode="Markdown")
        return

    if txt == "📈 Analisis Pasar":
        wait = await update.message.reply_text("🔍 Menganalisis pasar crypto futures...")
        resp = await gen_general(s.get("mode"),
            "Analisis kondisi pasar crypto futures saat ini secara mendalam. "
            "Apakah bullish, bearish, atau sideways? Pair apa paling menarik untuk scalping? "
            "Berikan tips trading konkret untuk kondisi pasar ini.", [])
        await wait.delete()
        await update.message.reply_text(f"📈 *ANALISIS PASAR*\n\n{resp}", parse_mode="Markdown")
        return

    if txt == "❓ Bantuan":
        await cmd_help(update, ctx); return

    # ── AUTO SIGNAL TOGGLE ───────────────────────────────────
    if txt in ("🤖 AUTO SIGNAL ⭕ OFF", "🤖 AUTO SIGNAL ✅ ON"):
        auto_info = AUTO_USERS.get(u.id, {})

        if txt == "🤖 AUTO SIGNAL ⭕ OFF":
            # Aktifkan — cek apakah sudah punya mode & modal
            mode  = s.get("mode")
            modal = s.get("modal")
            if not mode or not modal:
                await update.message.reply_text(
                    "⚠️ *Pilih mode & set modal dulu sebelum aktifkan Auto Signal!*\n\n"
                    "Caranya:\n1. Pilih mode (High/Medium/Low Risk)\n"
                    "2. Pilih pair\n3. Masukkan modal\n"
                    "4. Setelah sinyal pertama keluar → Auto Signal bisa diaktifkan",
                    parse_mode="Markdown", reply_markup=main_kb(False))
                return

            # Batalkan task lama kalau ada
            old_task = auto_info.get("task")
            if old_task and not old_task.done():
                old_task.cancel()

            # Buat task baru
            task = asyncio.create_task(auto_signal_loop(u.id, ctx.application))
            AUTO_USERS[u.id] = {
                "mode": mode, "modal": modal,
                "active": True, "last_sent": {}, "task": task
            }
            mode_label = {"high_risk":"🔴 HIGH RISK","medium_risk":"🟡 MEDIUM RISK","low_risk":"🟢 LOW RISK"}.get(mode, mode)
            await update.message.reply_text(
                f"✅ *AUTO SIGNAL AKTIF!*\n\n"
                f"🤖 Mode     : {mode_label}\n"
                f"💰 Modal    : ${modal}\n"
                f"🔍 Scan     : Setiap {SCAN_INTERVAL_MIN} menit\n"
                f"⭐ Min Score: {MIN_SCORE}/10\n"
                f"⏱ Cooldown : {COOLDOWN_MIN} menit/pair\n\n"
                f"Bot akan scan top {SCAN_TOP_N} pairs Binance otomatis.\n"
                f"Notif masuk kalau ada setup score ≥ {MIN_SCORE}/10! 🚨",
                parse_mode="Markdown", reply_markup=main_kb(True))

        else:  # Nonaktifkan
            task = auto_info.get("task")
            if task and not task.done(): task.cancel()
            if u.id in AUTO_USERS: AUTO_USERS[u.id]["active"] = False
            await update.message.reply_text(
                "⭕ *AUTO SIGNAL DINONAKTIFKAN*\n\nBot berhenti scan otomatis.",
                parse_mode="Markdown", reply_markup=main_kb(False))
        return

    # ── AUTO SIGNAL SETTINGS ─────────────────────────────────
    if txt == "⚙️ Auto Signal Settings":
        auto_info = AUTO_USERS.get(u.id, {})
        status = "✅ AKTIF" if auto_info.get("active") else "⭕ NONAKTIF"
        mode   = auto_info.get("mode", s.get("mode", "-"))
        modal  = auto_info.get("modal", s.get("modal", "-"))
        mode_label = {"high_risk":"🔴 HIGH RISK","medium_risk":"🟡 MEDIUM RISK","low_risk":"🟢 LOW RISK"}.get(mode, str(mode))
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏱ Scan tiap 5 menit",  callback_data="auto_interval_5"),
             InlineKeyboardButton("⏱ Scan tiap 15 menit", callback_data="auto_interval_15")],
            [InlineKeyboardButton("⏱ Scan tiap 30 menit", callback_data="auto_interval_30"),
             InlineKeyboardButton("⏱ Scan tiap 60 menit", callback_data="auto_interval_60")],
            [InlineKeyboardButton("⭐ Min Score: 6/10", callback_data="auto_score_6"),
             InlineKeyboardButton("⭐ Min Score: 7/10", callback_data="auto_score_7"),
             InlineKeyboardButton("⭐ Min Score: 8/10", callback_data="auto_score_8")],
            [InlineKeyboardButton("🔍 Scan Top 5 Pairs",  callback_data="auto_topn_5"),
             InlineKeyboardButton("🔍 Scan Top 10 Pairs", callback_data="auto_topn_10"),
             InlineKeyboardButton("🔍 Scan Top 20 Pairs", callback_data="auto_topn_20")],
        ])
        await update.message.reply_text(
            f"⚙️ *AUTO SIGNAL SETTINGS*\n\n"
            f"Status      : {status}\n"
            f"Mode        : {mode_label}\n"
            f"Modal       : ${modal}\n"
            f"Interval    : {SCAN_INTERVAL_MIN} menit\n"
            f"Min Score   : {MIN_SCORE}/10\n"
            f"Scan Pairs  : Top {SCAN_TOP_N}\n"
            f"Cooldown    : {COOLDOWN_MIN} menit\n\n"
            f"Pilih pengaturan:",
            parse_mode="Markdown", reply_markup=kb)
        return

    # State: custom pair
    if s["state"] == "custom_pair":
        pair = txt.upper().replace("/","").replace("-","")
        if not pair.endswith("USDT"): pair += "USDT"
        s["pair"] = pair; s["state"] = "asking_modal"
        rng = {"high_risk":"$1–$10","medium_risk":"$11–$100","low_risk":"$100+"}.get(s["mode"],"")
        await update.message.reply_text(
            f"✅ Pair: *{pair}*\n\nMasukkan modal ({rng}):\nContoh: `50`",
            parse_mode="Markdown")
        return

    # State: asking modal
    if s["state"] == "asking_modal":
        try:
            modal = float(txt.replace("$","").replace(",",""))
        except ValueError:
            await update.message.reply_text("❌ Masukkan angka saja. Contoh: `50`", parse_mode="Markdown")
            return
        mode = s["mode"]
        ok = (mode=="high_risk" and 1<=modal<=10) or \
             (mode=="medium_risk" and 11<=modal<=100) or \
             (mode=="low_risk" and modal>100)
        if not ok:
            rng = {"high_risk":"$1–$10","medium_risk":"$11–$100","low_risk":"di atas $100"}[mode]
            await update.message.reply_text(
                f"⚠️ Mode ini untuk modal *{rng}*. Masukkan ulang:",
                parse_mode="Markdown"); return
        s["modal"] = modal; s["state"] = "chatting"; s["history"] = []
        ml = {"high_risk":"🔴 HIGH RISK","medium_risk":"🟡 MEDIUM RISK","low_risk":"🟢 LOW RISK"}[mode]
        await update.message.reply_text(
            f"✅ *Setup siap!*\n"
            f"Mode: {ml} | Pair: `{s['pair']}` | Modal: `${modal}`\n\n"
            f"⏳ Mengambil data live & generating sinyal...",
            parse_mode="Markdown")
        try:
            sig = await gen_signal(mode, s["pair"], modal,
                f"Berikan sinyal trading futures {s['pair']} lengkap berdasarkan semua data live.",
                s["history"])
            await update.message.reply_text(sig, parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
        return

    # State: chatting
    if s["state"] == "chatting" and s["mode"] and s["modal"]:
        wait = await update.message.reply_text("🤔 Menganalisis data live...")
        try:
            resp = await gen_signal(s["mode"], s["pair"], s["modal"], txt, s["history"])
            if len(s["history"]) > 20: s["history"] = s["history"][-20:]
            await wait.delete()
            await update.message.reply_text(resp, parse_mode="Markdown")
        except Exception as e:
            await wait.delete()
            await update.message.reply_text(f"❌ Error: {e}\n\n/start untuk reset.")
        return

    # Default
    resp = await gen_general(s.get("mode"), txt, [])
    await update.message.reply_text(resp + "\n\n💡 Pilih mode dari keyboard di bawah untuk mulai!")

async def handle_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global SCAN_INTERVAL_MIN, MIN_SCORE, SCAN_TOP_N

    q = update.callback_query; await q.answer()
    data = q.data; s = sess(q.from_user.id)

    if data.startswith("page_"):
        await q.edit_message_reply_markup(reply_markup=pairs_kb(s["pairs"], int(data[5:]))); return

    if data == "pair_custom":
        s["state"] = "custom_pair"
        await q.edit_message_text("✍️ Ketik nama pair:\nContoh: `XRPUSDT` atau `SOLUSDT`", parse_mode="Markdown"); return

    if data.startswith("pair_"):
        pair = data[5:]; s["pair"] = pair; s["state"] = "asking_modal"
        rng = {"high_risk":"$1–$10","medium_risk":"$11–$100","low_risk":"di atas $100"}.get(s["mode"],"")
        await q.edit_message_text(
            f"✅ Pair: *{pair}*\n\nMasukkan modal ({rng}):\nContoh: `50`",
            parse_mode="Markdown")
        return

    # ── Auto Signal Settings callbacks ───────────────────────
    if data.startswith("auto_interval_"):
        SCAN_INTERVAL_MIN = int(data.split("_")[-1])
        await q.edit_message_text(
            f"✅ Interval scan diubah ke *{SCAN_INTERVAL_MIN} menit*\n\n"
            f"Berlaku mulai siklus berikutnya.",
            parse_mode="Markdown"); return

    if data.startswith("auto_score_"):
        MIN_SCORE = int(data.split("_")[-1])
        await q.edit_message_text(
            f"✅ Min score diubah ke *{MIN_SCORE}/10*\n\n"
            f"Makin tinggi = makin selektif, notif lebih jarang tapi lebih berkualitas.",
            parse_mode="Markdown"); return

    if data.startswith("auto_topn_"):
        SCAN_TOP_N = int(data.split("_")[-1])
        await q.edit_message_text(
            f"✅ Jumlah pair yang di-scan diubah ke *Top {SCAN_TOP_N}*",
            parse_mode="Markdown"); return


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token: raise RuntimeError("❌ Set TELEGRAM_BOT_TOKEN di .env")
    if not os.getenv("GROQ_API_KEY"): raise RuntimeError("❌ Set GROQ_API_KEY di .env")

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("🤖  Futures Trading Bot — GROQ EDITION")
    print(f"🧠  AI Model : {MODEL}")
    print("📡  Data     : Binance Futures API (real-time)")
    print("💰  Biaya AI : GRATIS ✅")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(CallbackQueryHandler(handle_cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
