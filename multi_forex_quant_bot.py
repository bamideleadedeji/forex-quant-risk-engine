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

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]
MAGIC_NUMBER = 234000
LOG_FILE = "trade_log.csv"

# Pair-specific lot sizing optimized for demo efficiency
LOT_SIZES = {
    "EURUSD": 0.10,
    "GBPUSD": 0.10,
    "USDJPY": 0.10,
    "AUDUSD": 0.10
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

def calculate_gjr_garch_volatility(closes: np.ndarray) -> float:
    """
    Computes rolling conditional volatility approximation (GJR-GARCH proxy)
    with asymmetric shock weighting.
    """
    returns = np.diff(np.log(closes))
    if len(returns) < 10:
        return 0.001
    
    negative_returns = returns[returns < 0]
    pos_var = np.var(returns[returns >= 0]) if len(returns[returns >= 0]) > 0 else 0.0001
    neg_var = np.var(negative_returns) if len(negative_returns) > 0 else 0.0001
    
    conditional_vol = np.sqrt(0.1 * np.var(returns) + 0.5 * pos_var + 0.4 * neg_var)
    return float(conditional_vol)

def check_multi_timeframe_cascade(symbol: str) -> str:
    """
    Evaluates multi-timeframe trend alignment across M5, M15, and M30 using EMA cross.
    Returns 'BUY', 'SELL', or 'HOLD'.
    """
    timeframes = {
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30
    }
    
    trends = []
    for tf_name, tf_const in timeframes.items():
        rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, 20)
        if rates is None or len(rates) < 20:
            return "HOLD"
        
        df = pd.DataFrame(rates)
        ema_fast = df['close'].ewm(span=9, adjust=False).mean().iloc[-1]
        ema_slow = df['close'].ewm(span=21, adjust=False).mean().iloc[-1]
        
        if ema_fast > ema_slow:
            trends.append("BUY")
        elif ema_fast < ema_slow:
            trends.append("SELL")
        else:
            trends.append("HOLD")
            
    if all(t == "BUY" for t in trends):
        return "BUY"
    elif all(t == "SELL" for t in trends):
        return "SELL"
    
    return "HOLD"

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
    lot_size = LOT_SIZES.get(symbol, 0.10)
    
    # Dynamic pip scaling for JPY pairs vs Standard pairs
    pip_multiplier = 0.01 if "JPY" in symbol else 0.0001
    sl_distance = 30 * pip_multiplier  # 30 pips Stop Loss
    tp_distance = 60 * pip_multiplier  # 60 pips Take Profit

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
        "deviation": 20,
        "magic": MAGIC_NUMBER,
        "comment": "QuantEngine GJR-GARCH",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,  # IOC ensures immediate fill without FOK rejection
    }

    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        err_msg = f"Order execution failed for {symbol} ({action}), retcode={result.retcode}"
        log_message(err_msg)
        log_trade_to_csv(symbol, action, lot_size, price, f"rejected_{result.retcode}")
    else:
        success_msg = f"Order filled successfully: {action} {lot_size} {symbol} @ {price}"
        log_message(success_msg)
        log_trade_to_csv(symbol, action, lot_size, price, "filled")

def main_trading_loop():
    log_message("Starting automated multi-forex GJR-GARCH execution loop...")
    
    if not initialize_mt5():
        return

    try:
        while True:
            if not mt5.terminal_info().connected:
                log_message("MT5 terminal disconnected. Attempting reconnection...")
                mt5.shutdown()
                time.sleep(10)
                if not initialize_mt5():
                    continue

            for symbol in SYMBOLS:
                rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 50)
                if rates is None or len(rates) < 50:
                    continue

                closes = np.array([r['close'] for r in rates])
                volatility = calculate_gjr_garch_volatility(closes)
                
                # Volatility gate to filter out dead market noise
                if volatility < 0.00005:
                    continue

                signal = check_multi_timeframe_cascade(symbol)
                if signal in ["BUY", "SELL"]:
                    log_message(f"Signal generated for {symbol}: {signal} (Volatility: {volatility:.5f})")
                    execute_market_order(symbol, signal)

            # 60-second scanning interval
            time.sleep(60)

    except KeyboardInterrupt:
        log_message("Bot stopped manually by user.")
    except Exception as e:
        err_trace = traceback.format_exc()
        log_message(f"CRITICAL ENGINE CRASH: {e}\n{err_trace}")
        # Emergency Telegram Watchdog trigger on unhandled exception
        send_telegram_alert(f"🚨 *Engine Crashed*\nException: `{str(e)}`")
    finally:
        mt5.shutdown()
        log_message("MT5 connection closed cleanly.")

if __name__ == "__main__":
    main_trading_loop()
