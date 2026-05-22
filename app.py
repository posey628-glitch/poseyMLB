from __future__ import annotations

from datetime import date, datetime
import pandas as pd
import streamlit as st
import requests
import re

# ---------------- IMPORTS ----------------
from data_fetcher import get_slate, get_hitter_stats, get_pitcher_stats, get_hitter_traditional
from models import build_matchup_table
from park_factors import get_park
from weather import fetch_weather, hr_multiplier
from props import hr_prob_per_pa, hr_prob_full_game

# ---------------- PAGE ----------------
st.set_page_config(layout="wide", page_title="MLB HR Dashboard ⚾")

# ---------------- CACHE ----------------
@st.cache_data(ttl=1800)
def c_slate(d):
    return get_slate(d)

@st.cache_data(ttl=1800)
def c_hitter():
    return get_hitter_stats()

@st.cache_data(ttl=1800)
def c_pitcher():
    return get_pitcher_stats()

@st.cache_data(ttl=1800)
def c_trad():
    return get_hitter_traditional()

@st.cache_data(ttl=900)
def c_weather(lat, lon):
    return fetch_weather(lat, lon, datetime.now())

# ✅ PLAYER LOOKUP (INLINE — NO IMPORT ERROR)
@st.cache_data(ttl=86400)
def get_player_lookup():
    try:
        url = "https://statsapi.mlb.com/api/v1/sports/1/players"
        r = requests.get(url, timeout=10)
        data = r.json()

        rows = []
        for p in data.get("people", []):
            rows.append({
                "player_id": p.get("id"),
                "player_name": p.get("fullName")
            })

        return pd.DataFrame(rows)

    except:
        return pd.DataFrame(columns=["player_id", "player_name"])

# ---------------- HELPERS ----------------
def clean_name(name):
    if not isinstance(name, str):
        return name
    return re.sub(r"[^a-zA-Z\s]", "", name).lower().strip()

def color_row(row):
    edge = row.get("model_edge")

    if pd.isna(edge):
        return [""] * len(row)

    if edge >= 8:
        return ["background-color:#b6fcb6"] * len(row)
    elif edge >= 4:
        return ["background-color:#fff3b0"] * len(row)
    elif edge >= 0:
        return ["background-color:#ffd6a5"] * len(row)
    else:
        return ["background-color:#ffadad"] * len(row)

# ---------------- SIDEBAR ----------------
with st.sidebar:
    selected_date = st.date_input("Slate", value=date.today())
    search = st.text_input("Search Player")
    min_hr = st.slider("Min HR %", 0.0, 50.0, 5.0)

# ---------------- LOAD DATA ----------------
slate = c_slate(str(selected_date))
if slate.empty:
    st.stop()

hitter = c_hitter().merge(c_trad(), on="player_id", how="left")

# ✅ Attach names via ID
lookup = get_player_lookup()
hitter = hitter.merge(lookup, on="player_id", how="left")

if "player_name" not in hitter.columns:
    st.error("Player name mapping failed.")
    st.stop()

pitcher = c_pitcher()

# ---------------- ENGINE ----------------
games = {}

for g in slate.itertuples():

    park = get_park(getattr(g, "venue", ""))

    weather = None
    if park.get("lat"):
        weather = c_weather(park.get("lat"), park.get("lon"))
    else:
        weather = {}

    hr_mult, _ = hr_multiplier(weather, park)

    # Minimal matchup build (safe)
    away_df = build_matchup_table([], pd.Series({}), hitter, pitcher)
    home_df = build_matchup_table([], pd.Series({}), hitter, pitcher)

    for df in [away_df, home_df]:

        if df.empty:
            continue

        if "hr_prob" not in df.columns:
            continue

        hr_vals = []
        edge_vals = []

        for row in df.itertuples():
            pa = hr_prob_per_pa(row._asdict(), {})
            hr_game = hr_prob_full_game(pa) * 100

            hr_vals.append(hr_game)
            edge_vals.append(hr_game - 12.5)

        df["hr_game_pct"] = hr_vals
        df["model_edge"] = edge_vals

    games[g.gamePk] = {
        "away": away_df,
        "home": home_df
    }

# ---------------- RENDER ----------------
def render(df):

    if df.empty:
        st.write("No data")
        return

    if search:
        df = df[df["player_name"].astype(str).str.contains(search, case=False, na=False)]

    if "hr_game_pct" in df.columns:
        df = df[df["hr_game_pct"] >= min_hr]

    cols = [c for c in [
        "player_name",
        "hr_game_pct",
        "model_edge"
    ] if c in df.columns]

    styled = df[cols].style.apply(color_row, axis=1)
    st.dataframe(styled, use_container_width=True)

# ---------------- UI ----------------
st.title("⚾ MLB HR Dashboard")

for g in slate.itertuples():

    st.markdown(f"### {g.away_team_abbr} @ {g.home_team_abbr}")

    ctx = games[g.gamePk]

    tab1, tab2 = st.tabs(["Away","Home"])

    with tab1:
        render(ctx["away"])

    with tab2:
        render(ctx["home"])
``
