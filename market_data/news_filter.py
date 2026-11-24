from datetime import datetime, timezone, timedelta
import requests


def check_high_impact_news(symbol: str, api_key: str, blackout_minutes: int = 60) -> tuple[bool, str]:
    if not api_key:
        return True, "PASS (No Finnhub key configured)"
    currencies = [symbol[:3].upper(), symbol[3:6].upper()]
    now_utc = datetime.now(timezone.utc)
    start_str = (now_utc - timedelta(minutes=blackout_minutes)).strftime("%Y-%m-%d")
    end_str = (now_utc + timedelta(minutes=blackout_minutes)).strftime("%Y-%m-%d")
    try:
        url = f"https://finnhub.io{start_str}&to={end_str}&token={api_key}"
        events = requests.get(url, timeout=8).json().get("economicCalendar", [])
        for ev in events:
            if str(ev.get("impact", "")).lower() != "high":
                continue
            country = str(ev.get("country", "")).upper()
            if any(c[:2] in country for c in currencies):
                ev_time = datetime.fromisoformat(str(ev.get("time", "")).replace("Z", "+00:00"))
                if abs((ev_time - now_utc).total_seconds()) <= blackout_minutes * 60:
                    return False, f"BLOCKED: High-impact news ({ev.get('event')})"
    except Exception:
        return True, "PASS (Calendar feed unreachable)"
    return True, "PASS"
