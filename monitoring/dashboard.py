"""
Forex Quant Risk Engine (FQRE v3.0) — Institutional Quant Research & Risk Control House
Dual-Mode Telemetry: Live MT5 Desktop Connection (Local Windows) + Synced MT5 Snapshot (Streamlit Cloud)
"""

import os
import sys
import json
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

snapshot_path = os.path.join(ROOT_DIR, "live_account_snapshot.json")
live_mt5 = None
mode_banner = None

# 1. Try Direct Local MT5 Terminal First
try:
    import MetaTrader5 as mt5
    if mt5.initialize():
        acc = mt5.account_info()
        pos = list(mt5.positions_get() or [])
        deals = mt5.history_deals_get(0, 2147483647) or []
        closed_rows = [{"profit": d.profit, "symbol": d.symbol, "volume": d.volume} for d in deals if getattr(d, "entry", 0) == 1]
        stats = summarize_closed_deals(pd.DataFrame(closed_rows), initial_balance=10000.0)
        live_mt5 = {
            "updated_utc": pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "login": acc.login,
            "server": acc.server,
            "balance": float(acc.balance),
            "equity": float(acc.equity),
            "free_margin": float(acc.margin_free),
            "floating_pnl": float(acc.profit),
            "stats": stats,
            "positions": [{"Ticket": p.ticket, "Symbol": p.symbol, "Side": "BUY" if p.type == 0 else "SELL", "Lots": p.volume, "Entry": p.price_open, "Current": p.price_current, "SL": p.sl, "TP": p.tp, "PnL_USD": round(p.profit, 2)} for p in pos],
        }
        mt5.shutdown()
        mode_banner = f"🟢 LIVE MT5 TERMINAL CONNECTED — Account #{live_mt5['login']} ({live_mt5['server']})"
except Exception:
    live_mt5 = None

# 2. Fallback to GitHub-Synced MT5 Snapshot on Streamlit Cloud
if live_mt5 is None and os.path.exists(snapshot_path):
    with open(snapshot_path, "r", encoding="utf-8") as f:
        live_mt5 = json.load(f)
    mode_banner = f"☁️ GITHUB-SYNCED MT5 TELEMETRY — Account #{live_mt5['login']} ({live_mt5['server']}) | Last Synced: {live_mt5.get('updated_utc', 'N/A')}"

if live_mt5:
    st.success(mode_banner)
    st_m = live_mt5["stats"]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Account Balance", f"${live_mt5['balance']:,.2f}")
    c2.metric("Live Equity", f"${live_mt5['equity']:,.2f}", delta=f"Floating: ${live_mt5['floating_pnl']:+,.2f}")
    c3.metric("Free Margin", f"${live_mt5['free_margin']:,.2f}")
    c4.metric("Closed Win Rate", f"{st_m['win_rate_pct']:.1f}%", delta=f"Profit Factor: {st_m['profit_factor']:.2f}")
    c5.metric("Sharpe / Max DD", f"{st_m['sharpe']:.2f}", delta=f"Max DD: {st_m['max_dd_pct']:.2f}%")

rich_csv = os.path.join(ROOT_DIR, "fqre_rich_audit.csv")
legacy_csv = os.path.join(ROOT_DIR, "trade_log.csv")
log_txt = os.path.join(ROOT_DIR, "bot_execution.log")
rich_df = pd.read_csv(rich_csv) if os.path.exists(rich_csv) else pd.DataFrame()
legacy_df = pd.read_csv(legacy_csv) if os.path.exists(legacy_csv) else pd.DataFrame()

tab1, tab2, tab3, tab4 = st.tabs(["📌 Open Positions & Exposure", "🛡️ Risk Gatekeeper Audit Trail", "📊 Historical Trade Ledger", "📜 Live Engine Console Log"])

with tab1:
    if live_mt5 and live_mt5.get("positions"):
        pos_df = pd.DataFrame(live_mt5["positions"])
        st.dataframe(pos_df, width="stretch")
        st.plotly_chart(px.bar(pos_df, x="Symbol", y="PnL_USD", color="Side", title="Open Position Floating PnL ($ USD)"), width="stretch")
    else:
        st.info("No open positions currently active on Account #81908061.")

with tab2:
    st.subheader("FQRE v3.0 Step-by-Step Institutional Risk Gatekeeper Trail")
    if not rich_df.empty:
        st.dataframe(rich_df.iloc[::-1], width="stretch")
    else:
        st.info("Risk gatekeeper events will appear here automatically on the next trade signal.")

with tab3:
    st.subheader("Historical Execution & Fill Ledger (trade_log.csv)")
    if not legacy_df.empty:
        st.dataframe(legacy_df.iloc[::-1], width="stretch")

with tab4:
    st.subheader("Recent Platform Execution Log (bot_execution.log)")
    if os.path.exists(log_txt):
        with open(log_txt, "r", encoding="utf-8", errors="ignore") as f:
            st.code("".join(f.readlines()[-60:]), language="text")
