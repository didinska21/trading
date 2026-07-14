#!/usr/bin/env python3
"""
🤖 Telegram Futures Trading Bot v5 — TESTNET EDITION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AI         : Groq llama-3.3-70b-versatile (GRATIS)
Exchange   : Binance (TESTNET untuk auto-trade) | Bybit | OKX | Gate.io | MEXC | Bitget | KuCoin (signal only)
Mode       : High Risk | Medium Risk | Low Risk
Auto Signal: Scan 24 jam, notif otomatis
Auto Trade : Full-auto execute di Binance Futures TESTNET (uang virtual, tanpa risiko nyata)

SETUP:
  1. https://console.groq.com → daftar → buat API key
  2. https://t.me/BotFather → /newbot → copy token
  3. https://testnet.binancefuture.com → login pakai GitHub → API Key → generate
     (ini API key TESTNET, terpisah dari akun Binance asli, saldo virtual $ dummy)
  4. Isi .env → jalankan: python trading_bot_v5_testnet.py

CATATAN TESTNET:
  Semua order dieksekusi ke Binance Futures Testnet dengan saldo virtual.
  Tidak ada uang nyata yang digunakan. Cocok untuk belajar/testing strategi
  sebelum pindah ke akun live (ganti BINANCE_TESTNET=false + API key asli).
"""

import os, asyncio, logging, aiohttp, json, hmac, hashlib, time
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

# ── TESTNET TOGGLE ─────────────────────────────────────────────
# Default TRUE. Set BINANCE_TESTNET=false di .env kalau nanti sudah
# siap pakai akun live + modal nyata.
USE_TESTNET = os.getenv("BINANCE_TESTNET", "true").strip().lower() != "false"

# ── Whitelist User ────────────────────────────────────────────
ADMIN_USERNAME = "@didinska"

def _load_whitelist() -> set[int]:
    raw = os.getenv("ALLOWED_USER_IDS", "").strip()
    if not raw: return set()
    ids = set()
    for x in raw.split(","):
        x = x.strip()
        if x.isdigit(): ids.add(int(x))
    return ids

ALLOWED_USERS: set[int] = _load_whitelist()

def is_allowed(uid: int) -> bool:
    if not ALLOWED_USERS: return True
    return uid in ALLOWED_USERS

# ── Groq Key Rotator ─────────────────────────────────────────
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
#  BINANCE AUTO TRADING ENGINE (TESTNET-AWARE)
# ══════════════════════════════════════════════════════════════
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")

# Base URL untuk TRADING (order, balance, posisi) — beda testnet vs live
BINANCE_TRADE_BASE = "https://testnet.binancefuture.com" if USE_TESTNET else "https://fapi.binance.com"

# Base URL untuk MARKET DATA (klines, ticker, depth, exchangeInfo)
# Testnet Binance Futures juga punya endpoint market data sendiri.
BINANCE_DATA_BASE = "https://testnet.binancefuture.com" if USE_TESTNET else "https://fapi.binance.com"

# Risk per trade per mode (% dari available balance)
RISK_PCT = {
    "high_risk":   5.0,
    "medium_risk": 3.0,
    "low_risk":    1.0,
}

# Leverage per mode
LEVERAGE_MAP = {
    "high_risk":   20,
    "medium_risk": 10,
    "low_risk":     5,
}

# State posisi aktif: {uid: {symbol, side, entry, qty, tp_order, sl_order, mode, pnl}}
ACTIVE_POSITIONS: dict[int, dict] = {}

def _bnb_sign(params: dict) -> dict:
    params["timestamp"] = int(time.time() * 1000)
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    sig = hmac.new(BINANCE_API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    params["signature"] = sig
    return params

def _bnb_headers() -> dict:
    return {"X-MBX-APIKEY": BINANCE_API_KEY}

async def bnb_get(sess: aiohttp.ClientSession, path: str, params: dict = None) -> dict:
    params = _bnb_sign(params or {})
    async with sess.get(f"{BINANCE_TRADE_BASE}{path}", params=params, headers=_bnb_headers()) as r:
        data = await r.json()
        if isinstance(data, dict) and data.get("code") and data["code"] < 0:
            raise Exception(f"Binance GET error {data['code']}: {data.get('msg')}")
        return data

async def bnb_post(sess: aiohttp.ClientSession, path: str, params: dict) -> dict:
    params = _bnb_sign(params)
    async with sess.post(f"{BINANCE_TRADE_BASE}{path}", params=params, headers=_bnb_headers()) as r:
        data = await r.json()
        if isinstance(data, dict) and data.get("code") and data["code"] < 0:
            raise Exception(f"Binance POST error {data['code']}: {data.get('msg')}")
        return data

async def bnb_delete(sess: aiohttp.ClientSession, path: str, params: dict) -> dict:
    params = _bnb_sign(params)
    async with sess.delete(f"{BINANCE_TRADE_BASE}{path}", params=params, headers=_bnb_headers()) as r:
        data = await r.json()
        return data

async def get_futures_balance(sess: aiohttp.ClientSession) -> float:
    data = await bnb_get(sess, "/fapi/v2/balance")
    for asset in data:
        if asset.get("asset") == "USDT":
            return float(asset.get("availableBalance", 0))
    return 0.0

async def get_symbol_info(sess: aiohttp.ClientSession, symbol: str) -> dict:
    data = await sess.get(f"{BINANCE_DATA_BASE}/fapi/v1/exchangeInfo")
    info = await data.json()
    for s in info.get("symbols", []):
        if s["symbol"] == symbol:
            result = {"symbol": symbol}
            for f in s.get("filters", []):
                if f["filterType"] == "PRICE_FILTER":
                    result["tickSize"] = float(f["tickSize"])
                elif f["filterType"] == "LOT_SIZE":
                    result["stepSize"] = float(f["stepSize"])
                    result["minQty"]   = float(f["minQty"])
            return result
    raise Exception(f"Symbol {symbol} tidak ditemukan di Binance Futures{'  Testnet' if USE_TESTNET else ''}")

def _round_step(value: float, step: float) -> float:
    precision = len(str(step).rstrip("0").split(".")[-1]) if "." in str(step) else 0
    return round(round(value / step) * step, precision)

def _round_tick(value: float, tick: float) -> float:
    precision = len(str(tick).rstrip("0").split(".")[-1]) if "." in str(tick) else 0
    return round(round(value / tick) * tick, precision)

async def set_leverage(sess: aiohttp.ClientSession, symbol: str, leverage: int):
    try:
        await bnb_post(sess, "/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage})
    except Exception as e:
        logger.warning(f"[LEVERAGE] {symbol} leverage {leverage}x: {e}")

async def get_open_position(sess: aiohttp.ClientSession, symbol: str) -> Optional[dict]:
    data = await bnb_get(sess, "/fapi/v2/positionRisk", {"symbol": symbol})
    for pos in data:
        if float(pos.get("positionAmt", 0)) != 0:
            return pos
    return None

async def cancel_all_orders(sess: aiohttp.ClientSession, symbol: str):
    try:
        await bnb_delete(sess, "/fapi/v1/allOpenOrders", {"symbol": symbol})
    except Exception as e:
        logger.warning(f"[CANCEL] {symbol}: {e}")

async def execute_trade(uid: int, symbol: str, side: str, mode: str,
                        tp_pct: float, sl_pct: float, app) -> dict:
    if not BINANCE_API_KEY or not BINANCE_API_SECRET:
        raise Exception("BINANCE_API_KEY / BINANCE_API_SECRET (testnet) belum diset di .env")

    leverage  = LEVERAGE_MAP[mode]
    risk_pct  = RISK_PCT[mode] / 100

    async with aiohttp.ClientSession() as sess:
        balance = await get_futures_balance(sess)
        if balance < 1:
            raise Exception(f"Balance testnet tidak cukup: ${balance:.2f}. Klaim faucet di testnet.binancefuture.com")

        info = await get_symbol_info(sess, symbol)
        tick = info.get("tickSize", 0.0001)
        step = info.get("stepSize", 0.001)

        ticker = await sess.get(f"{BINANCE_DATA_BASE}/fapi/v1/ticker/price?symbol={symbol}")
        price  = float((await ticker.json())["price"])

        risk_usdt  = balance * risk_pct
        notional   = risk_usdt * leverage
        qty        = _round_step(notional / price, step)
        if qty < info.get("minQty", 0.001):
            raise Exception(f"Qty terlalu kecil: {qty} (min {info.get('minQty')}). Tambah balance atau kurangi leverage.")

        await set_leverage(sess, symbol, leverage)

        if side == "BUY":
            tp_price = _round_tick(price * (1 + tp_pct/100), tick)
            sl_price = _round_tick(price * (1 - sl_pct/100), tick)
            tp_side  = "SELL"
            sl_side  = "SELL"
        else:
            tp_price = _round_tick(price * (1 - tp_pct/100), tick)
            sl_price = _round_tick(price * (1 + sl_pct/100), tick)
            tp_side  = "BUY"
            sl_side  = "BUY"

        entry_order = await bnb_post(sess, "/fapi/v1/order", {
            "symbol":   symbol,
            "side":     side,
            "type":     "MARKET",
            "quantity": qty,
        })
        entry_id = entry_order.get("orderId")
        logger.info(f"[TRADE{'(TESTNET)' if USE_TESTNET else ''}] Entry {side} {symbol} qty={qty} price={price} orderId={entry_id}")

        tp_order = await bnb_post(sess, "/fapi/v1/order", {
            "symbol":          symbol,
            "side":            tp_side,
            "type":            "TAKE_PROFIT_MARKET",
            "stopPrice":       tp_price,
            "closePosition":   "true",
            "timeInForce":     "GTE_GTC",
        })

        sl_order = await bnb_post(sess, "/fapi/v1/order", {
            "symbol":          symbol,
            "side":            sl_side,
            "type":            "STOP_MARKET",
            "stopPrice":       sl_price,
            "closePosition":   "true",
            "timeInForce":     "GTE_GTC",
        })

        result = {
            "symbol":    symbol,
            "side":      side,
            "entry":     price,
            "qty":       qty,
            "leverage":  leverage,
            "tp_price":  tp_price,
            "sl_price":  sl_price,
            "tp_pct":    tp_pct,
            "sl_pct":    sl_pct,
            "mode":      mode,
            "balance":   balance,
            "risk_usdt": risk_usdt,
            "notional":  notional,
            "entry_id":  entry_id,
            "tp_id":     tp_order.get("orderId"),
            "sl_id":     sl_order.get("orderId"),
            "open_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        }
        ACTIVE_POSITIONS[uid] = result
        return result

async def close_position(uid: int, symbol: str, reason: str = "manual") -> dict:
    async with aiohttp.ClientSession() as sess:
        pos = await get_open_position(sess, symbol)
        if not pos:
            ACTIVE_POSITIONS.pop(uid, None)
            return {"status": "no_position"}

        amt  = float(pos["positionAmt"])
        side = "SELL" if amt > 0 else "BUY"
        qty  = abs(amt)

        await cancel_all_orders(sess, symbol)
        close_order = await bnb_post(sess, "/fapi/v1/order", {
            "symbol":           symbol,
            "side":             side,
            "type":             "MARKET",
            "quantity":         qty,
            "reduceOnly":       "true",
        })

        entry_info = ACTIVE_POSITIONS.pop(uid, {})
        pnl = float(pos.get("unRealizedProfit", 0))
        logger.info(f"[CLOSE] {symbol} reason={reason} pnl={pnl}")
        return {
            "status":  "closed",
            "symbol":  symbol,
            "pnl":     pnl,
            "reason":  reason,
            "entry":   entry_info.get("entry", 0),
            "close_id": close_order.get("orderId"),
        }

async def monitor_positions(uid: int, app):
    logger.info(f"[MONITOR] Start uid={uid}")
    while True:
        try:
            pos_info = ACTIVE_POSITIONS.get(uid)
            if not pos_info:
                logger.info(f"[MONITOR] uid={uid} tidak ada posisi aktif, stop monitor")
                break

            symbol = pos_info["symbol"]
            side   = pos_info["side"]

            async with aiohttp.ClientSession() as sess:
                pos = await get_open_position(sess, symbol)

            if not pos:
                entry   = pos_info.get("entry", 0)
                tp_p    = pos_info.get("tp_price", 0)
                sl_p    = pos_info.get("sl_price", 0)
                ACTIVE_POSITIONS.pop(uid, None)

                async with aiohttp.ClientSession() as sess:
                    ticker = await sess.get(f"{BINANCE_DATA_BASE}/fapi/v1/ticker/price?symbol={symbol}")
                    cur    = float((await ticker.json())["price"])

                if side == "BUY":
                    hit_tp = cur >= tp_p
                else:
                    hit_tp = cur <= tp_p

                status  = "✅ TAKE PROFIT" if hit_tp else "🛑 STOP LOSS"
                notif = (
                    f"{'✅' if hit_tp else '🛑'} *POSISI DITUTUP (TESTNET)*\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"📌 Pair   : {symbol}\n"
                    f"🎯 Status : {status}\n"
                    f"⚡ Entry  : ${entry}\n"
                    f"📍 Close  : ${cur}\n"
                    f"🏹 Lev    : {pos_info.get('leverage')}x\n"
                    f"💰 Notional: ${pos_info.get('notional',0):.2f}"
                )
                try:
                    await app.bot.send_message(uid, notif, parse_mode="Markdown")
                except: pass
                break

            await asyncio.sleep(60)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[MONITOR] Error: {e}")
            await asyncio.sleep(60)

# ══════════════════════════════════════════════════════════════
#  EXCHANGE REGISTRY — semua public API, no key needed (SIGNAL ONLY)
#  Catatan: hanya Binance yang punya jalur auto-trade (testnet).
#  Exchange lain di sini murni untuk data & sinyal AI, tidak eksekusi order.
# ══════════════════════════════════════════════════════════════
EXCHANGES = {
    "binance": {"name": "Binance" + (" (Testnet)" if USE_TESTNET else ""), "emoji": "🟡", "base": BINANCE_DATA_BASE},
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

# ── BINANCE (market data — pakai BINANCE_DATA_BASE, testnet-aware) ──
async def binance_top_pairs(sess, limit=20):
    data = await _get(sess, f"{BINANCE_DATA_BASE}/fapi/v1/ticker/24hr")
    pairs = [p for p in data if p["symbol"].endswith("USDT")]
    return sorted(pairs, key=lambda x: float(x["quoteVolume"]), reverse=True)[:limit]

async def binance_market(sess, symbol, tf1, tf2):
    results = await asyncio.gather(
        _get(sess, f"{BINANCE_DATA_BASE}/fapi/v1/ticker/24hr", {"symbol": symbol}),
        _get(sess, f"{BINANCE_DATA_BASE}/fapi/v1/depth", {"symbol": symbol, "limit": 5}),
        _get(sess, f"{BINANCE_DATA_BASE}/fapi/v1/premiumIndex", {"symbol": symbol}),
        _get(sess, f"{BINANCE_DATA_BASE}/fapi/v1/klines", {"symbol": symbol, "interval": tf1, "limit": 100}),
        _get(sess, f"{BINANCE_DATA_BASE}/fapi/v1/klines", {"symbol": symbol, "interval": tf2, "limit": 60}),
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
            lst = raw["result"]["list"][::-1]
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

        price_now = c[-1]
        if price_now >= 1000:  dec = 2
        elif price_now >= 1:   dec = 4
        elif price_now >= 0.01: dec = 6
        elif price_now >= 0.0001: dec = 8
        else:                  dec = 10

        def fmt(val):
            if val == 0: return "0"
            abs_v = abs(val)
            if abs_v >= 1:       return f"{val:.4f}"
            elif abs_v >= 0.01:  return f"{val:.6f}"
            elif abs_v >= 0.0001: return f"{val:.8f}"
            else:                return f"{val:.10f}"

        rsi_lbl = "OVERSOLD 🟢" if r14<30 else "OVERBOUGHT 🔴" if r14>70 else "NETRAL ⚪"
        mac_lbl = "BULLISH 🟢" if hist>0 else "BEARISH 🔴"
        ema_lbl = "BULLISH KUAT 🟢" if e9>e21>e50 else "BEARISH KUAT 🔴" if e9<e21<e50 else "MIXED ⚪"
        vol_lbl = f"SPIKE 🔥 {vratio:.1f}x avg" if vratio>1.5 else f"Normal {vratio:.1f}x avg" if vratio>=0.8 else f"SEPI {vratio:.1f}x avg"

        L += [
            f"── {tf_label.upper()} ────────────────",
            f"Harga         : ${price_now:.{dec}f}",
            f"RSI(14/7)     : {r14} / {r7} → {rsi_lbl}",
            f"MACD Hist     : {fmt(hist)} → {mac_lbl}",
            f"EMA 9/21/50   : {fmt(e9)} / {fmt(e21)} / {fmt(e50)} → {ema_lbl}",
            f"BB U/M/L      : {fmt(bbu)} / {fmt(bbm)} / {fmt(bbl)}",
            f"Resistance    : ${res:.{dec}f}",
            f"Support       : ${sup:.{dec}f}",
            f"Volume        : {vol_lbl}",
            f"5 Candle      : {candles}\n",
        ]
    return "\n".join(L)

# ══════════════════════════════════════════════════════════════
#  SYSTEM PROMPTS
# ══════════════════════════════════════════════════════════════
PROMPTS = {
"high_risk": "Kamu adalah seorang trader futures profesional. Kamu hanya kasih sinyal ketika data konfirmasi kuat (RSI, MACD, EMA, volume, S&R). Jawab dalam Bahasa Indonesia, format harga desimal biasa (bukan scientific notation), sertakan Entry/TP/SL jelas atau WAIT jika setup belum valid.",
"medium_risk": "Kamu adalah fund manager futures yang disiplin dan sabar, prioritas preservasi modal, R:R minimum 1:2. Jawab dalam Bahasa Indonesia, format harga desimal biasa, sertakan Entry/TP/SL atau WAIT.",
"low_risk": "Kamu adalah chief risk officer yang sangat konservatif, hanya entry pada setup premium dengan confluence kuat, R:R minimum 1:3. Jawab dalam Bahasa Indonesia, format harga desimal biasa, sertakan Entry/TP/SL atau WAIT.",
}

GENERAL_PROMPT = """
Kamu adalah trader dan analis crypto futures profesional yang ramah.
Jawab pertanyaan seputar futures trading, teknikal analisis, manajemen risiko dalam Bahasa Indonesia.
Berikan jawaban yang praktis, konkret, dan berdasarkan pengalaman nyata trading.
"""

AUTO_SCAN_PROMPT = """
Kamu adalah AI scanner sinyal trading futures profesional.
Nilai kualitas setup dari data secara objektif dan cepat.
KRITERIA (total 10 poin):
+2 RSI <35 atau >65 | +2 MACD histogram searah membesar | +2 EMA alignment kuat
+2 Volume >1.5x rata-rata | +2 Harga dalam radius 1% dari S&R
Balas HANYA JSON: {"score": <0-10>, "arah": "LONG"/"SHORT", "alasan": "<max 12 kata>"}
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
        if exchange == "binance":
            sym = p["symbol"]; pr = float(p["lastPrice"]); chg = float(p["priceChangePercent"]); vol = float(p["quoteVolume"])/1e6
        elif exchange == "bybit":
            sym = p["symbol"]; pr = float(p.get("lastPrice",0)); chg = float(p.get("price24hPcnt",0))*100; vol = float(p.get("turnover24h",0))/1e6
        elif exchange == "okx":
            sym = p["instId"].replace("-SWAP","").replace("-USDT","USDT"); pr = float(p.get("last",0)); chg = 0; vol = float(p.get("volCcy24h",0))/1e6
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
    logger.info(f"[AUTO] Start loop uid={uid}")
    while True:
        try:
            info = AUTO_USERS.get(uid)
            if not info or not info.get("active"): break

            exchange    = info["exchange"]
            mode        = info["mode"]
            modal       = info["modal"]
            auto_trade  = info.get("auto_trade", False)
            now         = datetime.now(timezone.utc)

            if auto_trade and uid in ACTIVE_POSITIONS:
                pos = ACTIVE_POSITIONS[uid]
                logger.info(f"[AUTO] uid={uid} posisi aktif di {pos['symbol']}, skip scan")
                await asyncio.sleep(SCAN_INTERVAL_MIN * 60)
                continue

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
                score  = result.get("score", 0)
                arah   = result.get("arah", "?")
                alasan = result.get("alasan", "")
                logger.info(f"[AUTO] {symbol} score={score} arah={arah}")

                if score >= MIN_SCORE:
                    try:
                        sinyal = await gen_signal(exchange, mode, symbol, modal,
                            f"Berikan sinyal lengkap {symbol} — score setup {score}/10.", [])
                        exinfo = EXCHANGES[exchange]
                        notif = (
                            f"🚨 *AUTO SIGNAL ALERT!*{'  (TESTNET)' if USE_TESTNET else ''}\n"
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

                        if auto_trade and exchange == "binance" and arah in ("LONG","SHORT"):
                            if uid not in ACTIVE_POSITIONS:
                                side   = "BUY" if arah == "LONG" else "SELL"
                                tp_pct = 4.0 if mode == "high_risk" else 3.0 if mode == "medium_risk" else 2.0
                                sl_pct = 2.0 if mode == "high_risk" else 1.5 if mode == "medium_risk" else 1.0
                                try:
                                    await app.bot.send_message(uid,
                                        f"⚡ *AUTO TRADE EXECUTING...*{' (TESTNET)' if USE_TESTNET else ''}\n"
                                        f"📌 {symbol} | {arah} | {LEVERAGE_MAP[mode]}x\n"
                                        f"⏳ Mengirim order...",
                                        parse_mode="Markdown")

                                    trade = await execute_trade(uid, symbol, side, mode, tp_pct, sl_pct, app)

                                    trade_notif = (
                                        f"✅ *ORDER MASUK!*{'  (TESTNET — uang virtual)' if USE_TESTNET else ''}\n"
                                        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                                        f"📌 Pair     : *{symbol}*\n"
                                        f"🎯 Arah     : *{arah}*\n"
                                        f"⚡ Entry    : ${trade['entry']}\n"
                                        f"🏹 Leverage : {trade['leverage']}x\n"
                                        f"📦 Qty      : {trade['qty']}\n"
                                        f"💰 Notional : ${trade['notional']:.2f}\n"
                                        f"✅ TP       : ${trade['tp_price']} (+{tp_pct}%)\n"
                                        f"🛑 SL       : ${trade['sl_price']} (-{sl_pct}%)\n"
                                        f"💼 Risk     : ${trade['risk_usdt']:.2f} ({RISK_PCT[mode]}% balance)\n"
                                        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                                        f"🕐 {trade['open_time']}"
                                    )
                                    await app.bot.send_message(uid, trade_notif, parse_mode="Markdown")
                                    asyncio.create_task(monitor_positions(uid, app))

                                except Exception as te:
                                    await app.bot.send_message(uid,
                                        f"❌ *AUTO TRADE GAGAL*\n`{te}`",
                                        parse_mode="Markdown")
                                    logger.error(f"[TRADE] Execute error: {te}")

                        if "last_sent" not in info: info["last_sent"] = {}
                        info["last_sent"][symbol] = now
                        AUTO_USERS[uid] = info

                        if auto_trade and uid in ACTIVE_POSITIONS:
                            break

                    except Exception as e:
                        logger.error(f"[AUTO] Gagal kirim/execute: {e}")

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

    if not is_allowed(u.id):
        await update.message.reply_text(
            f"🔒 *Akses Ditolak*\n\n"
            f"Kamu belum terdaftar untuk menggunakan bot ini.\n\n"
            f"Hubungi admin untuk mendaftarkan User ID kamu:\n"
            f"👤 Telegram: *{ADMIN_USERNAME}*\n\n"
            f"Kirim pesan ke admin dengan menyertakan User ID kamu:\n"
            f"`{u.id}`",
            parse_mode="Markdown"
        )
        logger.info(f"[BLOCKED] uid={u.id} name={u.first_name} username={u.username}")
        return

    s = sess(u.id)
    s.update({"exchange":None,"mode":None,"pair":None,"modal":None,"state":"idle","history":[]})
    testnet_badge = "\n🧪 *MODE: TESTNET — uang virtual, tanpa risiko nyata*\n" if USE_TESTNET else ""
    await update.message.reply_text(
        f"🤖 *FUTURES TRADING BOT*{testnet_badge}\n\n"
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
    testnet_note = (
        "\n*Testnet:*\n"
        "Bot ini jalan di Binance Futures TESTNET — semua order pakai saldo virtual, "
        "tidak ada uang nyata. Kalau saldo testnet habis, klaim ulang di "
        "testnet.binancefuture.com (ada tombol faucet).\n"
    ) if USE_TESTNET else ""
    await update.message.reply_text(
        "❓ *BANTUAN*\n\n"
        "*Cara pakai:*\n"
        "1. /start → pilih exchange\n"
        "2. Pilih mode (High/Medium/Low Risk)\n"
        "3. Pilih pair dari daftar live\n"
        "4. Masukkan modal\n"
        "5. Sinyal langsung muncul!\n"
        "6. Tanya apapun tentang pair tersebut\n"
        f"{testnet_note}\n"
        "*Soal SL:*\n"
        "SL yang diberikan adalah HARGA yang kamu pasang di exchange sebagai stop order.\n\n"
        "*Auto Signal:*\n"
        "Bot scan pairs otomatis, kirim notif kalau ada setup bagus (score ≥7/10)\n\n"
        "*Auto Trading:*\n"
        "/autotrade on → aktifkan auto trading (butuh Binance Testnet API key)\n"
        "/autotrade off → matikan auto trading\n"
        "/posisi → lihat posisi aktif\n"
        "/closeposisi → tutup posisi sekarang\n\n"
        "/start → reset & ganti exchange",
        parse_mode="Markdown"
    )

async def cmd_autotrade(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u  = update.effective_user
    if not is_allowed(u.id):
        await update.message.reply_text("⛔ Akses ditolak."); return

    args = ctx.args
    if not args:
        status = "🟢 ON" if AUTO_USERS.get(u.id, {}).get("auto_trade") else "🔴 OFF"
        await update.message.reply_text(
            f"🤖 *AUTO TRADING*{' (TESTNET)' if USE_TESTNET else ''}\n\nStatus: {status}\n\n"
            f"Gunakan:\n`/autotrade on` — aktifkan\n`/autotrade off` — matikan",
            parse_mode="Markdown"); return

    cmd = args[0].lower()

    if cmd == "on":
        if not BINANCE_API_KEY or not BINANCE_API_SECRET:
            await update.message.reply_text(
                "❌ *BINANCE_API_KEY / BINANCE_API_SECRET (testnet) belum diset!*\n\n"
                "1. Buka https://testnet.binancefuture.com\n"
                "2. Login pakai akun GitHub\n"
                "3. Generate API Key\n\n"
                "Tambahkan ke `.env`:\n"
                "```\nBINANCE_API_KEY=xxx\nBINANCE_API_SECRET=xxx\nBINANCE_TESTNET=true\n```\n\n"
                "Restart bot setelah mengisi .env",
                parse_mode="Markdown"); return

        info = AUTO_USERS.get(u.id)
        if not info or not info.get("active"):
            await update.message.reply_text(
                "⚠️ *Auto Signal belum aktif!*\n\n"
                "Aktifkan dulu Auto Signal via menu 🤖 AUTO SIGNAL, "
                "kemudian ketik `/autotrade on` lagi.",
                parse_mode="Markdown"); return

        AUTO_USERS[u.id]["auto_trade"] = True
        mode    = info.get("mode","high_risk")
        risk    = RISK_PCT[mode]
        lev     = LEVERAGE_MAP[mode]

        try:
            async with aiohttp.ClientSession() as sess:
                bal = await get_futures_balance(sess)
            bal_txt = f"${bal:.2f} USDT (virtual)" if USE_TESTNET else f"${bal:.2f} USDT"
        except Exception as e:
            bal_txt = f"❌ Gagal cek balance: {e}"

        await update.message.reply_text(
            f"✅ *AUTO TRADING AKTIF!*{'  🧪 TESTNET' if USE_TESTNET else ''}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏦 Exchange  : Binance Futures{' Testnet' if USE_TESTNET else ''}\n"
            f"📊 Mode      : {mode.replace('_',' ').title()}\n"
            f"🏹 Leverage  : {lev}x\n"
            f"💼 Risk/trade: {risk}% balance\n"
            f"💰 Balance   : {bal_txt}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ Bot akan auto execute saat score ≥ {MIN_SCORE}/10\n"
            f"📲 Notif masuk setiap ada order buka/tutup\n"
            f"⚠️ Max 1 posisi bersamaan\n\n"
            f"Ketik `/autotrade off` untuk matikan",
            parse_mode="Markdown")

    elif cmd == "off":
        if u.id in AUTO_USERS:
            AUTO_USERS[u.id]["auto_trade"] = False
        await update.message.reply_text(
            "🔴 *AUTO TRADING DIMATIKAN*\n\n"
            "Bot tidak akan execute order baru.\n"
            "Posisi yang sudah terbuka tetap berjalan.\n"
            "Gunakan /closeposisi untuk tutup posisi manual.",
            parse_mode="Markdown")
    else:
        await update.message.reply_text("Gunakan: `/autotrade on` atau `/autotrade off`", parse_mode="Markdown")

async def cmd_posisi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not is_allowed(u.id):
        await update.message.reply_text("⛔ Akses ditolak."); return

    pos = ACTIVE_POSITIONS.get(u.id)
    if not pos:
        if BINANCE_API_KEY:
            try:
                async with aiohttp.ClientSession() as sess:
                    data = await bnb_get(sess, "/fapi/v2/positionRisk", {})
                open_pos = [p for p in data if float(p.get("positionAmt",0)) != 0]
                if open_pos:
                    lines = [f"📊 *POSISI TERBUKA{' (TESTNET)' if USE_TESTNET else ''}:*\n"]
                    for p in open_pos:
                        pnl = float(p.get("unRealizedProfit",0))
                        lines.append(
                            f"📌 {p['symbol']}\n"
                            f"   Qty   : {p['positionAmt']}\n"
                            f"   Entry : ${float(p['entryPrice']):.4f}\n"
                            f"   PnL   : ${pnl:+.2f}\n"
                        )
                    await update.message.reply_text("\n".join(lines), parse_mode="Markdown"); return
            except Exception as e:
                pass
        await update.message.reply_text("📭 Tidak ada posisi aktif saat ini."); return

    pnl_txt = ""
    if BINANCE_API_KEY:
        try:
            async with aiohttp.ClientSession() as sess:
                data = await bnb_get(sess, "/fapi/v2/positionRisk", {"symbol": pos["symbol"]})
            for p in data:
                if float(p.get("positionAmt",0)) != 0:
                    pnl_txt = f"\n💹 Unrealized PnL: *${float(p['unRealizedProfit']):+.2f}*"
        except: pass

    arah = "LONG 🟢" if pos["side"] == "BUY" else "SHORT 🔴"
    await update.message.reply_text(
        f"📊 *POSISI AKTIF*{'  🧪 TESTNET' if USE_TESTNET else ''}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Pair     : *{pos['symbol']}*\n"
        f"🎯 Arah     : {arah}\n"
        f"⚡ Entry    : ${pos['entry']}\n"
        f"🏹 Leverage : {pos['leverage']}x\n"
        f"📦 Qty      : {pos['qty']}\n"
        f"💰 Notional : ${pos['notional']:.2f}\n"
        f"✅ TP       : ${pos['tp_price']} (+{pos['tp_pct']}%)\n"
        f"🛑 SL       : ${pos['sl_price']} (-{pos['sl_pct']}%)\n"
        f"🕐 Buka     : {pos['open_time']}"
        f"{pnl_txt}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Gunakan /closeposisi untuk tutup manual",
        parse_mode="Markdown")

async def cmd_closeposisi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not is_allowed(u.id):
        await update.message.reply_text("⛔ Akses ditolak."); return

    pos = ACTIVE_POSITIONS.get(u.id)
    if not pos:
        await update.message.reply_text("📭 Tidak ada posisi aktif untuk ditutup."); return

    await update.message.reply_text(
        f"⏳ Menutup posisi *{pos['symbol']}*...", parse_mode="Markdown")
    try:
        result = await close_position(u.id, pos["symbol"], reason="manual")
        if result["status"] == "closed":
            pnl = result.get("pnl", 0)
            emoji = "🟢" if pnl >= 0 else "🔴"
            await update.message.reply_text(
                f"✅ *POSISI DITUTUP (MANUAL)*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 Pair  : *{result['symbol']}*\n"
                f"⚡ Entry : ${result['entry']}\n"
                f"{emoji} PnL   : *${pnl:+.2f}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━",
                parse_mode="Markdown")
        else:
            await update.message.reply_text("ℹ️ Posisi tidak ditemukan di exchange.")
    except Exception as e:
        await update.message.reply_text(f"❌ Gagal close posisi: `{e}`", parse_mode="Markdown")

async def handle_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    txt = update.message.text.strip()
    s = sess(u.id)

    if not is_allowed(u.id):
        await update.message.reply_text(
            f"🔒 Akses ditolak. Hubungi *{ADMIN_USERNAME}* untuk mendaftar.\nUser ID kamu: `{u.id}`",
            parse_mode="Markdown")
        return

    if not s["exchange"] and txt not in ("/start", "/help"):
        await update.message.reply_text(
            "⚠️ Pilih exchange dulu!\nKetik /start untuk mulai.",
            parse_mode="Markdown")
        return

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

    if s["state"] == "custom_pair":
        pair = txt.upper().replace("/","").replace("-","").replace(" ","")
        s["pair"] = pair; s["state"] = "asking_modal"
        await update.message.reply_text(f"✅ Pair: *{pair}*\n\nMasukkan modal ($):\nContoh: `5`", parse_mode="Markdown")
        return

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

    resp = await gen_general(s.get("mode"), txt, [])
    await update.message.reply_text(resp + "\n\n💡 Pilih mode dari keyboard atau /start untuk mulai.")

async def handle_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global SCAN_INTERVAL_MIN, MIN_SCORE, SCAN_TOP_N
    q = update.callback_query; await q.answer()
    data = q.data; uid = q.from_user.id; s = sess(uid)

    if not is_allowed(uid):
        await q.answer("🔒 Akses ditolak.", show_alert=True)
        return

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
    print("🤖  Futures Trading Bot v5 — TESTNET EDITION" if USE_TESTNET else "🤖  Futures Trading Bot v5")
    print(f"🧪  Mode    : {'TESTNET (uang virtual)' if USE_TESTNET else 'LIVE (uang nyata!) '}")
    print(f"🧠  AI      : {MODEL}")
    print(f"🔑  API Keys: {len(_groq_clients)} key aktif (auto-rotate jika 429)")
    print(f"🏦  Exchange: Binance{' Testnet' if USE_TESTNET else ''} (trading) | Bybit/OKX/Gate.io/MEXC/Bitget/KuCoin (signal only)")
    print(f"⚡  AutoTrade: {'✅ Siap (Binance testnet API key ditemukan)' if BINANCE_API_KEY else '⚠️  Tidak aktif (BINANCE_API_KEY belum diset)'}")
    print("💰  Biaya AI: GRATIS ✅")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start",        cmd_start))
    app.add_handler(CommandHandler("help",         cmd_help))
    app.add_handler(CommandHandler("autotrade",    cmd_autotrade))
    app.add_handler(CommandHandler("posisi",       cmd_posisi))
    app.add_handler(CommandHandler("closeposisi",  cmd_closeposisi))
    app.add_handler(CallbackQueryHandler(handle_cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
