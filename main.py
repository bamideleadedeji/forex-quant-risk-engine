import json
from datetime import datetime, timezone
import pandas as pd
import MetaTrader5 as mt5
from config.credentials import load_platform_config
from market_data.live_data import connect_mt5_with_retry, fetch_ohlcv_bars
from market_data.news_filter import check_high_impact_news
from strategy.ensemble import generate_ensemble_signal
from risk.position_size import compute_dynamic_lot_size
from risk.gatekeeper import evaluate_risk_gate
from execution.order_manager import dispatch_protected_order, manage_open_positions, close_positions_before_weekend, compute_intraday_sl_tp
from monitoring.audit import get_platform_logger, record_trade_audit
from email_alert import send_email_alert
from analytics.performance import summarize_closed_deals


def _sync_snapshot_and_notify(acc, positions, cycle_events):
    try:
        deals = mt5.history_deals_get(0, 2147483647) or []
        closed_rows = [{"profit": d.profit, "symbol": d.symbol, "volume": d.volume} for d in deals if getattr(d, "entry", 0) == 1]
        stats = summarize_closed_deals(pd.DataFrame(closed_rows), initial_balance=10000.0)
        snap = {
            "updated_utc": pd.Timestamp.now("UTC").strftime("%Y-%m-%d %H:%M:%S UTC"),
            "login": acc.login, "server": acc.server,
            "balance": float(acc.balance), "equity": float(acc.equity),
            "free_margin": float(acc.margin_free), "floating_pnl": float(acc.profit),
            "stats": stats,
            "positions": [{"Ticket": p.ticket, "Symbol": p.symbol, "Side": "BUY" if p.type == 0 else "SELL", "Lots": p.volume, "Entry": p.price_open, "Current": p.price_current, "SL": p.sl, "TP": p.tp, "PnL_USD": round(p.profit, 2)} for p in positions],
        }
        with open("live_account_snapshot.json", "w", encoding="utf-8") as sf:
            json.dump(snap, sf, indent=2)
    except Exception:
        pass
    for ev in cycle_events:
        if any(k in ev for k in ("FILLED", "BREACH", "BREAK-EVEN", "TRAILED", "WEEKEND")):
            send_email_alert(f"[FQRE v3.2 Alert] {ev[:60]}", f"Exness Account #{acc.login}\nEquity: ${acc.equity:,.2f} | Balance: ${acc.balance:,.2f}\n\n{ev}")


def run_fqre_cycle():
    events = []
    cfg = load_platform_config()
    logger = get_platform_logger(cfg["base_dir"])
    risk_cfg = cfg["risk"]
    now_utc = datetime.now(timezone.utc)

    if not connect_mt5_with_retry(cfg, wait_seconds=15):
        logger.warning("MT5 terminal not connected after 15s wait. Exiting cycle cleanly.")
        return

    acc = mt5.account_info()
    if not acc:
        mt5.shutdown()
        return

    positions = list(mt5.positions_get() or [])
    logger.info(f"[FQRE v3.2] Account #{acc.login} | Balance: ${acc.balance:,.2f} | Equity: ${acc.equity:,.2f} | Open: {len(positions)}/2")

    # Step 1: Friday >= 18:30 UTC Weekend Flat Protection
    if now_utc.weekday() == 4 and (now_utc.hour > 18 or (now_utc.hour == 18 and now_utc.minute >= 30)) and positions:
        events.extend(close_positions_before_weekend(positions, logger))
        positions = list(mt5.positions_get() or [])

    # Step 2: Early Profit-Lock Trailing (+0.5R -> BE, +1.0R -> +0.5R, +1.5R -> +1.0R)
    events.extend(manage_open_positions(positions, logger))
    positions = list(mt5.positions_get() or [])

    # Cap per-trade risk at 0.20% (~$20 max loss, matching winning Nov 20 sizing)
    effective_risk_pct = min(float(risk_cfg.get("risk_per_trade_pct", 0.002)), 0.0020)

    for sym in cfg["symbols"]:
        df_h1 = fetch_ohlcv_bars(sym, timeframe=mt5.TIMEFRAME_H1, count=150)
        df_h4 = fetch_ohlcv_bars(sym, timeframe=mt5.TIMEFRAME_H4, count=100)
        sig = generate_ensemble_signal(df_h1, min_adx=float(risk_cfg.get("min_adx_trend", 25.0)), df_h4=df_h4)
        tick = mt5.symbol_info_tick(sym)
        s_info = mt5.symbol_info(sym)
        if not tick or not s_info:
            continue

        pip_size = 0.01 if "JPY" in sym else 0.0001
        spread_pips = (tick.ask - tick.bid) / pip_size

        if sig["signal"] == "HOLD":
            logger.info(f"{sym:<9} | Bid: {tick.bid:<9} | Spread: {spread_pips:4.1f}p | HOLD ({sig['reason']})")
            continue

        news_ok, news_reason = check_high_impact_news(sym, cfg["finnhub_api_key"], int(risk_cfg["news_blackout_minutes"]))
        allowed, audit_trail = evaluate_risk_gate(
            symbol=sym, signal=sig["signal"], spread_pips=spread_pips,
            equity=float(acc.equity), balance=float(acc.balance), free_margin=float(acc.margin_free),
            open_positions=positions, risk_cfg=risk_cfg, news_ok=news_ok, news_reason=news_reason,
        )

        trail_str = " -> ".join(audit_trail)
        if not allowed:
            logger.info(f"{sym:<9} | REJECTED BY RISK LAYER | {trail_str}")
            record_trade_audit(cfg["base_dir"], {
                "Symbol": sym, "Signal": sig["signal"], "Lots": 0.0, "Entry_Price": tick.bid,
                "ATR14": sig["atr14"], "ADX14": sig["adx14"], "RSI": sig["rsi"], "Spread_Pips": round(spread_pips, 2),
                "Confidence": sig["confidence"], "Risk_Pct": effective_risk_pct,
                "Status": "RISK_BLOCKED", "Ticket": 0, "Audit_Trail": trail_str
            })
            continue

        exec_px = tick.ask if sig["signal"] == "BUY" else tick.bid
        sl_dist, _, _ = compute_intraday_sl_tp(sym, sig["signal"], exec_px, sig["atr14"], s_info)
        lots = compute_dynamic_lot_size(
            equity=float(acc.equity), sl_distance=sl_dist,
            tick_size=float(s_info.trade_tick_size), tick_value=float(s_info.trade_tick_value),
            risk_pct=effective_risk_pct,
            vol_min=float(s_info.volume_min), vol_max=float(s_info.volume_max), vol_step=float(s_info.volume_step),
        )

        exec_res = dispatch_protected_order(sym, sig["signal"], lots, sig["atr14"], risk_cfg, cfg["account"]["magic_number"])
        logger.info(f"{sym:<9} | {exec_res['status']} {sig['signal']} {lots} lots @ {exec_res['price']} | SL: {exec_res['sl']} | TP: {exec_res['tp']} | {trail_str}")
        record_trade_audit(cfg["base_dir"], {
            "Symbol": sym, "Signal": sig["signal"], "Lots": lots, "Entry_Price": exec_res["price"],
            "Stop_Loss": exec_res["sl"], "Take_Profit": exec_res["tp"], "ATR14": sig["atr14"],
            "ADX14": sig["adx14"], "RSI": sig["rsi"], "Spread_Pips": round(spread_pips, 2),
            "Confidence": sig["confidence"], "Risk_Pct": effective_risk_pct,
            "Status": exec_res["status"], "Ticket": exec_res["ticket"], "Audit_Trail": trail_str
        })
        if exec_res["status"] == "FILLED":
            events.append(f"{sym} FILLED {sig['signal']} {lots} lots @ {exec_res['price']} | SL: {exec_res['sl']} | TP: {exec_res['tp']}")
            positions = list(mt5.positions_get() or [])

    logger.info("[COMPLETE] FQRE v3.2 profit-lock cycle finished cleanly.")
    _sync_snapshot_and_notify(acc, list(mt5.positions_get() or []), events)
    mt5.shutdown()


if __name__ == "__main__":
    run_fqre_cycle()
