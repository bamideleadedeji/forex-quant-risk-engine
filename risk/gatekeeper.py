from datetime import datetime, timezone


def _usd_direction(symbol: str, side: str) -> int:
    sym = symbol.upper()
    is_buy = str(side).upper() in ("BUY", "0")
    if sym.startswith("USD"):
        return 1 if is_buy else -1
    if "USD" in sym:
        return -1 if is_buy else 1
    return 0


def evaluate_risk_gate(
    symbol: str,
    signal: str,
    spread_pips: float,
    equity: float,
    balance: float,
    free_margin: float,
    open_positions: list,
    risk_cfg: dict,
    news_ok: bool,
    news_reason: str,
    now_utc: datetime | None = None,
) -> tuple[bool, list[str]]:
    trail = []
    now_utc = now_utc or datetime.now(timezone.utc)

    start_hr = int(risk_cfg.get("session_start_utc", 7))
    end_hr = int(risk_cfg.get("session_end_utc", 20))
    if now_utc.weekday() in (5, 6) or (now_utc.weekday() == 4 and now_utc.hour >= int(risk_cfg.get("friday_cutoff_hour_utc", 15))):
        trail.append("Weekend/Friday Rollover Filter = BLOCKED")
        return False, trail
    if not (start_hr <= now_utc.hour < end_hr):
        trail.append(f"Session Window Filter = BLOCKED ({now_utc.hour:02d}:00 UTC)")
        return False, trail
    trail.append(f"Session/Weekend Filter = PASS ({now_utc.hour:02d}:00 UTC)")

    if not news_ok:
        trail.append(f"News Filter = {news_reason}")
        return False, trail
    trail.append("News Filter = PASS")

    max_spread = float(risk_cfg.get("max_spread_pips", 2.0))
    if spread_pips > max_spread:
        trail.append(f"Spread Filter = BLOCKED ({spread_pips:.1f}p > {max_spread}p)")
        return False, trail
    trail.append(f"Spread Filter = PASS ({spread_pips:.1f}p)")

    peak_ref = max(balance, equity, 1.0)
    dd_pct = (peak_ref - equity) / peak_ref
    max_dd = float(risk_cfg.get("max_drawdown_pct", 0.08))
    if dd_pct >= max_dd:
        trail.append(f"Drawdown Circuit Breaker = TRIPPED ({dd_pct*100:.2f}% >= {max_dd*100:.2f}%)")
        return False, trail
    trail.append(f"Drawdown Guard = PASS ({dd_pct*100:.2f}%)")

    open_syms = {getattr(p, "symbol", "") for p in open_positions}
    max_pos = int(risk_cfg.get("max_open_positions", 2))
    if symbol in open_syms or len(open_positions) >= max_pos:
        trail.append(f"Portfolio Cap = BLOCKED (Open: {len(open_positions)}/{max_pos})")
        return False, trail
    trail.append(f"Portfolio Cap = PASS ({len(open_positions)}/{max_pos})")

    new_dir = _usd_direction(symbol, signal)
    same_dir = sum(1 for p in open_positions if new_dir != 0 and _usd_direction(getattr(p, "symbol", ""), "BUY" if getattr(p, "type", 0) == 0 else "SELL") == new_dir)
    max_same = int(risk_cfg.get("max_same_dir_usd", 1))
    if new_dir != 0 and same_dir >= max_same:
        trail.append(f"Correlation Guard = BLOCKED (Same-Dir USD {same_dir}/{max_same})")
        return False, trail
    trail.append(f"Correlation Guard = PASS (Same-Dir USD {same_dir}/{max_same})")

    if free_margin < equity * 0.25:
        trail.append("Margin Guard = BLOCKED (Insufficient free margin)")
        return False, trail
    trail.append("Margin Guard = PASS")

    return True, trail
