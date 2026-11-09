import os
import time
import logging
import traceback
from datetime import datetime, timezone
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from dotenv import load_dotenv

load_dotenv()
try:
    from telegram_alert import send_telegram_alert
except ImportError:
    def send_telegram_alert(msg): pass

MT5_ACCOUNT = 81908061
MT5_SERVER = "Exness-MT5Trial10"
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "").strip()

# Explicitly use Exness .m suffix symbols confirmed for this demo account
SYMBOLS = ["EURUSDm", "GBPUSDm", "USDJPYm", "AUDUSDm", "USDCADm", "USDCHFm"]
MAGIC_NUMBER = 234000
LOG_FILE = "trade_log.csv"
LOT_SIZES = {
    "EURUSDm": 0.10, "GBPUSDm": 0.10, "USDJPYm": 0.10, 
    "AUDUSDm": 0.10, "USDCADm": 0.10, "USDCHFm": 0.10
}

logging.basicConfig(
    filename="bot_execution.log", 
    level=logging.INFO, 
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def log_message(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] {msg}")
    logging.info(msg)

def initialize_mt5():
    if not mt5.initialize(): 
        log_message("MT5 initialization failed.")
        return False
    if MT5_PASSWORD:
        authorized = mt5.login(MT5_ACCOUNT, password=MT5_PASSWORD, server=MT5_SERVER)
    else:
        authorized = mt5.login(MT5_ACCOUNT, server=MT5_SERVER)
    if not authorized:
        log_message(f"MT5 login failed: {mt5.last_error()}")
        return False
    log_message(f"Successfully connected to Exness Account #{MT5_ACCOUNT}")
    return True

def log_trade_to_csv(symbol, order_type, volume, price, result_status):
    row = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), 
        "symbol": symbol, 
        "type": order_type, 
        "volume": volume, 
        "price": price, 
        "result": result_status
    }
    df = pd.DataFrame([row])
    if os.path.exists(LOG_FILE):
        df.to_csv(LOG_FILE, mode='a', header=False, index=False)
    else:
        df.to_csv(LOG_FILE, mode='w', header=True, index=False)

def execute_market_order(symbol, action):
    # Ensure symbol is selected in Market Watch
    if not mt5.symbol_select(symbol, True):
        log_message(f"Failed to select symbol {symbol}")
        return

    tick = mt5.symbol_info_tick(symbol)
    if not tick: 
        log_message(f"Failed to get tick for {symbol}")
        return
        
    price = tick.ask if action == "BUY" else tick.bid
    order_type = mt5.ORDER_BUY if action == "BUY" else mt5.ORDER_SELL
    lot = LOT_SIZES.get(symbol, 0.10)
    
    pip = 0.01 if "JPY" in symbol else 0.0001
    sl = price - (25 * pip) if action == "BUY" else price + (25 * pip)
    tp = price + (50 * pip) if action == "BUY" else price - (50 * pip)
    
    req = {
        "action": mt5.TRADE_ACTION_DEAL, 
        "symbol": symbol, 
        "volume": lot, 
        "type": order_type,
        "price": price, 
        "sl": sl, 
        "tp": tp, 
        "deviation": 30, 
        "magic": MAGIC_NUMBER,
        "comment": "QuantEngine Forced", 
        "type_time": mt5.ORDER_TIME_GTC, 
        "type_filling": mt5.ORDER_FILLING_IOC
    }
    
    res = mt5.order_send(req)
    if res.retcode != mt5.TRADE_RETCODE_DONE:
        log_message(f"Order failed {symbol} {action}: retcode={res.retcode}")
        log_trade_to_csv(symbol, action, lot, price, f"rejected_{res.retcode}")
    else:
        msg = f"ORDER FILLED: {action} {lot} {symbol} @ {price}"
        log_message(msg)
        log_trade_to_csv(symbol, action, lot, price, "filled")
        send_telegram_alert(msg)

def main():
    if not initialize_mt5():
        return
    
    log_message("Starting forced execution trading loop...")

    while True:
        if not mt5.terminal_info().connected:
            log_message("Terminal disconnected. Reconnecting...")
            mt5.shutdown()
            time.sleep(5)
            if not initialize_mt5():
                continue

        acc = mt5.account_info()
        pos = mt5.positions_get()
        pos_count = len(pos) if pos else 0
        equity = acc.equity if acc else 0.0
        balance = acc.balance if acc else 0.0

        print("\n" + "="*66)
        print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] FORCED EXECUTION ENGINE (Exness MT5)")
        print("="*66)
        print(f"- Equity: ${equity:,.2f} | Balance: ${balance:,.2f} | Open Positions: {pos_count} / 3")
        print("-" * 66)
        print("FX basket quotes & signals:")

        for idx, sym in enumerate(SYMBOLS):
            tick = mt5.symbol_info_tick(sym)
            if not tick:
                print(f"  └─ {sym:<9} | Tick unavailable in Market Watch")
                continue
            
            signal = "BUY" if idx % 2 == 0 else "SELL"
            pip_scale = 0.01 if "JPY" in sym else 0.0001
            spread = round((tick.ask - tick.bid) / pip_scale, 1)
            
            print(f"  └─ {sym:<9} | Bid: {tick.bid:<9} | Ask: {tick.ask:<9} | Spread: {spread:>4.1f} pips | Signal: {signal}")
            
            if pos_count < 3:
                execute_market_order(sym, signal)
                pos_count += 1

        print("-" * 66)
        print("[COMPLETE] Cycle finished.")
        time.sleep(15)

if __name__ == "__main__":
    main()
