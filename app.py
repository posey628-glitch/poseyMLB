"""
app.py — MLB Daily Prop Betting Dashboard
==========================================
Built for daily HR and K prop research.

Top-level sections:
  1. 💰 Top Prop Edges       — Best HR / K bets across the slate
  2. 🎯 Slate Highlights     — Sleepers, GS, top HR/K candidates
  3. 📋 Slate Summary        — Pitcher rankings with Verdict
  4. 🎮 Per-game breakdowns  — Full matchups, pitch-match, BvP, arsenals

🔔 RESPONSIBLE BETTING REMINDER:
   Sportsbooks employ quants whose lines are sharp. A homebrew model
   rarely beats them long-term. Realistic outcome: occasional 3-5% edges.
   Bet small flat units. Shop lines. Treat this as one input.
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
# Sidebar
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
# Load shared data
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

# Optional context loads
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
# Header
# ---------------------------------------------------------------------------

st.title(f"⚾ MLB Props — {selected_date.strftime('%A, %B %d, %Y')}")
st.caption(f"{len(slate)} games · {len(hitter_stats)} hitters · {len(pitcher_stats)} pitchers loaded")


# ---------------------------------------------------------------------------
# Precompute per-game context
# ---------------------------------------------------------------------------

game_context_map = {}
all_prop_rows = []

progress = st.progress(0.0, text="Building game contexts...")
for idx, (_, game) in enumerate(slate.iterrows()):
    progress.progress((idx + 1) / len(slate), text=f"Game {idx+1}/{len(slate)}")

    park = get_park(game.get("venue", ""))
    gt = pd.to_datetime(game["gameTime"]) if pd.notna(game["gameTime"]) else datetime.now()
    weather = fetch_weather(park.get("lat"), park.get("lon"), gt) if park.get("lat") else {}
    wx_mult, wx_summary = hr_multiplier(weather, park)
    park_mult = park.get("hr_factor", 100) / 100.0
    full_hr_mult = wx_mult * park_mult

    # Vegas implied totals
    vegas_row = None
    if not vegas_df.empty:
        match = vegas_df[
            (vegas_df["away_abbr"] == game["away_team_abbr"]) &
            (vegas_df["home_abbr"] == game["home_team_abbr"])
        ]
        if len(match):
            vegas_row = match.iloc[0].to_dict()

    # Umpire
    ump = get_umpire_for_game(int(game["gamePk"])) if use_umpire else {"name": "TBD", "k_factor": 1.0, "bb_factor": 1.0}

    # Lineups (fallback to active roster)
    away_lineup = get_lineup(int(game["gamePk"]), "away") or [
        {"id": p["id"], "name": p["name"], "position": p["position"]}
        for p in get_team_roster(int(game["away_team_id"]))[:9]
    ]
    home_lineup = get_lineup(int(game["gamePk"]), "home") or [
        {"id": p["id"], "name": p["name"], "position": p["position"]}
        for p in get_team_roster(int(game["home_team_id"]))[:9]
    ]

    # Pitcher rows
    away_p = pitcher_stats[pitcher_stats["player_id"] == game["away_pitcher_id"]]
    home_p = pitcher_stats[pitcher_stats["player_id"] == game["home_pitcher_id"]]
    away_p_row = away_p.iloc[0].to_dict() if len(away_p) else {}
    home_p_row = home_p.iloc[0].to_dict() if len(home_p) else {}

    # Pitcher recent form + workload
    if use_recent_form:
        for side, pid in [("away", game["away_pitcher_id"]),
                          ("home", game["home_pitcher_id"])]:
            if pid and not pd.isna(pid):
                recent = get_pitcher_recent_form(int(pid))
                workload = get_pitcher_workload(int(pid))
                row_dict = away_p_row if side == "away" else home_p_row
                row_dict.update(recent)
                row_dict.update(workload)

    # Catcher framing factor — assume opposing team's starting catcher.
    # Without confirmed C in lineup, use league-avg.
    away_framing_factor = 1.0
    home_framing_factor = 1.0
    if use_umpire and not framing_df.empty:
        # Find catcher in lineup
        for lineup, target_var in [(away_lineup, "away_framing_factor"),
                                     (home_lineup, "home_framing_factor")]:
          catchers = [p for p in lineup if p.get("position") == "C"]
            if catchers and "player_id" in framing_df.columns:
                cid = catchers[0]["id"]
                f_row = framing_df[framing_df["player_id"] == cid]
                if len(f_row) and "framing_k_factor" in f_row.columns:
                    if target_var == "away_framing_factor":
                        away_framing_factor = float(f_row.iloc[0]["framing_k_factor"])
                    else:
                        home_framing_factor = float(f_row.iloc[0]["framing_k_factor"])

    # Hitter recent form
    away_recent = {}
    home_recent = {}
    if use_recent_form:
        for p in away_lineup:
            if p.get("id"):
                away_recent[p["id"]] = get_hitter_recent_form_trad(int(p["id"]))
        for p in home_lineup:
            if p.get("id"):
                home_recent[p["id"]] = get_hitter_recent_form_trad(int(p["id"]))

    # Build matchup tables
    away_matchup = build_matchup_table(
        away_lineup, pd.Series(home_p_row) if home_p_row else None,
        hitter_stats, pitcher_stats, recent_form_dict=away_recent,
    )
    home_matchup = build_matchup_table(
        home_lineup, pd.Series(away_p_row) if away_p_row else None,
        hitter_stats, pitcher_stats, recent_form_dict=home_recent,
    )

    # Pitch match scores
    if use_pitch_match and not pitcher_arsenal_all.empty and not hitter_pitch_arsenal.empty:
        away_pm = lineup_pitch_match(
            away_lineup, game["home_pitcher_id"],
            hitter_pitch_arsenal, pitcher_arsenal_all,
        )
        home_pm = lineup_pitch_match(
            home_lineup, game["away_pitcher_id"],
            hitter_pitch_arsenal, pitcher_arsenal_all,
        )
        if not away_pm.empty and not away_matchup.empty:
            away_matchup = away_matchup.merge(
                away_pm[["player_id", "pitch_match_score", "best_pitch", "best_pitch_xwoba",
                          "worst_pitch", "weighted_xwoba"]],
                on="player_id", how="left",
            )
        if not home_pm.empty and not home_matchup.empty:
            home_matchup = home_matchup.merge(
                home_pm[["player_id", "pitch_match_score", "best_pitch", "best_pitch_xwoba",
                          "worst_pitch", "weighted_xwoba"]],
                on="player_id", how="left",
            )

    # Layer on sleeper / GS / HR prob (existing system - kept for backward compat)
    away_matchup = hr_probability(away_matchup, pd.Series(home_p_row) if home_p_row else None, full_hr_mult)
    home_matchup = hr_probability(home_matchup, pd.Series(away_p_row) if away_p_row else None, full_hr_mult)
    away_matchup = find_sleepers(away_matchup, season_hr_col="home_run")
    home_matchup = find_sleepers(home_matchup, season_hr_col="home_run")
    away_matchup = grand_slam_probability(away_matchup, pd.Series(home_p_row) if home_p_row else None, full_hr_mult)
    home_matchup = grand_slam_probability(home_matchup, pd.Series(away_p_row) if away_p_row else None, full_hr_mult)

    # NEW: Calibrated HR probability per hitter (the prop-betting number)
    for matchup_df, opp_p_row, opp_p_id, framing_for_their_K in [
        (away_matchup, home_p_row, game["home_pitcher_id"], home_framing_factor),
        (home_matchup, away_p_row, game["away_pitcher_id"], away_framing_factor),
    ]:
        if matchup_df.empty:
            continue
        hr_pa_list = []
        hr_game_list = []
        verdict_list = []
        for _, hrow in matchup_df.iterrows():
            ph_factor = park_hand_factor(game.get("venue", ""), hrow.get("bats", ""))
            ttop = ttop_multiplier(hrow.get("lineup_pos", 5))
            pm_score = hrow.get("pitch_match_score") if "pitch_match_score" in matchup_df.columns else None
            p_pa = hr_prob_per_pa(
                hitter_row=hrow.to_dict(),
                pitcher_row=opp_p_row,
                park_factor=park_mult,
                park_hand_factor=ph_factor,
                weather_mult=wx_mult,
                pitch_match_score=pm_score,
                ttop_mult=ttop,
                defense_factor=1.0,
            )
            p_game = hr_prob_full_game(p_pa, expected_pa=4.3 if hrow.get("lineup_pos", 5) <= 5 else 3.8)
            hr_pa_list.append(round(p_pa * 100, 2))
            hr_game_list.append(round(p_game * 100, 1))
            # Verdict: combined matchup + HR prob
            avg_score = ((hrow.get("matchup", 50) or 50) + p_game * 200) / 2
            verdict_list.append(verdict_color(avg_score, scale=(45, 65)))
        matchup_df["hr_pa_pct"] = hr_pa_list
        matchup_df["hr_game_pct"] = hr_game_list
        matchup_df["verdict"] = verdict_list

    # Calibrated K projection per pitcher
    away_lineup_k_pct = away_matchup["k_pct"].mean() if "k_pct" in away_matchup.columns and not away_matchup.empty else 22
    home_lineup_k_pct = home_matchup["k_pct"].mean() if "k_pct" in home_matchup.columns and not home_matchup.empty else 22

    away_k_proj = k_total_projection(
        away_p_row, home_lineup_k_pct,
        ump_k_factor=ump.get("k_factor", 1.0),
        catcher_framing_factor=away_framing_factor,
    ) if away_p_row else {}
    home_k_proj = k_total_projection(
        home_p_row, away_lineup_k_pct,
        ump_k_factor=ump.get("k_factor", 1.0),
        catcher_framing_factor=home_framing_factor,
    ) if home_p_row else {}

    ctx = {
        "park": park, "weather": weather, "wx_mult": wx_mult, "park_mult": park_mult,
        "hr_mult": full_hr_mult, "summary": wx_summary,
        "vegas": vegas_row, "ump": ump,
        "away_lineup": away_lineup, "home_lineup": home_lineup,
        "away_p_row": away_p_row, "home_p_row": home_p_row,
        "away_matchup": away_matchup, "home_matchup": home_matchup,
        "away_k_proj": away_k_proj, "home_k_proj": home_k_proj,
        "away_framing": away_framing_factor, "home_framing": home_framing_factor,
    }
    game_context_map[game["gamePk"]] = ctx

    # Collect for top prop edges section
    for df_, opp_pitcher, opp_pitcher_row in [
        (away_matchup, game["home_pitcher"], home_p_row),
        (home_matchup, game["away_pitcher"], away_p_row),
    ]:
        if df_.empty:
            continue
        x = df_.copy()
        x["game"] = f"{game['away_team_abbr']} @ {game['home_team_abbr']}"
        x["opp_pitcher"] = opp_pitcher
        x["venue"] = game.get("venue", "")
        x["wx"] = wx_summary
        x["vegas_total"] = vegas_row.get("total") if vegas_row else None
        x["pitcher_hand"] = opp_pitcher_row.get("p_throws", "—") if opp_pitcher_row else "—"
        all_prop_rows.append(x)

progress.empty()


# ---------------------------------------------------------------------------
# TOP PROP EDGES — most important section for prop bettors
# ---------------------------------------------------------------------------

st.subheader("💰 Top Prop Edges Today")
st.caption(
    "Most actionable plays based on the model. **HR Game%** = chance of "
    "hitting ≥1 HR. **K Proj** = projected K total. "
    "🟢 = strong play · 🟡 = lean · 🔴 = fade. "
    "Compare these against sportsbook lines for edge."
)

if all_prop_rows:
    combined = pd.concat(all_prop_rows, ignore_index=True)

    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown("**⚾ Best HR Plays**")
        st.caption("Sorted by HR Game%. Compare to book's HR price.")
        hr_cols = ["verdict", "player_name", "game", "opp_pitcher", "pitcher_hand",
                   "hr_game_pct", "hr_pa_pct", "home_run", "barrel_pct",
                   "pitch_match_score", "best_pitch", "wx", "vegas_total"]
        keep = [c for c in hr_cols if c in combined.columns]
        hr_df = combined[keep].copy().sort_values("hr_game_pct", ascending=False).head(20)
        hr_df = hr_df.rename(columns={
            "verdict": "", "player_name": "Hitter", "game": "Game",
            "opp_pitcher": "vs", "pitcher_hand": "T",
            "hr_game_pct": "HR Game%", "hr_pa_pct": "HR PA%",
            "home_run": "Season HR", "barrel_pct": "Brl%",
            "pitch_match_score": "Pitch Match", "best_pitch": "Best Pitch",
            "wx": "Conditions", "vegas_total": "O/U",
        })
        # Format & color
        green = [c for c in ["HR Game%", "HR PA%", "Brl%", "Pitch Match"] if c in hr_df.columns]
        sty = hr_df.style
        if green:
            sty = sty.background_gradient(cmap="RdYlGn", subset=green)
        sty = sty.format({
            "HR Game%": "{:.1f}%", "HR PA%": "{:.2f}%",
            "Brl%": "{:.1f}%", "Pitch Match": "{:.1f}",
            "O/U": "{:.1f}",
        })
        st.dataframe(sty, hide_index=True, use_container_width=True, height=620)

    with col_b:
        st.markdown("**🔥 Best K Plays (Pitchers)**")
        st.caption("Projected K range. Compare to book's K O/U line.")
        k_rows = []
        for gpk, ctx in game_context_map.items():
            game = slate[slate["gamePk"] == gpk].iloc[0]
            for side in ("away", "home"):
                p_proj = ctx[f"{side}_k_proj"]
                p_row = ctx[f"{side}_p_row"]
                if not p_proj or p_proj.get("mean") is None:
                    continue
                k_rows.append({
                    "verdict": verdict_color(p_proj["mean"] * 10),  # 7 K = 70 score-ish
                    "Pitcher": game[f"{side}_pitcher"],
                    "Team": game[f"{side}_team_abbr"],
                    "vs": game[f"{'home' if side == 'away' else 'away'}_team_abbr"],
                    "Proj K": p_proj["mean"],
                    "Range": f"{p_proj['low']:.1f}-{p_proj['high']:.1f}",
                    "Blend K/9": p_proj.get("blended_k9"),
                    "Opp K%": round(p_proj.get("lineup_adj", 1) * 22, 1),
                    "P(O 5.5)": p_proj.get("p_over_5.5"),
                    "P(O 6.5)": p_proj.get("p_over_6.5"),
                    "P(O 7.5)": p_proj.get("p_over_7.5"),
                    "P(O 8.5)": p_proj.get("p_over_8.5"),
                    "Ump": ctx["ump"].get("name", "TBD")[:18],
                })
        if k_rows:
            k_df = pd.DataFrame(k_rows).sort_values("Proj K", ascending=False).head(20)
            sty = k_df.style.background_gradient(
                cmap="RdYlGn",
                subset=[c for c in ["Proj K", "Blend K/9",
                                     "P(O 5.5)", "P(O 6.5)", "P(O 7.5)", "P(O 8.5)"]
                        if c in k_df.columns]
            ).format({
                "Proj K": "{:.1f}", "Blend K/9": "{:.2f}",
                "Opp K%": "{:.1f}%",
                "P(O 5.5)": "{:.0%}", "P(O 6.5)": "{:.0%}",
                "P(O 7.5)": "{:.0%}", "P(O 8.5)": "{:.0%}",
            })
            st.dataframe(sty, hide_index=True, use_container_width=True, height=620)

st.divider()


# ---------------------------------------------------------------------------
# Slate Highlights — sleepers and GS (kept from earlier versions)
# ---------------------------------------------------------------------------

with st.expander("🎯 Slate Highlights — Sleepers & Grand Slam Candidates", expanded=False):
    combined = pd.concat(all_prop_rows, ignore_index=True) if all_prop_rows else pd.DataFrame()
    if not combined.empty:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**🌙 Sleeper HR Picks**")
            st.caption("Today's HR conditions exceed their season pace.")
            cols = ["verdict", "player_name", "game", "opp_pitcher",
                    "sleeper_score", "hr_game_pct", "home_run", "wx"]
            keep = [c for c in cols if c in combined.columns]
            s_df = combined[combined.get("is_sleeper", False)][keep].copy()
            s_df = s_df.sort_values("sleeper_score", ascending=False).head(15)
            s_df = s_df.rename(columns={
                "verdict": "", "player_name": "Hitter", "game": "Game",
                "opp_pitcher": "vs", "sleeper_score": "Sleeper",
                "hr_game_pct": "HR%", "home_run": "HR", "wx": "Conditions",
            })
            if not s_df.empty:
                sty = s_df.style.background_gradient(
                    cmap="RdYlGn",
                    subset=[c for c in ["Sleeper", "HR%"] if c in s_df.columns]
                ).format({"Sleeper": "{:.1f}", "HR%": "{:.1f}%"})
                st.dataframe(sty, hide_index=True, use_container_width=True, height=480)

        with c2:
            st.markdown("**🎰 Grand Slam Candidates**")
            cols = ["verdict", "player_name", "game", "opp_pitcher",
                    "gs_score", "lineup_pos", "hr_game_pct"]
            keep = [c for c in cols if c in combined.columns]
            gs = combined[keep].copy().sort_values("gs_score", ascending=False).head(15)
            gs = gs.rename(columns={
                "verdict": "", "player_name": "Hitter", "game": "Game",
                "opp_pitcher": "vs", "gs_score": "GS Score",
                "lineup_pos": "Slot", "hr_game_pct": "HR%",
            })
            sty = gs.style.background_gradient(
                cmap="RdYlGn",
                subset=[c for c in ["GS Score", "HR%"] if c in gs.columns]
            ).format({"GS Score": "{:.2f}", "HR%": "{:.1f}%"})
            st.dataframe(sty, hide_index=True, use_container_width=True, height=480)


# ---------------------------------------------------------------------------
# Slate Summary — pitcher rankings
# ---------------------------------------------------------------------------

st.subheader("📋 Slate Summary — Starting Pitchers")
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
    # Add verdict column
    pitcher_slate["verdict"] = pitcher_slate["test_score"].apply(
        lambda x: verdict_color(x, scale=(45, 65))
    )
    cols_show = ["verdict", "pitcher_name", "team", "home_away", "opp", "throws",
                 "test_score", "kHR", "proj_k", "form_arrow",
                 "era", "whip", "k9", "bb9", "hr9",
                 "k_pct", "whiff_pct", "csw_pct",
                 "xwoba_allowed", "barrel_allowed",
                 "recent_era", "recent_k9", "days_rest", "avg_recent_pitches"]
    display = pitcher_slate[[c for c in cols_show if c in pitcher_slate.columns]].rename(columns={
        "verdict": "", "pitcher_name": "Pitcher", "team": "Tm", "home_away": "",
        "opp": "Opp", "throws": "T", "test_score": "Test", "kHR": "kHR",
        "proj_k": "Proj K", "form_arrow": "Trend",
        "era": "ERA", "whip": "WHIP", "k9": "K/9", "bb9": "BB/9", "hr9": "HR/9",
        "k_pct": "K%", "whiff_pct": "Whiff%", "csw_pct": "CSW%",
        "xwoba_allowed": "xwOBA", "barrel_allowed": "Brl%",
        "recent_era": "L5 ERA", "recent_k9": "L5 K/9",
        "days_rest": "Rest", "avg_recent_pitches": "Pitches",
    })
    green = [c for c in ["Test", "kHR", "Proj K", "K/9", "K%", "Whiff%", "CSW%", "L5 K/9"]
             if c in display.columns]
    red = [c for c in ["ERA", "WHIP", "BB/9", "HR/9", "xwOBA", "Brl%", "L5 ERA"]
           if c in display.columns]
    sty = display.style.background_gradient(cmap="RdYlGn", subset=green)
    if red:
        sty = sty.background_gradient(cmap="RdYlGn_r", subset=red)
    sty = sty.format({
        **{c: "{:.1f}" for c in ["Test", "kHR", "Proj K", "K%", "Whiff%", "CSW%", "Brl%"] if c in display.columns},
        **{c: "{:.2f}" for c in ["ERA", "WHIP", "K/9", "BB/9", "HR/9", "L5 ERA", "L5 K/9"] if c in display.columns},
        "xwOBA": "{:.3f}",
        "Rest": "{:.0f}d", "Pitches": "{:.0f}",
    })
    st.dataframe(sty, hide_index=True, use_container_width=True, height=420)


st.divider()


# ---------------------------------------------------------------------------
# Per-game tabs
# ---------------------------------------------------------------------------

st.subheader("🎮 Game Breakdowns")


def _render_matchup(df: pd.DataFrame, title: str = ""):
    """Render hitter table with full column set + verdict."""
    if df is None or df.empty:
        st.write("No matchup data")
        return

    show_cols = [
        "verdict", "player_name", "lineup_pos", "bats", "position",
        # Calibrated prop numbers (most important)
        "hr_game_pct", "hr_pa_pct",
        # Composites
        "matchup", "test_score", "ceiling", "zone_fit",
        "hr_form_label", "kHR",
        # Pitch match
        "pitch_match_score", "best_pitch", "best_pitch_xwoba", "worst_pitch",
        # Stats
        "iso", "xwoba", "xwobacon",
        "barrel_pct", "pulled_brl_pct", "hard_hit", "sweet_spot_pct",
        "fb_pct", "la", "k_pct", "bb_pct", "whiff_pct",
        "home_run", "recent_hr", "recent_iso",
        "sleeper_score", "gs_score",
    ]
    keep = [c for c in show_cols if c in df.columns]
    display = df[keep].copy().rename(columns={
        "verdict": "", "player_name": "Hitter", "lineup_pos": "#",
        "position": "Pos", "bats": "B",
        "hr_game_pct": "HR Game%", "hr_pa_pct": "HR PA%",
        "matchup": "Matchup", "test_score": "Test", "ceiling": "Ceiling",
        "zone_fit": "Zone Fit", "hr_form_label": "HR Form", "kHR": "kHR",
        "pitch_match_score": "Pitch Match", "best_pitch": "Best Pitch",
        "best_pitch_xwoba": "Best xwOBA", "worst_pitch": "Worst Pitch",
        "iso": "ISO", "xwoba": "xwOBA", "xwobacon": "xwOBAcon",
        "barrel_pct": "Brl%", "pulled_brl_pct": "PulledBrl%",
        "hard_hit": "HH%", "sweet_spot_pct": "SwSpot%",
        "fb_pct": "FB%", "la": "LA",
        "k_pct": "K%", "bb_pct": "BB%", "whiff_pct": "Whiff%",
        "home_run": "HR", "recent_hr": "L15 HR", "recent_iso": "L15 ISO",
        "sleeper_score": "Sleeper", "gs_score": "GS",
    })

    green = [c for c in ["HR Game%", "HR PA%", "Matchup", "Test", "Ceiling",
                          "Zone Fit", "kHR", "Pitch Match", "ISO", "xwOBA", "xwOBAcon",
                          "Brl%", "PulledBrl%", "HH%", "SwSpot%", "FB%",
                          "BB%", "HR", "L15 HR", "Sleeper", "GS"]
             if c in display.columns]
    red = [c for c in ["K%", "Whiff%"] if c in display.columns]

    sty = display.style
    if green:
        sty = sty.background_gradient(cmap="RdYlGn", subset=green)
    if red:
        sty = sty.background_gradient(cmap="RdYlGn_r", subset=red)
    sty = sty.format({
        **{c: "{:.1f}" for c in ["Matchup", "Test", "Ceiling", "kHR",
                                  "Pitch Match", "Sleeper"] if c in display.columns},
        "HR Game%": "{:.1f}%", "HR PA%": "{:.2f}%",
        "GS": "{:.2f}", "Zone Fit": "{:.3f}",
        "ISO": "{:.3f}", "xwOBA": "{:.3f}", "xwOBAcon": "{:.3f}",
        "L15 ISO": "{:.3f}", "Best xwOBA": "{:.3f}",
        "Brl%": "{:.1f}%", "PulledBrl%": "{:.1f}%",
        "HH%": "{:.1f}%", "SwSpot%": "{:.1f}%", "FB%": "{:.1f}%",
        "K%": "{:.1f}%", "BB%": "{:.1f}%", "Whiff%": "{:.1f}%",
        "LA": "{:.1f}",
    })
    if title:
        st.markdown(f"#### {title}")
    st.dataframe(sty, hide_index=True, use_container_width=True)


def _label(row):
    try:
        t = pd.to_datetime(row["gameTime"]).tz_convert("US/Eastern")
        t_str = t.strftime("%-I:%M %p ET")
    except Exception:
        t_str = "TBD"
    return f"{row['away_team_abbr']} @ {row['home_team_abbr']} · {t_str}"


tabs = st.tabs([_label(r) for _, r in slate.iterrows()])

for tab, (_, game) in zip(tabs, slate.iterrows()):
    with tab:
        ctx = game_context_map[game["gamePk"]]
        park = ctx["park"]
        vegas = ctx.get("vegas") or {}
        ump = ctx.get("ump", {})

        # Top metrics row
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric(f"Away ({game['away_team_abbr']})",
                  game["away_pitcher"] or "TBD",
                  delta=f"Proj K: {ctx['away_k_proj'].get('mean', '—')}")
        m2.metric(f"Home ({game['home_team_abbr']})",
                  game["home_pitcher"] or "TBD",
                  delta=f"Proj K: {ctx['home_k_proj'].get('mean', '—')}")
        m3.metric("HR Mult", f"{ctx['hr_mult']:.2f}×",
                  delta=f"{(ctx['hr_mult'] - 1) * 100:+.0f}%")
        if vegas and vegas.get("total"):
            m4.metric("Vegas Total", f"{vegas['total']:.1f}",
                      delta=f"AT: {vegas.get('away_implied', '—')} | HT: {vegas.get('home_implied', '—')}")
        else:
            m4.metric("Vegas Total", "—")
        m5.metric("HP Umpire", ump.get("name", "TBD")[:15])

        with st.container(border=True):
            cols = st.columns([3, 2])
            with cols[0]:
                roof = park.get("roof", "open")
                st.markdown(
                    f"📍 **{game.get('venue', 'TBD')}** ({roof}) · "
                    f"Park HR: **{park.get('hr_factor', 100)}**"
                )
                st.markdown(f"🌤️ {ctx['summary'] or 'No weather data'}")
            with cols[1]:
                st.caption(
                    f"**Catcher framing:** "
                    f"Away C: {ctx['away_framing']:.2f}× · "
                    f"Home C: {ctx['home_framing']:.2f}× K factor"
                )

        # Matchup tables
        _render_matchup(
            ctx["away_matchup"],
            title=f"🏏 {game['away_team']} vs {game['home_pitcher']}"
        )
        _render_matchup(
            ctx["home_matchup"],
            title=f"🏏 {game['home_team']} vs {game['away_pitcher']}"
        )

        # K projection detail card
        st.markdown("#### 🎯 K Projection Detail")
        kp1, kp2 = st.columns(2)
        for col, side, label in [(kp1, "away", game["away_pitcher"]),
                                  (kp2, "home", game["home_pitcher"])]:
            with col:
                p_proj = ctx[f"{side}_k_proj"]
                if not p_proj or p_proj.get("mean") is None:
                    col.write(f"**{label}** — no data")
                    continue
                col.markdown(
                    f"**{label}** · Projected K: **{p_proj['mean']:.1f}** "
                    f"(range {p_proj['low']:.1f}–{p_proj['high']:.1f})"
                )
                col.caption(
                    f"Blended K/9: {p_proj['blended_k9']:.2f} · "
                    f"Opp lineup adj: {p_proj['lineup_adj']:.2f}× · "
                    f"Catcher framing: {ctx[f'{side}_framing']:.2f}×"
                )
                lines_df = pd.DataFrame([
                    {"Line": "O 5.5", "Prob": p_proj.get("p_over_5.5", 0)},
                    {"Line": "O 6.5", "Prob": p_proj.get("p_over_6.5", 0)},
                    {"Line": "O 7.5", "Prob": p_proj.get("p_over_7.5", 0)},
                    {"Line": "O 8.5", "Prob": p_proj.get("p_over_8.5", 0)},
                ])
                col.dataframe(
                    lines_df.style.background_gradient(cmap="RdYlGn", subset=["Prob"])
                    .format({"Prob": "{:.0%}"}),
                    hide_index=True, use_container_width=True,
                )

        # Optional: BvP & similar arsenal
        if use_bvp:
            with st.expander("📜 Batter-vs-Pitcher + Similar Arsenal"):
                st.caption(
                    "Career BvP samples are usually too small to trust. "
                    "'Similar Arsenal' aggregates BvP across 10 pitchers with "
                    "most similar pitch mix — more reliable signal."
                )
                if not pitcher_arsenal_all.empty:
                    similar_away = find_similar_pitchers(
                        game["home_pitcher_id"], "", pitcher_arsenal_all, n=10
                    ) if game["home_pitcher_id"] else []
                    similar_home = find_similar_pitchers(
                        game["away_pitcher_id"], "", pitcher_arsenal_all, n=10
                    ) if game["away_pitcher_id"] else []

                    for label, lineup, opp_pid, similar_pids in [
                        (f"{game['away_team']} vs {game['home_pitcher']}",
                         ctx["away_lineup"], game["home_pitcher_id"], similar_away),
                        (f"{game['home_team']} vs {game['away_pitcher']}",
                         ctx["home_lineup"], game["away_pitcher_id"], similar_home),
                    ]:
                        if not opp_pid:
                            continue
                        st.markdown(f"**{label}**")
                        bvp = bvp_for_lineup(lineup, int(opp_pid))
                        if not bvp.empty:
                            sim_rows = []
                            for p in lineup:
                                if p.get("id") and similar_pids:
                                    sim = hitter_vs_similar(int(p["id"]), similar_pids)
                                    sim_rows.append({
                                        "player_id": p["id"],
                                        "sim_pa": sim.get("pa", 0),
                                        "sim_avg": sim.get("avg", 0),
                                        "sim_iso": sim.get("iso", 0),
                                        "sim_hr": sim.get("hr", 0),
                                    })
                            if sim_rows:
                                bvp = bvp.merge(pd.DataFrame(sim_rows), on="player_id", how="left")
                            cols_keep = [c for c in [
                                "player_name", "pa", "avg", "iso", "hr", "k_pct",
                                "sim_pa", "sim_avg", "sim_iso", "sim_hr",
                            ] if c in bvp.columns]
                            st.dataframe(bvp[cols_keep], hide_index=True, use_container_width=True)
                        else:
                            st.caption("No BvP data")

        # Pitcher arsenal
        with st.expander("📊 Pitcher Arsenals"):
            if pitcher_arsenal_all.empty:
                pitcher_arsenal_all_local = get_pitcher_arsenal()
            else:
                pitcher_arsenal_all_local = pitcher_arsenal_all
            for p_name, p_id in [
                (game["away_pitcher"], game["away_pitcher_id"]),
                (game["home_pitcher"], game["home_pitcher_id"]),
            ]:
                if not p_id or pd.isna(p_id):
                    continue
                st.markdown(f"**{p_name}**")
                a = pitcher_arsenal_all_local[
                    pitcher_arsenal_all_local.get("player_id") == p_id
                ] if "player_id" in pitcher_arsenal_all_local.columns else pd.DataFrame()
                if len(a):
                    summary_cols = [c for c in [
                        "pitch_name", "pitch_usage", "pitches",
                        "ba", "slg", "woba", "est_ba", "est_slg", "est_woba",
                        "whiff_percent", "k_percent", "put_away",
                        "hard_hit_percent",
                    ] if c in a.columns]
                    a_display = a[summary_cols].rename(columns={
                        "pitch_name": "Pitch", "pitch_usage": "Usage%",
                        "pitches": "Pitches", "ba": "BA", "slg": "SLG",
                        "woba": "wOBA", "est_ba": "xBA", "est_slg": "xSLG",
                        "est_woba": "xwOBA", "whiff_percent": "SwStr%",
                        "k_percent": "K%", "put_away": "PutAway%",
                        "hard_hit_percent": "HH%",
                    })
                    sty = a_display.style.background_gradient(
                        cmap="RdYlGn_r",
                        subset=[c for c in ["BA", "SLG", "wOBA", "xBA", "xSLG", "xwOBA", "HH%"] if c in a_display.columns]
                    ).background_gradient(
                        cmap="RdYlGn",
                        subset=[c for c in ["SwStr%", "K%", "PutAway%"] if c in a_display.columns]
                    ).format({
                        "BA": "{:.3f}", "SLG": "{:.3f}", "wOBA": "{:.3f}",
                        "xBA": "{:.3f}", "xSLG": "{:.3f}", "xwOBA": "{:.3f}",
                        "Usage%": "{:.1f}%", "SwStr%": "{:.1f}%",
                        "K%": "{:.1f}%", "PutAway%": "{:.1f}%", "HH%": "{:.1f}%",
                    })
                    st.dataframe(sty, hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------------
# All Hitters Database (every team, not just lineups)
# ---------------------------------------------------------------------------

with st.expander("📚 All Hitters Database (every team on slate)"):
    st.caption(
        "Every active position player on every team in today's slate. "
        "Use this to scan beyond the confirmed lineup. Sort by any column."
    )
    rosters = get_all_team_rosters(slate)
    all_hitter_ids = set()
    for r in rosters.values():
        for p in r:
            all_hitter_ids.add(p["id"])
    db = hitter_stats[hitter_stats.get("player_id", pd.Series()).isin(all_hitter_ids)].copy() \
        if "player_id" in hitter_stats.columns else pd.DataFrame()
    if not db.empty:
        cols_keep = [c for c in [
            "player_name", "pa", "home_run", "iso", "xwoba", "xwobacon",
            "barrel_pct", "barrel_batted_rate", "hard_hit", "hard_hit_percent",
            "fb_pct", "flyballs_percent", "k_pct", "k_percent",
            "bb_pct", "bb_percent", "sweet_spot_pct", "sweet_spot_percent",
        ] if c in db.columns]
        st.dataframe(db[cols_keep].sort_values("home_run", ascending=False)
                     if "home_run" in db.columns else db[cols_keep],
                     hide_index=True, use_container_width=True, height=500)
