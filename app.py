from __future__ import annotations

from datetime import date, datetime
import pandas as pd
import streamlit as st
import re

from data_fetcher import *
from models import *
from park_factors import get_park
from weather import fetch_weather, hr_multiplier
from sleepers import *
from pitch_match import *
from game_context import *
from props import *

# ---------------- PAGE ----------------
st.set_page_config(layout="wide", page_title="Posey MLB HR & K")

# ---------------- CACHE ----------------
@st.cache_data(ttl=1800)
def c_slate(d): return get_slate(d)

@st.cache_data(ttl=1800)
def c_hitter(): return get_hitter_stats()

@st.cache_data(ttl=1800)
def c_pitcher(): return get_pitcher_stats()

@st.cache_data(ttl=1800)
def c_h_trad(): return get_hitter_traditional()

@st.cache_data(ttl=86400)
def c_lookup(): return get_player_lookup()

@st.cache_data(ttl=3600)
def c_fg(): return get_fangraphs_hitter_stats()

@st.cache_data(ttl=900)
def c_weather(lat, lon, dt):
    return fetch_weather(lat, lon, dt)

# ---------------- HELPERS ----------------

def clean_name(name):
    if not isinstance(name, str):
        return name
    return re.sub(r"[^a-zA-Z\s]", "", name).lower().strip()

def apply_fallback(df):
    df["data_source"] = "statcast"

    if "iso" in df.columns and "iso_fg" in df.columns:
        mask = df["iso"].isna() & df["iso_fg"].notna()
        df.loc[mask, "iso"] = df.loc[mask, "iso_fg"]
        df.loc[mask, "data_source"] = "fangraphs"

    return df

def add_score(df):
    scores = []
    for r in df.itertuples():
        s = 0
        if not pd.isna(getattr(r,"iso",None)): s+=2
        if not pd.isna(getattr(r,"xwoba",None)): s+=2
        scores.append(s)
    df["data_score"] = scores
    return df

def color(row):
    e = row.get("model_edge")
    if pd.isna(e): return [""]*len(row)
    if e >= 8: return ["background-color:#b6fcb6"]*len(row)
    if e >= 4: return ["background-color:#fff3b0"]*len(row)
    if e >= 0: return ["background-color:#ffd6a5"]*len(row)
    return ["background-color:#ffadad"]*len(row)

# ---------------- SIDEBAR ----------------
with st.sidebar:
    date_sel = st.date_input("Slate", value=date.today())
    search = st.text_input("Search")
    min_hr = st.slider("Min HR %",0.0,50.0,5.0)

# ---------------- LOAD ----------------
slate = c_slate(date_sel.isoformat())
if slate.empty: st.stop()

hitter = c_hitter().merge(c_h_trad(), on="player_id", how="left")

lookup = c_lookup()
hitter = hitter.merge(lookup, on="player_id", how="left")

if "player_name" not in hitter.columns:
    st.error("Player lookup failed"); st.stop()

hitter["player_name_clean"] = hitter["player_name"].apply(clean_name)

fg = c_fg()
if not fg.empty:
    hitter = hitter.merge(fg, on="player_name_clean", how="left")

pitcher = c_pitcher()

# ---------------- ENGINE ----------------
games = {}

for g in slate.itertuples():

    park = get_park(getattr(g,"venue",""))
    wx = c_weather(park.get("lat"), park.get("lon"), datetime.now()) if park.get("lat") else {}
    mult,_ = hr_multiplier(wx, park)

    away = get_lineup(g.gamePk,"away")
    home = get_lineup(g.gamePk,"home")

    ap = pitcher[pitcher.player_id==g.away_pitcher_id]
    hp = pitcher[pitcher.player_id==g.home_pitcher_id]

    ap = ap.iloc[0].to_dict() if len(ap) else {}
    hp = hp.iloc[0].to_dict() if len(hp) else {}

    a = build_matchup_table(away,pd.Series(hp),hitter,pitcher)
    h = build_matchup_table(home,pd.Series(ap),hitter,pitcher)

    for df,p in [(a,hp),(h,ap)]:

        if df.empty: continue

        df = apply_fallback(df)
        df = add_score(df)

        df = hr_probability(df,pd.Series(p),mult)

        if "hr_prob" not in df.columns: continue

        vals,edges = [],[]

        for r in df.itertuples():
            pa = hr_prob_per_pa(r._asdict(),p)
            ghr = hr_prob_full_game(pa)*100
            vals.append(ghr)
            edges.append(ghr-12.5)

        df["hr_game_pct"]=vals
        df["model_edge"]=edges

    games[g.gamePk]=dict(away=a,home=h)

# ---------------- RENDER ----------------
def render(df):

    if df.empty:
        st.write("No data"); return

    if search:
        df=df[df["player_name"].str.contains(search,case=False,na=False)]

    if "hr_game_pct" in df.columns:
        df=df[df["hr_game_pct"]>=min_hr]

    cols=[c for c in [
        "player_name","hr_game_pct","model_edge","iso","xwoba","data_score","data_source"
    ] if c in df.columns]

    st.dataframe(df[cols].style.apply(color,axis=1), use_container_width=True)

# ---------------- UI ----------------
st.title("⚾ MLB HR Dashboard")

for g in slate.itertuples():
    ctx = games[g.gamePk]

    st.markdown(f"### {g.away_team_abbr} @ {g.home_team_abbr}")

    t1,t2 = st.tabs(["Away","Home"])

    with t1: render(ctx["away"])
    with t2: render(ctx["home"])
