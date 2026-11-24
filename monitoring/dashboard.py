"""
Forex Quant Risk Engine (FQRE v3.0) — Institutional Quant Research & Risk Control House
Dual-Mode Telemetry: Live MT5 Desktop Connection (Local Windows) + GitHub Audit Logs (Streamlit Cloud)
"""

import os
import sys
import pandas as pd
import plotly.express as px
import streamlit as st

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from analytics.performance import summarize_closed_deals

st.set_page_config(page_title="Forex Quant Risk Engine (FQRE v3.0)", page_icon="📈", layout="wide")
st.title("📈 Forex Quant Risk Engine (FQRE v3.0) — Quant Research & Risk Control House")
st.caption("Modular Institutional Architecture • Exness Account #81908061 • Dynamic ATR Sizing • Correlation & Drawdown Guards")
st.divider()

# 1. Attempt Live MT5 Desktop Telemetry (Windows Local), else load committed GitHub Audit Logs (Streamlit Cloud)
live_mt5 = None
try:
    import MetaTrader5 as mt5
    if mt5.initialize():
        acc = mt5.account_info()
        pos = list(mt5.positions_get() or [])
        deals = mt5.history_deals_get(0, 2147483647) or []
        closed_rows = [{"profit": d.profit, "symbol": d.symbol, "volume": d.volume} for d in deals if getattr(d, "entry", 0) == 1]
        live_mt5 = {
            "login": acc.login,
            "server": acc.server,
            "balance": float(acc.balance),
            "equity": float(acc.equity),
            "free_margin": float(acc.margin_free),
            "floating_pnl": float(acc.profit),
            "positions": [{"Ticket": p.ticket, "Symbol": p.symbol, "Side": "BUY" if p.type == 0 else "SELL", "Lots": p.volume, "Entry": p.price_open, "Current": p.price_current, "SL": p.sl, "TP": p.tp, "PnL_USD": round(p.profit, 2)} for p in pos],
            "closed_df": pd.DataFrame(closed_rows),
        }
        mt5.shutdown()
except Exception:
    live_mt5 = None

rich_csv = os.path.join(ROOT_DIR, "fqre_rich_audit.csv")
legacy_csv = os.path.join(ROOT_DIR, "trade_log.csv")
log_txt = os.path.join(ROOT_DIR, "bot_execution.log")

rich_df = pd.read_csv(rich_csv) if os.path.exists(rich_csv) else pd.DataFrame()
legacy_df = pd.read_csv(legacy_csv) if os.path.exists(legacy_csv) else pd.DataFrame()

if live_mt5:
    st.success(f"🟢 LIVE MT5 TERMINAL CONNECTED — Account #{live_mt5['login']} ({live_mt5['server']})")
    stats = summarize_closed_deals(live_mt5["closed_df"], initial_balance=10000.0)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Account Balance", f"${live_mt5['balance']:,.2f}")
    c2.metric("Live Equity", f"${live_mt5['equity']:,.2f}", delta=f"Floating: ${live_mt5['floating_pnl']:+,.2f}")
    c3.metric("Free Margin", f"${live_mt5['free_margin']:,.2f}")
    c4.metric("Closed Win Rate", f"{stats['win_rate_pct']:.1f}%", delta=f"Profit Factor: {stats['profit_factor']:.2f}")
    c5.metric("Sharpe / Max DD", f"{stats['sharpe']:.2f}", delta=f"Max DD: {stats['max_dd_pct']:.2f}%")
else:
    st.info("☁️ CLOUD AUDIT TELEMETRY MODE — Displaying Verified Execution & Risk Logs Synced from Exness #81908061")
    total_cycles = len(rich_df) + len(legacy_df)
    filled_cnt = int((legacy_df["Status"] == "FILLED").sum()) if (not legacy_df.empty and "Status" in legacy_df.columns) else 0
    if not rich_df.empty and "Status" in rich_df.columns:
        filled_cnt += int((rich_df["Status"] == "FILLED").sum())
    blocked_cnt = int((rich_df["Status"] == "RISK_BLOCKED").sum()) if (not rich_df.empty and "Status" in rich_df.columns) else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Account & Evaluation", "#81908061 (Exness)", delta="4-Month Demo Audit")
    c2.metric("Risk Per Trade / Max Pos", "0.35% ATR / 3 Max", delta="1.8x ATR SL : 3.6x ATR TP")
    c3.metric("Verified Orders Filled", f"{filled_cnt}", delta="Institutional Risk Gate Active")
    c4.metric("Risk-Layer Blocked Signals", f"{blocked_cnt}", delta=f"Total Logged Events: {total_cycles}")

tab1, tab2, tab3, tab4 = st.tabs(["📌 Open Positions & Exposure", "🛡️ Risk Gatekeeper Audit Trail", "📊 Historical Trade Ledger", "📜 Live Engine Console Log"])

with tab1:
    if live_mt5 and live_mt5["positions"]:
        pos_df = pd.DataFrame(live_mt5["positions"])
        st.dataframe(pos_df, width="stretch")
        st.plotly_chart(px.bar(pos_df, x="Symbol", y="PnL_USD", color="Side", title="Open Position Floating PnL ($ USD)"), width="stretch")
    else:
        st.write("No open positions currently reported by local terminal (or running in Streamlit Cloud log-viewer mode).")

with tab2:
    st.subheader("FQRE v3.0 Step-by-Step Institutional Risk Gatekeeper Trail")
    if not rich_df.empty:
        st.dataframe(rich_df.iloc[::-1], width="stretch")
    else:
        st.info("No FQRE v3.0 risk-gate events recorded yet.")

with tab3:
    st.subheader("Historical Execution & Fill Ledger (trade_log.csv)")
    if not legacy_df.empty:
        st.dataframe(legacy_df.iloc[::-1], width="stretch")
    else:
        st.info("No historical trades recorded yet.")

with tab4:
    st.subheader("Recent Platform Execution Log (bot_execution.log)")
    if os.path.exists(log_txt):
        with open(log_txt, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-60:]
        st.code("".join(lines), language="text")
