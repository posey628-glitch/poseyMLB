"""
props.py
=========
Converts internal model outputs into actual betting-relevant numbers:

  - HR probability per hitter (0.00 - 1.00) — directly comparable to "+450 HR" odds
  - Strikeout total projection per pitcher (with std dev range)
  - Implied odds → break-even threshold logic
  - "Edge vs market" calculations

Designed for prop betting on HR and K markets.

CALIBRATION NOTE:
  League-avg HR/PA in 2024-25 was ~3.0%. A "good" HR prop hitter sits at 5-7%.
  Aaron Judge in a great matchup ~12-15%. The model targets this range.

  League-avg K/9 is ~8.6. Strong starter K projection 6.5-9.5 over 5-6 IP.
  Strider/Skubal types project 8.5-11 in a good matchup.
"""

from __future__ import annotations

import math
import numpy as np
import pandas as pd


# Base HR rate per PA, MLB-wide. Used as anchor for prob calibration.
LEAGUE_HR_PER_PA = 0.030
LEAGUE_K_PER_9 = 8.6


def hr_prob_per_pa(
    hitter_row: dict,
    pitcher_row: dict,
    park_factor: float = 1.0,
    park_hand_factor: float = 1.0,
    weather_mult: float = 1.0,
    pitch_match_score: float | None = None,
    ttop_mult: float = 1.0,
    defense_factor: float = 1.0,
) -> float:
    """
    Returns P(HR | single PA today).

    Base = hitter season HR/PA × pitcher HR/9 adjustment × park × weather
            × pitch match × TTOP × defense.
    """
    pa = hitter_row.get("pa", 0) or 0
    hr = hitter_row.get("home_run", 0) or 0

    # Hitter base rate, regressed to league average for small samples
    if pa >= 100:
        h_base = hr / pa
    elif pa > 0:
        # Bayesian shrink: blend with league mean
        h_base = (hr + LEAGUE_HR_PER_PA * 100) / (pa + 100)
    else:
        h_base = LEAGUE_HR_PER_PA

    # Pitcher HR/9 adjustment
    p_hr9 = pitcher_row.get("hr9", 1.2) if pitcher_row else 1.2
    # League avg HR/9 ~ 1.20. Convert to per-PA multiplier.
    p_hr_per_pa = (p_hr9 / 9) / 4.3  # ~4.3 PA per inning
    league_p_hr_per_pa = (1.20 / 9) / 4.3
    pitcher_mult = p_hr_per_pa / league_p_hr_per_pa if league_p_hr_per_pa else 1.0
    pitcher_mult = max(0.5, min(2.0, pitcher_mult))  # cap

    # Pitch match adjustment - 50 score = neutral, 100 = double, 0 = halve
    pm_mult = 1.0
    if pitch_match_score is not None:
        pm_mult = 0.5 + (pitch_match_score / 100)
        pm_mult = max(0.6, min(1.6, pm_mult))

    prob = (
        h_base
        * pitcher_mult
        * park_factor
        * park_hand_factor
        * weather_mult
        * pm_mult
        * ttop_mult
        * defense_factor
    )
    return float(np.clip(prob, 0.001, 0.30))


def hr_prob_full_game(prob_per_pa: float, expected_pa: float = 4.2) -> float:
    """
    P(at least 1 HR in the game) = 1 - (1 - p_pa) ^ PA
    Default 4.2 PA for typical starter / top-of-order hitter.
    """
    return float(1 - (1 - prob_per_pa) ** expected_pa)


def k_total_projection(
    pitcher_row: dict,
    opp_lineup_k_pct: float,
    ump_k_factor: float = 1.0,
    catcher_framing_factor: float = 1.0,
    park_k_factor: float = 1.0,
    expected_ip: float = 5.5,
    recent_k9_weight: float = 0.35,
) -> dict:
    """
    Project pitcher K total, blending season K% with recent form, then
    multiplied by environmental factors.

    Returns dict with: mean, low, high (1 std dev), prob_over_X for common lines.
    """
    if not pitcher_row:
        return {"mean": None}

    # Season K% and recent K/9
    season_k_pct = pitcher_row.get("k_percent", pitcher_row.get("k_pct", 22)) or 22
    recent_k9 = pitcher_row.get("recent_k9", None)
    season_k9 = pitcher_row.get("k9", 8.5) or 8.5

    # Blend recent + season K/9
    if recent_k9 is not None and recent_k9 > 0:
        blended_k9 = recent_k9 * recent_k9_weight + season_k9 * (1 - recent_k9_weight)
    else:
        blended_k9 = season_k9

    # Opposing lineup adjustment: their K% vs league
    lineup_adj = (opp_lineup_k_pct or 22) / 22  # 22% = league avg

    # Apply environmental factors
    proj_k9 = (
        blended_k9
        * lineup_adj
        * ump_k_factor
        * catcher_framing_factor
        * park_k_factor
    )

    mean = proj_k9 * expected_ip / 9

    # Std dev: empirically ~25% of mean for a single start
    sigma = mean * 0.25

    # Probabilities of common K totals (Normal approximation)
    def p_over(line):
        from math import erf, sqrt
        if sigma == 0:
            return 0.5
        z = (line + 0.5 - mean) / (sigma * sqrt(2))
        return float(1 - 0.5 * (1 + erf(z)))

    return {
        "mean": round(mean, 2),
        "low": round(mean - sigma, 2),
        "high": round(mean + sigma, 2),
        "sigma": round(sigma, 2),
        "blended_k9": round(blended_k9, 2),
        "lineup_adj": round(lineup_adj, 3),
        "p_over_5.5": round(p_over(5.5), 3),
        "p_over_6.5": round(p_over(6.5), 3),
        "p_over_7.5": round(p_over(7.5), 3),
        "p_over_8.5": round(p_over(8.5), 3),
    }


def implied_prob_from_american(odds: int) -> float:
    """Convert American odds to implied probability (with vig)."""
    if odds is None:
        return None
    if odds < 0:
        return -odds / (-odds + 100)
    return 100 / (odds + 100)


def american_from_prob(p: float) -> int:
    """Inverse: probability → American odds (fair, no vig)."""
    if p is None or p <= 0 or p >= 1:
        return None
    if p >= 0.5:
        return int(round(-p / (1 - p) * 100))
    return int(round((1 - p) / p * 100))


def edge_vs_market(model_prob: float, market_odds: int) -> dict:
    """
    Compare model probability to a sportsbook line.

    Returns:
      market_prob   - what the book is implying (includes vig)
      fair_odds     - what odds the model thinks are fair
      edge_pct      - (model_prob - market_prob) / market_prob * 100
      kelly         - optional Kelly stake (cap at 25% for safety)
    """
    if model_prob is None or market_odds is None:
        return {}
    mp = implied_prob_from_american(market_odds)
    if mp is None:
        return {}
    edge_pct = (model_prob - mp) / mp * 100
    fair = american_from_prob(model_prob)
    # Decimal odds for Kelly
    dec = 1 + (market_odds / 100 if market_odds > 0 else 100 / -market_odds)
    b = dec - 1
    q = 1 - model_prob
    kelly_full = (b * model_prob - q) / b if b > 0 else 0
    kelly_quarter = max(0, min(0.25, kelly_full / 4))  # quarter Kelly capped
    return {
        "market_prob": round(mp, 4),
        "fair_odds": fair,
        "edge_pct": round(edge_pct, 1),
        "kelly_quarter": round(kelly_quarter, 4),
        "recommend": "✅ BET" if edge_pct > 5 else "—" if edge_pct > -3 else "❌ FADE",
    }


def verdict_color(score: float, scale: tuple = (40, 60)) -> str:
    """
    Convert any 0-100 score into a stoplight verdict.
      < scale[0]   = 🔴 Fade
      < scale[1]   = 🟡 Neutral
      ≥ scale[1]   = 🟢 Smash
    """
    if score is None or pd.isna(score):
        return "—"
    if score >= scale[1]:
        return "🟢"
    if score >= scale[0]:
        return "🟡"
    return "🔴"
