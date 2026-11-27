import MetaTrader5 as mt5


def resolve_filling_mode(symbol_info) -> int:
    filling = getattr(symbol_info, "filling_mode", 0)
    if filling & 1:
        return mt5.ORDER_FILLING_FOK
    if filling & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def dispatch_protected_order(symbol: str, signal: str, lots: float, atr14: float, risk_cfg: dict, magic_number: int) -> dict:
    s_info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if not s_info or not tick:
        return {"status": "ERROR", "reason": "Missing symbol/tick info"}

    sl_mult = float(risk_cfg.get("atr_sl_multiplier", 1.8))
    tp_mult = float(risk_cfg.get("atr_tp_multiplier", 3.6))
    min_stop = (s_info.trade_stops_level + 10) * s_info.point
    sl_dist = max(atr14 * sl_mult, min_stop)
    tp_dist = sl_dist * (tp_mult / sl_mult)

    digits = s_info.digits
    exec_px = tick.ask if signal == "BUY" else tick.bid
    sl_px = round(exec_px - sl_dist if signal == "BUY" else exec_px + sl_dist, digits)
    tp_px = round(exec_px + tp_dist if signal == "BUY" else exec_px - tp_dist, digits)

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
        "comment": "FQRE_v31_Inst",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": resolve_filling_mode(s_info),
    }
    res = mt5.order_send(req)
    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
        return {"status": "FILLED", "ticket": res.order, "price": exec_px, "sl": sl_px, "tp": tp_px, "lots": lots}
    code = res.retcode if res else "NONE"
    return {"status": f"REJECTED_{code}", "ticket": 0, "price": exec_px, "sl": sl_px, "tp": tp_px, "lots": lots}


def manage_open_positions(positions: list, logger) -> list[str]:
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
            if r_dist > 0 and gain >= 1.5 * r_dist:
                new_sl = round(p.price_open + 0.75 * r_dist, digits)
                if new_sl > p.sl + pip:
                    res = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": p.ticket, "symbol": p.symbol, "sl": new_sl, "tp": p.tp})
                    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                        msg = f"{p.symbol} #{p.ticket} TRAILED SL -> {new_sl} (+0.75R locked)"
                        logger.info(msg); events.append(msg)
            elif r_dist > 0 and gain >= 1.0 * r_dist:
                be_sl = round(p.price_open + 2 * pip, digits)
                if be_sl > p.sl + pip:
                    res = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": p.ticket, "symbol": p.symbol, "sl": be_sl, "tp": p.tp})
                    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                        msg = f"{p.symbol} #{p.ticket} BREAK-EVEN SL -> {be_sl} (+2p locked)"
                        logger.info(msg); events.append(msg)
        else:
            r_dist = (p.price_open - p.tp) / 2.0
            gain = p.price_open - tick.ask
            if r_dist > 0 and gain >= 1.5 * r_dist:
                new_sl = round(p.price_open - 0.75 * r_dist, digits)
                if new_sl < p.sl - pip:
                    res = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": p.ticket, "symbol": p.symbol, "sl": new_sl, "tp": p.tp})
                    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                        msg = f"{p.symbol} #{p.ticket} TRAILED SL -> {new_sl} (+0.75R locked)"
                        logger.info(msg); events.append(msg)
            elif r_dist > 0 and gain >= 1.0 * r_dist:
                be_sl = round(p.price_open - 2 * pip, digits)
                if be_sl < p.sl - pip:
                    res = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": p.ticket, "symbol": p.symbol, "sl": be_sl, "tp": p.tp})
                    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                        msg = f"{p.symbol} #{p.ticket} BREAK-EVEN SL -> {be_sl} (+2p locked)"
                        logger.info(msg); events.append(msg)
    return events
