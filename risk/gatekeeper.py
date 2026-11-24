from datetime import datetime, timezone


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
    """Centralized institutional risk gatekeeper: NO order bypasses this layer."""
    trail = []
    now_utc = now_utc or datetime.now(timezone.utc)

    # 1. Weekend & Friday Rollover Protection
    if now_utc.weekday() in (5, 6) or (now_utc.weekday() == 4 and now_utc.hour >= int(risk_cfg.get("friday_cutoff_hour_utc", 15))):
        trail.append("Weekend/Friday Rollover Filter = BLOCKED")
        return False, trail
    trail.append("Session/Weekend Filter = PASS")

    # 2. High-Impact News Filter
    if not news_ok:
        trail.append(f"News Filter = {news_reason}")
        return False, trail
    trail.append("News Filter = PASS")

    # 3. Spread Filter
    max_spread = float(risk_cfg.get("max_spread_pips", 2.5))
    if spread_pips > max_spread:
        trail.append(f"Spread Filter = BLOCKED ({spread_pips:.1f}p > {max_spread}p)")
        return False, trail
    trail.append(f"Spread Filter = PASS ({spread_pips:.1f}p)")

    # 4. Drawdown & Daily Loss Circuit Breaker
    peak_ref = max(balance, equity, 1.0)
    dd_pct = (peak_ref - equity) / peak_ref
    max_dd = float(risk_cfg.get("max_drawdown_pct", 0.08))
    if dd_pct >= max_dd:
        trail.append(f"Drawdown Circuit Breaker = TRIPPED ({dd_pct*100:.2f}% >= {max_dd*100:.2f}%)")
        return False, trail
    trail.append(f"Drawdown Guard = PASS ({dd_pct*100:.2f}%)")

    # 5. Max Open Positions & Duplicate Symbol Guard
    open_syms = {getattr(p, "symbol", "") for p in open_positions}
    max_pos = int(risk_cfg.get("max_open_positions", 3))
    if symbol in open_syms or len(open_positions) >= max_pos:
        trail.append(f"Portfolio Cap = BLOCKED (Open: {len(open_positions)}/{max_pos})")
        return False, trail
    trail.append(f"Portfolio Cap = PASS ({len(open_positions)}/{max_pos})")

    # 6. Currency Correlation Guard (Max USD Exposure)
    usd_open = sum(1 for p in open_positions if "USD" in getattr(p, "symbol", ""))
    max_usd = int(risk_cfg.get("max_usd_exposure", 2))
    if "USD" in symbol and usd_open >= max_usd:
        trail.append(f"Correlation Guard = BLOCKED (USD exposure {usd_open}/{max_usd})")
        return False, trail
    trail.append(f"Correlation Guard = PASS (USD {usd_open}/{max_usd})")

    # 7. Free Margin Sufficiency
    if free_margin < equity * 0.25:
        trail.append("Margin Guard = BLOCKED (Insufficient free margin)")
        return False, trail
    trail.append("Margin Guard = PASS")

    return True, trail
