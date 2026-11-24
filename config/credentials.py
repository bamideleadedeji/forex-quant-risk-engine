import os
import yaml
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def load_platform_config() -> dict:
    cfg_path = os.path.join(BASE_DIR, "config", "config.yaml")
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["mt5_password"] = os.getenv("MT5_PASSWORD", "").strip()
    cfg["finnhub_api_key"] = os.getenv("FINNHUB_API_KEY", "").strip()
    cfg["base_dir"] = BASE_DIR
    return cfg
