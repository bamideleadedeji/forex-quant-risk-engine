import os
import time
import logging
import traceback
from datetime import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from dotenv import load_dotenv

load_dotenv()
from telegram_alert import send_telegram_alert

# --- CONFIGURATION & ACCOUNT PARAMETERS ---
MT5_ACCOUNT = 81908061
MT5_SERVER = "Exness-MT5Trial10"
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "").strip()

BASE_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]
MAGIC_NUMBER = 234000
LOG_FILE = "trade_log.csv"

LOT_SIZES = {
    "EURUSD": 0.10,
    "GBPUSD": 0.10,
    "USDJPY": 0.10,
    "AUDUSD": 0.10
}

logging.basicConfig(
    filename="bot_execution.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

def log_message(msg: str):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")
    logging.info(msg)

def initialize_mt5():
    """Initializes MetaTrader 5 and logs into the Exness demo account."""
    if not mt5.initialize():
        err = f"MT5 Initialization failed, error code = {mt5.last_error()}"
        log_message(err)
        send_telegram_alert(f"🚨 *Critical Failure*\n{err}")
        return False

    if MT5_PASSWORD:
        authorized = mt5.login(MT5_ACCOUNT, password=MT5_PASSWORD, server=MT5_SERVER)
    else:
        authorized = mt5.login(MT5_ACCOUNT, server=MT5_SERVER)

    if not authorized:
        err = f"Failed to connect to Exness account #{MT5_ACCOUNT}, error code = {mt5.last_error()}"
        log_message(err)
        send_telegram_alert(f"🚨 *Critical Login Failure*\n{err}")
        mt5.shutdown()
        return False

    log_message(f"Successfully connected to Exness Account #{MT5_ACCOUNT} on server {MT5_SERVER}")
    return True

def resolve_exness_symbol(base_symbol: str) -> str:
    """
    Auto-detects the exact broker symbol name on Exness 
    (handling suffixes like .m, .pro, .raw or no suffix).
    """
    # Test exact match first
    info = mt5.symbol_info(base_symbol)
    if info is not None:
        if not info.visible:
            mt5.symbol_select(base_symbol, True)
        return base_symbol

    # Test common Exness suffixes
    suffixes = [".m", "pro", ".pro", ".raw", ".s"]
    for sfx in suffixes:
        candidate = f"{base_symbol}{sfx}"
        info = mt5.symbol_info(candidate)
        if info is not None:
            if not info.visible:
                mt5.symbol_select(candidate, True)
            return candidate

    # Search all available terminal symbols as fallback
    all_symbols = mt5.symbols_get()
    if all_symbols:
        for sym in all_symbols:
            if base_symbol in sym.name:
                if not sym.visible:
                    mt5.symbol_select(sym.name, True)
                return sym.name

    return base_symbol

def log_trade_to_csv(symbol, order_type, volume, price, result_status):
    row = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": symbol,
        "type": order_type,
        "volume": volume,
        "price": price,
        "result": result_status
    }
    df_new = pd.DataFrame([row])
    if os.path.exists(LOG_FILE):
        df_new.to_csv(LOG_FILE, mode='a', header=False, index=False)
    else:
        df_new.to_csv(LOG_FILE, mode='w', header=True, index=False)

def calculate_volatility(closes: np.ndarray) -> float:
    returns = np.diff(np.log(closes))
    if len(returns) < 5:
        return 0.001
    return float(np.std(returns))

def check_signal(symbol: str) -> str:
    """Evaluates M5 and M15 trend alignment with verbose logging."""
    rates_m5 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 15)
    rates_m15 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 15)
    
    if rates_m5 is None or rates_m15 is None or len(rates_m5) < 15 or len(rates_m15) < 15:
        return "HOLD"
    
    df_m5 = pd.DataFrame(rates_m5)
    df_m15 = pd.DataFrame(rates_m15)
    
    ema_fast_5 = df_m5['close'].ewm(span=5, adjust=False).mean().iloc[-1]
    ema_slow_5 = df_m5['close'].ewm(span=12, adjust=False).mean().iloc[-1]
    
    ema_fast_15 = df_m15['close'].ewm(span=5, adjust=False).mean().iloc[-1]
    ema_slow_15 = df_m15['close'].ewm(span=12, adjust=False).mean().iloc[-1]
    
    if ema_fast_5 > ema_slow_5 and ema_fast_15 > ema_slow_15:
        return "BUY"
    elif ema_fast_5 < ema_slow_5 and ema_fast_15 < ema_slow_15:
        return "SELL"
        
    return "HOLD"

def execute_market_order(symbol: str, action: str):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        log_message(f"Failed to get tick for resolved symbol {symbol}")
        return

    price = tick.ask if action == "BUY" else tick.bid
    order_type = mt5.ORDER_BUY if action == "BUY" else mt5.ORDER_SELL
    lot_size = LOT_SIZES.get(symbol.replace(".m","").replace("pro",""), 0.10)
    
    pip_multiplier = 0.01 if "JPY" in symbol else 0.0001
    sl = price - (25 * pip_multiplier) if action == "BUY" else price + (25 * pip_multiplier)
    tp = price + (50 * pip_multiplier) if action == "BUY" else price - (50 * pip_multiplier)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot_size,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 30,
        "magic": MAGIC_NUMBER,
        "comment": "QuantEngine Live",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        err_msg = f"Order failed for {symbol} ({action}), retcode={result.retcode}"
        log_message(err_msg)
        log_trade_to_csv(symbol, action, lot_size, price, f"rejected_{result.retcode}")
    else:
        success_msg = f"ORDER FILLED: {action} {lot_size} {symbol} @ {price}"
        log_message(success_msg)
        log_trade_to_csv(symbol, action, lot_size, price, "filled")
        send_telegram_alert(success_msg)

def main_loop():
    log_message("Starting self-healing automated execution engine...")
    if not initialize_mt5():
        return

    # Resolve exact broker symbol names on startup
    resolved_symbols = {}
    for base in BASE_SYMBOLS:
        actual = resolve_exness_symbol(base)
        resolved_symbols[base] = actual
        log_message(f"Mapped base symbol '{base}' -> broker symbol '{actual}'")

    try:
        while True:
            if not mt5.terminal_info().connected:
                log_message("MT5 disconnected. Reconnecting...")
                mt5.shutdown()
                time.sleep(5)
                if not initialize_mt5():
                    continue

            for base, symbol in resolved_symbols.items():
                rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 30)
                if rates is None or len(rates) < 30:
                    log_message(f"Warning: Could not fetch rates for {symbol}")
                    continue

                closes = np.array([r['close'] for r in rates])
                vol = calculate_volatility(closes)
                
                signal = check_signal(symbol)
                log_message(f"Scanned {symbol} | Vol: {vol:.5f} | Signal: {signal}")

                if signal in ["BUY", "SELL"]:
                    execute_market_order(symbol, signal)

            time.sleep(15)

    except KeyboardInterrupt:
        log_message("Bot stopped by user.")
    except Exception as e:
        err_trace = traceback.format_exc()
        log_message(f"CRITICAL CRASH: {e}\n{err_trace}")
        send_telegram_alert(f"🚨 *Engine Crashed*\n`{str(e)}`")
    finally:
        mt5.shutdown()
        log_message("MT5 closed cleanly.")

if __name__ == "__main__":
    main_loop()
