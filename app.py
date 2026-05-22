"""
app.py — MLB Daily Prop Betting Dashboard
==========================================
Built for isolated daily HR and K prop research.

Top-level sections:
  1. 📖 Dashboard Legend      — Interpret abbreviations & colors
  2. 📋 Pitcher Slate Overview — Summary of starting pitching across baseball
  3. 🎮 Isolated Matchups     — Full game-by-game player data vs. today's pitchers
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import streamlit as st

from data_fetcher import (
    get_slate, get_lineup, get_team_roster, get_all_team_rosters,
    get_hitter_stats, get_pitcher_stats, get_pitcher_arsenal,
    get_hitter_traditional, get_pitcher_traditional,
    get_pitcher_recent_form, get_hitter_recent_form_trad,
)
from models import build_matchup_table, build_pitcher_slate
from park_factors import get_park
from weather import fetch_weather, hr_multiplier
from sleepers import hr_probability, find_sleepers, grand_slam_probability
from splits import (
    bvp_for_lineup, find_similar_pitchers, hitter_vs_similar,
)
from pitch_match import get_hitter_pitch_arsenal, lineup_pitch_match
from game_context import (
    get_umpire_for_game, get_catcher_framing, get_team_defense,
    get_vegas_totals, get_pitcher_workload,
    ttop_multiplier, park_hand_factor,
)
from props import (
    hr_prob_per_pa, hr_prob_full_game, k_total_projection,
    verdict_color, edge_vs_market,
)


st.set_page_config(page_title="MLB Prop Dashboard", layout="wide", page_icon="⚾")


# ---------------------------------------------------------------------------
# Sidebar Settings
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("⚾ MLB Props")
    selected_date = st.date_input("Slate date", value=date.today())

    st.markdown("### Features")
    use_pitch_match = st.checkbox("Pitch-match analysis", value=True,
        help="How well each hitter performs against today's pitcher's specific pitch mix. High impact.")
    use_vegas = st.checkbox("Vegas implied totals", value=True,
        help="Market totals as best environmental signal.")
    use_umpire = st.checkbox("Umpire / catcher framing", value=True,
        help="HP ump + catcher framing affect K rate.")
    use_recent_form = st.checkbox("Recent L15 form", value=True,
        help="Hitter L15 / pitcher L5 starts. ~30s extra load.")
    use_bvp = st.checkbox("BvP & similar arsenal", value=False,
        help="Career batter-vs-pitcher (often noisy small samples).")

    st.markdown("---")
    st.markdown("### Updated")
    now = datetime.now().strftime("%I:%M %p")
    st.caption(f"Data refreshed: {now}")
    if st.button("🔄 Force refresh"):
        st.cache_data.clear()
        st.rerun()

    st.markdown("---")
    st.warning(
        "**Bet responsibly.** Sportsbook lines are sharp. "
        "Use this as one input, not a guarantee. Bet flat units. "
        "Shop lines. Set a daily limit and stick to it."
    )


# ---------------------------------------------------------------------------
# Load shared season data
# ---------------------------------------------------------------------------

with st.spinner("Loading slate..."):
    slate = get_slate(selected_date.isoformat())

if slate.empty:
    st.warning(f"No games on {selected_date}. Off day or future date.")
    st.stop()

with st.spinner("Loading Statcast season stats..."):
    hitter_stats = get_hitter_stats()
    pitcher_stats = get_pitcher_stats()
    hitter_trad = get_hitter_traditional()
    pitcher_trad = get_pitcher_traditional()

if not hitter_trad.empty and "player_id" in hitter_stats.columns:
    hitter_stats = hitter_stats.merge(
        hitter_trad.drop(columns=["player_name"], errors="ignore"),
        on="player_id", how="left", suffixes=("", "_t"),
    )
if not pitcher_trad.empty and "player_id" in pitcher_stats.columns:
    pitcher_stats = pitcher_stats.merge(
        pitcher_trad.drop(columns=["player_name"], errors="ignore"),
        on="player_id", how="left", suffixes=("", "_t"),
    )

hitter_pitch_arsenal = pd.DataFrame()
pitcher_arsenal_all = pd.DataFrame()
if use_pitch_match:
    with st.spinner("Loading pitch-match data..."):
        hitter_pitch_arsenal = get_hitter_pitch_arsenal()
        pitcher_arsenal_all = get_pitcher_arsenal()

vegas_df = pd.DataFrame()
if use_vegas:
    vegas_df = get_vegas_totals(selected_date.isoformat())

framing_df = pd.DataFrame()
if use_umpire:
    framing_df = get_catcher_framing()


# ---------------------------------------------------------------------------
# Main Header
# ---------------------------------------------------------------------------

st.title(f"⚾ MLB Props — {selected_date.strftime('%A, %B %d, %Y')}")
st.caption(f"{len(slate)} games · {len(hitter_stats)} hitters · {len(pitcher_stats)} pitchers loaded")


# ---------------------------------------------------------------------------
# Quick Reference Dashboard Legend
# ---------------------------------------------------------------------------

with st.expander("📖 Dashboard Metric Legend & Quick Reference Guide", expanded=False):
    st.markdown("### How to read the Data Grids")
    st.caption("Use this guide to quickly interpret the color-coded models and advanced Statcast abbreviations.")
    
    leg_col1, leg_col2, leg_col3 = st.columns(3)
    
    with leg_col1:
        st.markdown("**🟢 Betting Color Signals**")
        st.markdown(
            "- **Green (🟢 Strong Play):** High-percentile model match. Matchup factors heavily favor an 'Over' or breakout performance.\n"
            "- **Yellow (🟡 Neutral / Lean):** Solid baseline data matching historical season pace, but lacks extreme environmental or matchup signals.\n"
            "- **Red (🔴 Fade / Under):** Poor matchup fit, negative park/weather traits, or high-risk splits. Candidate to stay under line."
        )
        st.markdown("**📈 Primary Betting Metrics**")
        st.markdown(
            "- **HR Game%:** The calculated, environment-adjusted probability that a hitter hits 1 or more home runs today.\n"
            "- **Proj K / Range:** The projected strikeout ceiling and basement for a starting pitcher based on umpire zones, framing, and lineup trends."
        )

    with leg_col2:
        st.markdown("**🔥 Advanced Hitter Metrics**")
        st.markdown(
            "- **ISO (Isolated Power):** Measures raw power by calculating extra-base hits per at-bat ($SLG - BA$). Greater than .200 is excellent.\n"
            "- **xwOBA (Expected Weighted On-Base Average):** Formulated using Statcast launch angle and exit velocity. Tells you how well a batter is *actually* hitting regardless of defensive luck.\n"
            "- **xwOBAcon:** Expected weighted on-base average strictly on *contact* (excluding walks and strikeouts). Tracks pure hard-hit quality.\n"
            "- **Brl% (Barrel %):** Percent of batted balls hit with the perfect combination of exit velocity and launch angle (the sweet spot for HRs)."
        )

    with leg_col3:
        st.markdown("**💎 Advanced Pitcher & Context Metrics**")
        st.markdown(
            "- **Pitch Match:** A custom matching score tracking how well today's lineup handles this specific pitcher's signature pitch arsenal.\n"
            "- **kHR Matrix:** A weighted metric cross-referencing a pitcher's home run allowance rate against the lineup's collective power depth.\n"
            "- **Whiff% / CSW%:** The percentage of empty swings per swing (Whiff) and Called Strikes + Whiffs (CSW). Crucial indicators for betting K props.\n"
            "- **HR Mult:** Environmental factor combining real-time wind speed, temperature, humidity, and individual park dimensions (1.00× is neutral)."
        )


# ---------------------------------------------------------------------------
# Precompute per-game context & build isolated matchups
# ---------------------------------------------------------------------------

game_context_map = {}
progress = st.progress(0.0, text="Assembling game environments...")

for idx, (_, game) in enumerate(slate.iterrows()):
    progress.progress((idx + 1) / len(slate), text=f"Game {idx+1}/{len(slate)}")

    park = get_park(game.get("venue", ""))
    gt = pd.to_datetime(game["gameTime"]) if pd.notna(game["gameTime"]) else datetime.now()
    weather = fetch_weather(park.get("lat"), park.get("lon"), gt) if park.get("lat") else {}
    wx_mult, wx_summary = hr_multiplier(weather, park)
    park_mult = park.get("hr_factor", 100) / 100.0
    full_hr_mult = wx_mult * park_mult

    vegas_row = None
    if not vegas_df.empty:
        match = vegas_df[
            (vegas_df["away_abbr"] == game["away_team_abbr"]) &
            (vegas_df["home_abbr"] == game["home_team_abbr"])
        ]
        if len(match):
            vegas_row = match.iloc[0].to_dict()

    ump = get_umpire_for_game(int(game["gamePk"])) if use_umpire else {"name": "TBD", "k_factor": 1.0, "bb_factor": 1.0}

    away_lineup = get_lineup(int(game["gamePk"]), "away") or [
        {"id": p["id"], "name": p["name"], "position": p["position"]}
        for p in get_team_roster(int(game["away_team_id"]))[:9]
    ]
    home_lineup = get_lineup(int(game["gamePk"]), "home") or [
        {"id": p["id"], "name": p["name"], "position": p["position"]}
        for p in get_team_roster(int(game["home_team_id"]))[:9]
    ]

    away_p = pitcher_stats[pitcher_stats["player_id"] == game["away_pitcher_id"]]
    home_p = pitcher_stats[pitcher_stats["player_id"] == game["home_pitcher_id"]]
    away_p_row = away_p.iloc[0].to_dict() if len(away_p) else {}
    home_p_row = home_p.iloc[0].to_dict() if len(home_p) else {}

    if use_recent_form:
        for side, pid in [("away", game["away_pitcher_id"]), ("home", game["home_pitcher_id"])]:
            if pid and not pd.isna(pid):
                recent = get_pitcher_recent_form(int(pid))
                workload = get_pitcher_workload(int(pid))
                row_dict = away_p_row if side == "away" else home_p_row
                row_dict.update(recent)
                row_dict.update(workload)

    away_recent = {}
    home_recent = {}
    if use_recent_form:
        for p in away_lineup:
            if p.get("id"):
                away_recent[p["id"]] = get_hitter_recent_form_trad(int(p["id"]))
        for p in home_lineup:
            if p.get("id"):
                home_recent[p["id"]] = get_hitter_recent_form_trad(int(p["id"]))

    away_matchup = build_matchup_table(
        away_lineup, pd.Series(home_p_row) if home_p_row else None,
        hitter_stats, pitcher_stats, recent_form_dict=away_recent,
    )
    home_matchup = build_matchup_table(
        home_lineup, pd.Series(away_p_row) if away_p_row else None,
        hitter_stats, pitcher_stats, recent_form_dict=home_recent,
    )

    if use_pitch_match and not pitcher_arsenal_all.empty and not hitter_pitch_arsenal.empty:
        away_pm = lineup_pitch_match(away_lineup, game["home_pitcher_id"], hitter_pitch_arsenal, pitcher_arsenal_all)
        home_pm = lineup_pitch_match(home_lineup, game["away_pitcher_id"], hitter_pitch_arsenal, pitcher_arsenal_all)
        
        pm_target_cols = ["player_id", "pitch_match_score", "best_pitch", "best_pitch_xwoba", "worst_pitch", "weighted_xwoba"]
        
        if not away_pm.empty and not away_matchup.empty:
            away_pm_keep = [col for col in pm_target_cols if col in away_pm.columns]
            if "player_id" in away_pm_keep:
                away_matchup = away_matchup.merge(away_pm[away_pm_keep], on="player_id", how="left")
                
        if not home_pm.empty and not home_matchup.empty:
            home_pm_keep = [col for col in pm_target_cols if col in home_pm.columns]
            if "player_id" in home_pm_keep:
                home_matchup = home_matchup.merge(home_pm[home_pm_keep], on="player_id", how="left")

    away_matchup = hr_probability(away_matchup, pd.Series(home_p_row) if home_p_row else None, full_hr_mult)
    home_matchup = hr_probability(home_matchup, pd.Series(away_p_row) if away_p_row else None, full_hr_mult)
    away_matchup = find_sleepers(away_matchup, season_hr_col="home_run")
    home_matchup = find_sleepers(home_matchup, season_hr_col="home_run")
    away_matchup = grand_slam_probability(away_matchup, pd.Series(home_p_row) if home_p_row else None, full_hr_mult)
    home_matchup = grand_slam_probability(home_matchup, pd.Series(away_p_row) if away_p_row else None, full_hr_mult)

    for matchup_df, opp_p_row in [(away_matchup, home_p_row), (home_matchup, away_p_row)]:
        if matchup_df.empty:
            continue
        hr_pa_list, hr_game_list, verdict_list = [], [], []
        for _, hrow in matchup_df.iterrows():
            ph_factor = park_hand_factor(game.get("venue", ""), hrow.get("bats", ""))
            ttop = ttop_multiplier(hrow.get("lineup_pos", 5))
            pm_score = hrow.get("pitch_match_score") if "pitch_match_score" in matchup_df.columns else None
            p_pa = hr_prob_per_pa(
                hitter_row=hrow.to_dict(), pitcher_row=opp_p_row,
                park_factor=park_mult, park_hand_factor=ph_factor,
                weather_mult=wx_mult, pitch_match_score=pm_score, ttop_mult=ttop, defense_factor=1.0
            )
            p_game = hr_prob_full_game(p_pa, expected_pa=4.3 if hrow.get("lineup_pos", 5) <= 5 else 3.8)
            hr_pa_list.append(round(p_pa * 100, 2))
            hr_game_list.append(round(p_game * 100, 1))
            avg_score = ((hrow.get("matchup", 50) or 50) + p_game * 200) / 2
            verdict_list.append(verdict_color(avg_score, scale=(45, 65)))
        matchup_df["hr_pa_pct"] = hr_pa_list
        matchup_df["hr_game_pct"] = hr_game_list
        matchup_df["verdict"] = verdict_list

    away_lineup_k_pct = away_matchup["k_pct"].mean() if "k_pct" in away_matchup.columns and not away_matchup.empty else 22
    home_lineup_k_pct = home_matchup["k_pct"].mean() if "k_pct" in home_matchup.columns and not home_matchup.empty else 22

    away_k_proj = k_total_projection(away_p_row, home_lineup_k_pct, ump_k_factor=ump.get("k_factor", 1.0)) if away_p_row else {}
    home_k_proj = k_total_projection(home_p_row, away_lineup_k_pct, ump_k_factor=ump.get("k_factor", 1.0)) if home_p_row else {}

    game_context_map[game["gamePk"]] = {
        "park": park, "weather": weather, "wx_mult": wx_mult, "park_mult": park_mult,
        "hr_mult": full_hr_mult, "summary": wx_summary, "vegas": vegas_row, "ump": ump,
        "away_lineup": away_lineup, "home_lineup": home_lineup,
        "away_p_row": away_p_row, "home_p_row": home_p_row,
        "away_matchup": away_matchup, "home_matchup": home_matchup,
        "away_k_proj": away_k_proj, "home_k_proj": home_k_proj,
        "away_framing": away_p_row.get("catcher_framing_k_factor", 1.0),
        "home_framing": home_p_row.get("catcher_framing_k_factor", 1.0),
    }

progress.empty()


# ---------------------------------------------------------------------------
# 📋 Slate Summary — Starting Pitchers (Absolute Reset-Index Resolution)
# ---------------------------------------------------------------------------

st.subheader("📋 Starting Pitcher Overview")
pitcher_slate = build_pitcher_slate(slate, pitcher_stats, {
    int(pid): {"recent_era": ctx[f"{side}_p_row"].get("recent_era"),
                "recent_k9": ctx[f"{side}_p_row"].get("recent_k9"),
                "days_rest": ctx[f"{side}_p_row"].get("days_rest"),
                "avg_recent_pitches": ctx[f"{side}_p_row"].get("avg_recent_pitches")}
    for gpk, ctx in game_context_map.items()
    for side in ("away", "home")
    for pid in [slate[slate["gamePk"] == gpk].iloc[0][f"{side}_pitcher_id"]]
    if pid and not pd.isna(pid)
})

if not pitcher_slate.empty:
    pitcher_slate["verdict"] = pitcher_slate["test_score"].apply(lambda x: verdict_color(x, scale=(45, 65)))
    
    rename_mapping = {
        "test_score": "Test", "kHR": "kHR", "proj_k": "Proj K", "form_arrow": "Trend",
        "era": "ERA", "whip": "WHIP", "k9": "K/9", "bb9": "BB/9", "hr9": "HR/9",
        "k_pct": "K%", "whiff_pct": "Whiff%", "csw_pct": "CSW%", "xwoba_allowed": "xwOBA", "barrel_allowed": "Brl%",
        "recent_era": "L5 ERA", "recent_k9": "L5 K/9", "days_rest": "Rest", "avg_recent_pitches": "Pitches"
    }
    
    base_cols = ["verdict", "pitcher_name", "team", "home_away", "opp", "throws"]
    existing_data_cols = [c for c in rename_mapping.keys() if c in pitcher_slate.columns]
    
    # Pristine serialization isolation block
    display = pitcher_slate[base_cols + existing_data_cols].copy()
    display = display.rename(columns={
        **{"verdict": "", "pitcher_name": "Pitcher", "team": "Tm", "home_away": "", "opp": "Opp", "throws": "T"},
        **rename_mapping
    })
    
    # Force single baseline evaluation tracking
    display = display.reset_index(drop=True)
    
    green_p = [c for c in ["Test", "kHR", "Proj K", "K/9", "K%", "Whiff%", "CSW%", "L5 K/9"] 
               if c in display.columns and pd.to_numeric(display[c], errors='coerce').notna().any()]
    red_p = [c for c in ["ERA", "WHIP", "BB/9", "HR/9", "xwOBA", "Brl%", "L5 ERA"] 
             if c in display.columns and pd.to_numeric(display[c], errors='coerce').notna().any()]
    
    sty = display.style
    if green_p:
        sty = sty.background_gradient(cmap="RdYlGn", subset=green_p)
    if red_p:
        sty = sty.background_gradient(cmap="RdYlGn_r", subset=red_p)
        
    format_dict = {}
    one_decimal_targets = ["Test", "kHR", "Proj K", "K%", "Whiff%", "CSW%", "Brl%"]
    two_decimal_targets = ["ERA", "WHIP", "K/9", "BB/9", "HR/9", "L5 ERA", "L5 K/9"]
    
    for c in one_decimal_targets:
        if c in display.columns: format_dict[c] = "{:.1f}"
    for c in two_decimal_targets:
        if c in display.columns: format_dict[c] = "{:.2f}"
            
    if "xwOBA" in display.columns: format_dict["xwOBA"] = "{:.3f}"
    if "Rest" in display.columns: format_dict["Rest"] = "{:.0f}d"
    if "Pitches" in display.columns: format_dict["Pitches"] = "{:.0f}"

    sty = sty.format(format_dict, na_rep="—")
    st.dataframe(sty, hide_index=True, use_container_width=True, height=350)

st.divider()


# ---------------------------------------------------------------------------
# 🎮 Game Breakdowns Section (Isolated Matchups)
# ---------------------------------------------------------------------------

st.subheader("🎮 Isolated Game-by-Game Matchups")
st.caption("Every active position player mapped directly to today's starting pitcher. Columns are color-weighted dynamically.")

def _render_isolated_matchup(df: pd.DataFrame):
    """Render hitter grid using localized layout settings."""
    if df is None or df.empty:
        st.write("No roster or lineup data compiled.")
        return

    show_cols = [
        "verdict", "player_name", "lineup_pos", "bats", "position",
        "hr_game_pct", "hr_pa_pct", "matchup", "test_score", "barrel_pct", "iso", "xwoba", "xwobacon",
        "pitch_match_score", "best_pitch", "best_pitch_xwoba", "worst_pitch",
        "fb_pct", "la", "k_pct", "bb_pct", "whiff_pct", "home_run", "recent_hr", "sleeper_score", "gs_score",
    ]
    keep = [c for c in show_cols if c in df.columns]
    display = df[keep].copy().rename(columns={
        "verdict": "", "player_name": "Hitter", "lineup_pos": "#", "position": "Pos", "bats": "B",
        "hr_game_pct": "HR Game%", "hr_pa_pct": "HR PA%", "matchup": "Matchup", "test_score": "Test",
        "barrel_pct": "Brl%", "iso": "ISO", "xwoba": "xwOBA", "xwobacon": "xwOBAcon",
        "pitch_match_score": "Pitch Match", "best_pitch": "Best Pitch", "best_pitch_xwoba": "Best xwOBA", "worst_pitch": "Worst Pitch",
        "fb_pct": "FB%", "la": "LA", "k_pct": "K%", "bb_pct": "BB%", "whiff_pct": "Whiff%",
        "home_run": "HR", "recent_hr": "L15 HR", "sleeper_score": "Sleeper", "gs_score": "GS",
    })
    
    display = display.reset_index(drop=True)

    green_m = [c for c in ["HR Game%", "HR PA%", "Matchup", "Test", "Pitch Match", "ISO", "xwOBA", "xwOBAcon",
                           "Brl%", "FB%", "BB%", "HR", "L15 HR", "Sleeper", "GS"] 
               if c in display.columns and pd.to_numeric(display[c], errors='coerce').notna().any()]
    red_m = [c for c in ["K%", "Whiff%"] 
             if c in display.columns and pd.to_numeric(display[c], errors='coerce').notna().any()]

    sty = display.style
    if green_m:
        sty = sty.background_gradient(cmap="RdYlGn", subset=green_m)
    if red_m:
        sty = sty.background_gradient(cmap="RdYlGn_r", subset=red_m)
        
    m_format_dict = {}
    m_one_decimal = ["Matchup", "Test", "Pitch Match", "Sleeper"]
    for c in m_one_decimal:
        if c in display.columns: m_format_dict[c] = "{:.1f}"
            
    if "HR Game%" in display.columns: m_format_dict["HR Game%"] = "{:.1f}%"
    if "HR PA%" in display.columns: m_format_dict["HR PA%"] = "{:.2f}%"
    if "GS" in display.columns: m_format_dict["GS"] = "{:.2f}"
    if "ISO" in display.columns: m_format_dict["ISO"] = "{:.3f}"
    if "xwOBA" in display.columns: m_format_dict["xwOBA"] = "{:.3f}"
    if "xwOBAcon" in display.columns: m_format_dict["xwOBAcon"] = "{:.3f}"
    if "Best xwOBA" in display.columns: m_format_dict["Best xwOBA"] = "{:.3f}"
    if "Brl%" in display.columns: m_format_dict["Brl%"] = "{:.1f}%"
    if "FB%" in display.columns: m_format_dict["FB%"] = "{:.1f}%"
    if "K%" in display.columns: m_format_dict["K%"] = "{:.1f}%"
    if "BB%" in display.columns: m_format_dict["BB%"] = "{:.1f}%"
    if "Whiff%" in display.columns: m_format_dict["Whiff%"] = "{:.1f}%"
    if "LA" in display.columns: m_format_dict["LA"] = "{:.1f}"

    sty = sty.format(m_format_dict, na_rep="—")
    st.dataframe(sty, hide_index=True, use_container_width=True)

def _get_label_string(row):
    try:
        t = pd.to_datetime(row["gameTime"]).tz_convert("US/Eastern")
        return f"🏟️ {row['away_team_abbr']} @ {row['home_team_abbr']} ({t.strftime('%-I:%M %p ET')})"
    except Exception:
        return f"🏟️ {row['away_team_abbr']} @ {row['home_team_abbr']}"

# Create isolated views inside containers for each game
for _, game in slate.iterrows():
    ctx = game_context_map[game["gamePk"]]
    park = ctx["park"]
    vegas = ctx.get("vegas") or {}
    ump = ctx.get("ump", {})
    
    panel_title = _get_label_string(game)
    
    with st.container(border=True):
        st.markdown(f"### {panel_title}")
        
        # Environmental and Market Data Row
        env1, env2, env3, env4, env5 = st.columns(5)
        env1.metric(f"Away Starter ({game['away_team_abbr']})", game["away_pitcher"] or "TBD", delta=f"Proj K: {ctx['away_k_proj'].get('mean', '—')}")
        env2.metric(f"Home Starter ({game['home_team_abbr']})", game["home_pitcher"] or "TBD", delta=f"Proj K: {ctx['home_k_proj'].get('mean', '—')}")
        env3.metric("Weather Multiplier", f"{ctx['hr_mult']:.2f}×", delta=f"{(ctx['hr_mult'] - 1) * 100:+.0f}% Impact")
        
        if vegas and vegas.get("total"):
            env4.metric("Vegas Line (O/U)", f"{vegas['total']:.1f}", delta=f"Away Implied: {vegas.get('away_implied', '—')} | Home Implied: {vegas.get('home_implied', '—')}")
        else:
            env4.metric("Vegas Line (O/U)", "—")
            
        env5.metric("Plate Umpire", ump.get("name", "TBD")[:16])

        # Stadium info sub-card
        st.caption(
            f"📍 **Venue:** {game.get('venue', 'TBD')} ({park.get('roof', 'open')})  ·  "
            f"**Base Park HR Factor:** {park.get('hr_factor', 100)}  ·  "
            f"**Atmospheric Context:** {ctx['summary'] or 'No wind data available'}  ·  "
            f"**Catcher Framing Influence:** Away: {ctx['away_framing']:.2f}× / Home: {ctx['home_framing']:.2f}×"
        )
        
        # Dynamic Data Tabs for separate batting views
        away_tab_title = f"🏏 {game['away_team_abbr']} Hitters vs {game['home_pitcher']}"
        home_tab_title = f"🏏 {game['home_team_abbr']} Hitters vs {game['away_pitcher']}"
        k_tab_title = "🎯 Pitcher Strikeout Projections"
        
        batting_tabs = st.tabs([away_tab_title, home_tab_title, k_tab_title])
        
        with batting_tabs[0]:
            _render_isolated_matchup(ctx["away_matchup"])
            
        with batting_tabs[1]:
            _render_isolated_matchup(ctx["home_matchup"])
            
        with batting_tabs[2]:
            kp1, kp2 = st.columns(2)
            for col, side, side_label in [(kp1, "away", game["away_pitcher"]), (kp2, "home", game["home_pitcher"])]:
                with col:
                    p_proj = ctx[f"{side}_k_proj"]
                    if not p_proj or p_proj.get("mean") is None:
                        st.write(f"**{side_label}** — Baseline data unavailable.")
                        continue
                    st.markdown(f"**{side_label}**  ·  Calculated Mean: **{p_proj['mean']:.1f} K** (Expected Variance: {p_proj['low']:.1f}–{p_proj['high']:.1f})")
                    st.caption(f"Adjusted K/9: {p_proj['blended_k9']:.2f}  ·  Lineup Strikeout Rate Factor: {p_proj['lineup_adj']:.2f}×")
                    
                    lines_df = pd.DataFrame([
                        {"Line Threshold": "Over 5.5 Strikeouts", "Probability Edge": p_proj.get("p_over_5.5", 0)},
                        {"Line Threshold": "Over 6.5 Strikeouts", "Probability Edge": p_proj.get("p_over_6.5", 0)},
                        {"Line Threshold": "Over 7.5 Strikeouts", "Probability Edge": p_proj.get("p_over_7.5", 0)},
                        {"Line Threshold": "Over 8.5 Strikeouts", "Probability Edge": p_proj.get("p_over_8.5", 0)},
                    ])
                    
                    lines_df = lines_df.reset_index(drop=True)
                    k_subset = ["Probability Edge"] if "Probability Edge" in lines_df.columns and pd.to_numeric(lines_df["Probability Edge"], errors='coerce').notna().any() else []
                    
                    k_sty = lines_df.style
                    if k_subset:
                        k_sty = k_sty.background_gradient(cmap="RdYlGn", subset=k_subset)
                        
                    st.dataframe(
                        k_sty.format({"Probability Edge": "{:.0%}"}, na_rep="—"),
                        hide_index=True, use_container_width=True
                    )
                    
        # Optional Context Expanders localized strictly inside the game card
        if use_bvp or not pitcher_arsenal_all.empty:
            with st.expander("🔬 Supplemental Batter-vs-Pitcher & Arsenal Details", expanded=False):
                sub_c1, sub_c2 = st.columns(2)
                
                with sub_c1:
                    if use_bvp and game["home_pitcher_id"]:
                        st.markdown(f"**{game['away_team_abbr']} Lineup History vs. Pitcher**")
                        bvp_a = bvp_for_lineup(ctx["away_lineup"], int(game["home_pitcher_id"]))
                        if not bvp_a.empty:
                            st.dataframe(bvp_a.reset_index(drop=True), hide_index=True, use_container_width=True)
                        else:
                            st.caption("No historical head-to-head tracking found for this lineup split.")
                            
                with sub_c2:
                    if not pitcher_arsenal_all.empty:
                        for p_name, p_id in [(game["away_pitcher"], game["away_pitcher_id"]), (game["home_pitcher"], game["home_pitcher_id"])]:
                            if p_id and pd.notna(p_id):
                                st.markdown(f"**{p_name} Pitch Profiles**")
                                a = pitcher_arsenal_all[pitcher_arsenal_all.get("player_id") == p_id] if "player_id" in pitcher_arsenal_all.columns else pd.DataFrame()
                                if not a.empty:
                                    summary_cols = [c for c in ["pitch_name", "pitch_usage", "ba", "slg", "woba", "whiff_percent"] if c in a.columns]
                                    a_disp = a[summary_cols].rename(columns={"pitch_name": "Pitch", "pitch_usage": "Usage%", "whiff_percent": "Whiff%"})
                                    st.dataframe(a_disp.reset_index(drop=True).style.format({"Usage%": "{:.1f}%", "Whiff%": "{:.1f}%"}, na_rep="—"), hide_index=True, use_container_width=True)
