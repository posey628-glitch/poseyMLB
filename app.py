from __future__ import annotations

from datetime import date, datetime
import pandas as pd
import streamlit as st

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

@st.cache_data(ttl=1800)
def c_pitch_arsenal(): return get_pitcher_arsenal()

@st.cache_data(ttl=1800)
def c_hitter_pitch(): return get_hitter_pitch_arsenal()

@st.cache_data(ttl=900)
def c_weather(lat, lon, dt): return fetch_weather(lat, lon, dt)

# ---------------- HELPERS ----------------

def calculate_4tier_emoji(score, scale=(45,65)):
    if pd.isna(score): return "⚪"
    low, high = scale
    mid = (low+high)/2
    if score >= high: return "🟢"
    elif score >= mid: return "🟡"
    elif score >= low: return "🟠"
    return "🔴"

def row_color(row):
    edge = row.get("model_edge", None)
    if pd.isna(edge): return [""] * len(row)

    if edge >= 8:
        return ["background-color: rgba(0,255,0,0.15)"] * len(row)
    elif edge >= 4:
        return ["background-color: rgba(255,255,0,0.15)"] * len(row)
    elif edge >= 0:
        return ["background-color: rgba(255,165,0,0.15)"] * len(row)
    else:
        return ["background-color: rgba(255,0,0,0.10)"] * len(row)

# ---------------- SIDEBAR ----------------

with st.sidebar:
    st.title("⚾ MLB Props")

    selected_date = st.date_input("Slate", value=date.today())

    use_pitch_match = st.checkbox("Pitch Match", True)
    use_umpire = st.checkbox("Umpire", True)

    st.markdown("## 🎯 Filters")

    search_player = st.text_input("Search Player")
    min_hr = st.slider("Min HR%", 0.0, 50.0, 5.0)

# ---------------- LOAD ----------------

slate = c_slate(selected_date.isoformat())
if slate.empty:
    st.stop()

hitter_stats = c_hitter().merge(c_h_trad(), on="player_id", how="left")
pitcher_stats = c_pitcher().merge(c_p_trad(), on="player_id", how="left")

pitcher_arsenal = c_pitch_arsenal()
hitter_pitch = c_hitter_pitch()

# ---------------- FILTER ----------------

def apply_filters(df):
    if df.empty: return df

    if search_player and "player_name" in df.columns:
        df = df[df["player_name"].str.contains(search_player, case=False, na=False)]

    if "hr_game_pct" in df.columns:
        df = df[df["hr_game_pct"] >= min_hr]

    return df

# ---------------- ENGINE ----------------

game_map = {}

for g in slate.itertuples():

    park = get_park(getattr(g,"venue",""))
    weather = c_weather(park.get("lat"), park.get("lon"), datetime.now()) if park.get("lat") else {}

    wx_mult, wx_text = hr_multiplier(weather, park)
    park_mult = park.get("hr_factor",100)/100
    total_mult = wx_mult * park_mult

    ump = get_umpire_for_game(int(g.gamePk)) if use_umpire else {"k_factor":1}

    away_lineup = get_lineup(g.gamePk,"away")
    home_lineup = get_lineup(g.gamePk,"home")

    ap = pitcher_stats[pitcher_stats.player_id==g.away_pitcher_id]
    hp = pitcher_stats[pitcher_stats.player_id==g.home_pitcher_id]

    ap = ap.iloc[0].to_dict() if len(ap) else {}
    hp = hp.iloc[0].to_dict() if len(hp) else {}

    away_df = build_matchup_table(away_lineup, pd.Series(hp), hitter_stats, pitcher_stats)
    home_df = build_matchup_table(home_lineup, pd.Series(ap), hitter_stats, pitcher_stats)

    if use_pitch_match:
        for df,lineup,pid in [(away_df,away_lineup,g.home_pitcher_id),(home_df,home_lineup,g.away_pitcher_id)]:
            pm = lineup_pitch_match(lineup,pid,hitter_pitch,pitcher_arsenal)
            if isinstance(pm, pd.DataFrame) and not pm.empty:
                df[:] = df.merge(pm,on="player_id",how="left")

    for df,p in [(away_df,hp),(home_df,ap)]:

        if df.empty: continue

        df = hr_probability(df, pd.Series(p), total_mult)

        # DO NOT FAKE DATA — only compute if possible
        if "hr_prob" not in df.columns:
            continue

        hr_game, edges = [], []

        for row in df.itertuples():

            pa = hr_prob_per_pa(
                row._asdict(), p,
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

# ---------------- TOP EDGES ----------------

st.subheader("🔥 Top Model HR Edges")

all_hitters = [ctx["away"] for ctx in game_map.values()] + \
              [ctx["home"] for ctx in game_map.values()]

combined = pd.concat([df for df in all_hitters if not df.empty], ignore_index=True)

if not combined.empty and "model_edge" in combined.columns:
    st.dataframe(
        combined.sort_values("model_edge", ascending=False)[[
            "player_name","hr_game_pct","model_edge","iso","xwoba"
        ]],
        use_container_width=True
    )

# ---------------- RENDER ----------------

def render(df):

    if df.empty:
        st.write("No data")
        return

    df = apply_filters(df)

    cols = [c for c in [
        "player_name","hr_game_pct","model_edge","iso","xwoba"
    ] if c in df.columns]

    display_df = df[cols].copy()

    styled = display_df.style.apply(row_color, axis=1)

    st.dataframe(styled, use_container_width=True)

# ---------------- UI ----------------

st.subheader("🎮 Game Matchups")

for g in slate.itertuples():

    ctx = game_map[g.gamePk]

    st.markdown(f"## {g.away_team_abbr} @ {g.home_team_abbr}")

    c1,c2 = st.columns(2)
    c1.metric("HR Mult", f"{ctx['hr_mult']:.2f}")
    c2.metric("Weather", ctx["weather"])

    t1,t2 = st.tabs(["Away","Home"])

    with t1: render(ctx["away"])
    with t2: render(ctx["home"])
``
