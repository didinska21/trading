#!/usr/bin/env python3
"""
🤖 Telegram Futures Trading Bot v4
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AI       : Groq llama-3.3-70b-versatile (GRATIS)
Exchange : Binance | Bybit | OKX | Gate.io | MEXC | Bitget | KuCoin
Mode     : High Risk | Medium Risk | Low Risk
Extra    : Auto Signal 24 jam

SETUP:
  1. https://console.groq.com → daftar → buat API key
  2. https://t.me/BotFather → /newbot → copy token
  3. Isi .env → jalankan: python trading_bot_v4.py
"""

import os, asyncio, logging, aiohttp, json
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
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════
MODEL = "llama-3.3-70b-versatile"

# ── Groq Key Rotator ─────────────────────────────────────────
# Dukung sampai 10 key: GROQ_API_KEY_1 … GROQ_API_KEY_10
# Juga baca GROQ_API_KEY sebagai fallback key tunggal
_groq_keys: list[str] = []
for i in range(1, 11):
    k = os.getenv(f"GROQ_API_KEY_{i}")
    if k: _groq_keys.append(k)
if not _groq_keys:
    single = os.getenv("GROQ_API_KEY")
    if single: _groq_keys.append(single)

if not _groq_keys:
    raise RuntimeError("❌ Tidak ada GROQ_API_KEY ditemukan di .env!")

_groq_clients = [Groq(api_key=k) for k in _groq_keys]
_current_key_idx = 0

def _get_groq() -> Groq:
    return _groq_clients[_current_key_idx]

def _rotate_key(reason: str = ""):
    global _current_key_idx
    prev = _current_key_idx
    _current_key_idx = (_current_key_idx + 1) % len(_groq_clients)
    logger.warning(f"[KEY ROTATE] key #{prev+1} → #{_current_key_idx+1} | alasan: {reason}")

def _call_groq(messages, max_tokens=2000, temperature=0.7):
    """Panggil Groq API dengan auto-rotate jika 429."""
    import groq as groq_lib
    tried = 0
    total = len(_groq_clients)
    while tried < total:
        client = _get_groq()
        try:
            resp = client.chat.completions.create(
                model=MODEL, messages=messages,
                max_tokens=max_tokens, temperature=temperature,
            )
            return resp.choices[0].message.content
        except groq_lib.RateLimitError as e:
            tried += 1
            _rotate_key(f"429 rate limit — {str(e)[:60]}")
            if tried >= total:
                raise Exception(f"Semua {total} API key Groq kena rate limit! Coba lagi nanti.")
        except Exception as e:
            raise

SCAN_INTERVAL_MIN = 15
SCAN_TOP_N        = 10
MIN_SCORE         = 7
COOLDOWN_MIN      = 60

AUTO_USERS: dict[int, dict] = {}
SESSIONS:   dict[int, dict] = {}

# ══════════════════════════════════════════════════════════════
#  EXCHANGE REGISTRY — semua public API, no key needed
# ══════════════════════════════════════════════════════════════
EXCHANGES = {
    "binance": {"name": "Binance",  "emoji": "🟡", "base": "https://fapi.binance.com"},
    "bybit":   {"name": "Bybit",    "emoji": "🟠", "base": "https://api.bybit.com"},
    "okx":     {"name": "OKX",      "emoji": "🔵", "base": "https://www.okx.com"},
    "gateio":  {"name": "Gate.io",  "emoji": "🟢", "base": "https://api.gateio.ws"},
    "mexc":    {"name": "MEXC",     "emoji": "🔴", "base": "https://contract.mexc.com"},
    "bitget":  {"name": "Bitget",   "emoji": "⚫", "base": "https://api.bitget.com"},
    "kucoin":  {"name": "KuCoin",   "emoji": "🟤", "base": "https://api-futures.kucoin.com"},
}

# ══════════════════════════════════════════════════════════════
#  EXCHANGE API ADAPTERS
# ══════════════════════════════════════════════════════════════

async def _get(session, url, params=None):
    async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
        if r.status != 200:
            raise Exception(f"HTTP {r.status}: {await r.text()}")
        return await r.json()

# ── BINANCE ──────────────────────────────────────────────────
async def binance_top_pairs(sess, limit=20):
    data = await _get(sess, "https://fapi.binance.com/fapi/v1/ticker/24hr")
    pairs = [p for p in data if p["symbol"].endswith("USDT")]
    return sorted(pairs, key=lambda x: float(x["quoteVolume"]), reverse=True)[:limit]

async def binance_market(sess, symbol, tf1, tf2):
    results = await asyncio.gather(
        _get(sess, "https://fapi.binance.com/fapi/v1/ticker/24hr", {"symbol": symbol}),
        _get(sess, "https://fapi.binance.com/fapi/v1/depth", {"symbol": symbol, "limit": 5}),
        _get(sess, "https://fapi.binance.com/fapi/v1/premiumIndex", {"symbol": symbol}),
        _get(sess, "https://fapi.binance.com/fapi/v1/klines", {"symbol": symbol, "interval": tf1, "limit": 100}),
        _get(sess, "https://fapi.binance.com/fapi/v1/klines", {"symbol": symbol, "interval": tf2, "limit": 60}),
        return_exceptions=True
    )
    tick, ob, fund, kl1, kl2 = results

    def parse_klines(raw):
        if isinstance(raw, Exception) or not raw: return None
        return {
            "c": [float(k[4]) for k in raw], "h": [float(k[2]) for k in raw],
            "l": [float(k[3]) for k in raw], "v": [float(k[5]) for k in raw],
        }

    price  = float(tick["lastPrice"]) if not isinstance(tick, Exception) else 0
    change = float(tick["priceChangePercent"]) if not isinstance(tick, Exception) else 0
    vol24  = float(tick["quoteVolume"])/1e6 if not isinstance(tick, Exception) else 0

    bids = ob.get("bids",[]) if not isinstance(ob, Exception) else []
    asks = ob.get("asks",[]) if not isinstance(ob, Exception) else []
    tbv  = sum(float(b[1]) for b in bids[:5]) if bids else 0
    tav  = sum(float(a[1]) for a in asks[:5]) if asks else 0

    fr = float(fund.get("lastFundingRate",0))*100 if not isinstance(fund, Exception) else 0

    return {
        "price": price, "change": change, "vol24": vol24,
        "bid_vol": tbv, "ask_vol": tav,
        "funding": fr,
        "kl1": parse_klines(kl1), "kl2": parse_klines(kl2),
        "tf1": tf1, "tf2": tf2,
    }

async def binance_symbol_fmt(symbol): return symbol  # BTCUSDT

# ── BYBIT ────────────────────────────────────────────────────
async def bybit_top_pairs(sess, limit=20):
    data = await _get(sess, "https://api.bybit.com/v5/market/tickers", {"category": "linear"})
    pairs = [p for p in data["result"]["list"] if p["symbol"].endswith("USDT")]
    return sorted(pairs, key=lambda x: float(x.get("turnover24h",0)), reverse=True)[:limit]

async def bybit_market(sess, symbol, tf1, tf2):
    tf_map = {"1m":"1","5m":"5","15m":"15","1h":"60"}
    results = await asyncio.gather(
        _get(sess, "https://api.bybit.com/v5/market/tickers", {"category":"linear","symbol":symbol}),
        _get(sess, "https://api.bybit.com/v5/market/orderbook", {"category":"linear","symbol":symbol,"limit":5}),
        _get(sess, "https://api.bybit.com/v5/market/kline", {"category":"linear","symbol":symbol,"interval":tf_map.get(tf1,"5"),"limit":100}),
        _get(sess, "https://api.bybit.com/v5/market/kline", {"category":"linear","symbol":symbol,"interval":tf_map.get(tf2,"15"),"limit":60}),
        return_exceptions=True
    )
    tick, ob, kl1, kl2 = results

    def parse_klines(raw):
        if isinstance(raw, Exception) or not raw: return None
        try:
            lst = raw["result"]["list"][::-1]  # Bybit returns newest first
            return {
                "c": [float(k[4]) for k in lst], "h": [float(k[2]) for k in lst],
                "l": [float(k[3]) for k in lst], "v": [float(k[5]) for k in lst],
            }
        except: return None

    t = tick["result"]["list"][0] if not isinstance(tick, Exception) and tick["result"]["list"] else {}
    price  = float(t.get("lastPrice", 0))
    change = float(t.get("price24hPcnt", 0))*100
    vol24  = float(t.get("turnover24h", 0))/1e6
    fr     = float(t.get("fundingRate", 0))*100

    bids = ob["result"]["b"] if not isinstance(ob, Exception) else []
    asks = ob["result"]["a"] if not isinstance(ob, Exception) else []
    tbv  = sum(float(b[1]) for b in bids) if bids else 0
    tav  = sum(float(a[1]) for a in asks) if asks else 0

    return {
        "price": price, "change": change, "vol24": vol24,
        "bid_vol": tbv, "ask_vol": tav, "funding": fr,
        "kl1": parse_klines(kl1), "kl2": parse_klines(kl2),
        "tf1": tf1, "tf2": tf2,
    }

# ── OKX ──────────────────────────────────────────────────────
async def okx_top_pairs(sess, limit=20):
    data = await _get(sess, "https://www.okx.com/api/v5/market/tickers", {"instType":"SWAP"})
    pairs = [p for p in data["data"] if p["instId"].endswith("USDT-SWAP")]
    return sorted(pairs, key=lambda x: float(x.get("volCcy24h",0)), reverse=True)[:limit]

async def okx_market(sess, symbol, tf1, tf2):
    inst = symbol.replace("USDT","") + "-USDT-SWAP"
    tf_map = {"1m":"1m","5m":"5m","15m":"15m","1h":"1H"}
    results = await asyncio.gather(
        _get(sess, "https://www.okx.com/api/v5/market/ticker", {"instId":inst}),
        _get(sess, "https://www.okx.com/api/v5/market/books", {"instId":inst,"sz":"5"}),
        _get(sess, "https://www.okx.com/api/v5/public/funding-rate", {"instId":inst}),
        _get(sess, "https://www.okx.com/api/v5/market/candles", {"instId":inst,"bar":tf_map.get(tf1,"5m"),"limit":"100"}),
        _get(sess, "https://www.okx.com/api/v5/market/candles", {"instId":inst,"bar":tf_map.get(tf2,"15m"),"limit":"60"}),
        return_exceptions=True
    )
    tick, ob, fund, kl1, kl2 = results

    def parse_klines(raw):
        if isinstance(raw, Exception) or not raw: return None
        try:
            lst = raw["data"][::-1]
            return {
                "c": [float(k[4]) for k in lst], "h": [float(k[2]) for k in lst],
                "l": [float(k[3]) for k in lst], "v": [float(k[5]) for k in lst],
            }
        except: return None

    t = tick["data"][0] if not isinstance(tick, Exception) and tick.get("data") else {}
    price  = float(t.get("last", 0))
    change = float(t.get("sodUtc8","0") or 0)
    vol24  = float(t.get("volCcy24h", 0))/1e6
    fr     = float(fund["data"][0].get("fundingRate",0))*100 if not isinstance(fund,Exception) and fund.get("data") else 0

    bids = ob["data"][0]["bids"] if not isinstance(ob,Exception) and ob.get("data") else []
    asks = ob["data"][0]["asks"] if not isinstance(ob,Exception) and ob.get("data") else []
    tbv  = sum(float(b[1]) for b in bids) if bids else 0
    tav  = sum(float(a[1]) for a in asks) if asks else 0

    return {
        "price": price, "change": change, "vol24": vol24,
        "bid_vol": tbv, "ask_vol": tav, "funding": fr,
        "kl1": parse_klines(kl1), "kl2": parse_klines(kl2),
        "tf1": tf1, "tf2": tf2,
    }

# ── GATE.IO ──────────────────────────────────────────────────
async def gateio_top_pairs(sess, limit=20):
    data = await _get(sess, "https://api.gateio.ws/api/v4/futures/usdt/tickers")
    return sorted(data, key=lambda x: float(x.get("volume_24h_quote",0)), reverse=True)[:limit]

async def gateio_market(sess, symbol, tf1, tf2):
    contract = symbol if symbol.endswith("_USDT") else symbol.replace("USDT","_USDT")
    tf_map = {"1m":"1m","5m":"5m","15m":"15m","1h":"1h"}
    results = await asyncio.gather(
        _get(sess, f"https://api.gateio.ws/api/v4/futures/usdt/tickers", {"contract":contract}),
        _get(sess, f"https://api.gateio.ws/api/v4/futures/usdt/order_book", {"contract":contract,"limit":5}),
        _get(sess, f"https://api.gateio.ws/api/v4/futures/usdt/candlesticks", {"contract":contract,"interval":tf_map.get(tf1,"5m"),"limit":100}),
        _get(sess, f"https://api.gateio.ws/api/v4/futures/usdt/candlesticks", {"contract":contract,"interval":tf_map.get(tf2,"15m"),"limit":60}),
        return_exceptions=True
    )
    tick, ob, kl1, kl2 = results

    def parse_klines(raw):
        if isinstance(raw, Exception) or not raw: return None
        try:
            return {
                "c": [float(k["c"]) for k in raw], "h": [float(k["h"]) for k in raw],
                "l": [float(k["l"]) for k in raw], "v": [float(k["v"]) for k in raw],
            }
        except: return None

    t = tick[0] if not isinstance(tick,Exception) and tick else {}
    price  = float(t.get("last", 0))
    change = float(t.get("change_percentage", 0))
    vol24  = float(t.get("volume_24h_quote", 0))/1e6
    fr     = float(t.get("funding_rate", 0))*100

    bids = ob.get("bids",[]) if not isinstance(ob,Exception) else []
    asks = ob.get("asks",[]) if not isinstance(ob,Exception) else []
    tbv  = sum(float(b["s"]) for b in bids) if bids else 0
    tav  = sum(float(a["s"]) for a in asks) if asks else 0

    return {
        "price": price, "change": change, "vol24": vol24,
        "bid_vol": tbv, "ask_vol": tav, "funding": fr,
        "kl1": parse_klines(kl1), "kl2": parse_klines(kl2),
        "tf1": tf1, "tf2": tf2,
    }

# ── MEXC ─────────────────────────────────────────────────────
async def mexc_top_pairs(sess, limit=20):
    data = await _get(sess, "https://contract.mexc.com/api/v1/contract/ticker")
    pairs = [p for p in data["data"] if p["symbol"].endswith("_USDT")]
    return sorted(pairs, key=lambda x: float(x.get("amount24",0)), reverse=True)[:limit]

async def mexc_market(sess, symbol, tf1, tf2):
    sym = symbol if "_" in symbol else symbol.replace("USDT","_USDT")
    tf_map = {"1m":"Min1","5m":"Min5","15m":"Min15","1h":"Hour1"}
    results = await asyncio.gather(
        _get(sess, f"https://contract.mexc.com/api/v1/contract/ticker", {"symbol":sym}),
        _get(sess, f"https://contract.mexc.com/api/v1/contract/depth", {"symbol":sym,"limit":5}),
        _get(sess, f"https://contract.mexc.com/api/v1/contract/kline/{sym}", {"interval":tf_map.get(tf1,"Min5"),"limit":100}),
        _get(sess, f"https://contract.mexc.com/api/v1/contract/kline/{sym}", {"interval":tf_map.get(tf2,"Min15"),"limit":60}),
        return_exceptions=True
    )
    tick, ob, kl1, kl2 = results

    def parse_klines(raw):
        if isinstance(raw, Exception) or not raw: return None
        try:
            d = raw["data"]
            closes = d.get("close",[]) or d.get("closePrices",[])
            highs  = d.get("high",[])  or d.get("highPrices",[])
            lows   = d.get("low",[])   or d.get("lowPrices",[])
            vols   = d.get("vol",[])   or d.get("vol",[])
            return {"c":[float(x) for x in closes],"h":[float(x) for x in highs],
                    "l":[float(x) for x in lows],"v":[float(x) for x in vols]}
        except: return None

    t = tick["data"] if not isinstance(tick,Exception) and tick.get("data") else {}
    price  = float(t.get("lastPrice",0))
    change = float(t.get("riseFallRate",0))*100
    vol24  = float(t.get("amount24",0))/1e6
    fr     = float(t.get("fundingRate",0))*100

    bids = ob["data"].get("bids",[]) if not isinstance(ob,Exception) and ob.get("data") else []
    asks = ob["data"].get("asks",[]) if not isinstance(ob,Exception) and ob.get("data") else []
    tbv  = sum(float(b[1]) for b in bids) if bids else 0
    tav  = sum(float(a[1]) for a in asks) if asks else 0

    return {
        "price": price, "change": change, "vol24": vol24,
        "bid_vol": tbv, "ask_vol": tav, "funding": fr,
        "kl1": parse_klines(kl1), "kl2": parse_klines(kl2),
        "tf1": tf1, "tf2": tf2,
    }

# ── BITGET ───────────────────────────────────────────────────
async def bitget_top_pairs(sess, limit=20):
    data = await _get(sess, "https://api.bitget.com/api/v2/mix/market/tickers", {"productType":"USDT-FUTURES"})
    pairs = data.get("data",[])
    return sorted(pairs, key=lambda x: float(x.get("usdtVolume",0)), reverse=True)[:limit]

async def bitget_market(sess, symbol, tf1, tf2):
    sym = symbol if symbol.endswith("USDT") else symbol+"USDT"
    tf_map = {"1m":"1m","5m":"5m","15m":"15m","1h":"1H"}
    results = await asyncio.gather(
        _get(sess, "https://api.bitget.com/api/v2/mix/market/ticker", {"symbol":sym,"productType":"USDT-FUTURES"}),
        _get(sess, "https://api.bitget.com/api/v2/mix/market/depth", {"symbol":sym,"productType":"USDT-FUTURES","limit":"5"}),
        _get(sess, "https://api.bitget.com/api/v2/mix/market/candles", {"symbol":sym,"productType":"USDT-FUTURES","granularity":tf_map.get(tf1,"5m"),"limit":"100"}),
        _get(sess, "https://api.bitget.com/api/v2/mix/market/candles", {"symbol":sym,"productType":"USDT-FUTURES","granularity":tf_map.get(tf2,"15m"),"limit":"60"}),
        return_exceptions=True
    )
    tick, ob, kl1, kl2 = results

    def parse_klines(raw):
        if isinstance(raw, Exception) or not raw: return None
        try:
            lst = raw["data"]
            return {
                "c": [float(k[4]) for k in lst], "h": [float(k[2]) for k in lst],
                "l": [float(k[3]) for k in lst], "v": [float(k[5]) for k in lst],
            }
        except: return None

    t = tick["data"][0] if not isinstance(tick,Exception) and tick.get("data") else {}
    price  = float(t.get("lastPr",0))
    change = float(t.get("change24h",0))*100
    vol24  = float(t.get("usdtVolume",0))/1e6
    fr     = float(t.get("fundingRate",0))*100

    bids = ob["data"].get("bids",[]) if not isinstance(ob,Exception) and ob.get("data") else []
    asks = ob["data"].get("asks",[]) if not isinstance(ob,Exception) and ob.get("data") else []
    tbv  = sum(float(b[0])*float(b[1]) for b in bids) if bids else 0
    tav  = sum(float(a[0])*float(a[1]) for a in asks) if asks else 0

    return {
        "price": price, "change": change, "vol24": vol24,
        "bid_vol": tbv, "ask_vol": tav, "funding": fr,
        "kl1": parse_klines(kl1), "kl2": parse_klines(kl2),
        "tf1": tf1, "tf2": tf2,
    }

# ── KUCOIN ───────────────────────────────────────────────────
async def kucoin_top_pairs(sess, limit=20):
    data = await _get(sess, "https://api-futures.kucoin.com/api/v1/contracts/active")
    pairs = [p for p in data["data"] if p["symbol"].endswith("USDTM")]
    return sorted(pairs, key=lambda x: float(x.get("turnoverOf24h",0)), reverse=True)[:limit]

async def kucoin_market(sess, symbol, tf1, tf2):
    sym = symbol.replace("USDT","USDTM") if not symbol.endswith("USDTM") else symbol
    tf_map = {"1m":1,"5m":5,"15m":15,"1h":60}
    results = await asyncio.gather(
        _get(sess, f"https://api-futures.kucoin.com/api/v1/ticker", {"symbol":sym}),
        _get(sess, f"https://api-futures.kucoin.com/api/v1/level2/depth5", {"symbol":sym}),
        _get(sess, f"https://api-futures.kucoin.com/api/v1/kline/query", {"symbol":sym,"granularity":tf_map.get(tf1,5)}),
        _get(sess, f"https://api-futures.kucoin.com/api/v1/kline/query", {"symbol":sym,"granularity":tf_map.get(tf2,15)}),
        return_exceptions=True
    )
    tick, ob, kl1, kl2 = results

    def parse_klines(raw):
        if isinstance(raw, Exception) or not raw: return None
        try:
            lst = raw["data"]
            return {
                "c": [float(k[4]) for k in lst], "h": [float(k[2]) for k in lst],
                "l": [float(k[3]) for k in lst], "v": [float(k[5]) for k in lst],
            }
        except: return None

    t = tick["data"] if not isinstance(tick,Exception) and tick.get("data") else {}
    price  = float(t.get("price",0))
    change = 0
    vol24  = float(t.get("turnoverOf24h",0))/1e6
    fr     = 0

    bids = ob["data"].get("bids",[]) if not isinstance(ob,Exception) and ob.get("data") else []
    asks = ob["data"].get("asks",[]) if not isinstance(ob,Exception) and ob.get("data") else []
    tbv  = sum(float(b[1]) for b in bids) if bids else 0
    tav  = sum(float(a[1]) for a in asks) if asks else 0

    return {
        "price": price, "change": change, "vol24": vol24,
        "bid_vol": tbv, "ask_vol": tav, "funding": fr,
        "kl1": parse_klines(kl1), "kl2": parse_klines(kl2),
        "tf1": tf1, "tf2": tf2,
    }

# ── DISPATCHER ───────────────────────────────────────────────
TOP_PAIRS_FN = {
    "binance": binance_top_pairs,
    "bybit":   bybit_top_pairs,
    "okx":     okx_top_pairs,
    "gateio":  gateio_top_pairs,
    "mexc":    mexc_top_pairs,
    "bitget":  bitget_top_pairs,
    "kucoin":  kucoin_top_pairs,
}
MARKET_FN = {
    "binance": binance_market,
    "bybit":   bybit_market,
    "okx":     okx_market,
    "gateio":  gateio_market,
    "mexc":    mexc_market,
    "bitget":  bitget_market,
    "kucoin":  kucoin_market,
}

TF_MAP = {
    "high_risk":   ("1m",  "5m"),
    "medium_risk": ("5m",  "15m"),
    "low_risk":    ("15m", "1h"),
}

# ══════════════════════════════════════════════════════════════
#  TECHNICAL INDICATORS
# ══════════════════════════════════════════════════════════════
class TA:
    @staticmethod
    def rsi(c, p=14):
        if len(c) < p+1: return 50.0
        gains  = [max(c[i]-c[i-1],0) for i in range(-p,0)]
        losses = [max(c[i-1]-c[i],0) for i in range(-p,0)]
        ag = sum(gains)/p; al = sum(losses)/p or 0.001
        return round(100 - 100/(1+ag/al), 2)

    @staticmethod
    def ema(c, p):
        if len(c) < p: return c[-1]
        k = 2/(p+1); e = sum(c[:p])/p
        for x in c[p:]: e = x*k + e*(1-k)
        return round(e, 8)

    @staticmethod
    def macd(c):
        e12 = TA.ema(c,12); e26 = TA.ema(c,26)
        m = e12-e26; s = m*0.9
        return round(m,8), round(s,8), round(m-s,8)

    @staticmethod
    def bb(c, p=20):
        if len(c) < p: return c[-1], c[-1], c[-1]
        sl = c[-p:]; mid = sum(sl)/p
        std = (sum((x-mid)**2 for x in sl)/p)**0.5
        return round(mid+2*std,8), round(mid,8), round(mid-2*std,8)

    @staticmethod
    def sr(h, l, n=20):
        return round(max(h[-n:]),8), round(min(l[-n:]),8)

    @staticmethod
    def avg_vol(v, p=20):
        return sum(v[-p:])/p if v else 1

# ══════════════════════════════════════════════════════════════
#  MARKET DATA BUILDER → teks untuk AI
# ══════════════════════════════════════════════════════════════
async def collect(exchange: str, symbol: str, mode: str) -> str:
    tf1, tf2 = TF_MAP.get(mode, ("5m","15m"))
    exname = EXCHANGES[exchange]["name"]

    async with aiohttp.ClientSession() as sess:
        try:
            d = await MARKET_FN[exchange](sess, symbol, tf1, tf2)
        except Exception as e:
            return f"[ERROR ambil data {exname}: {e}]"

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L = [
        f"═══ DATA LIVE {symbol} — {exname} ═══",
        f"🕐 {now}\n",
        f"Harga Terkini : ${d['price']:,.8g}",
        f"Perubahan 24H : {d['change']:+.2f}%",
        f"Volume 24H    : ${d['vol24']:.2f}M",
        f"Funding Rate  : {d['funding']:.4f}% ({'Longs bayar Shorts' if d['funding']>0 else 'Shorts bayar Longs'})",
        f"Order Book    : Bid {d['bid_vol']:.2f} vs Ask {d['ask_vol']:.2f} → {'BELI DOMINAN 🟢' if d['bid_vol']>d['ask_vol'] else 'JUAL DOMINAN 🔴'}\n",
    ]

    for tf_label, kl in [(tf1, d["kl1"]), (tf2, d["kl2"])]:
        if not kl:
            L.append(f"[{tf_label}] Data tidak tersedia\n"); continue
        c, h, l, v = kl["c"], kl["h"], kl["l"], kl["v"]
        r14 = TA.rsi(c,14); r7 = TA.rsi(c,7)
        m, s, hist = TA.macd(c)
        bbu, bbm, bbl = TA.bb(c)
        res, sup = TA.sr(h, l)
        e9  = TA.ema(c,9); e21 = TA.ema(c,21); e50 = TA.ema(c,50)
        avgv = TA.avg_vol(v); vratio = v[-1]/avgv if avgv else 1
        candles = " ".join(["🟢" if c[-(j+1)]>c[-(j+2)] else "🔴" for j in range(5)][::-1])

        rsi_lbl = "OVERSOLD 🟢" if r14<30 else "OVERBOUGHT 🔴" if r14>70 else "NETRAL ⚪"
        mac_lbl = "BULLISH 🟢" if hist>0 else "BEARISH 🔴"
        ema_lbl = "BULLISH KUAT 🟢" if e9>e21>e50 else "BEARISH KUAT 🔴" if e9<e21<e50 else "MIXED ⚪"
        vol_lbl = f"SPIKE 🔥 {vratio:.1f}x" if vratio>1.5 else f"Normal {vratio:.1f}x"

        L += [
            f"── {tf_label.upper()} ────────────────",
            f"Harga         : ${c[-1]:,.8g}",
            f"RSI(14/7)     : {r14} / {r7} → {rsi_lbl}",
            f"MACD Hist     : {hist:.8g} → {mac_lbl}",
            f"EMA 9/21/50   : {e9:.6g} / {e21:.6g} / {e50:.6g} → {ema_lbl}",
            f"BB U/M/L      : {bbu:.6g} / {bbm:.6g} / {bbl:.6g}",
            f"Resistance    : ${res:,.8g}",
            f"Support       : ${sup:,.8g}",
            f"Volume        : {vol_lbl} avg",
            f"5 Candle      : {candles}\n",
        ]
    return "\n".join(L)

# ══════════════════════════════════════════════════════════════
#  SYSTEM PROMPTS — UPGRADED
# ══════════════════════════════════════════════════════════════
PROMPTS = {

"high_risk": """
Kamu adalah seorang trader futures profesional kelas dunia dengan 15 tahun pengalaman.
Kamu mengelola dana prop firm senilai $10.000.000. Setiap sinyal yang kamu keluarkan adalah NYATA.
Reputasi, karir, dan seluruh track record hidupmu bergantung pada akurasi analisismu.
Kamu tidak pernah asal-asalan. Kamu tidak pernah tebak-tebakan. Kamu hanya entry ketika data KONFIRMASI.

IDENTITASMU:
- Win rate kamu di atas 70% karena kamu DISIPLIN pada data
- Kamu pernah kehilangan segalanya akibat 1 sinyal ceroboh — dan kamu tidak akan ulangi itu
- Kamu hanya kasih sinyal ketika MINIMAL 4 dari 5 indikator konfirmasi
- Jika data tidak jelas → kamu dengan tegas bilang WAIT, bukan paksa entry

KONSEP SL — PAHAMI INI:
- SL = harga stop order yang dipasang user di exchange
- LONG: SL di BAWAH entry, di bawah support kuat terdekat
- SHORT: SL di ATAS entry, di atas resistance kuat terdekat
- SL harus logis: jika harga sampai sana, berarti setup sudah terbukti salah
- JANGAN pasang SL terlalu ketat (kena noise) atau terlalu jauh (rugi terlalu besar)

PROSES WAJIB SEBELUM OUTPUT — LAKUKAN DALAM PIKIRANMU:
Langkah 1 — Tentukan bias arah:
  → RSI(14) di mana? Di bawah 30 = oversold bias LONG | Di atas 70 = overbought bias SHORT
  → RSI(7) konfirmasi? Jika RSI(14) oversold tapi RSI(7) masih turun = belum saatnya entry
  → EMA alignment: 9>21>50 = bullish | 9<21<50 = bearish | campur = hindari

Langkah 2 — Konfirmasi momentum:
  → MACD histogram: positif & membesar = bullish kuat | negatif & membesar = bearish kuat
  → Jika histogram berlawanan dengan bias = SINYAL LEMAH, pertimbangkan WAIT

Langkah 3 — Validasi entry zone:
  → Apakah harga dekat Support (untuk LONG) atau Resistance (untuk SHORT)?
  → Ideal: entry dalam radius 0.5% dari level S&R
  → Jika harga di tengah range = JANGAN entry, tunggu rejection di S&R

Langkah 4 — Filter volume:
  → Volume candle terakhir vs rata-rata 20 candle
  → Jika volume < 0.8x rata-rata = pasar sepi, SINYAL TIDAK VALID
  → Jika volume > 1.5x = konfirmasi kuat

Langkah 5 — Cek funding & order book:
  → Funding rate > +0.1% = pasar terlalu long → SHORT lebih aman
  → Funding rate < -0.1% = pasar terlalu short → LONG lebih aman
  → Order book: bid dominan = tekanan beli | ask dominan = tekanan jual

Langkah 6 — Self-check WAJIB sebelum output:
  □ RSI konfirmasi arah? YA / TIDAK
  □ MACD histogram searah? YA / TIDAK
  □ EMA alignment valid? YA / TIDAK
  □ Volume cukup (>0.8x)? YA / TIDAK
  □ Harga di dekat S&R? YA / TIDAK
  → Jika kurang dari 3 YA → OUTPUT: WAIT, jelaskan alasan
  → Jika 3-4 YA → Sinyal MODERAT, beri catatan
  → Jika 5 YA → Sinyal KUAT, full confidence

ATURAN FORMAT OUTPUT — TIDAK BOLEH DILANGGAR:
- Baris Entry, TP, SL: HANYA harga dan persentase. TITIK.
- Contoh BENAR : ✅ TP1 : $0.00650 (+3.2%)
- Contoh SALAH : ✅ TP1 : $0.00650 (+3.2% karena resistance di sana)  ← DILARANG
- Analisis: maksimal 4 kalimat, padat, gunakan angka dari data

OUTPUT FORMAT — IKUTI PERSIS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔴 SINYAL HIGH RISK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 Pair      : [PAIR]
📍 Harga     : $[harga sekarang]
⭐ Kekuatan  : [KUAT 5/5 / MODERAT 3/5 / WAIT]
🎯 Arah      : LONG 🟢 / SHORT 🔴
⚡ Entry     : $[harga]
✅ TP1       : $[harga] (+X%)
✅ TP2       : $[harga] (+X%)
🛑 SL        : $[harga] (-X%)
📊 RSI(14/7) : [nilai] / [nilai] → [OVERSOLD/OVERBOUGHT/NETRAL]
📈 MACD Hist : [nilai] → [BULLISH/BEARISH]
📉 EMA       : [BULLISH KUAT/BEARISH KUAT/MIXED]
🎯 Support   : $[nilai]
🎯 Resist    : $[nilai]
📦 Volume    : [X]x rata-rata → [SPIKE/NORMAL/SEPI]
💸 Funding   : [nilai]% → [interpretasi singkat]
✅ Konfirmasi: [indikator mana saja yang konfirmasi]
⚠️ Risiko    : [1 kalimat risiko utama setup ini]
📝 Analisis  :
[Maksimal 4 kalimat. Sebutkan angka spesifik. Jelaskan mengapa entry ini valid atau tidak.]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""",

"medium_risk": """
Kamu adalah fund manager futures profesional dengan track record 12 tahun.
Kamu mengelola portofolio $5.000.000 milik ratusan klien yang mempercayai kamu.
Filosofimu: "Preserve capital first, profit second." Satu keputusan buruk bisa hancurkan kepercayaan ratusan orang.
Kamu TIDAK PERNAH entry tanpa konfirmasi multi-indikator. Kamu selalu tunggu setup yang sempurna.

IDENTITASMU:
- Kamu sabar. Kamu tidak FOMO. Kamu tidak kejar harga.
- Win rate 65%+ dengan R:R rata-rata 1:2.5
- Kamu lebih baik miss peluang daripada masuk di setup yang meragukan
- Entry zone = harga rentang, bukan harga pasti — karena market tidak presisi

KONSEP SL:
- SL = stop order yang dipasang di exchange, di bawah/atas S&R terdekat yang kuat
- LONG: SL beberapa poin di bawah support → jika support jebol, setup salah
- SHORT: SL beberapa poin di atas resistance → jika resistance jebol, setup salah
- R:R MINIMUM 1:2. Jika tidak bisa capai R:R ini → JANGAN entry

PROSES WAJIB SEBELUM OUTPUT:
Langkah 1 — Identifikasi trend utama:
  → EMA 9/21/50: semua searah = trend kuat | campur = sideways/hindari
  → Timeframe lebih besar (tf2) harus konfirmasi arah tf1

Langkah 2 — Cari zona entry optimal:
  → Fibonacci retrace dari high-low data: 0.382 / 0.5 / 0.618 adalah zona emas
  → Entry di dekat S&R + Fibonacci confluence = setup terbaik
  → Berikan RANGE entry, bukan harga pasti

Langkah 3 — Konfirmasi momentum:
  → RSI: zona 30-40 = area akumulasi (LONG) | 60-70 = area distribusi (SHORT)
  → RSI 40-60 = sideways → HINDARI entry baru
  → MACD: histogram membesar searah = momentum valid

Langkah 4 — Volume & market structure:
  → Candle sebelum entry: apakah ada rejection candle? (pin bar, engulfing)
  → Volume saat rejection harus di atas rata-rata

Langkah 5 — Hitung R:R sebelum commit:
  → R:R = (TP1 - Entry) / (Entry - SL) untuk LONG
  → Jika R:R < 1:2 → geser TP atau batalkan

Langkah 6 — Self-check WAJIB:
  □ Trend tf2 searah dengan arah entry? YA / TIDAK
  □ RSI di zona yang tepat (bukan 40-60)? YA / TIDAK
  □ MACD histogram konfirmasi? YA / TIDAK
  □ Volume di atas rata-rata? YA / TIDAK
  □ Ada level S&R / Fibonacci di zona entry? YA / TIDAK
  □ R:R minimal 1:2 tercapai? YA / TIDAK
  → Kurang dari 4 YA → WAIT
  → 4-5 YA → Sinyal dengan catatan
  → 6 YA → Full confidence

ATURAN FORMAT:
- TP/SL baris: HANYA harga + persentase. Tidak ada teks tambahan apapun.
- Analisis: maksimal 5 kalimat, padat, gunakan angka nyata dari data

OUTPUT FORMAT — IKUTI PERSIS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🟡 SINYAL MEDIUM RISK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 Pair      : [PAIR]
📍 Harga     : $[harga sekarang]
⭐ Kekuatan  : [KUAT 6/6 / MODERAT 4/6 / WAIT]
🎯 Arah      : LONG 🟢 / SHORT 🔴
⚡ Entry     : $[bawah] – $[atas]
✅ TP1       : $[harga] (+X%) → tutup 50%
✅ TP2       : $[harga] (+X%) → tutup 30%
✅ TP3       : $[harga] (+X%) → tutup 20%
🛑 SL        : $[harga] (-X%)
📊 R:R       : 1:[angka]
📊 RSI(14/7) : [nilai] / [nilai] → [label]
📈 MACD Hist : [nilai] → [BULLISH/BEARISH]
📉 EMA       : [BULLISH KUAT/BEARISH KUAT/MIXED]
🎯 Support   : $[nilai] | Resist: $[nilai]
📦 Volume    : [X]x rata-rata → [label]
💸 Funding   : [nilai]%
🔍 Tunggu    : [kondisi konfirmasi sebelum entry]
✅ Konfirmasi: [checklist yang terpenuhi, misal: RSI✓ MACD✓ EMA✓]
📝 Analisis  :
[Maksimal 5 kalimat. Angka spesifik. Jelaskan setup, zona entry, dan manajemen posisi.]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""",

"low_risk": """
Kamu adalah chief risk officer sekaligus senior trader di hedge fund dengan AUM $50.000.000.
Tugasmu bukan hanya cari profit — tugasmu adalah MELINDUNGI MODAL KLIEN di atas segalanya.
Kamu sudah melewati crash 2018, 2020, 2022. Kamu tahu: pasar bisa irasional lebih lama dari kamu bisa solvent.
Filosofimu: "Jika ragu, tidak usah masuk. Peluang selalu datang lagi. Modal yang habis tidak kembali."

IDENTITASMU:
- Win rate 60%+ dengan R:R rata-rata 1:3.5
- Kamu hanya entry 2-3x seminggu, bukan setiap hari
- Setup yang "lumayan" tidak cukup. Kamu hanya mau setup yang "sempurna"
- Kamu selalu siapkan skenario terbaik DAN terburuk sebelum entry

KONSEP SL:
- SL = stop order di S&R MAJOR yang sudah diuji minimal 3 kali di timeframe 1H
- Level S&R yang baru diuji 1-2 kali = TIDAK CUKUP KUAT untuk dijadikan SL
- LONG: SL di bawah support major → jika jebol, trend sudah berubah
- SHORT: SL di atas resistance major → jika jebol, bullish reversal sudah terjadi
- R:R MINIMUM 1:3. Di bawah itu → tolak setup, cari yang lebih baik

PROSES WAJIB SEBELUM OUTPUT:
Langkah 1 — Analisis struktur market tf2 (timeframe besar) DULU:
  → Apakah tf2 dalam uptrend, downtrend, atau ranging?
  → Jangan pernah melawan trend tf2 kecuali ada divergence yang sangat kuat
  → Identifikasi S&R MAJOR di tf2 — ini yang paling penting

Langkah 2 — Cari confluence zone:
  → S&R major + EMA 50 di area yang sama = zona sangat kuat
  → S&R major + Bollinger Band + RSI divergence = setup premium
  → Semakin banyak confluence → semakin kuat setup

Langkah 3 — Tunggu konfirmasi candle:
  → Pin bar di S&R = kemungkinan reversal
  → Engulfing candle = konfirmasi kuat
  → Doji di S&R = ketidakpastian, tunggu candle berikutnya
  → Entry SETELAH candle konfirmasi close, bukan sebelum

Langkah 4 — RSI divergence check:
  → Bullish divergence: harga lower low tapi RSI higher low = reversal potensi kuat
  → Bearish divergence: harga higher high tapi RSI lower high = reversal potensi kuat
  → Tidak ada divergence = setup biasa saja, butuh lebih banyak konfirmasi lain

Langkah 5 — Hitung R:R ketat:
  → R:R = jarak ke TP1 / jarak ke SL
  → Minimum 1:3 untuk entry
  → Jika tidak tercapai → cari pair lain atau tunggu harga lebih baik

Langkah 6 — Skenario terburuk:
  → Jika SL kena, apakah ada alasan fundamental yang mungkin menyebabkan itu?
  → Apakah ada event makro (CPI, FOMC) dalam waktu dekat yang bisa guncang pasar?

Langkah 7 — Self-check WAJIB (paling ketat):
  □ Trend tf2 jelas dan tidak berlawanan? YA / TIDAK
  □ S&R major teridentifikasi dengan jelas? YA / TIDAK
  □ Ada confluence (minimal 2 level bertemu)? YA / TIDAK
  □ RSI tidak di zona netral (40-60)? YA / TIDAK
  □ MACD searah? YA / TIDAK
  □ Volume konfirmasi? YA / TIDAK
  □ Ada candle konfirmasi di S&R? YA / TIDAK
  □ R:R minimal 1:3 tercapai? YA / TIDAK
  → Kurang dari 5 YA → WAJIB output WAIT, jelaskan apa yang kurang
  → 5-6 YA → Sinyal dengan catatan kehati-hatian
  → 7-8 YA → Setup premium, full confidence

ATURAN FORMAT:
- TP/SL baris: HANYA harga + persentase. Tidak ada teks apapun setelahnya.
- Jika WAIT: jelaskan dengan spesifik kondisi apa yang harus terpenuhi dulu
- Analisis: maksimal 6 kalimat, mendalam, gunakan angka nyata

OUTPUT FORMAT — IKUTI PERSIS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🟢 SINYAL LOW RISK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 Pair      : [PAIR]
📍 Harga     : $[harga sekarang]
⭐ Kekuatan  : [PREMIUM 8/8 / KUAT 6/8 / MODERAT 5/8 / ⏳ WAIT]
🎯 Arah      : LONG 🟢 / SHORT 🔴 / ⏳ WAIT
⚡ Entry     : $[harga] ← setelah candle konfirmasi close
✅ TP1       : $[harga] (+X%) → tutup 40%, geser SL ke breakeven
✅ TP2       : $[harga] (+X%) → tutup 40%, aktifkan trailing stop
✅ TP3       : $[harga] (+X%) → tutup 20% sisa
🛑 SL        : $[harga] (-X%)
📊 R:R       : 1:[angka] (minimum 1:3)
📊 RSI(14/7) : [nilai] / [nilai] → [divergence/label]
📈 MACD Hist : [nilai] → [BULLISH/BEARISH]
📉 EMA       : [BULLISH KUAT/BEARISH KUAT/MIXED]
🎯 S&R Major : Support $[nilai] | Resist $[nilai]
📦 Volume    : [X]x rata-rata → [label]
💸 Funding   : [nilai]%
🕯 Candle    : [pattern yang terdeteksi atau 'Tunggu konfirmasi']
✅ Konfirmasi: [checklist terpenuhi: RSI✓ MACD✓ EMA✓ SR✓ dll]
✅ Entry sah jika : [kondisi spesifik]
❌ Setup batal jika: [kondisi invalidasi]
📝 Analisis  :
[Maksimal 6 kalimat. Jelaskan struktur market, confluence zone, skenario terbaik & terburuk.]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""",
}

GENERAL_PROMPT = """
Kamu adalah trader dan analis crypto futures profesional yang ramah.
Jawab pertanyaan seputar futures trading, teknikal analisis, manajemen risiko dalam bahasa Indonesia.
Berikan jawaban yang praktis, konkret, dan berdasarkan pengalaman nyata trading.
"""

AUTO_SCAN_PROMPT = """
Kamu adalah AI scanner sinyal trading futures profesional.
Tugasmu: nilai kualitas setup dari data yang diberikan secara objektif dan cepat.

KRITERIA PENILAIAN (total 10 poin):
+2 → RSI: di bawah 35 (oversold) atau di atas 65 (overbought) — bukan zona netral
+2 → MACD histogram: positif membesar (bullish) atau negatif membesar (bearish)
+2 → EMA alignment: 9>21>50 bullish KUAT atau 9<21<50 bearish KUAT (bukan mixed)
+2 → Volume: candle terakhir >1.5x rata-rata 20 candle (spike konfirmasi)
+2 → Posisi harga: dalam radius 1% dari level Support atau Resistance terdekat

ATURAN:
- Nilai OBJEKTIF berdasarkan data, bukan asumsi
- Jika data tidak lengkap untuk satu kriteria → beri 0 untuk kriteria itu
- Score 7+ = layak untuk sinyal lengkap
- Score di bawah 7 = skip, tidak worth

Balas HANYA dengan JSON ini, tidak ada teks lain sama sekali:
{"score": <angka 0-10>, "arah": "LONG" atau "SHORT", "alasan": "<1 kalimat max 15 kata>"}
"""

# ══════════════════════════════════════════════════════════════
#  TOP PAIRS HELPER
# ══════════════════════════════════════════════════════════════
async def get_top_pairs(exchange: str, limit=20) -> tuple[str, list]:
    exinfo = EXCHANGES[exchange]
    async with aiohttp.ClientSession() as sess:
        try:
            raw = await TOP_PAIRS_FN[exchange](sess, limit)
        except Exception as e:
            return f"❌ Gagal ambil pairs dari {exinfo['name']}: {e}", []

    lines = [f"🔥 *TOP PAIRS — {exinfo['emoji']} {exinfo['name']} (Live)*\n"]
    symbols = []
    for i, p in enumerate(raw[:limit], 1):
        # Normalisasi field per exchange
        if exchange == "binance":
            sym = p["symbol"]; pr = float(p["lastPrice"]); chg = float(p["priceChangePercent"]); vol = float(p["quoteVolume"])/1e6
        elif exchange == "bybit":
            sym = p["symbol"]; pr = float(p.get("lastPrice",0)); chg = float(p.get("price24hPcnt",0))*100; vol = float(p.get("turnover24h",0))/1e6
        elif exchange == "okx":
            sym = p["instId"].replace("-SWAP","").replace("-USDT",""+"USDT"); pr = float(p.get("last",0)); chg = 0; vol = float(p.get("volCcy24h",0))/1e6
        elif exchange == "gateio":
            sym = p["contract"]; pr = float(p.get("last",0)); chg = float(p.get("change_percentage",0)); vol = float(p.get("volume_24h_quote",0))/1e6
        elif exchange == "mexc":
            sym = p["symbol"]; pr = float(p.get("lastPrice",0)); chg = float(p.get("riseFallRate",0))*100; vol = float(p.get("amount24",0))/1e6
        elif exchange == "bitget":
            sym = p.get("symbol",""); pr = float(p.get("lastPr",0)); chg = float(p.get("change24h",0))*100; vol = float(p.get("usdtVolume",0))/1e6
        elif exchange == "kucoin":
            sym = p["symbol"]; pr = float(p.get("lastTradePrice",0)); chg = 0; vol = float(p.get("turnoverOf24h",0))/1e6
        else:
            continue

        em = "🟢" if chg >= 0 else "🔴"
        lines.append(f"{i:>2}. `{sym:<14}` {em} {chg:+.2f}% | ${pr:,.6g} | Vol: ${vol:.0f}M")
        symbols.append(sym)

    return "\n".join(lines), symbols

# ══════════════════════════════════════════════════════════════
#  AI FUNCTIONS
# ══════════════════════════════════════════════════════════════
async def gen_signal(exchange: str, mode: str, symbol: str, modal: float, user_msg: str, history: list) -> str:
    try:
        mdata = await collect(exchange, symbol, mode)
    except Exception as e:
        mdata = f"[ERROR: {e}]"

    exname = EXCHANGES[exchange]["name"]
    prompt = (
        f"DATA LIVE DARI {exname.upper()}:\n{mdata}\n\n"
        f"USER INFO:\n• Exchange: {exname}\n• Pair: {symbol}\n• Modal: ${modal}\n• Mode: {mode.replace('_',' ').upper()}\n\n"
        f"PERMINTAAN: {user_msg}\n\n"
        f"Gunakan harga dan angka NYATA dari data di atas. Hitung TP/SL dari harga terkini."
    )
    msgs = [{"role":"system","content":PROMPTS[mode]}] + history[-6:] + [{"role":"user","content":prompt}]
    answer = await asyncio.to_thread(_call_groq, msgs, 2000, 0.7)
    history.append({"role":"user","content":f"[{symbol}] {user_msg}"})
    history.append({"role":"assistant","content":answer})
    return answer

async def gen_general(mode: Optional[str], msg: str, history: list) -> str:
    sys = PROMPTS.get(mode, GENERAL_PROMPT)
    msgs = [{"role":"system","content":sys}] + history[-6:] + [{"role":"user","content":msg}]
    return await asyncio.to_thread(_call_groq, msgs, 1200, 0.7)

async def scan_score(exchange: str, symbol: str, mode: str) -> dict:
    try:
        mdata = await collect(exchange, symbol, mode)
        msgs = [
            {"role":"system","content":AUTO_SCAN_PROMPT},
            {"role":"user","content":f"DATA {symbol}:\n{mdata}"},
        ]
        raw = await asyncio.to_thread(_call_groq, msgs, 120, 0.2)
        raw = raw.strip().replace("```json","").replace("```","").strip()
        result = json.loads(raw)
        result["score"] = int(result.get("score",0))
        return result
    except Exception as e:
        return {"score":0,"arah":"NONE","alasan":str(e)}

# ══════════════════════════════════════════════════════════════
#  AUTO SIGNAL LOOP
# ══════════════════════════════════════════════════════════════
async def auto_signal_loop(uid: int, app):
    from datetime import timedelta
    logger.info(f"[AUTO] Start loop uid={uid}")
    while True:
        try:
            info = AUTO_USERS.get(uid)
            if not info or not info.get("active"): break

            exchange = info["exchange"]
            mode     = info["mode"]
            modal    = info["modal"]
            now      = datetime.now(timezone.utc)

            async with aiohttp.ClientSession() as sess:
                try:
                    raw = await TOP_PAIRS_FN[exchange](sess, SCAN_TOP_N)
                    if exchange == "binance":   symbols = [p["symbol"] for p in raw]
                    elif exchange == "bybit":   symbols = [p["symbol"] for p in raw]
                    elif exchange == "okx":     symbols = [p["instId"].replace("-SWAP","").replace("-USDT","USDT") for p in raw]
                    elif exchange == "gateio":  symbols = [p["contract"] for p in raw]
                    elif exchange == "mexc":    symbols = [p["symbol"] for p in raw]
                    elif exchange == "bitget":  symbols = [p.get("symbol","") for p in raw]
                    elif exchange == "kucoin":  symbols = [p["symbol"] for p in raw]
                    else: symbols = []
                except Exception as e:
                    logger.warning(f"[AUTO] Gagal ambil pairs: {e}")
                    await asyncio.sleep(60); continue

            for symbol in symbols:
                last = info.get("last_sent",{}).get(symbol)
                if last and (now-last).total_seconds()/60 < COOLDOWN_MIN:
                    continue
                result = await scan_score(exchange, symbol, mode)
                score  = result.get("score",0)
                arah   = result.get("arah","?")
                alasan = result.get("alasan","")
                logger.info(f"[AUTO] {symbol} score={score}")
                if score >= MIN_SCORE:
                    try:
                        sinyal = await gen_signal(exchange, mode, symbol, modal,
                            f"Berikan sinyal lengkap {symbol} — score setup {score}/10.", [])
                        exinfo = EXCHANGES[exchange]
                        notif = (
                            f"🚨 *AUTO SIGNAL ALERT!*\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"⭐ Score   : *{score}/10*\n"
                            f"📌 Pair    : *{symbol}*\n"
                            f"🏦 Exchange: {exinfo['emoji']} {exinfo['name']}\n"
                            f"🎯 Arah    : *{arah}*\n"
                            f"💡 Alasan  : {alasan}\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━"
                        )
                        await app.bot.send_message(uid, notif, parse_mode="Markdown")
                        await app.bot.send_message(uid, sinyal, parse_mode="Markdown")
                        if "last_sent" not in info: info["last_sent"] = {}
                        info["last_sent"][symbol] = now
                        AUTO_USERS[uid] = info
                    except Exception as e:
                        logger.error(f"[AUTO] Gagal kirim: {e}")
                await asyncio.sleep(3)

            logger.info(f"[AUTO] Selesai, tunggu {SCAN_INTERVAL_MIN} menit")
            await asyncio.sleep(SCAN_INTERVAL_MIN * 60)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[AUTO] Error: {e}")
            await asyncio.sleep(60)

# ══════════════════════════════════════════════════════════════
#  SESSION
# ══════════════════════════════════════════════════════════════
def sess(uid: int) -> dict:
    if uid not in SESSIONS:
        SESSIONS[uid] = {
            "exchange": None, "mode": None, "pair": None,
            "modal": None, "state": "idle", "history": [], "pairs": []
        }
    return SESSIONS[uid]

# ══════════════════════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════════════════════
def exchange_kb():
    """Inline keyboard pilih exchange — tampil setelah /start"""
    rows = []
    items = list(EXCHANGES.items())
    for i in range(0, len(items), 2):
        row = []
        for key, info in items[i:i+2]:
            row.append(InlineKeyboardButton(f"{info['emoji']} {info['name']}", callback_data=f"exch_{key}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)

def main_kb(auto_active=False):
    auto_lbl = "🤖 AUTO SIGNAL ✅ ON" if auto_active else "🤖 AUTO SIGNAL ⭕ OFF"
    return ReplyKeyboardMarkup([
        [KeyboardButton("🔴 HIGH RISK"),  KeyboardButton("🟡 MEDIUM RISK")],
        [KeyboardButton("🟢 LOW RISK"),   KeyboardButton("📊 Top Pairs")],
        [KeyboardButton(auto_lbl),        KeyboardButton("📈 Analisis Pasar")],
        [KeyboardButton("🏦 Ganti Exchange"), KeyboardButton("❓ Bantuan")],
    ], resize_keyboard=True, input_field_placeholder="Pilih mode atau ketik...")

def pairs_kb(pair_list: list, page=0, per=9):
    start = page*per; chunk = pair_list[start:start+per]
    rows, row = [], []
    for p in chunk:
        row.append(InlineKeyboardButton(p, callback_data=f"pair_{p}"))
        if len(row) == 3: rows.append(row); row = []
    if row: rows.append(row)
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"page_{page-1}"))
    if start+per < len(pair_list): nav.append(InlineKeyboardButton("➡️", callback_data=f"page_{page+1}"))
    if nav: rows.append(nav)
    rows.append([InlineKeyboardButton("✍️ Ketik Manual", callback_data="pair_custom")])
    return InlineKeyboardMarkup(rows)

# ══════════════════════════════════════════════════════════════
#  HANDLERS
# ══════════════════════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    s = sess(u.id)
    s.update({"exchange":None,"mode":None,"pair":None,"modal":None,"state":"idle","history":[]})
    await update.message.reply_text(
        f"🤖 *FUTURES TRADING BOT*\n\n"
        f"Halo *{u.first_name}*!\n\n"
        f"Bot ini akan bantu kamu analisis futures trading dengan data live dari exchange pilihanmu.\n\n"
        f"*Fitur:*\n"
        f"• 3 mode trading (High/Medium/Low Risk)\n"
        f"• Data pair & harga real-time\n"
        f"• Sinyal Entry, TP, SL langsung\n"
        f"• Auto Signal scan 24 jam\n\n"
        f"*AI:* Groq llama-3.3-70b (GRATIS)\n\n"
        f"⚠️ Hanya alat bantu analisis, bukan jaminan profit.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"*Pilih exchange untuk mulai:*",
        parse_mode="Markdown", reply_markup=exchange_kb()
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ *BANTUAN*\n\n"
        "*Cara pakai:*\n"
        "1. /start → pilih exchange\n"
        "2. Pilih mode (High/Medium/Low Risk)\n"
        "3. Pilih pair dari daftar live\n"
        "4. Masukkan modal\n"
        "5. Sinyal langsung muncul!\n"
        "6. Tanya apapun tentang pair tersebut\n\n"
        "*Soal SL:*\n"
        "SL yang diberikan adalah HARGA yang kamu pasang di exchange sebagai stop order. "
        "Bukan liquidation otomatis — kamu yang pasang manual di exchange.\n\n"
        "*Auto Signal:*\n"
        "Bot scan pairs otomatis, kirim notif kalau ada setup bagus (score ≥7/10)\n\n"
        "/start → reset & ganti exchange",
        parse_mode="Markdown"
    )

async def handle_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    txt = update.message.text.strip()
    s = sess(u.id)

    # Belum pilih exchange
    if not s["exchange"] and txt not in ("/start", "/help"):
        await update.message.reply_text(
            "⚠️ Pilih exchange dulu!\nKetik /start untuk mulai.",
            parse_mode="Markdown")
        return

    # Mode buttons
    MODE_BTN = {
        "🔴 HIGH RISK":  ("high_risk",  "🔴 HIGH RISK"),
        "🟡 MEDIUM RISK":("medium_risk","🟡 MEDIUM RISK"),
        "🟢 LOW RISK":   ("low_risk",   "🟢 LOW RISK"),
    }
    if txt in MODE_BTN:
        key, label = MODE_BTN[txt]
        s.update({"mode":key,"pair":None,"modal":None,"history":[],"state":"selecting_pair"})
        wait = await update.message.reply_text("⏳ Mengambil pairs live...")
        ptxt, plst = await get_top_pairs(s["exchange"], 20)
        s["pairs"] = plst
        await wait.delete()
        exinfo = EXCHANGES[s["exchange"]]
        await update.message.reply_text(
            f"*{label}* — {exinfo['emoji']} {exinfo['name']}\nPilih pair:",
            parse_mode="Markdown", reply_markup=pairs_kb(plst))
        return

    if txt == "📊 Top Pairs":
        wait = await update.message.reply_text("⏳ Mengambil data...")
        ptxt, _ = await get_top_pairs(s["exchange"], 20)
        await wait.delete()
        await update.message.reply_text(ptxt, parse_mode="Markdown")
        return

    if txt == "🏦 Ganti Exchange":
        s.update({"exchange":None,"mode":None,"pair":None,"modal":None,"state":"idle","history":[]})
        await update.message.reply_text("Pilih exchange:", reply_markup=exchange_kb())
        return

    if txt == "📈 Analisis Pasar":
        wait = await update.message.reply_text("🔍 Menganalisis pasar...")
        resp = await gen_general(s.get("mode"),
            "Analisis kondisi pasar crypto futures saat ini. Bullish atau bearish? "
            "Pair apa yang menarik untuk scalping? Tips trading konkret.", [])
        await wait.delete()
        await update.message.reply_text(f"📈 *ANALISIS PASAR*\n\n{resp}", parse_mode="Markdown")
        return

    if txt == "❓ Bantuan":
        await cmd_help(update, ctx); return

    # AUTO SIGNAL TOGGLE
    if txt in ("🤖 AUTO SIGNAL ⭕ OFF", "🤖 AUTO SIGNAL ✅ ON"):
        auto_info = AUTO_USERS.get(u.id, {})
        if txt == "🤖 AUTO SIGNAL ⭕ OFF":
            mode  = s.get("mode")
            modal = s.get("modal")
            if not mode or not modal:
                await update.message.reply_text(
                    "⚠️ Set mode & modal dulu sebelum aktifkan Auto Signal!\n"
                    "Pilih mode → pilih pair → masukkan modal → setelah sinyal pertama → aktifkan Auto Signal.",
                    reply_markup=main_kb(False)); return
            old = auto_info.get("task")
            if old and not old.done(): old.cancel()
            task = asyncio.create_task(auto_signal_loop(u.id, ctx.application))
            exinfo = EXCHANGES[s["exchange"]]
            AUTO_USERS[u.id] = {
                "exchange":s["exchange"],"mode":mode,"modal":modal,
                "active":True,"last_sent":{},"task":task
            }
            await update.message.reply_text(
                f"✅ *AUTO SIGNAL AKTIF!*\n\n"
                f"🏦 Exchange : {exinfo['emoji']} {exinfo['name']}\n"
                f"📊 Mode     : {mode.replace('_',' ').upper()}\n"
                f"💰 Modal    : ${modal}\n"
                f"🔍 Scan     : Setiap {SCAN_INTERVAL_MIN} menit\n"
                f"⭐ Min Score: {MIN_SCORE}/10\n\n"
                f"Notif otomatis masuk kalau ada setup bagus! 🚨",
                parse_mode="Markdown", reply_markup=main_kb(True))
        else:
            task = auto_info.get("task")
            if task and not task.done(): task.cancel()
            if u.id in AUTO_USERS: AUTO_USERS[u.id]["active"] = False
            await update.message.reply_text("⭕ *AUTO SIGNAL NONAKTIF*", parse_mode="Markdown", reply_markup=main_kb(False))
        return

    # State: custom pair
    if s["state"] == "custom_pair":
        pair = txt.upper().replace("/","").replace("-","").replace(" ","")
        s["pair"] = pair; s["state"] = "asking_modal"
        await update.message.reply_text(f"✅ Pair: *{pair}*\n\nMasukkan modal ($):\nContoh: `5`", parse_mode="Markdown")
        return

    # State: asking modal
    if s["state"] == "asking_modal":
        try:
            modal = float(txt.replace("$","").replace(",",""))
            if modal <= 0: raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Masukkan angka yang valid. Contoh: `5`", parse_mode="Markdown"); return
        s["modal"] = modal; s["state"] = "chatting"; s["history"] = []
        ml = {"high_risk":"🔴 HIGH RISK","medium_risk":"🟡 MEDIUM RISK","low_risk":"🟢 LOW RISK"}[s["mode"]]
        exinfo = EXCHANGES[s["exchange"]]
        await update.message.reply_text(
            f"✅ *Setup siap!*\n"
            f"🏦 {exinfo['emoji']} {exinfo['name']} | {ml}\n"
            f"📌 {s['pair']} | Modal: ${modal}\n\n"
            f"⏳ Mengambil data live...", parse_mode="Markdown")
        try:
            sig = await gen_signal(s["exchange"], s["mode"], s["pair"], modal,
                f"Berikan sinyal trading futures {s['pair']} lengkap.", s["history"])
            await update.message.reply_text(sig, parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
        return

    # State: chatting
    if s["state"] == "chatting":
        wait = await update.message.reply_text("🤔 Menganalisis...")
        try:
            resp = await gen_signal(s["exchange"], s["mode"], s["pair"], s["modal"], txt, s["history"])
            if len(s["history"]) > 20: s["history"] = s["history"][-20:]
            await wait.delete()
            await update.message.reply_text(resp, parse_mode="Markdown")
        except Exception as e:
            await wait.delete()
            await update.message.reply_text(f"❌ Error: {e}\n\n/start untuk reset.")
        return

    # Default
    resp = await gen_general(s.get("mode"), txt, [])
    await update.message.reply_text(resp + "\n\n💡 Pilih mode dari keyboard atau /start untuk mulai.")

async def handle_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global SCAN_INTERVAL_MIN, MIN_SCORE, SCAN_TOP_N
    q = update.callback_query; await q.answer()
    data = q.data; uid = q.from_user.id; s = sess(uid)

    # Pilih exchange
    if data.startswith("exch_"):
        key = data[5:]
        s["exchange"] = key
        s["state"] = "idle"
        exinfo = EXCHANGES[key]
        auto_on = AUTO_USERS.get(uid,{}).get("active", False)
        await q.edit_message_text(
            f"✅ Exchange: *{exinfo['emoji']} {exinfo['name']}*\n\nSilakan pilih mode trading dari keyboard di bawah!",
            parse_mode="Markdown")
        await ctx.bot.send_message(uid, f"Pilih mode trading:", reply_markup=main_kb(auto_on))
        return

    if data.startswith("page_"):
        await q.edit_message_reply_markup(reply_markup=pairs_kb(s["pairs"], int(data[5:]))); return

    if data == "pair_custom":
        s["state"] = "custom_pair"
        await q.edit_message_text("✍️ Ketik nama pair:\nContoh: `BTCUSDT` atau `SOLUSDT`", parse_mode="Markdown"); return

    if data.startswith("pair_"):
        pair = data[5:]; s["pair"] = pair; s["state"] = "asking_modal"
        await q.edit_message_text(f"✅ Pair: *{pair}*\n\nMasukkan modal ($):\nContoh: `5`", parse_mode="Markdown"); return

    # Auto signal settings
    if data.startswith("auto_interval_"):
        SCAN_INTERVAL_MIN = int(data.split("_")[-1])
        await q.edit_message_text(f"✅ Interval scan: *{SCAN_INTERVAL_MIN} menit*", parse_mode="Markdown"); return
    if data.startswith("auto_score_"):
        MIN_SCORE = int(data.split("_")[-1])
        await q.edit_message_text(f"✅ Min score: *{MIN_SCORE}/10*", parse_mode="Markdown"); return
    if data.startswith("auto_topn_"):
        SCAN_TOP_N = int(data.split("_")[-1])
        await q.edit_message_text(f"✅ Scan top *{SCAN_TOP_N}* pairs", parse_mode="Markdown"); return

# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════
def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token: raise RuntimeError("❌ Set TELEGRAM_BOT_TOKEN di .env")
    if not _groq_keys: raise RuntimeError("❌ Tidak ada GROQ_API_KEY ditemukan di .env")

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("🤖  Futures Trading Bot v4")
    print(f"🧠  AI      : {MODEL}")
    print(f"🔑  API Keys: {len(_groq_clients)} key aktif (auto-rotate jika 429)")
    print(f"🏦  Exchange: Binance | Bybit | OKX | Gate.io | MEXC | Bitget | KuCoin")
    print("💰  Biaya AI: GRATIS ✅")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(CallbackQueryHandler(handle_cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
