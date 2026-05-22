from __future__ import annotations

from datetime import date, datetime
import pandas as pd
import streamlit as st
import requests

# ---------------- IMPORTS ----------------
from data_fetcher import (
    get_slate,
    get_hitter_stats,
    get_pitcher_stats,
    get_hitter_traditional,
    get_lineup
)

from models import build_matchup_table
from park_factors import get_park
from weather import fetch_weather, hr_multiplier
from props import hr_prob_per_pa, hr_prob_full_game

# ---------------- PAGE ----------------
st.set_page_config(layout="wide", page_title="MLB HR Dashboard ⚾")

# ---------------- CACHE ----------------
@st.cache_data(ttl=1800)
def c_slate(d): return get_slate(d)

@st.cache_data(ttl=1800)
def c_hitter(): return get_hitter_stats()

@st.cache_data(ttl=1800)
def c_pitcher(): return get_pitcher_stats()

@st.cache_data(ttl=1800)
def c_trad(): return get_hitter_traditional()

@st.cache_data(ttl=900)
def c_weather(lat, lon):
    return fetch_weather(lat, lon, datetime.now())

@st.cache_data(ttl=86400)
def get_player_lookup():
    try:
        url = "https://statsapi.mlb.com/api/v1/sports/1/players"
        r = requests.get(url, timeout=10)
        data = r.json()

        return pd.DataFrame([
            {"player_id": p.get("id"), "player_name": p.get("fullName")}
            for p in data.get("people", [])
        ])
    except:
        return pd.DataFrame(columns=["player_id", "player_name"])

# ---------------- HELPERS ----------------
def color_row(row):
    e = row.get("model_edge")

    if pd.isna(e):
        return [""] * len(row)

    if e >= 8:
        return ["background-color:#b6fcb6"] * len(row)
    elif e >= 4:
        return ["background-color:#fff3b0"] * len(row)
    elif e >= 0:
        return ["background-color:#ffd6a5"] * len(row)
    return ["background-color:#ffadad"] * len(row)

# ---------------- SIDEBAR ----------------
with st.sidebar:
    selected_date = st.date_input("Slate", value=date.today())
    search = st.text_input("Search Player")
    min_hr = st.slider("Min HR %", 0.0, 50.0, 5.0)

# ---------------- LOAD ----------------
slate = c_slate(str(selected_date))

if slate.empty:
    st.warning("No games available")
    st.stop()

hitter = c_hitter().merge(c_trad(), on="player_id", how="left")

# ✅ add player names (ID → name)
lookup = get_player_lookup()
hitter = hitter.merge(lookup, on="player_id", how="left")

if "player_name" not in hitter.columns:
    st.error("Player lookup failed")
    st.stop()

# ✅ build name map (CRITICAL FIX)
id_to_name = dict(zip(hitter["player_id"], hitter["player_name"]))

pitcher = c_pitcher()

# ---------------- ENGINE ----------------
games = {}

for g in slate.itertuples():

    park = get_park(getattr(g, "venue", ""))

    weather = {}
    if park.get("lat"):
        weather = c_weather(park.get("lat"), park.get("lon"))

    hr_mult, _ = hr_multiplier(weather, park)

    # ✅ fetch lineups
    away_raw = get_lineup(g.gamePk, "away")
    home_raw = get_lineup(g.gamePk, "home")

    # ✅ normalize to IDs
    if not away_raw:
        away_ids = hitter.head(9)["player_id"].tolist()
    else:
        away_ids = [p if isinstance(p, int) else p.get("id") for p in away_raw]

    if not home_raw:
        home_ids = hitter.head(9)["player_id"].tolist()
    else:
        home_ids = [p if isinstance(p, int) else p.get("id") for p in home_raw]

    # ✅ FINAL FIX: attach names
    away_lineup = [
        {"id": pid, "name": id_to_name.get(pid, "Unknown")}
        for pid in away_ids if pd.notna(pid)
    ]

    home_lineup = [
        {"id": pid, "name": id_to_name.get(pid, "Unknown")}
        for pid in home_ids if pd.notna(pid)
    ]

    # ✅ build matchup tables
    away_df = build_matchup_table(away_lineup, pd.Series({}), hitter, pitcher)
    home_df = build_matchup_table(home_lineup, pd.Series({}), hitter, pitcher)

    for df in (away_df, home_df):

        if df.empty:
            continue

        if "hr_prob" not in df.columns:
            continue

        hr_vals, edge_vals = [], []

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
        "model_edge",
        "iso",
        "xwoba"
    ] if c in df.columns]

    if not cols:
        st.write("No displayable data")
        return

    st.dataframe(
        df[cols].style.apply(color_row, axis=1),
        use_container_width=True
    )

# ---------------- UI ----------------
st.title("⚾ MLB HR Dashboard")

for g in slate.itertuples():

    st.markdown(f"### {g.away_team_abbr} @ {g.home_team_abbr}")

    ctx = games.get(g.gamePk, {"away": pd.DataFrame(), "home": pd.DataFrame()})

    t1, t2 = st.tabs(["Away", "Home"])

    with t1:
        render(ctx["away"])

    with t2:
        render(ctx["home"])
