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
from splits import *
from pitch_match import *
from game_context import *
from props import *

# ---------------- PAGE CONFIG ----------------
st.set_page_config(layout="wide", page_title="Posey MLB HR & K", page_icon="⚾")
st.markdown("<style>div[data-testid='stMetric']{min-height:85px}</style>", unsafe_allow_html=True)

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
def c_weather(lat, lon, dt):
    return fetch_weather(lat, lon, dt)

@st.cache_data(ttl=1200)
def c_vegas(d): return get_vegas_totals(d)

# ---------------- HELPERS ----------------
def calculate_4tier_emoji(score, scale=(45,65)):
    if pd.isna(score): return "⚪"
    low, high = scale
    mid = (low+high)/2
    if score >= high: return "🟢"
    elif score >= mid: return "🟡"
    elif score >= low: return "🟠"
    return "🔴"

def edge_color(val):
    if val >= 8:
        return "background-color: rgba(0,255,0,0.25)"
    elif val >= 4:
        return "background-color: rgba(255,255,0,0.2)"
    elif val < 0:
        return "background-color: rgba(255,0,0,0.15)"
    return ""

# ---------------- SIDEBAR ----------------
with st.sidebar:
    st.title("⚾ MLB Props")

    selected_date = st.date_input("Slate", value=date.today())

    use_pitch_match = st.checkbox("Pitch Match", True)
    use_vegas = st.checkbox("Vegas", True)
    use_umpire = st.checkbox("Umpire", True)

    st.markdown("---")
    st.markdown("## 🎯 Filters")

    search_player = st.text_input("Search Player")
    min_hr = st.slider("Min HR%", 0.0, 50.0, 5.0)
    min_pm = st.slider("Min Pitch Match", 0.0, 100.0, 40.0)

    sort_metric = st.selectbox(
        "Sort By",
        ["hr_game_pct", "model_edge", "iso", "xwoba", "pitch_match_score"]
    )

    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.rerun()

# ---------------- LOAD ----------------
slate = c_slate(selected_date.isoformat())
if slate.empty:
    st.warning("No games")
    st.stop()

hitter_stats = c_hitter().merge(c_h_trad(), on="player_id", how="left")
pitcher_stats = c_pitcher().merge(c_p_trad(), on="player_id", how="left")

pitcher_arsenal = c_pitch_arsenal()
hitter_pitch = c_hitter_pitch()
vegas_df = c_vegas(selected_date.isoformat()) if use_vegas else pd.DataFrame()

# ---------------- FILTER ----------------
def apply_filters(df):
    if df.empty: return df

    if search_player:
        df = df[df["player_name"].str.contains(search_player, case=False, na=False)]

    if "hr_game_pct" in df.columns:
        df = df[df["hr_game_pct"] >= min_hr]

    if "pitch_match_score" in df.columns:
        df = df[df["pitch_match_score"].fillna(0) >= min_pm]

    if sort_metric in df.columns:
        df = df.sort_values(sort_metric, ascending=False)

    return df

# ---------------- ENGINE ----------------
game_map = {}

for g in slate.itertuples():

    park = get_park(getattr(g,"venue",""))

    weather = c_weather(
        park.get("lat"),
        park.get("lon"),
        datetime.now()
    ) if park.get("lat") else {}

    wx_mult, wx_text = hr_multiplier(weather, park)
    park_mult = park.get("hr_factor",100)/100
    total_mult = wx_mult * park_mult

    ump = get_umpire_for_game(int(g.gamePk)) if use_umpire else {"k_factor":1}

    away_lineup = get_lineup(int(g.gamePk),"away")
    home_lineup = get_lineup(int(g.gamePk),"home")

    ap = pitcher_stats[pitcher_stats.player_id==g.away_pitcher_id]
    hp = pitcher_stats[pitcher_stats.player_id==g.home_pitcher_id]

    ap = ap.iloc[0].to_dict() if len(ap) else {}
    hp = hp.iloc[0].to_dict() if len(hp) else {}

    away_df = build_matchup_table(away_lineup,pd.Series(hp),hitter_stats,pitcher_stats)
    home_df = build_matchup_table(home_lineup,pd.Series(ap),hitter_stats,pitcher_stats)

    if use_pitch_match:
        for df,lineup,pid in [(away_df,away_lineup,g.home_pitcher_id),(home_df,home_lineup,g.away_pitcher_id)]:
            pm = lineup_pitch_match(lineup,pid,hitter_pitch,pitcher_arsenal)
            if not pm.empty and "player_id" in pm.columns:
                df[:] = df.merge(pm,on="player_id",how="left")

    for df,p in [(away_df,hp),(home_df,ap)]:

        if df.empty: continue

        df[:] = hr_probability(df,pd.Series(p),total_mult)
        df[:] = find_sleepers(df)
        df[:] = grand_slam_probability(df,pd.Series(p),total_mult)

        hr_game = []
        edges = []

        for row in df.itertuples():
            pa = hr_prob_per_pa(row._asdict(),p,
                park_factor=park_mult,
                weather_mult=wx_mult)

            game_hr = hr_prob_full_game(pa)*100
            hr_game.append(game_hr)

            # MODEL EDGE (NO SPORTSBOOK)
            edge = game_hr - 12.5
            edges.append(edge)

        df["hr_game_pct"] = hr_game
        df["model_edge"] = edges

    away_k = k_total_projection(ap,22,ump_k_factor=ump.get("k_factor",1))
    home_k = k_total_projection(hp,22,ump_k_factor=ump.get("k_factor",1))

    game_map[g.gamePk] = dict(
        away=away_df,
        home=home_df,
        away_k=away_k,
        home_k=home_k,
        weather=wx_text,
        hr_mult=total_mult
    )

# ---------------- SHARP PLAYS ----------------
st.subheader("🔥 Top Model HR Edges")

all_hitters = []
for ctx in game_map.values():
    if not ctx["away"].empty: all_hitters.append(ctx["away"])
    if not ctx["home"].empty: all_hitters.append(ctx["home"])

if all_hitters:
    combined = pd.concat(all_hitters)
    elite = combined.sort_values("model_edge", ascending=False).head(20)

    st.dataframe(
        elite[[
            "player_name",
            "hr_game_pct",
            "model_edge",
            "iso",
            "xwoba",
            "pitch_match_score"
        ]],
        use_container_width=True
    )

# ---------------- RENDER ----------------
def render(df):

    if df.empty:
        st.write("No data")
        return

    df = apply_filters(df)

    if df.empty:
        st.warning("No players match filters")
        return

    df["alert"] = df["test_score"].apply(calculate_4tier_emoji)

    cols = [c for c in [
        "alert","player_name","lineup_pos",
        "hr_game_pct","model_edge",
        "iso","xwoba","pitch_match_score","k_pct"
    ] if c in df.columns]

    styled = df[cols].style.applymap(edge_color, subset=["model_edge"])

    st.dataframe(styled, use_container_width=True)

# ---------------- UI ----------------
st.subheader("🎮 Game Matchups")

for g in slate.itertuples():

    ctx = game_map[g.gamePk]

    st.markdown(f"## {g.away_team_abbr} @ {g.home_team_abbr}")

    c1,c2 = st.columns(2)
    c1.metric("HR Multiplier", f"{ctx['hr_mult']:.2f}")
    c2.metric("Weather", ctx["weather"])

    combined = pd.concat([ctx["away"],ctx["home"]])
    if not combined.empty:
        best = combined.sort_values("hr_game_pct",ascending=False).iloc[0]
        st.success(f"👑 Top HR: {best['player_name']} ({best['hr_game_pct']:.1f}%)")

    t1,t2,t3 = st.tabs(["Away","Home","K Props"])

    with t1: render(ctx["away"])
    with t2: render(ctx["home"])

    with t3:
        st.write("Away K:",ctx["away_k"])
        st.write("Home K:",ctx["home_k"])

# ---------------- PITCHERS ----------------
st.subheader("📋 Pitcher Overview")

ps = build_pitcher_slate(slate,pitcher_stats,{})
ps["alert"] = ps["test_score"].apply(calculate_4tier_emoji)

st.dataframe(ps,use_container_width=True)
