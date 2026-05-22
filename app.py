from __future__ import annotations

from datetime import date, datetime
import pandas as pd
import streamlit as st
import re

# ---------------- IMPORTS ----------------
from data_fetcher import *
from models import *
from park_factors import get_park
from weather import fetch_weather, hr_multiplier
from sleepers import *
from pitch_match import *
from game_context import *
from props import *

# ---------------- PAGE ----------------
st.set_page_config(layout="wide", page_title="Posey MLB HR & K", page_icon="⚾")

# ---------------- CACHE ----------------
@st.cache_data(ttl=1800)
def c_slate(d): return get_slate(d)

@st.cache_data(ttl=1800)
def c_hitter(): return get_hitter_stats()

@st.cache_data(ttl=1800)
def c_pitcher(): return get_pitcher_stats()

@st.cache_data(ttl=1800)
def c_h_trad(): return get_hitter_traditional()

@st.cache_data(ttl=1800)
def c_p_trad(): return get_pitcher_traditional()

@st.cache_data(ttl=3600)
def c_fangraphs(): return get_fangraphs_hitter_stats()

@st.cache_data(ttl=1800)
def c_pitch_arsenal(): return get_pitcher_arsenal()

@st.cache_data(ttl=1800)
def c_hitter_pitch(): return get_hitter_pitch_arsenal()

@st.cache_data(ttl=900)
def c_weather(lat, lon, dt):
    return fetch_weather(lat, lon, dt)

# ---------------- HELPERS ----------------

def clean_name(name: str):
    if not isinstance(name, str):
        return name
    name = re.sub(r"[^a-zA-Z\s]", "", name)
    return name.lower().strip()

def calculate_4tier_emoji(score, scale=(45,65)):
    if pd.isna(score): return "⚪"
    low, high = scale
    mid = (low+high)/2
    if score >= high: return "🟢"
    elif score >= mid: return "🟡"
    elif score >= low: return "🟠"
    return "🔴"

def apply_stat_fallbacks(df):

    df["data_source"] = "statcast"

    if "iso" in df.columns and "iso_fg" in df.columns:
        use_fg = df["iso"].isna() & df["iso_fg"].notna()
        df.loc[use_fg, "iso"] = df.loc[use_fg, "iso_fg"]
        df.loc[use_fg, "data_source"] = "fangraphs"

    if "xwoba" in df.columns and "woba_fg" in df.columns:
        df["xwoba"] = df["xwoba"].combine_first(df["woba_fg"])

    return df

def add_data_score(df):

    scores = []

    for row in df.itertuples():

        s = 0

        if not pd.isna(getattr(row,"iso",None)): s += 2
        if not pd.isna(getattr(row,"xwoba",None)): s += 2
        if not pd.isna(getattr(row,"barrel_pct",None)): s += 2
        if not pd.isna(getattr(row,"k_pct",None)): s += 1

        scores.append(s)

    df["data_score"] = scores
    return df

def row_color(row):

    e = row.get("model_edge", None)

    if pd.isna(e): return [""]*len(row)

    if e >= 8:
        return ["background-color: rgba(0,255,0,0.15)"]*len(row)
    elif e >= 4:
        return ["background-color: rgba(255,255,0,0.15)"]*len(row)
    elif e >= 0:
        return ["background-color: rgba(255,165,0,0.15)"]*len(row)
    else:
        return ["background-color: rgba(255,0,0,0.10)"]*len(row)

# ---------------- SIDEBAR ----------------

with st.sidebar:
    st.title("⚾ MLB Props")
    selected_date = st.date_input("Slate", value=date.today())
    search_player = st.text_input("Search Player")
    min_hr = st.slider("Min HR%",0.0,50.0,5.0)

    if st.button("Refresh"):
        st.cache_data.clear()
        st.rerun()

# ---------------- LOAD ----------------

slate = c_slate(selected_date.isoformat())
if slate.empty:
    st.stop()

hitter_stats = c_hitter().merge(c_h_trad(), on="player_id", how="left")

# ✅ CLEAN NAME
hitter_stats["player_name_clean"] = hitter_stats["player_name"].apply(clean_name)

# ✅ FANGRAPHS
fg = c_fangraphs()
if not fg.empty:
    hitter_stats = hitter_stats.merge(fg, on="player_name_clean", how="left")

pitcher_stats = c_pitcher().merge(c_p_trad(), on="player_id", how="left")

pitcher_arsenal = c_pitch_arsenal()
hitter_pitch = c_hitter_pitch()

# ---------------- ENGINE ----------------

game_map = {}

for g in slate.itertuples():

    park = get_park(getattr(g,"venue",""))

    weather = c_weather(park.get("lat"), park.get("lon"), datetime.now()) if park.get("lat") else {}

    wx_mult, wx_text = hr_multiplier(weather, park)
    park_mult = park.get("hr_factor",100)/100
    total_mult = wx_mult * park_mult

    away_lineup = get_lineup(g.gamePk,"away")
    home_lineup = get_lineup(g.gamePk,"home")

    ap = pitcher_stats[pitcher_stats.player_id==g.away_pitcher_id]
    hp = pitcher_stats[pitcher_stats.player_id==g.home_pitcher_id]

    ap = ap.iloc[0].to_dict() if len(ap) else {}
    hp = hp.iloc[0].to_dict() if len(hp) else {}

    away_df = build_matchup_table(away_lineup, pd.Series(hp), hitter_stats, pitcher_stats)
    home_df = build_matchup_table(home_lineup, pd.Series(ap), hitter_stats, pitcher_stats)

    away_df = apply_stat_fallbacks(away_df)
    home_df = apply_stat_fallbacks(home_df)

    away_df = add_data_score(away_df)
    home_df = add_data_score(home_df)

    for df,p in [(away_df,hp),(home_df,ap)]:

        if df.empty: continue

        df = hr_probability(df, pd.Series(p), total_mult)

        if "hr_prob" not in df.columns:
            continue

        hr_game, edges = [], []

        for row in df.itertuples():

            pa = hr_prob_per_pa(
                row._asdict(),
                p,
                park_factor=park_mult,
                weather_mult=wx_mult
            )

            game_hr = hr_prob_full_game(pa)*100
            hr_game.append(game_hr)
            edges.append(game_hr - 12.5)

        df["hr_game_pct"] = hr_game
        df["model_edge"] = edges

    game_map[g.gamePk] = dict(
        away=away_df,
        home=home_df,
        weather=wx_text,
        hr_mult=total_mult
    )

# ---------------- VALIDATION ----------------

st.subheader("🧪 Data Validation")

all_df = pd.concat(
    [ctx["away"] for ctx in game_map.values()] +
    [ctx["home"] for ctx in game_map.values()],
    ignore_index=True
)

if not all_df.empty:
    st.write("Total Hitters:", len(all_df))
    st.write("Missing ISO:", all_df["iso"].isna().sum() if "iso" in all_df.columns else "-")

# ---------------- RENDER ----------------

def render(df):

    if df.empty:
        st.write("No data")
        return

    if search_player:
        df = df[df["player_name"].str.contains(search_player, case=False, na=False)]

    if "hr_game_pct" in df.columns:
        df = df[df["hr_game_pct"] >= min_hr]

    cols = [c for c in [
        "player_name","hr_game_pct","model_edge",
        "iso","xwoba","data_score","data_source"
    ] if c in df.columns]

    display_df = df[cols].copy()
    styled = display_df.style.apply(row_color, axis=1)

    st.dataframe(styled, use_container_width=True)

# ---------------- UI ----------------

st.subheader("🎮 Games")

for g in slate.itertuples():

    ctx = game_map[g.gamePk]

    st.markdown(f"### {g.away_team_abbr} @ {g.home_team_abbr}")

    c1,c2 = st.columns(2)
    c1.metric("HR Mult", f"{ctx['hr_mult']:.2f}")
    c2.metric("Weather", ctx["weather"])

    t1,t2 = st.tabs(["Away","Home"])

    with t1:
        render(ctx["away"])

    with t2:
        render(ctx["home"])
