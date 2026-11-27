import numpy as np
import pandas as pd


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    work["ema20"] = work["close"].ewm(span=20, adjust=False).mean()
    work["ema50"] = work["close"].ewm(span=50, adjust=False).mean()

    delta = work["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    work["rsi"] = 100.0 - (100.0 / (1.0 + rs))

    high_low = work["high"] - work["low"]
    high_close = (work["high"] - work["close"].shift()).abs()
    low_close = (work["low"] - work["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    work["atr14"] = tr.rolling(14).mean()

    up_move = work["high"].diff()
    down_move = -work["low"].diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_di = 100.0 * pd.Series(plus_dm, index=work.index).rolling(14).mean() / work["atr14"].replace(0, np.nan)
    minus_di = 100.0 * pd.Series(minus_dm, index=work.index).rolling(14).mean() / work["atr14"].replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    work["plus_di"] = plus_di
    work["minus_di"] = minus_di
    work["adx14"] = dx.rolling(14).mean()
    return work


def generate_ensemble_signal(df: pd.DataFrame, min_adx: float = 25.0, df_h4: pd.DataFrame | None = None) -> dict:
    eff_adx = max(float(min_adx), 25.0)
    if df.empty or len(df) < 60:
        return {"signal": "HOLD", "confidence": 0.0, "atr14": 0.0, "adx14": 0.0, "rsi": 50.0, "reason": "Insufficient bars"}

    ind = compute_indicators(df).iloc[-2]
    close_px = float(ind["close"])
    ema20 = float(ind["ema20"])
    ema50 = float(ind["ema50"])
    adx = float(ind["adx14"]) if pd.notna(ind["adx14"]) else 0.0
    rsi = float(ind["rsi"]) if pd.notna(ind["rsi"]) else 50.0
    atr = float(ind["atr14"]) if pd.notna(ind["atr14"]) else 0.0
    p_di = float(ind["plus_di"]) if pd.notna(ind["plus_di"]) else 0.0
    m_di = float(ind["minus_di"]) if pd.notna(ind["minus_di"]) else 0.0

    if adx < eff_adx or atr <= 0:
        return {"signal": "HOLD", "confidence": 0.0, "atr14": atr, "adx14": round(adx, 1), "rsi": round(rsi, 1), "reason": f"Weak trend (ADX={adx:.1f} < {eff_adx:.1f})"}

    h4_bias = "BOTH"
    if df_h4 is not None and not df_h4.empty and len(df_h4) >= 55:
        h4_ind = compute_indicators(df_h4).iloc[-2]
        h4_bias = "BULL" if float(h4_ind["close"]) > float(h4_ind["ema50"]) else "BEAR"

    if close_px > ema20 > ema50 and p_di > m_di and 50.0 <= rsi <= 66.0:
        if h4_bias == "BEAR":
            return {"signal": "HOLD", "confidence": 0.0, "atr14": atr, "adx14": round(adx, 1), "rsi": round(rsi, 1), "reason": "Blocked: H1 Bullish vs H4 Bearish"}
        conf = min(0.95, round(0.60 + (adx - eff_adx) * 0.015, 2))
        return {"signal": "BUY", "confidence": conf, "atr14": atr, "adx14": round(adx, 1), "rsi": round(rsi, 1), "reason": f"H1+H4 Bullish (ADX={adx:.1f}, +DI>{m_di:.0f}, RSI={rsi:.1f})"}

    if close_px < ema20 < ema50 and m_di > p_di and 34.0 <= rsi <= 50.0:
        if h4_bias == "BULL":
            return {"signal": "HOLD", "confidence": 0.0, "atr14": atr, "adx14": round(adx, 1), "rsi": round(rsi, 1), "reason": "Blocked: H1 Bearish vs H4 Bullish"}
        conf = min(0.95, round(0.60 + (adx - eff_adx) * 0.015, 2))
        return {"signal": "SELL", "confidence": conf, "atr14": atr, "adx14": round(adx, 1), "rsi": round(rsi, 1), "reason": f"H1+H4 Bearish (ADX={adx:.1f}, -DI>{p_di:.0f}, RSI={rsi:.1f})"}

    return {"signal": "HOLD", "confidence": 0.0, "atr14": atr, "adx14": round(adx, 1), "rsi": round(rsi, 1), "reason": f"No alignment (ADX={adx:.1f}, +DI={p_di:.0f}/-DI={m_di:.0f}, RSI={rsi:.1f})"}
