import numpy as np
import pandas as pd


def summarize_closed_deals(deals_df: pd.DataFrame, initial_balance: float = 10000.0) -> dict:
    if deals_df.empty:
        return {"trades": 0, "win_rate_pct": 0.0, "profit_factor": 0.0, "net_pnl": 0.0, "sharpe": 0.0, "max_dd_pct": 0.0}

    pnls = deals_df["profit"].astype(float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    win_rate = round(len(wins) / max(len(pnls), 1) * 100.0, 2)
    gross_profit = float(wins.sum())
    gross_loss = abs(float(losses.sum()))
    pf = round(gross_profit / max(gross_loss, 1e-6), 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

    equity_curve = initial_balance + pnls.cumsum()
    peaks = equity_curve.cummax()
    dd_pct = round(float(((peaks - equity_curve) / peaks.replace(0, 1)).max() * 100.0), 2)
    sharpe = round(float(pnls.mean() / max(pnls.std(), 1e-6) * np.sqrt(252)), 2) if len(pnls) > 1 else 0.0

    return {
        "trades": len(pnls),
        "win_rate_pct": win_rate,
        "profit_factor": pf,
        "net_pnl": round(float(pnls.sum()), 2),
        "sharpe": sharpe,
        "max_dd_pct": dd_pct,
    }
