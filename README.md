# ⚾ MLB Daily Props Dashboard

Built specifically for daily HR and K prop betting research.

**⚠️ Bet responsibly.** Sportsbook lines are sharp — their quants are good. Realistic outcome with this tool: occasionally find 3-5% edges. Bet flat units. Shop lines across books. Set a daily limit. This is one input, not a guarantee.

---

## 🚀 Quick start

```bash
pip install -r requirements.txt
streamlit run app.py
```

## ☁️ Deploy free on streamlit.app

Push files to a GitHub repo → [share.streamlit.io](https://share.streamlit.io) → New app → point to `app.py`. Auto-refreshes daily.

---

## 💰 The Prop Betting Sections

### Top Prop Edges (top of dashboard)
Two side-by-side panels — the actionable plays:

- **⚾ Best HR Plays** — Calibrated probabilities (HR Game% and HR PA%) sorted high to low. Compare against the sportsbook's HR price. If the book has someone at +400 (implied 20%) and your model says 25%, that's a +5% edge.
- **🔥 Best K Plays** — Projected K total per pitcher with low-high range AND probability of going over 5.5 / 6.5 / 7.5 / 8.5. Compare directly to the book's strikeout O/U.

### 🟢🟡🔴 Verdict column
Every hitter and pitcher gets a stoplight verdict. Smash / Lean / Fade based on composite percentile across the slate.

---

## 📊 Data sources (all free, no API keys)

| Source | What we use |
|---|---|
| MLB Stats API | Slate, probable pitchers, lineups, traditional stats, BvP, umpires |
| Baseball Savant | Statcast hitter & pitcher stats, **pitch-arsenal-stats** (hitter performance by pitch type), catcher framing, team OAA |
| Open-Meteo | Game-time weather (temp, wind, pressure) |
| ESPN | Vegas game totals & implied team runs |

---

## 🧠 What's in the model

### HR probability per hitter
Multi-factor formula:
1. **Hitter base rate** — season HR/PA, Bayesian-regressed for small samples
2. **Pitcher HR/9** allowed
3. **Park factor** + **handedness-specific park factor** (e.g. Yankee Stadium short porch for LHH)
4. **Weather multiplier** — temp + wind direction relative to CF bearing + pressure
5. **Pitch-match score** — How well hitter's per-pitch-type xwOBA matches today's pitcher's arsenal usage
6. **TTOP** — Times Through Order penalty (top of order gets boost in late PAs)
7. **Defense** — Team OAA behind pitcher

Returns:
- **HR PA%** — Probability per single PA
- **HR Game%** — Probability of ≥1 HR in the game (compound across expected PAs)

### K total projection per pitcher
1. **Blended K/9** — Recent L5 starts weighted with season K/9
2. **Opposing lineup K%** — Lineup-wide adjustment
3. **HP umpire K factor** — Some umps call ~10% more strikes
4. **Catcher framing K factor** — Starting catcher's framing runs
5. **Park K factor** — Some parks suppress / amplify Ks

Returns:
- **Projected K total** (with low-high std dev range)
- **P(Over X.5)** for common K lines

---

## 🎯 Key feature: Pitch-Match Analysis

This is the killer signal. For each hitter, Statcast tracks performance per pitch type (xwOBA vs FF, vs SL, vs CB, etc.). For each pitcher, we know their usage % per pitch type.

**Pitch Match Score** = `Σ (pitcher_usage% × hitter_xwoba_vs_that_pitch)`

If Hitter A has a .450 xwOBA vs sliders and today's pitcher throws 38% sliders, that's a major edge. The table shows:
- **Pitch Match Score** (0–100, percentile-ranked across the slate)
- **Best Pitch** — The pitch type this hitter mashes that today's pitcher throws
- **Best xwOBA** — Their xwOBA vs that pitch
- **Worst Pitch** — Pitch they struggle most against

---

## 🌤️ Weather & Wind

- **Wind direction is computed relative to each park's CF bearing**. So "10mph wind from the south" at Wrigley (faces NE) becomes a partial out-blowing component, not generic boost.
- Each game shows: 💨 X mph OUT (HR boost) / 🌬️ X mph IN (kills flies) / X mph cross
- Temperature, humidity, pressure all factored in
- Dome = neutral · Retractable = approximated neutral

---

## 📋 Other features (kept from prior versions)

- **Slate Summary** — Pitcher rankings with full stats including L5 ERA, L5 K/9, days rest, recent pitch counts (workload flag)
- **Slate Highlights** — Sleeper HR picks + Grand Slam candidates
- **All Hitters Database** — Every position player on every team in today's slate, not just confirmed lineups
- **Pitcher Arsenals** — Per pitch type: BA/SLG/wOBA + xBA/xSLG/xwOBA, SwStr%, K%, PutAway%
- **BvP & Similar Arsenal** (optional toggle) — Career stats vs this pitcher AND vs 10 pitchers with most similar arsenal

---

## 🛠 Customize

All weights are plain Python:

| File | What to tweak |
|---|---|
| `models.py` → `SCORING_WEIGHTS` | Composite scoring weights |
| `props.py` → `hr_prob_per_pa` | HR formula calibration |
| `props.py` → `k_total_projection` | K projection blending |
| `props.py` → `verdict_color` | Stoplight thresholds |
| `sleepers.py` | Sleeper detection threshold |
| `weather.py` | Weather → HR multiplier coefficients |
| `park_factors.py` | Annual park factor updates |
| `game_context.py` → `PARK_HAND_FACTORS` | Handedness-specific park factors |

---

## 📁 Files

| File | Role |
|---|---|
| `app.py` | Streamlit dashboard |
| `data_fetcher.py` | MLB Stats + Savant API calls |
| `models.py` | Composite scoring |
| `sleepers.py` | Sleeper HR + GS logic |
| `splits.py` | Handedness splits, BvP, similar arsenal |
| `pitch_match.py` | Pitch-by-pitch hitter performance |
| `game_context.py` | Umpire, framing, defense, Vegas, TTOP, park-hand |
| `props.py` | Calibrated HR % and K projections |
| `park_factors.py` | Park HR factors + lat/lon |
| `weather.py` | Open-Meteo + wind math |
| `requirements.txt` | Deps |

---

## 🔍 Honest caveats

- **The market is sharp.** Vegas's odds compilers have access to all the same data and more. Don't expect to consistently print.
- **Lineups matter.** GS Score and lineup_pos-dependent metrics are unreliable until lineups post (~3 hours before first pitch). Refresh then.
- **BvP samples are noisy.** I include them but the pitch-match score is more reliable.
- **Day/night and temp splits aren't included.** Individual player splits over <500 PA are not predictive. The aggregate weather effect IS real and IS in the model.
- **Vegas totals via ESPN** sometimes lag on early-morning loads. Force refresh closer to game time.
- **Umpire data is fallback-only** by default (would need scraping UmpScorecards for real ump-specific K factors). What's shown is the HP umpire name from MLB Stats API.

---

## 🎯 Possible v5 additions

- UmpScorecards integration for real ump K factors (currently fallback-only)
- FanGraphs Stuff+ / Location+ scraping
- Lineup confirmation push notifications
- Historical backtest mode ("how have these picks done?")
- DraftKings / FanDuel salary import for DFS optimization
- Multi-game parlays with correlation modeling

The whole thing is ~2,500 lines. Read the code, own the model.
