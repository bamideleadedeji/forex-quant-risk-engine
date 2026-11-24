import math


def compute_dynamic_lot_size(equity: float, sl_distance: float, tick_size: float, tick_value: float, risk_pct: float, vol_min: float = 0.01, vol_max: float = 10.0, vol_step: float = 0.01) -> float:
    if equity <= 0 or sl_distance <= 0 or tick_size <= 0 or tick_value <= 0:
        return vol_min
    risk_dollars = equity * risk_pct
    loss_per_lot = (sl_distance / tick_size) * tick_value
    raw_lots = risk_dollars / max(loss_per_lot, 1e-6)
    stepped = math.floor(raw_lots / vol_step) * vol_step
    return round(max(vol_min, min(vol_max, stepped)), 2)
