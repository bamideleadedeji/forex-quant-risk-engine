import os
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Forex Quant Analytics Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📈 Forex Quant Risk Engine — Live Analytics Dashboard")
st.caption("Real-Time Telemetry & Performance Attribution for Exness MT5 Demo Account #81908061")
st.divider()

log_file = os.environ.get("LOG_FILE", "trade_log.csv")

# Top Metrics Bar
col1, col2, col3, col4 = st.columns(4)
col1.metric("MT5 Account ID", "81908061")
col2.metric("Server", "Exness-MT5Trial10")

if os.path.exists(log_file):
    df = pd.read_csv(log_file)
    total_logs = len(df)
    filled_trades = len(df[df["result"] == "filled"]) if "result" in df.columns else 0
    rejected_trades = len(df[df["result"].str.contains("rejected|fail", case=False, na=False)]) if "result" in df.columns else 0

    col3.metric("Total Logged Events", total_logs)
    col4.metric("Successful Fills", filled_trades)

    st.metric("Rejected / Failed Fills", rejected_trades)

    st.subheader("📋 Live SQLite/CSV Audit Trail")
    st.dataframe(df, use_container_width=True)

    if not df.empty:
        st.download_button(
            label="📥 Download Trade Audit CSV",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name="forex_audit_export.csv",
            mime="text/csv",
        )
else:
    col3.metric("Total Logged Events", 0)
    col4.metric("Successful Fills", 0)
    st.info(f"⏳ Waiting for trade logs (`{log_file}`). The multi-timeframe cascade is actively scanning the basket.")

if st.sidebar.button("🔄 Refresh Telemetry"):
    st.rerun()
