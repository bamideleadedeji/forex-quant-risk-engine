import os
import pandas as pd
from datetime import datetime, timezone

LOG_FILE = "trade_log.csv"
REPORT_FILE = "execution_audit_report.txt"

def analyze_and_export():
    if not os.path.exists(LOG_FILE):
        print(f"[ERROR] {LOG_FILE} not found.")
        return

    df = pd.read_csv(LOG_FILE)
    if df.empty:
        print("[INFO] Trade log is empty.")
        return

    total_executions = len(df)
    symbols = ", ".join(df['symbol'].dropna().unique()) if 'symbol' in df.columns else "N/A"
    
    result_counts = df['result'].value_counts().to_dict() if 'result' in df.columns else {}
    filled_count = result_counts.get('filled', 0)
    blackout_count = result_counts.get('news_blackout_active', 0)
    
    # Calculate execution metrics
    report_content = f"""=====================================================================
EXECUTIVE QUANTITATIVE PERFORMANCE AUDIT REPORT
Generated (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}
=====================================================================
1. PORTFOLIO SUMMARY
   - Total Logged Events      : {total_executions}
   - Active Symbols Tracked   : {symbols}
   - Successful Order Fills   : {filled_count}
   - News Blackout Protections: {blackout_count}

2. EXECUTION RESULT BREAKDOWN
"""
    for res, count in result_counts.items():
        report_content += f"   - {res:<24}: {count}\n"

    report_content += "\n3. RECENT EXECUTION TELEMETRY\n"
    report_content += df.tail(10).to_string(index=False)
    report_content += "\n=====================================================================\n"

    # Print to terminal
    print(report_content)

    # Export to text report file
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report_content)
    
    print(f"[SUCCESS] Executive audit report successfully exported to '{REPORT_FILE}'")

if __name__ == "__main__":
    analyze_and_export()
