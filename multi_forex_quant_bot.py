"""
Multi-Asset Proportional Risk Automation Engine (Exness MT5)

Run modes
---------
- Single cycle (default):     python multi_forex_quant_bot.py
- Loop mode (e.g. 5h45m):     set LOOP_MINUTES=345 and run the same command.
  Loop mode connects to MT5 once and runs one cycle every 15 minutes,
  just after each M15 candle closes.

Required environment variables
------------------------------
EXNESS_MT5_LOGIN, EXNESS_MT5_PASSWORD, EXNESS_MT5_SERVER
Optional: FINNHUB_API_KEY, MT5_PATH, LOOP_MINUTES, MAX_SPREAD_PIPS, LOG_FILE,
          TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

import csv
import glob
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5
import pandas as pd
import requests

# Force UTF-8 encoding for Windows console / GitHub Actions runners
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ----------------------------------------------------------------------
# 1. Credentials & Configuration (Environment & Secure Fallbacks)
# ----------------------------------------------------------------------
raw_login = os.environ.get("EXNESS_MT5_LOGIN", "81908061").strip()
MT5_LOGIN = int(raw_login) if raw_login.isdigit() else 81908061
MT5_PASSWORD = os.environ.get("EXNESS_MT5_PASSWORD", "T3xy*B7@(PgfG").strip()
if not MT5_PASSWORD:
    MT5_PASSWORD = "T3xy*B7@(Pgf"

MT5_SERVER = os.environ.get("EXNESS_MT5_SERVER", "Exness-MT5Trial10").strip()
if not MT5_SERVER:
    MT5_SERVER = "Exness-MT5Trial10"

NEWS_API_KEY = os.environ.get("FINNHUB_API_KEY", "").strip()

# Telegram Alert Configuration
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

BASKET_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF"]

BASE_LOT_PER_1000_EQUITY = 0.01   # 0.01 lots per $1,000 equity
MAX_CONCURRENT_OPEN_TRADES = 3    # Max simultaneous trades opened by this bot
NEWS_BLACKOUT_MINUTES = 15        # Pause 15m before & after high-impact news
MAX_SPREAD_PIPS = float(os.environ.get("MAX_SPREAD_PIPS", "2.0"))

SL_PIPS = 20.0
TP_PIPS = 40.0
MAGIC_NUMBER = 1008274
LOG_FILE = os.environ.get("LOG_FILE", "trade_log.csv")
LOOP_MINUTES = int(os.environ.get("LOOP_MINUTES", "0") or 0)

# Remembers the last candle acted on per symbol
_last_signal_bar = {}


# ----------------------------------------------------------------------
# 2. Telegram Notification Helper
# ----------------------------------------------------------------------
def send_telegram_alert(message: str):
    """Sends immediate operational alert to user phone via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"  [WARN] Failed to send Telegram alert: {e}")


# ----------------------------------------------------------------------
# 3. Logging Routine
# ----------------------------------------------------------------------
def log_event(**row):
    """Appends one row to the CSV log."""
    row = {"utc_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), **row}
    fields = ["utc_time", "symbol", "signal", "price", "lots", "sl", "tp", "result", "ticket"]
    try:
        new_file = not os.path.isfile(LOG_FILE)
        with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            if new_file:
                writer.writeheader()
            writer.writerow({k: row.get(k, "") for k in fields})
    except Exception as e:
        print(f"  [WARN] Could not write log: {e}")


# ----------------------------------------------------------------------
# 4. MT5 Terminal Connection
# ----------------------------------------------------------------------
def find_terminal_path():
    """Finds terminal64.exe: MT5_PATH env var, known locations, then a search."""
    candidates = [
        os.environ.get("MT5_PATH", "").strip(),
        r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe",
        r"C:\Program Files\MetaTrader 5\terminal64.exe",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    for pf in (r"C:\Program Files", r"C:\Program Files (x86)"):
        matches = glob.glob(os.path.join(pf, "MetaTrader*", "terminal64.exe"))
        if matches:
            return matches[0]
    return None


def connect_mt5(retries=5, wait=15) -> bool:
    """Starts/attaches to the terminal and logs in, with retries."""
    kwargs = dict(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER, timeout=60000)
    path = find_terminal_path()
    if path:
        kwargs["path"] = path
        print(f"[INFO] Using terminal: {path}")
    else:
        print("[WARN] terminal64.exe not found; letting MT5 package auto-detect.")

    for attempt in range(1, retries + 1):
        if mt5.initialize(**kwargs):
            print(f"[SUCCESS] Connected to Exness MT5 account #{MT5_LOGIN} ({MT5_SERVER})")
            term = mt5.terminal_info()
            acct = mt5.account_info()
            if term is not None and not term.trade_allowed:
                print("[WARN] Algo/Auto trading is DISABLED in the terminal.")
            if acct is not None and not acct.trade_allowed:
                print("[WARN] Trading is not allowed on this account.")
            return True
        print(f"[RETRY {attempt}/{retries}] MT5 init failed: {mt5.last_error()}")
        mt5.shutdown()
        if attempt < retries:
            time.sleep(wait)
    return False


def ensure_connection() -> bool:
    """Reconnects if the terminal connection dropped (used in loop mode)."""
    try:
        if mt5.terminal_info() is not None and mt5.account_info() is not None:
            return True
    except Exception:
        pass
    print("[INFO] Connection lost, reconnecting...")
    mt5.shutdown()
    return connect_mt5()


# ----------------------------------------------------------------------
# 5. Macro News Filter (Finnhub)
# ----------------------------------------------------------------------
def is_news_blackout_active() -> tuple[bool, str]:
    if not NEWS_API_KEY:
        return False, "News filter skipped (no FINNHUB_API_KEY)"
    try:
        now_utc = datetime.now(timezone.utc)
        start_date = (now_utc - timedelta(minutes=NEWS_BLACKOUT_MINUTES)).strftime("%Y-%m-%d")
        end_date = (now_utc + timedelta(minutes=NEWS_BLACKOUT_MINUTES)).strftime("%Y-%m-%d")

        url = "https://finnhub.io/api/v1/calendar/economic"
        res = requests.get(
            url,
            params={"from": start_date, "to": end_date, "token": NEWS_API_KEY},
            timeout=8,
        )
        if res.status_code != 200:
            return False, f"News feed unavailable (HTTP {res.status_code}); filter skipped"

        events = res.json().get("economicCalendar", [])
        major_countries = {"US", "EU", "GB", "JP", "AU", "CA", "CH", "DE"}

        for event in events:
            impact = str(event.get("impact", "")).lower()
            country = str(event.get("country", "")).upper()
            if impact not in ("high", "3") or country not in major_countries:
                continue
            event_time_str = event.get("time", "")
            if not event_time_str:
                continue
            try:
                event_dt = datetime.strptime(event_time_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            minutes_away = abs((event_dt - now_utc).total_seconds()) / 60.0
            if minutes_away <= NEWS_BLACKOUT_MINUTES:
                msg = f"High-impact event ({country}: {event.get('event', 'News')}) at {event_time_str} UTC"
                send_telegram_alert(f"⚠️ *NEWS BLACKOUT ACTIVE*\n{msg}")
                return True, msg

        return False, "Clear"
    except Exception as e:
        return False, f"News feed note: {e}; filter skipped"


# ----------------------------------------------------------------------
# 6. Symbols, Sizing & Signal Engine
# ----------------------------------------------------------------------
def resolve_exness_symbol(base_pair: str) -> str:
    if mt5.symbol_info(base_pair) is not None:
        mt5.symbol_select(base_pair, True)
        return base_pair
    matches = mt5.symbols_get(f"*{base_pair}*")
    if matches:
        sym = sorted((m.name for m in matches), key=len)[0]
        mt5.symbol_select(sym, True)
        return sym
    return base_pair


def pip_size(info) -> float:
    return info.point * 10 if info.digits in (3, 5) else info.point


def normalize_volume(lots: float, info) -> float:
    step = info.volume_step or 0.01
    vmin = info.volume_min or 0.01
    vmax = info.volume_max or 100.0
    vol = math.floor(lots / step + 1e-9) * step
    vol = max(vmin, min(vmax, vol))
    return round(vol, 2)


def calculate_proportional_lots(equity: float) -> float:
    return max(0.01, round(equity / 1000.0 * BASE_LOT_PER_1000_EQUITY, 2))


def evaluate_market_signal(symbol: str):
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 1, 120)
    if rates is None or len(rates) < 60:
        return "NEUTRAL", None

    df = pd.DataFrame(rates)
    df["ema_fast"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=21, adjust=False).mean()

    prev_fast, curr_fast = df["ema_fast"].iloc[-2], df["ema_fast"].iloc[-1]
    prev_slow, curr_slow = df["ema_slow"].iloc[-2], df["ema_slow"].iloc[-1]
    bar_time = int(df["time"].iloc[-1])

    if prev_fast <= prev_slow and curr_fast > curr_slow:
        return "BUY", bar_time
    if prev_fast >= prev_slow and curr_fast < curr_slow:
        return "SELL", bar_time
    return "NEUTRAL", bar_time


# ----------------------------------------------------------------------
# 7. Order Execution
# ----------------------------------------------------------------------
def pick_filling_mode(info) -> int:
    modes = info.filling_mode
    if modes & 2:
        return mt5.ORDER_FILLING_IOC
    if modes & 1:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


def execute_market_order(symbol: str, order_type: str, base_lots: float) -> bool:
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if tick is None or info is None:
        print(f"  [FAIL] Unable to fetch market info for {symbol}")
        log_event(symbol=symbol, signal=order_type, result="no_market_info")
        return False

    lots = normalize_volume(base_lots, info)
    pip = pip_size(info)

    if order_type == "BUY":
        price = tick.ask
        sl = round(price - SL_PIPS * pip, info.digits)
        tp = round(price + TP_PIPS * pip, info.digits)
        action = mt5.ORDER_TYPE_BUY
    else:
        price = tick.bid
        sl = round(price + SL_PIPS * pip, info.digits)
        tp = round(price - TP_PIPS * pip, info.digits)
        action = mt5.ORDER_TYPE_SELL

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lots,
        "type": action,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": MAGIC_NUMBER,
        "comment": "QuantRiskEngine Auto",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": pick_filling_mode(info),
    }

    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        ret_code = result.retcode if result else "NO_RESPONSE"
        comment = result.comment if result else mt5.last_error()
        print(f"  [FAIL] Order rejected for {symbol}: retcode {ret_code} ({comment})")
        log_event(symbol=symbol, signal=order_type, price=price, lots=lots, sl=sl, tp=tp,
                  result=f"rejected_{ret_code}")
        
        # Telegram Alert for Order Rejection
        send_telegram_alert(
            f"❌ *ORDER REJECTED*\n"
            f"*Symbol:* {symbol}\n"
            f"*Signal:* {order_type}\n"
            f"*Error Code:* {ret_code}\n"
            f"*Reason:* {comment}"
        )
        return False

    print(f"  [ORDER PLACED] {order_type} {lots} lots {symbol} @ {price:.5f} | "
          f"SL {sl:.5f} | TP {tp:.5f} (ticket #{result.order})")
    log_event(symbol=symbol, signal=order_type, price=price, lots=lots, sl=sl, tp=tp,
              result="filled", ticket=result.order)

    # Telegram Alert for Order Execution
    send_telegram_alert(
        f"🚀 *ORDER EXECUTED*\n"
        f"*Symbol:* {symbol}\n"
        f"*Side:* {order_type}\n"
        f"*Volume:* {lots} lots\n"
        f"*Price:* {price:.5f}\n"
        f"*SL:* {sl:.5f} | *TP:* {tp:.5f}\n"
        f"*Ticket:* `#{result.order}`"
    )
    return True


# ----------------------------------------------------------------------
# 8. One Trading Cycle
# ----------------------------------------------------------------------
def run_cycle():
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print("\n" + "=" * 66)
    print(f"[{timestamp} UTC] Multi-Asset Proportional Risk Engine (Exness MT5)")
    print("=" * 66)

    log_event(symbol="SYSTEM", signal="INIT", result="cycle_executed")

    is_news, news_msg = is_news_blackout_active()
    print(f"• News filter: {news_msg}")
    if is_news:
        print("[HOLD] News volatility protection active. No new trades this cycle.")
        return

    account = mt5.account_info()
    if account is None:
        print(f"[FAIL] Could not read account info: {mt5.last_error()}")
        return

    equity, balance = account.equity, account.balance
    base_lots = calculate_proportional_lots(equity)

    all_positions = mt5.positions_get() or []
    my_positions = [p for p in all_positions if p.magic == MAGIC_NUMBER]
    open_count = len(my_positions)
    open_symbols = [p.symbol for p in my_positions]

    print(f"• Equity: ${equity:,.2f} | Balance: ${balance:,.2f}")
    print(f"• Sizing: {base_lots:.2f} lots per trade")
    print(f"• Bot positions open: {open_count} / {MAX_CONCURRENT_OPEN_TRADES}")
    print("-" * 66)

    if open_count >= MAX_CONCURRENT_OPEN_TRADES:
        print("[HOLD] Max concurrent trades reached. Positions exit via SL/TP.")
        return

    print("FX basket quotes & signals:")
    for base_pair in BASKET_PAIRS:
        symbol = resolve_exness_symbol(base_pair)
        tick = mt5.symbol_info_tick(symbol)
        info = mt5.symbol_info(symbol)

        if tick is None or info is None:
            print(f"  └─ {symbol:<8} | Quote unavailable (market closed or symbol inactive)")
            continue

        spread_pips = round((tick.ask - tick.bid) / pip_size(info), 1)
        signal, bar_time = evaluate_market_signal(symbol)
        print(f"  └─ {symbol:<8} | Bid: {tick.bid:<9.5f} | Ask: {tick.ask:<9.5f} | "
              f"Spread: {spread_pips:>4.1f} pips | Signal: {signal}")

        if signal not in ("BUY", "SELL"):
            continue
        if symbol in open_symbols:
            continue
        if bar_time is not None and _last_signal_bar.get(symbol) == bar_time:
            continue
        if spread_pips > MAX_SPREAD_PIPS:
            print(f"     [SKIP] Spread {spread_pips} pips exceeds limit {MAX_SPREAD_PIPS}.")
            log_event(symbol=symbol, signal=signal, result=f"skipped_spread_{spread_pips}")
            continue
        if open_count >= MAX_CONCURRENT_OPEN_TRADES:
            print(f"     [HOLD] Max trades reached. Skipping {symbol}.")
            continue

        if execute_market_order(symbol, signal, base_lots):
            open_count += 1
            open_symbols.append(symbol)
            if bar_time is not None:
                _last_signal_bar[symbol] = bar_time

    print("-" * 66)
    print("[COMPLETE] Cycle finished.")


# ----------------------------------------------------------------------
# 9. Entry Point
# ----------------------------------------------------------------------
def seconds_until_next_candle(buffer_seconds=5) -> float:
    now = datetime.now(timezone.utc)
    minutes_to_next = 15 - (now.minute % 15)
    nxt = (now + timedelta(minutes=minutes_to_next)).replace(second=0, microsecond=0)
    return max(1.0, (nxt - now).total_seconds() + buffer_seconds)


def main():
    if not connect_mt5():
        print("[FAIL] Could not connect to MT5. Exiting.")
        sys.exit(1)

    try:
        if LOOP_MINUTES <= 0:
            run_cycle()
            return

        end_time = time.time() + LOOP_MINUTES * 60
        print(f"[INFO] Loop mode: running for {LOOP_MINUTES} minutes.")
        while True:
            try:
                if ensure_connection():
                    run_cycle()
                else:
                    print("[WARN] Reconnect failed; will retry next cycle.")
            except Exception as e:
                print(f"[ERROR] Cycle crashed: {e}")
            wait = seconds_until_next_candle()
            if time.time() + wait >= end_time:
                break
            time.sleep(wait)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
