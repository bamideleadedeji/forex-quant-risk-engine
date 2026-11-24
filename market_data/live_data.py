import time
import pandas as pd
import MetaTrader5 as mt5


def connect_mt5_with_retry(cfg: dict, wait_seconds: int = 15) -> bool:
    acc_cfg = cfg["account"]
    ok = mt5.initialize(
        path=acc_cfg["mt5_path"],
        login=int(acc_cfg["login"]),
        password=cfg["mt5_password"],
        server=acc_cfg["server"],
    ) if cfg.get("mt5_password") else mt5.initialize()
    if not ok:
        return False
    for _ in range(wait_seconds):
        term = mt5.terminal_info()
        if term and term.connected and term.trade_allowed:
            return True
        time.sleep(1)
    return False


def fetch_ohlcv_bars(symbol: str, timeframe=mt5.TIMEFRAME_H1, count: int = 150) -> pd.DataFrame:
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if rates is None or len(rates) < 60:
        return pd.DataFrame()
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df
