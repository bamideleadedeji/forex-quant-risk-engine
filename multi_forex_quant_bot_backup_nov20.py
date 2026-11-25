import os
import math
import time
import logging
from datetime import datetime, timezone, timedelta
import requests
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

MT5_PATH = r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe"
MT5_ACCOUNT = 81908061
MT5_SERVER = "Exness-MT5Trial10"
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "").strip()
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "").strip()

SYMBOLS = ["EURUSDm", "GBPUSDm", "USDJPYm", "AUDUSDm", "USDCADm", "USDCHFm"]
MAGIC_NUMBER = 234000
LOG_FILE = os.path.join(BASE_DIR, "trade_log.csv")
EXEC_LOG_FILE = os.path.join(BASE_DIR, "bot_execution.log")

LOT_PER_1K_EQUITY = 0.01
MAX_OPEN_POSITIONS = 3
MAX_USD_EXPOSURE = 2
SL_PERCENTAGE = 0.0020
TP_PERCENTAGE = 0.0040
MAX_DEVIATION = 20
MAX_SPREAD_PIPS = 2.5
NEWS_BLACKOUT_MINUTES = 60

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(EXEC_LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("ExnessQuantEngine")


def append_trade_log(symbol: str, signal: str, price, lots, sl, tp, result: str, ticket=""):
    row = {
        "utc_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": symbol,
        "signal": signal,
        "price": price,
        "lots": lots,
        "sl": sl,
        "tp": tp,
        "result": result,
        "ticket": ticket
    }
    df_new = pd.DataFrame([row])
    df_new.to_csv(LOG_FILE, mode="a", header=not os.path.exists(LOG_FILE), index=False)


def check_finnhub_macro_blackout() -> tuple[bool, str]:
    now_utc = datetime.now(timezone.utc)
    target_currencies = {"USD", "EUR", "GBP", "JPY"}
    central_bank_keywords = (
        "fed", "fomc", "interest rate", "ecb", "boe", "boj", "bank of england",
        "bank of japan", "non-farm", "nonfarm", "cpi", "inflation", "gdp", "powell", "lagarde", "bailey", "ueda"
    )
    headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}

    # 1. Try Finnhub first if key is present
    if FINNHUB_API_KEY:
        try:
            from_date = (now_utc - timedelta(days=1)).strftime("%Y-%m-%d")
            to_date = (now_utc + timedelta(days=1)).strftime("%Y-%m-%d")
            resp = requests.get(
                "https://finnhub.io",
                params={"from": from_date, "to": to_date, "token": FINNHUB_API_KEY},
                headers=headers,
                timeout=10
            )
            if resp.status_code == 200 and resp.text.strip():
                events = resp.json().get("economicCalendar", [])
                for ev in events:
                    impact = str(ev.get("impact", "")).lower()
                    currency = str(ev.get("currency", ev.get("country", ""))).upper()
                    event_name = str(ev.get("event", "")).lower()
                    ev_time_str = str(ev.get("time", "")).strip()
                    if not ev_time_str:
                        continue
                    is_major = any(c in currency for c in target_currencies)
                    is_high = (impact == "high") or any(k in event_name for k in central_bank_keywords)
                    if is_major and is_high:
                        ev_dt = datetime.strptime(ev_time_str[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                        diff_mins = abs((ev_dt - now_utc).total_seconds()) / 60.0
                        if diff_mins <= NEWS_BLACKOUT_MINUTES:
                            return True, f"{currency} High-Impact '{ev.get('event')}' ({diff_mins:.0f}m away)"
                return False, "Finnhub calendar clear."
        except Exception:
            pass

    # 2. Automatic Fallback: Live ForexFactory / FairEconomy Weekly Macro Calendar
    try:
        ff_resp = requests.get("https://faireconomy.media", headers=headers, timeout=12)
        if ff_resp.status_code == 200 and ff_resp.text.strip():
            ff_events = ff_resp.json()
            for ev in ff_events:
                currency = str(ev.get("country", "")).upper()
                impact = str(ev.get("impact", "")).lower()
                title = str(ev.get("title", ""))
                date_str = str(ev.get("date", "")).strip()
                if currency not in target_currencies or not date_str:
                    continue
                is_high = (impact == "high") or any(k in title.lower() for k in central_bank_keywords)
                if not is_high:
                    continue
                ev_dt = datetime.fromisoformat(date_str).astimezone(timezone.utc)
                diff_mins = abs((ev_dt - now_utc).total_seconds()) / 60.0
                if diff_mins <= NEWS_BLACKOUT_MINUTES:
                    return True, f"{currency} High-Impact '{title}' at {ev_dt.strftime('%H:%M UTC')} ({diff_mins:.0f}m away)"
            logger.info("Macro news calendar verified clean (0 Fed/ECB/BoE/BoJ events within +/-60m).")
            return False, "Macro calendar clear"
    except Exception as exc:
        logger.warning(f"Macro calendar fallback warning: {exc}")

    return False, "Macro calendar check skipped"

def compute_dynamic_lot_size(equity: float, sym_info) -> float:
    raw_lots = (max(0.0, equity) / 1000.0) * LOT_PER_1K_EQUITY
    vol_min = sym_info.volume_min if sym_info else 0.01
    vol_max = sym_info.volume_max if sym_info else 100.0
    vol_step = sym_info.volume_step if (sym_info and sym_info.volume_step > 0) else 0.01

    stepped = math.floor(raw_lots / vol_step) * vol_step
    clamped = max(vol_min, min(vol_max, stepped))
    decimals = max(0, len(str(vol_step).split(".")) if "." in str(vol_step) else 0)
    return round(clamped, decimals)


def evaluate_quant_signal(sym: str) -> tuple[str, str]:
    rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 80)
    if rates is None or len(rates) < 60:
        return "HOLD", "Insufficient H1 candles"

    df = pd.DataFrame(rates)
    close = df["close"]
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    last_close = float(close.iloc[-1])
    last_ema20 = float(ema20.iloc[-1])
    last_ema50 = float(ema50.iloc[-1])
    last_rsi = float(rsi.iloc[-1]) if not np.isnan(rsi.iloc[-1]) else 50.0

    if last_close > last_ema20 > last_ema50 and 45.0 <= last_rsi <= 68.0:
        return "BUY", f"Bullish EMA20>EMA50 & RSI={last_rsi:.1f}"
    if last_close < last_ema20 < last_ema50 and 32.0 <= last_rsi <= 55.0:
        return "SELL", f"Bearish EMA20<EMA50 & RSI={last_rsi:.1f}"

    return "HOLD", f"No trend alignment (EMA20={last_ema20:.5f}, EMA50={last_ema50:.5f}, RSI={last_rsi:.1f})"


def enforce_server_sltp_on_open_positions(positions):
    """Audits all open positions and attaches server-side SL/TP if any position has SL=0 or TP=0."""
    if not positions:
        return
    for p in positions:
        if p.sl == 0.0 or p.tp == 0.0:
            sym_info = mt5.symbol_info(p.symbol)
            if not sym_info:
                continue
            digits = sym_info.digits
            point = sym_info.point
            min_dist = max((sym_info.trade_stops_level + 10) * point, 0.0)
            entry = p.price_open
            sl_dist = max(entry * SL_PERCENTAGE, min_dist)
            tp_dist = max(entry * TP_PERCENTAGE, min_dist * 2.0)

            if p.type == 0:  # BUY
                new_sl = round(entry - sl_dist, digits)
                new_tp = round(entry + tp_dist, digits)
            else:            # SELL
                new_sl = round(entry + sl_dist, digits)
                new_tp = round(entry - tp_dist, digits)

            req = {
                "action": mt5.TRADE_ACTION_SLTP,
                "symbol": p.symbol,
                "position": p.ticket,
                "sl": new_sl,
                "tp": new_tp,
            }
            res = mt5.order_send(req)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"[SL/TP GUARD] Attached missing SL={new_sl}, TP={new_tp} to Ticket #{p.ticket} ({p.symbol})")
            else:
                logger.warning(f"[SL/TP GUARD] Failed to attach SL/TP to Ticket #{p.ticket}: {res.retcode if res else 'NONE'}")


def get_usd_exposure_count(open_positions) -> int:
    if not open_positions:
        return 0
    return sum(1 for pos in open_positions if "USD" in pos.symbol)


def has_open_symbol_position(open_positions, sym: str) -> bool:
    if not open_positions:
        return False
    return any(pos.symbol == sym for pos in open_positions)


def run_single_cycle():
    is_blackout, blackout_reason = check_finnhub_macro_blackout()
    if is_blackout:
        logger.warning(f"[NEWS BLACKOUT ACTIVE] {blackout_reason}")
        append_trade_log("SYSTEM", "HOLD", "", "", "", "", "news_blackout_active", "")
        return

    if not mt5.initialize(path=MT5_PATH):
        logger.error(f"MT5 Init Failed ({MT5_PATH}): {mt5.last_error()}")
        return

    if MT5_PASSWORD:
        authorized = mt5.login(MT5_ACCOUNT, password=MT5_PASSWORD, server=MT5_SERVER)
    else:
        authorized = mt5.login(MT5_ACCOUNT, server=MT5_SERVER)

    if not authorized:
        logger.error(f"MT5 Login Failed for #{MT5_ACCOUNT}: {mt5.last_error()}")
        mt5.shutdown()
        return

    acc = mt5.account_info()
    positions = mt5.positions_get()
    enforce_server_sltp_on_open_positions(positions)
    positions = mt5.positions_get()
    pos_count = len(positions) if positions else 0
    usd_exposure = get_usd_exposure_count(positions)
    equity = acc.equity if acc else 0.0
    balance = acc.balance if acc else 0.0

    logger.info(f"Connected to Exness Account #{MT5_ACCOUNT} | Equity: ${equity:,.2f} | Balance: ${balance:,.2f} | Open: {pos_count}/{MAX_OPEN_POSITIONS}")

    for sym in SYMBOLS:
        if not mt5.symbol_select(sym, True):
            logger.warning(f"Symbol select failed: {sym}")
            continue

        tick = mt5.symbol_info_tick(sym)
        sym_info = mt5.symbol_info(sym)
        if not tick or not sym_info:
            logger.warning(f"Tick/SymbolInfo unavailable: {sym}")
            continue

        pip_scale = 0.01 if "JPY" in sym else 0.0001
        spread_pips = round((tick.ask - tick.bid) / pip_scale, 2)
        lot_size = compute_dynamic_lot_size(equity, sym_info)
        signal, sig_reason = evaluate_quant_signal(sym)

        logger.info(f"{sym:<9} | Bid: {tick.bid:<9} | Ask: {tick.ask:<9} | Spread: {spread_pips:>4.1f}p | Lots: {lot_size:.2f} | {signal} ({sig_reason})")

        if signal == "HOLD":
            continue

        if spread_pips > MAX_SPREAD_PIPS:
            logger.info(f"  -> Skipped {sym}: Spread {spread_pips}p > {MAX_SPREAD_PIPS}p limit.")
            continue

        if has_open_symbol_position(positions, sym):
            logger.info(f"  -> Skipped {sym}: Active position already open on {sym}.")
            continue

        if pos_count >= MAX_OPEN_POSITIONS:
            logger.info(f"  -> Skipped {sym}: Max open positions ({MAX_OPEN_POSITIONS}) reached.")
            continue

        is_usd_pair = "USD" in sym
        if is_usd_pair and usd_exposure >= MAX_USD_EXPOSURE:
            logger.info(f"  -> [CORRELATION GUARD] Skipped {sym}: Max USD exposure ({MAX_USD_EXPOSURE}) reached.")
            continue

        digits = sym_info.digits
        point = sym_info.point
        min_stop_dist = max((sym_info.trade_stops_level + 10) * point, 0.0)

        price = tick.ask if signal == "BUY" else tick.bid
        order_type = mt5.ORDER_TYPE_BUY if signal == "BUY" else mt5.ORDER_TYPE_SELL

        raw_sl_dist = max(price * SL_PERCENTAGE, min_stop_dist)
        raw_tp_dist = max(price * TP_PERCENTAGE, min_stop_dist * 2.0)

        if signal == "BUY":
            sl = round(price - raw_sl_dist, digits)
            tp = round(price + raw_tp_dist, digits)
        else:
            sl = round(price + raw_sl_dist, digits)
            tp = round(price - raw_tp_dist, digits)

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym,
            "volume": lot_size,
            "type": order_type,
            "price": round(price, digits),
            "sl": sl,
            "tp": tp,
            "deviation": MAX_DEVIATION,
            "magic": MAGIC_NUMBER,
            "comment": "ICIE FX Quant Guard",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        res = mt5.order_send(req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"  -> [SUCCESS] FILLED {signal} {lot_size} {sym} @ {price} | SL: {sl} | TP: {tp} | Ticket: {res.order}")
            append_trade_log(sym, signal, round(price, digits), lot_size, sl, tp, "filled", res.order)
            pos_count += 1
            if is_usd_pair:
                usd_exposure += 1
        else:
            code = res.retcode if res else "NONE"
            comment = res.comment if res else str(mt5.last_error())
            logger.warning(f"  -> [REJECTED] {sym} Code: {code} ({comment})")

    logger.info("[COMPLETE] Institutional FX risk execution cycle finished cleanly.")
    mt5.shutdown()


if __name__ == "__main__":
    run_single_cycle()



