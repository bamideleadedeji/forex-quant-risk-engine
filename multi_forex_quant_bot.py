import os
import time
import logging
import traceback
from datetime import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from dotenv import load_dotenv

# Load local environment variables (.env)
load_dotenv()

# Import emergency Telegram watchdog
from telegram_alert import send_telegram_alert

# --- CONFIGURATION & ACCOUNT PARAMETERS ---
MT5_ACCOUNT = 81908061
MT5_SERVER = "Exness-MT5Trial10"
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "").strip()

BASE_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF"]
MAGIC_NUMBER = 234000
LOG_FILE = "trade_log.csv"

LOT_SIZES = {
    "EURUSD": 0.10,
    "GBPUSD": 0.10,
    "USDJPY": 0.10,
    "AUDUSD": 0.10,
    "USDCAD": 0.10,
    "USDCHF": 0.10
}

# Configure local logging
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
    info = mt5.symbol_info(base_symbol)
    if info is not None:
        if not info.visible:
            mt5.symbol_select(base_symbol, True)
        return base_symbol

    suffixes = [".m", "pro", ".pro", ".raw", ".s"]
    for sfx in suffixes:
        candidate = f"{base_symbol}{sfx}"
        info = mt5.symbol_info(candidate)
        if info is not None:
            if not info.visible:
                mt5.symbol_select(candidate, True)
            return candidate

    all_symbols = mt5.symbols_get()
    if all_symbols:
        for sym in all_symbols:
            if base_symbol in sym.name:
                if not sym.visible:
                    mt5.symbol_select(sym.name, True)
                return sym.name

    return base_symbol

def log_trade_to_csv(symbol, order_type, volume, price, result_status):
    """Appends trade execution telemetry to trade_log.csv for audit tracking."""
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
    """Computes rolling volatility approximation."""
    returns = np.diff(np.log(closes))
    if len(returns) < 5:
        return 0.001
    return float(np.std(returns))

def check_signal_with_momentum(df_m5, df_m15) -> str:
    """
    Evaluates trend alignment across M5 and M15 with momentum fallback 
    to guarantee active execution during demo testing.
    """
    ema_fast_5 = df_m5['close'].ewm(span=5, adjust=False).mean().iloc[-1]
    ema_slow_5 = df_m5['close'].ewm(span=12, adjust=False).mean().iloc[-1]
    
    ema_fast_15 = df_m15['close'].ewm(span=5, adjust=False).mean().iloc[-1]
    ema_slow_15 = df_m15['close'].ewm(span=12, adjust=False).mean().iloc[-1]
    
    if ema_fast_5 > ema_slow_5 and ema_fast_15 > ema_slow_15:
        return "BUY"
    elif ema_fast_5 < ema_slow_5 and ema_fast_15 < ema_slow_15:
        return "SELL"
    
    # Active momentum fallback to prevent indefinite NEUTRAL stagnation
    price_change = df_m5['close'].iloc[-1] - df_m5['close'].iloc[-3]
    if price_change > 0.00008:
        return "BUY"
    elif price_change < -0.00008:
        return "SELL"
        
    return "NEUTRAL"

def execute_market_order(symbol: str, action: str):
    """
    Executes market order on MT5 with dynamic JPY pip scaling 
    and IOC filling mode to eliminate broker rejections.
    """
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None or not symbol_info.visible:
        if not mt5.symbol_select(symbol, True):
            log_message(f"Failed to select symbol {symbol}")
            return

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        log_message(f"Failed to get tick for {symbol}")
        return

    price = tick.ask if action == "BUY" else tick.bid
    order_type = mt5.ORDER_BUY if action == "BUY" else mt5.ORDER_SELL
    
    base_clean = symbol.replace(".m", "").replace("pro", "").replace(".raw", "").replace(".s", "")
    lot_size = LOT_SIZES.get(base_clean, 0.10)
    
    # Dynamic pip scaling for JPY pairs vs Standard pairs
    pip_multiplier = 0.01 if "JPY" in symbol else 0.0001
    sl_distance = 25 * pip_multiplier  # 25 pips Stop Loss
    tp_distance = 50 * pip_multiplier  # 50 pips Take Profit

    sl = price - sl_distance if action == "BUY" else price + sl_distance
    tp = price + tp_distance if action == "BUY" else price - tp_distance

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
        "comment": "QuantEngine Pro",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        err_msg = f"Order execution failed for {symbol} ({action}), retcode={result.retcode}"
        log_message(err_msg)
        log_trade_to_csv(symbol, action, lot_size, price, f"rejected_{result.retcode}")
    else:
        success_msg = f"ORDER FILLED: {action} {lot_size} {symbol} @ {price}"
        log_message(success_msg)
        log_trade_to_csv(symbol, action, lot_size, price, "filled")
        send_telegram_alert(success_msg)

def main_trading_loop():
    log_message("Starting professional automated execution engine...")
    
    if not initialize_mt5():
        return

    resolved_symbols = {}
    for base in BASE_SYMBOLS:
        actual = resolve_exness_symbol(base)
        resolved_symbols[base] = actual
        log_message(f"Mapped base symbol '{base}' -> broker symbol '{actual}'")

    try:
        while True:
            if not mt5.terminal_info().connected:
                log_message("MT5 terminal disconnected. Reconnecting...")
                mt5.shutdown()
                time.sleep(5)
                if not initialize_mt5():
                    continue

            for base, symbol in resolved_symbols.items():
                rates_m5 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 20)
                rates_m15 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 20)
                
                if rates_m5 is None or rates_m15 is None or len(rates_m5) < 20 or len(rates_m15) < 20:
                    continue

                df_m5 = pd.DataFrame(rates_m5)
                df_m15 = pd.DataFrame(rates_m15)
                
                closes = df_m15['close'].values
                volatility = calculate_volatility(closes)
                
                signal = check_signal_with_momentum(df_m5, df_m15)
                log_message(f"Scanned {symbol} | Vol: {volatility:.5f} | Signal: {signal}")

                if signal in ["BUY", "SELL"]:
                    execute_market_order(symbol, signal)

            # 15-second scanning interval for active demo verification
            time.sleep(15)

    except KeyboardInterrupt:
        log_message("Bot stopped manually by user.")
    except Exception as e:
        err_trace = traceback.format_exc()
        log_message(f"CRITICAL ENGINE CRASH: {e}\n{err_trace}")
        send_telegram_alert(f"🚨 *Engine Crashed*\nException: `{str(e)}`")
    finally:
        mt5.shutdown()
        log_message("MT5 connection closed cleanly.")

if __name__ == "__main__":
    main_trading_loop()
