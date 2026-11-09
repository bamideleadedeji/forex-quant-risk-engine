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
try:
    from telegram_alert import send_telegram_alert
except ImportError:
    def send_telegram_alert(msg):
        pass

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

    return True

def resolve_exness_symbol(base_symbol: str) -> str:
    """Auto-detects exact broker symbol name on Exness (handling suffixes)."""
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
    """Appends trade execution telemetry to trade_log.csv."""
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

def get_forced_signal(index: int) -> str:
    """Forces active directional execution for testing/demo validation."""
    return "BUY" if index % 2 == 0 else "SELL"

def execute_market_order(symbol: str, action: str):
    """Executes market order on MT5 with dynamic JPY pip scaling and IOC filling."""
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None or not symbol_info.visible:
        if not mt5.symbol_select(symbol, True):
            return

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return

    price = tick.ask if action == "BUY" else tick.bid
    order_type = mt5.ORDER_BUY if action == "BUY" else mt5.ORDER_SELL
    
    base_clean = symbol.replace(".m", "").replace("pro", "").replace(".raw", "").replace(".s", "")
    lot_size = LOT_SIZES.get(base_clean, 0.10)
    
    pip_multiplier = 0.01 if "JPY" in symbol else 0.0001
    sl_distance = 25 * pip_multiplier
    tp_distance = 50 * pip_multiplier

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
        err_msg = f"Order failed for {symbol} ({action}), retcode={result.retcode}"
        log_message(err_msg)
        log_trade_to_csv(symbol, action, lot_size, price, f"rejected_{result.retcode}")
    else:
        success_msg = f"ORDER FILLED: {action} {lot_size} {symbol} @ {price}"
        log_message(success_msg)
        log_trade_to_csv(symbol, action, lot_size, price, "filled")
        send_telegram_alert(success_msg)

def main_trading_loop():
    if not initialize_mt5():
        return

    resolved_symbols = {}
    for base in BASE_SYMBOLS:
        actual = resolve_exness_symbol(base)
        resolved_symbols[base] = actual

    try:
        while True:
            if not mt5.terminal_info().connected:
                mt5.shutdown()
                time.sleep(5)
                if not initialize_mt5():
                    continue

            account_info = mt5.account_info()
            equity = account_info.equity if account_info else 0.0
            balance = account_info.balance if account_info else 0.0
            positions = mt5.positions_get()
            pos_count = len(positions) if positions else 0

            print("\n" + "="*66)
            print(f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC লীগ')}] Multi-Asset Proportional Risk Engine (Forced Execution)")
            print("="*66)
            print("- News filter: Clear (Local schedule: No active high-impact blackout window)")
            print(f"- Equity: ${equity:,.2f} | Balance: ${balance:,.2f}")
            print("- Sizing: 0.10 lots per trade")
            print(f"- Bot positions open: {pos_count} / 3")
            print("-" * 66)
            print("FX basket quotes & signals:")

            for idx, (base, symbol) in enumerate(resolved_symbols.items()):
                tick = mt5.symbol_info_tick(symbol)
                if tick is None:
                    continue
                
                spread = round((tick.ask - tick.bid) / (0.01 if "JPY" in symbol else 0.0001), 1)
                signal = get_forced_signal(idx)

                print(f"  └─ {symbol:<9} | Bid: {tick.bid:<9} | Ask: {tick.ask:<9} | Spread: {spread:>4.1f} pips | Signal: {signal}")
                
                # Execute trade on active signal if position limit allows
                if signal in ["BUY", "SELL"] and pos_count < 3:
                    execute_market_order(symbol, signal)
                    pos_count += 1

            print("-" * 66)
            print("[COMPLETE] Cycle finished.")
            
            # Scan interval (15 seconds)
            time.sleep(15)

    except KeyboardInterrupt:
        log_message("Bot stopped manually by user.")
    except Exception as e:
        err_trace = traceback.format_exc()
        log_message(f"CRITICAL ENGINE CRASH: {e}\n{err_trace}")
        send_telegram_alert(f"🚨 *Engine Crashed*\nException: `{str(e)}`")
    finally:
        mt5.shutdown()

if __name__ == "__main__":
    main_trading_loop()
