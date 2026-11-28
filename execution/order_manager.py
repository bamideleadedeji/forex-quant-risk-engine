import MetaTrader5 as mt5


def resolve_filling_mode(symbol_info) -> int:
    filling = getattr(symbol_info, "filling_mode", 0)
    if filling & 1:
        return mt5.ORDER_FILLING_FOK
    if filling & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def compute_intraday_sl_tp(symbol: str, signal: str, exec_px: float, atr14: float, s_info) -> tuple[float, float, float]:
    """Bounds SL/TP to realistic intraday London/NY range (0.22% SL / 0.44% TP, capped by ATR)."""
    digits = s_info.digits
    min_stop = (s_info.trade_stops_level + 10) * s_info.point
    pct_sl = exec_px * 0.0022
    atr_sl = atr14 * 1.35 if atr14 > 0 else pct_sl
    sl_dist = max(min(pct_sl, atr_sl) if atr_sl > min_stop else pct_sl, min_stop)
    tp_dist = sl_dist * 2.0
    sl_px = round(exec_px - sl_dist if signal == "BUY" else exec_px + sl_dist, digits)
    tp_px = round(exec_px + tp_dist if signal == "BUY" else exec_px - tp_dist, digits)
    return sl_dist, sl_px, tp_px


def dispatch_protected_order(symbol: str, signal: str, lots: float, atr14: float, risk_cfg: dict, magic_number: int) -> dict:
    s_info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if not s_info or not tick:
        return {"status": "ERROR", "reason": "Missing symbol/tick info"}

    exec_px = tick.ask if signal == "BUY" else tick.bid
    _, sl_px, tp_px = compute_intraday_sl_tp(symbol, signal, exec_px, atr14, s_info)

    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(lots),
        "type": mt5.ORDER_TYPE_BUY if signal == "BUY" else mt5.ORDER_TYPE_SELL,
        "price": float(exec_px),
        "sl": sl_px,
        "tp": tp_px,
        "deviation": 20,
        "magic": int(magic_number),
        "comment": "FQRE_v32_ProfitLock",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": resolve_filling_mode(s_info),
    }
    res = mt5.order_send(req)
    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
        return {"status": "FILLED", "ticket": res.order, "price": exec_px, "sl": sl_px, "tp": tp_px, "lots": lots}
    code = res.retcode if res else "NONE"
    return {"status": f"REJECTED_{code}", "ticket": 0, "price": exec_px, "sl": sl_px, "tp": tp_px, "lots": lots}


def manage_open_positions(positions: list, logger) -> list[str]:
    """Early Profit Lock: +0.5R -> Break-Even (+2p) | +1.0R -> Lock +0.5R | +1.5R -> Lock +1.0R."""
    events = []
    for p in positions:
        s_info = mt5.symbol_info(p.symbol)
        tick = mt5.symbol_info_tick(p.symbol)
        if not s_info or not tick or p.tp <= 0 or p.sl <= 0:
            continue
        digits = s_info.digits
        pip = 0.01 if "JPY" in p.symbol else 0.0001
        if p.type == 0:
            r_dist = (p.tp - p.price_open) / 2.0
            gain = tick.bid - p.price_open
            target_sl = None
            label = ""
            if r_dist > 0 and gain >= 1.5 * r_dist:
                target_sl = round(p.price_open + 1.0 * r_dist, digits); label = "+1.0R locked"
            elif r_dist > 0 and gain >= 1.0 * r_dist:
                target_sl = round(p.price_open + 0.5 * r_dist, digits); label = "+0.5R locked"
            elif r_dist > 0 and gain >= 0.5 * r_dist:
                target_sl = round(p.price_open + 2 * pip, digits); label = "BREAK-EVEN (+2p)"
            if target_sl and target_sl > p.sl + pip:
                res = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": p.ticket, "symbol": p.symbol, "sl": target_sl, "tp": p.tp})
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    msg = f"{p.symbol} #{p.ticket} TRAILED SL -> {target_sl} ({label})"
                    logger.info(msg); events.append(msg)
        else:
            r_dist = (p.price_open - p.tp) / 2.0
            gain = p.price_open - tick.ask
            target_sl = None
            label = ""
            if r_dist > 0 and gain >= 1.5 * r_dist:
                target_sl = round(p.price_open - 1.0 * r_dist, digits); label = "+1.0R locked"
            elif r_dist > 0 and gain >= 1.0 * r_dist:
                target_sl = round(p.price_open - 0.5 * r_dist, digits); label = "+0.5R locked"
            elif r_dist > 0 and gain >= 0.5 * r_dist:
                target_sl = round(p.price_open - 2 * pip, digits); label = "BREAK-EVEN (+2p)"
            if target_sl and target_sl < p.sl - pip:
                res = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": p.ticket, "symbol": p.symbol, "sl": target_sl, "tp": p.tp})
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    msg = f"{p.symbol} #{p.ticket} TRAILED SL -> {target_sl} ({label})"
                    logger.info(msg); events.append(msg)
    return events


def close_positions_before_weekend(positions: list, logger) -> list[str]:
    """Closes open positions Friday >= 18:30 UTC so weekend gaps never erode equity."""
    events = []
    for p in positions:
        s_info = mt5.symbol_info(p.symbol)
        tick = mt5.symbol_info_tick(p.symbol)
        if not s_info or not tick:
            continue
        close_type = mt5.ORDER_TYPE_SELL if p.type == 0 else mt5.ORDER_TYPE_BUY
        close_px = tick.bid if p.type == 0 else tick.ask
        res = mt5.order_send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": p.volume,
            "type": close_type, "position": p.ticket, "price": close_px,
            "deviation": 20, "magic": p.magic, "comment": "FQRE_v32_WeekendClose",
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": resolve_filling_mode(s_info),
        })
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            msg = f"{p.symbol} #{p.ticket} CLOSED BEFORE WEEKEND @ {close_px} (PnL: ${p.profit:+.2f})"
            logger.info(msg); events.append(msg)
    return events
