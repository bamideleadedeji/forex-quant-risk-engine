import os
import csv
import logging
from datetime import datetime, timezone


def get_platform_logger(base_dir: str) -> logging.Logger:
    logger = logging.getLogger("FQRE_Platform")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        fh = logging.FileHandler(os.path.join(base_dir, "bot_execution.log"), encoding="utf-8")
        fh.setFormatter(fmt)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(fh)
        logger.addHandler(sh)
    return logger


def record_trade_audit(base_dir: str, row: dict) -> None:
    csv_path = os.path.join(base_dir, "fqre_rich_audit.csv")
    fieldnames = [
        "Timestamp_UTC", "Symbol", "Signal", "Lots", "Entry_Price", "Stop_Loss", "Take_Profit",
        "ATR14", "ADX14", "RSI", "Spread_Pips", "Confidence", "Risk_Pct", "Status", "Ticket", "Audit_Trail"
    ]
    file_exists = os.path.exists(csv_path)
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        row_out = {"Timestamp_UTC": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), **row}
        writer.writerow({k: row_out.get(k, "") for k in fieldnames})
