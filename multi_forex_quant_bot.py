import csv
import glob
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5
import numpy as np
import pandas as pd
import requests

# Force UTF-8 encoding for Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ----------------------------------------------------------------------
# 1. Credentials & Configuration
# ----------------------------------------------------------------------
MT5_LOGIN = 81908061
MT5_PASSWORD = "T3xy*B7@(Pgf"
MT5_SERVER = "Exness-MT5Trial10"
MAGIC_NUMBER = 1008061

NEWS_API_KEY = os.environ.get("FINNHUB_API_KEY", "dbjlpihr01qonjmerf9gdbjlpihr01qonjmerfa0").strip()

# Basket includes major forex pairs + Gold (XAUUSDm) for fast scalping
BASKET_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "XAUUSDm"]

BASE_LOT_PER_1000_EQUITY = 0.01   # 0.01 lots per $1,000 equity
MAX_CONCURRENT_OPEN_TRADES = 5    # Optimized concurrency
NEWS_BLACKOUT_MINUTES = 30        # Strict 30m news guard
MAX_SPREAD_PIPS = float(os.environ.get("MAX_SPREAD_PIPS", "3.0"))

# Calibrated GJR-GARCH Volatility Ceiling (3.5% daily conditional volatility)
MAX_ALLOWED_GJR_GARCH_VOL = 0.035

LOG_FILE = os.environ.get("LOG_FILE", "trade_log.csv")
LOOP_MINUTES = int(os.environ.get("LOOP_MINUTES", "0") or 0)

# State tracker to prevent duplicate orders per bar
_last_signal_bar = {}


# ----------------------------------------------------------------------
# 2. Logging & Audit Trail
# ----------------------------------------------------------------------
def log_event(**row):
    """Appends one row to the CSV audit log with guaranteed error handling."""
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
# 3. MT5 Terminal Connection & Management
# ----------------------------------------------------------------------
def find_terminal_path():
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
                print("[WARN] Algo/Auto trading is DISABLED in the terminal. Orders will be rejected.")
            if acct is not None and not acct.trade_allowed:
                print("[WARN] Trading is not allowed on this account.")
            return True
        print(f"[RETRY {attempt}/{retries}] MT5 init failed: {mt5.last_error()}")
        mt5.shutdown()
        if attempt < retries:
            time.sleep(wait)
    return False


def ensure_connection() -> bool:
    try:
        if mt5.terminal_info() is not None and mt5.account_info() is not None:
            return True
    except Exception:
        pass
    print("[INFO] Connection lost, reconnecting...")
    mt5.shutdown()
    return connect_mt5()


# ----------------------------------------------------------------------
# 4. Fail-Safe Macro News Guard
# ----------------------------------------------------------------------
def is_news_blackout_active() -> tuple[bool, str]:
    """
    FAIL-SAFE DESIGN: If Finnhub API key is missing or feed fails, defaults to Open.
    """
    if not NEWS_API_KEY:
        return False, "News filter bypass: No FINNHUB_API_KEY provided (Fail-Open active)"
        
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
            return False, f"News feed unavailable (HTTP {res.status_code}); bypassing"

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
                return True, f"High-impact event ({country}: {event.get('event', 'News')}) at {event_time_str} UTC"

        return False, "Clear (No high-impact news within 30m window)"
    except Exception as e:
        return False, f"News feed error: {e}; bypassing"


# ----------------------------------------------------------------------
# 5. Volatility Estimation (GJR-GARCH Approximation)
# ----------------------------------------------------------------------
def calculate_gjr_garch_volatility(price_series: pd.Series) -> float:
    try:
        log_returns = np.log(price_series / price_series.shift(1)).dropna() * 100.0
        omega = 0.02
        alpha = 0.05
        gamma = 0.08
        beta = 0.88
        
        variance = np.var(log_returns) if len(log_returns) > 0 else 0.1
        for r in log_returns.tail(30):
            indicator = 1.0 if r < 0 else 0.0
            variance = omega + (alpha + gamma * indicator) * (r ** 2) + beta * variance
            
        daily_vol = math.sqrt(max(variance, 1e-6)) / 100.0
        return float(daily_vol)
    except Exception:
        return 0.012


# ----------------------------------------------------------------------
# 6. Symbol Resolution & Sizing Engine
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
    return round(max(vmin, min(vmax, vol)), 2)


def calculate_proportional_lots(equity: float) -> float:
    return max(0.01, round(equity / 1000.0 * BASE_LOT_PER_1000_EQUITY, 2))


# ----------------------------------------------------------------------
# 7. Multi-Timeframe Cascade Signal Engine (Gold M1 -> M5 -> M15)
# ----------------------------------------------------------------------
def compute_signal_on_timeframe(symbol: str, timeframe):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, 100)
    if rates is None or len(rates) < 50:
        return "NEUTRAL", None, 0.0

    df = pd.DataFrame(rates)
    df["ema_fast"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=21, adjust=False).mean()
    
    # RSI calculation (14 periods)
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / (avg_loss + 1e-6)
    df["rsi"] = 100.0 - (100.0 / (1.0 + rs))

    curr_close = df["close"].iloc[-1]
    curr_fast = df["ema_fast"].iloc[-1]
    curr_slow = df["ema_slow"].iloc[-1]
    curr_rsi = df["rsi"].iloc[-1]
    bar_time = int(df["time"].iloc[-1])

    if curr_fast > curr_slow and curr_rsi > 42.0:
        return "BUY", bar_time, curr_close
    elif curr_fast < curr_slow and curr_rsi < 58.0:
        return "SELL", bar_time, curr_close

    return "NEUTRAL", bar_time, curr_close


def evaluate_market_signal(symbol: str):
    """
    Gold Cascade Strategy:
    1. Check M1 (1-minute) first.
    2. If NEUTRAL, check M5 (5-minute).
    3. If NEUTRAL, check M15 (15-minute).
    Forex pairs check M15 directly.
    """
    if "XAU" in symbol.upper():
        # Cascade: M1 -> M5 -> M15
        for tf_name, tf in [("M1", mt5.TIMEFRAME_M1), ("M5", mt5.TIMEFRAME_M5), ("M15", mt5.TIMEFRAME_M15)]:
            sig, b_time, c_close = compute_signal_on_timeframe(symbol, tf)
            if sig in ("BUY", "SELL"):
                return f"{sig} ({tf_name})", b_time, c_close
        return "NEUTRAL", None, 0.0
    else:
        # Standard Forex on M15
        sig, b_time, c_close = compute_signal_on_timeframe(symbol, mt5.TIMEFRAME_M15)
        return sig, b_time, c_close


# ----------------------------------------------------------------------
# 8. Order Execution Engine
# ----------------------------------------------------------------------
def pick_filling_mode(info) -> int:
    modes = info.filling_mode
    if modes & 2:
        return mt5.ORDER_FILLING_IOC
    if modes & 1:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


def execute_order(symbol: str, signal_raw: str, base_lots: float, gjr_vol: float, current_price: float) -> bool:
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if tick is None or info is None:
        print(f"  [FAIL] Unable to fetch market info for {symbol}")
        log_event(symbol=symbol, signal=signal_raw, result="no_market_info")
        return False

    lots = normalize_volume(base_lots, info)
    pip = pip_size(info)

    # Clean signal string (e.g., "BUY (M1)" -> "BUY")
    order_type = "BUY" if "BUY" in signal_raw.upper() else "SELL"

    sl_pips = max(15.0, round(gjr_vol * 10000 * 1.5, 1))
    tp_pips = max(30.0, round(sl_pips * 2.0, 1))

    if order_type == "BUY":
        price = tick.ask
        sl = round(price - sl_pips * pip, info.digits)
        tp = round(price + tp_pips * pip, info.digits)
        trade_type = mt5.ORDER_TYPE_BUY
    else:
        price = tick.bid
        sl = round(price + sl_pips * pip, info.digits)
        tp = round(price - tp_pips * pip, info.digits)
        trade_type = mt5.ORDER_TYPE_SELL

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lots,
        "type": trade_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 30,
        "magic": MAGIC_NUMBER,
        "comment": f"QuantPro {signal_raw}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": pick_filling_mode(info),
    }

    result = mt5.order_send(request)
    if result is None:
        print(f"  [FAIL] No response from MT5 for {symbol}: {mt5.last_error()}")
        log_event(symbol=symbol, signal=signal_raw, price=price, lots=lots, sl=sl, tp=tp, result="no_response")
        return False

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"  [FAIL] Order rejected for {symbol}: retcode {result.retcode} ({result.comment})")
        log_event(symbol=symbol, signal=signal_raw, price=price, lots=lots, sl=sl, tp=tp, result=f"rejected_{result.retcode}")
        return False

    print(f"  [ORDER PLACED] {order_type} {lots} lots {symbol} @ {price:.5f} | SL {sl:.5f} | TP {tp:.5f} (ticket #{result.order})")
    log_event(symbol=symbol, signal=signal_raw, price=price, lots=lots, sl=sl, tp=tp, result="filled", ticket=result.order)
    return True


# ----------------------------------------------------------------------
# 9. Main Execution Cycle
# ----------------------------------------------------------------------
def run_cycle():
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print("\n" + "=" * 66)
    print(f"[{timestamp} UTC] Multi-Asset Proportional Quant Engine (Exness MT5)")
    print("=" * 66)

    is_news, news_msg = is_news_blackout_active()
    print(f"• News filter: {news_msg}")
    if is_news:
        print("[HOLD] News protection active. No new trades this cycle.")
        log_event(symbol="SYSTEM", signal="HOLD", result="news_blackout_active")
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
        print("[HOLD] Max concurrent trades reached.")
        return

    print("FX & Gold basket quotes & multi-timeframe scans:")
    for base_pair in BASKET_PAIRS:
        symbol = resolve_exness_symbol(base_pair)
        tick = mt5.symbol_info_tick(symbol)
        info = mt5.symbol_info(symbol)

        if tick is None or info is None:
            print(f"  └─ {symbol:<8} | Quote unavailable")
            continue

        spread_pips = round((tick.ask - tick.bid) / pip_size(info), 1)
        
        rates_vol = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 100)
        df_vol = pd.DataFrame(rates_vol) if rates_vol is not None else pd.DataFrame()
        gjr_vol = calculate_gjr_garch_volatility(df_vol["close"]) if not df_vol.empty else 0.01

        signal_raw, bar_time, current_close = evaluate_market_signal(symbol)
        print(f"  └─ {symbol:<8} | Bid: {tick.bid:<9.5f} | Spread: {spread_pips:>4.1f}p | Vol: {gjr_vol*100:.2f}% | Signal: {signal_raw}")

        if gjr_vol > MAX_ALLOWED_GJR_GARCH_VOL:
            print(f"     [SKIP] Volatility {gjr_vol*100:.2f}% exceeds ceiling.")
            continue
        if "NEUTRAL" in signal_raw.upper():
            continue
        if symbol in open_symbols:
            continue
        if bar_time is not None and _last_signal_bar.get(symbol) == bar_time:
            continue
        if spread_pips > MAX_SPREAD_PIPS:
            print(f"     [SKIP] Spread {spread_pips} pips exceeds limit.")
            continue
        if open_count >= MAX_CONCURRENT_OPEN_TRADES:
            break

        if execute_order(symbol, signal_raw, base_lots, gjr_vol, current_close):
            open_count += 1
            open_symbols.append(symbol)
            if bar_time is not None:
                _last_signal_bar[symbol] = bar_time

    print("-" * 66)
    print("[COMPLETE] Cycle finished successfully.")


# ----------------------------------------------------------------------
# 10. Entry Point & Scheduler
# ----------------------------------------------------------------------
def seconds_until_next_candle(buffer_seconds=5) -> float:
    now = datetime.now(timezone.utc)
    minutes_to_next = 1 - (now.minute % 1) # Scan every minute for fast M1 scalping
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
