"""
app.py — Posey MLB HR & K Data Dashboard (Final Production Build)
"""
from __future__ import annotations
from datetime import date, datetime
import pandas as pd
import streamlit as st

# --- ALL HELPERS ---
from data_fetcher import (
    get_slate, get_lineup, get_team_roster, get_hitter_stats, get_pitcher_stats, 
    get_pitcher_arsenal, get_hitter_traditional, get_pitcher_traditional, 
    get_pitcher_recent_form, get_hitter_recent_form_trad
)
from models import build_matchup_table, build_pitcher_slate
from park_factors import get_park
from weather import fetch_weather, hr_multiplier
from sleepers import hr_probability, find_sleepers, grand_slam_probability
from splits import bvp_for_lineup
from pitch_match import get_hitter_pitch_arsenal, lineup_pitch_match
from game_context import (
    get_umpire_for_game, get_vegas_totals, get_pitcher_workload, 
    ttop_multiplier, park_hand_factor
)
from props import hr_prob_per_pa, hr_prob_full_game, k_total_projection, verdict_color

# --- PAGE CONFIG ---
st.set_page_config(page_title="Posey MLB HR & K Data", layout="wide", page_icon="⚾")

# --- ENGINE FUNCTIONS ---
def calculate_4tier_emoji(score: float) -> str:
    if pd.isna(score) or score > 95: return "⚪"
    if score >= 65: return "🟢"
    if score >= 55: return "🟡"
    if score >= 45: return "🟠"
    return "🔴"

def impute_stats(df: pd.DataFrame, type="hitter"):
    defaults = {"barrel_pct": 7.8, "iso": 0.165, "xwoba": 0.318, "xwobacon": 0.365, "la": 12.0} if type == "hitter" else {"era": 3.85, "k9": 8.6}
    for col, val in defaults.items():
        if col in df.columns: df[col] = df[col].fillna(val)
    return df

# --- SIDEBAR ---
with st.sidebar:
    selected_date = st.date_input("Slate date", value=date.today())
    if st.button("🔄 Refresh Data"): st.cache_data.clear(); st.rerun()

# --- DATA LOAD ---
slate = get_slate(selected_date.isoformat())
hitter_stats, pitcher_stats = get_hitter_stats(), get_pitcher_stats()
if slate.empty: st.stop()

# --- RENDER ENGINE ---
def render_game(game):
    pk = int(game["gamePk"])
    away_lineup = get_lineup(pk, "away")
    home_lineup = get_lineup(pk, "home")
    
    # Logic to build tables
    away_matchup = build_matchup_table(away_lineup, None, hitter_stats, pitcher_stats)
    home_matchup = build_matchup_table(home_lineup, None, hitter_stats, pitcher_stats)
    
    # Process Matchups
    for df in [away_matchup, home_matchup]:
        df = impute_stats(df, "hitter")
        df["alert"] = df["test_score"].apply(lambda x: calculate_4tier_emoji(x))
    
    st.subheader(f"🏟️ {game['away_team_abbr']} @ {game['home_team_abbr']}")
    col1, col2 = st.columns(2)
    with col1: st.dataframe(away_matchup, column_config={"alert": st.column_config.TextColumn("Signal", help="🟢Elite, 🟡Pace, 🟠Caution, 🔴Fade")})
    with col2: st.dataframe(home_matchup, column_config={"alert": st.column_config.TextColumn("Signal", help="🟢Elite, 🟡Pace, 🟠Caution, 🔴Fade")})

# --- EXECUTION ---
for _, game in slate.iterrows():
    try:
        render_game(game)
    except Exception as e:
        st.error(f"Error in {game['away_team_abbr']}: {e}")

st.subheader("📋 Starting Pitcher Overview")
st.dataframe(build_pitcher_slate(slate, pitcher_stats, {}))
