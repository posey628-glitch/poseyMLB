from __future__ import annotations

from datetime import date, datetime
import pandas as pd
import streamlit as st
import re
import requests

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

# ✅ ✅ INLINE PLAYER LOOKUP (FIXES ERROR FOREVER)
@st.cache_data(ttl=86400)
def get_player_lookup():
    try:
        url = "https://statsapi.mlb.com/api/v1/sports/1/players"
        r = requests.get(url, timeout=10)
        data = r.json()

        players = [
            {"player_id": p["id"], "player_name": p["fullName"]}
            for p in data.get("people", [])
        ]

        return pd.DataFrame(players)

    except:
        return pd.DataFrame(columns=["player_id", "player_name"])

# ✅ INLINE NAME CLEAN
def clean_name(name):
    if not isinstance(name, str):
        return name
    return re.sub(r"[^a-zA-Z\s]", "", name).lower().strip()

# ✅ COLORS
def color_row(row):
    e = row.get("model_edge", None)

    if pd.isna(e):
        return [""] * len(row)

    if e >= 8:
        return ["background-color:#b6fcb6"] * len(row)
    elif e >= 4:
        return ["background-color:#fff3b0"] * len(row)
    elif e >= 0:
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

# ✅ FIX: Add player names via lookup
lookup = get_player_lookup()
hitter = hitter.merge(lookup, on="player_id", how="left")

if "player_name" not in hitter.columns:
    st.error("Player lookup failed")
    st.stop()

pitcher = c_pitcher()

# ---------------- ENGINE ----------------

games = {}

for g in slate.itertuples():

    park = get_park(getattr(g, "venue", ""))

    wx = (
        c_weather(park.get("lat"), park.get("lon"))
        if park.get("lat")
        else {}
    )

    mult, _ = hr_multiplier(wx, park)

    away = build_matchup_table([], pd.Series({}), hitter, pitcher)
    home = build_matchup_table([], pd.Series({}), hitter, pitcher)

    for df in [away, home]:

        if df.empty:
            continue

        if "hr_prob" not in df.columns:
            continue

        hr_vals = []
        edge_vals = []

        for row in df.itertuples():

            pa = hr_prob_per_pa(row._asdict(), {})
            ghr = hr_prob_full_game(pa) * 100

            hr_vals.append(ghr)
            edge_vals.append(ghr - 12.5)

        df["hr_game_pct"] = hr_vals
        df["model_edge"] = edge_vals

    games[g.gamePk] = dict(away=away, home=home)

# ---------------- RENDER ----------------

def render(df):

    if df.empty:
        st.write("No data")
        return

    if search:
        df = df[df["player_name"].str.contains(search, case=False, na=False)]

    if "hr_game_pct" in df.columns:
        df = df[df["hr_game_pct"] >= min_hr]

    cols = [c for c in [
        "player_name","hr_game_pct","model_edge"
    ] if c in df.columns]

    st.dataframe(
        df[cols].style.apply(color_row, axis=1),
        use_container_width=True
    )

# ---------------- UI ----------------

st.title("⚾ MLB HR Dashboard")

for g in slate.itertuples():

    ctx = games[g.gamePk]

    st.markdown(f"### {g.away_team_abbr} @ {g.home_team_abbr}")

    t1, t2 = st.tabs(["Away","Home"])

    with t1:
        render(ctx["away"])

    with t2:
        render(ctx["home"])
``
