#!/usr/bin/env python
"""analyse.py -- the PRE-REGISTERED forward-return analysis.

Implements the frozen framework in RESEARCH_MEMO.md. Nothing here chooses a
signal, a horizon, a universe or a decision rule: all of that was fixed on
2026-07-10, before a second snapshot existed. This file is the mechanical
translation of that design and must not become a place where the design drifts.

FROZEN BY THE MEMO (never change without a dated register row)
  menu        N = 5 -- S1a rating change [PRIMARY], S1b best-target revision,
              S1c Smart-Score decile, S2 residualised composite, S3 gated revision
  universe    in-universe AND liquid, point-in-time
  return      TOTALRETURN over {1w context, 1m PRIMARY, 3m context}, delisting-aware
  headline    style-neutral drift-adjusted NET alpha, unconditionally
  costs       one-way by 21d median dollar volume: >= $100m 3bp, $25-100m 8bp,
              $10-25m 15bp; charged on actual trades only; 0/5/10/20 sweep alongside
  KEEP        (1) 95% time-blocked bootstrap CI excludes zero, AND (2) survives the
              drift-matched random-entry null within BH-FDR q = 0.10 over N = 5,
              AND (3) 1w and 3m point estimates share the 1m sign
  gates       interim read unlocks at 8 captures and carries NO verdict; verdict
              eligibility needs >= 26 captures AND >= 20 matured 1m cohorts

IMPLEMENTATION CHOICES NOT FIXED BY THE MEMO -- register rows 8 and 9, printable
with --register. Row 8 (2026-08-08, while provably blind) fixed the block length,
the null matching, the insider mapping, the S1a direction and full-universe
fitting. Row 9 (2026-08-13, pre-flight -- gate still shut, no measurement ever
run) wired the row-3 retention band and episode netting, formation-time-only
selection, the delisted market leg, the uncosted null, realised DSR moments,
N_eff context and delisted-symbol resolution, and repaired the S2 null defect.

Reads data/merged/ (which carries Norgate returns) and pulls price history, so
unlike the diagnostics this DOES touch forward returns. That is the point of it,
and it is why the accrual guards below are hard refusals rather than warnings.

    python scripts/analyse.py --selftest      # synthetic; runs offline, no NDU
    python scripts/fixture_check.py           # end-to-end synthetic fixture panels
    python scripts/analyse.py                 # guarded; refuses below 8 captures
    python scripts/analyse.py --force-interim # interim read, NO verdict, once >= 8

Python months are 1-indexed; every date shift goes through returns.py, which
clamps month-end overflow and snaps to XNYS sessions.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import returns as R                                    # noqa: E402

MERGE_DIR = ROOT / "data" / "merged"
OUT_DIR = ROOT / "data" / "analysis"

MIN_SNAPSHOTS = 8            # interim read unlocks here, and carries no verdict
VERDICT_CAPTURES = 26        # memo: verdict eligibility
VERDICT_COHORTS = 20         # memo: matured 1m cohorts before the block bootstrap holds

SCHEMES = ("S1a", "S1b", "S1c", "S2", "S3")
PRIMARY = R.PRIMARY_HORIZON  # "1m"

CONSENSUS_ORDER = ["Strong Buy", "Moderate Buy", "Hold", "Moderate Sell", "Strong Sell"]
RANK = {c: i for i, c in enumerate(CONSENSUS_ORDER)}   # lower index = more bullish

# Memo: one-way, by 21-day median dollar volume. Judgement estimates, conservative
# for personal-order size -- NOT measured spreads.
COST_TIERS = ((100e6, 3.0), (25e6, 8.0), (10e6, 15.0))   # (ADV floor, one-way bps)
COST_SWEEP_BPS = (0.0, 5.0, 10.0, 20.0)

DECILE_MIN_NAMES = 90        # memo: smallest top set of score classes reaching >= 90
COHORT_WEEKS = 4             # memo: 4-week overlapping Jegadeesh-Titman cohorts
BOOTSTRAP_DRAWS = 2000
NULL_DRAWS = 1000
FDR_Q = 0.10
WINSOR = (0.01, 0.99)

REGISTER_ROW9 = """
  (f) RETENTION BAND AND EPISODE NETTING (conformance to register row 3).
      Ranked constructions (S1c, S2) re-enter a maturing name without a round
      trip while it sits inside twice the entry threshold -- for the
      class-built S1c bucket, the smallest top set of score classes reaching
      180 names; for S2, the top two deciles of the residualised composite.
      Event buckets re-enter only on a fresh qualifying event. Costs are
      charged per holding EPISODE: a name held through any of the four live
      cohorts is not sold and rebought when it re-enters, so only a name
      entering from flat pays, once, at its entering cohort. The prior code
      netted against the immediately preceding cohort alone and carried no
      band, which overstated costs and turnover and dropped band names.
  (g) FORMATION FROM FORMATION-TIME INFORMATION ONLY. Selection and cohort
      formation run on every week from trailing data (risk_frame);
      measurement fills in matured horizons afterwards. The prior code formed
      S2 on the measured frame, letting forward maturity select the formation
      universe. Horizon series stay aligned on weeks with a measured PRIMARY
      window, so cross-horizon comparisons read the same weeks.
  (h) DELISTED WINDOW, MARKET LEG. The beta leg of a delisted window covers
      the name's lived sessions (market_return_between), mirroring the
      registered per-session drift proration -- one window, every component.
      Stated plainly: not uniformly conservative in either direction;
      symmetric-window is the like-for-like reading.
  (i) NULL IS UNCOSTED, AND CONSUMES THE REALISED COHORTS. The observed
      statistic is net of the scheme's costs; the drift-matched null pays
      nothing, which hands the null a cost advantage and makes the test
      stricter. The null resamples the exact cohorts the panel measured; the
      prior code re-ran selection and never built S2's picks, leaving S2's
      null p-value silently at 1.0 -- a scheme that could never pass KEEP.
  (j) DSR MOMENTS REALISED. Skewness and kurtosis in the deflated Sharpe come
      from the realised net cohort series (Gaussian fallback below 8
      observations); the trial-variance term keeps the estimation-variance
      proxy. Effective N is reported alongside as context only:
      N_eff = (sum lambda)^2 / sum lambda^2 over the eigenvalues of the
      net-return correlation matrix (the em-rotation-lab convention).
  (k) DELISTED-SYMBOL RESOLUTION. A liquid name that resolved at capture but
      not on the live feed at analyse time is looked up in the delisted
      database (suffixed namespace, span-checked against the name's earliest
      anchor) and measured with the authoritative delisted flag; resolution
      failures are REPORTED as survivorship holes, never silently dropped.
"""

REGISTER_ROW8 = """
  (a) BOOTSTRAP BLOCK LENGTH = 4 weekly cohorts. The memo fixes "episode-block" /
      "time-blocked" but not the length. Four is the cohort overlap itself: with
      4-week overlapping cohorts a name appears in four consecutive weekly cohort
      returns, so the shortest block that breaks the mechanical dependence is 4.
      Choosing it from the design rather than from the autocorrelation of realised
      returns is deliberate -- fitting the block length to the data would tune the
      confidence interval to the result.
  (b) NULL DRIFT MATCHING = decile of trailing idiosyncratic drift, matched within
      formation week. Each selected name is replaced by a random liquid name from
      the same weekly drift decile, preserving cohort size and week. The memo names
      a "drift-matched random-entry null" without fixing the matching granularity;
      deciles keep cells populated at ~90 names on a ~900-name universe.
  (c) INSIDER AXIS MAPPING for S2 and S3: "Positive" -> +1, "Negative" -> -1,
      "Neutral" / missing -> 0. S3's "no net insider selling" is therefore
      insider_signal != "Negative", so a MISSING field ADMITS a name rather than
      excluding it. State the direction plainly: this is the LESS restrictive
      reading and it admits on absence of evidence. It is chosen because the field
      is missing on roughly 40% of the liquid universe (364 of 899 at 2026-08-07),
      and excluding those would cut S3's universe by a arbitrary two-fifths on a
      data-availability artefact rather than on the insider condition the memo
      names. The gate still bites hard: 432 names carry "Negative" and are
      excluded. "No net insider selling" is read as the field not indicating
      selling, which a missing field does not.
  (d) S1a "improvement" = a strictly more bullish consensus label (a fall in the
      CONSENSUS_ORDER index), confirmed by Last Rating Date inside the window under
      the register-row-7 inclusive bound. Long-only, so downgrades select nothing.
  (e) STYLE NEUTRALISATION is an OLS residual against log market cap, trailing
      dividend yield, trailing beta and sector dummies, fitted cross-sectionally on
      the FULL liquid universe each week (not on the selected names). That is what
      makes the weekly residual mean identically zero and the EW universe the
      embedded benchmark, per the memo.
"""


# --------------------------------------------------------------------------
# panel assembly
# --------------------------------------------------------------------------

def load_merges(merge_dir: Path | None = None) -> list[dict]:
    d = merge_dir or MERGE_DIR
    files = sorted(d.glob("merged_*.json")) if d.exists() else []
    return [json.loads(f.read_text(encoding="utf-8")) for f in files]


def liquid_map(merge: dict) -> dict:
    return {r["ticker"]: r for r in merge["records"] if r.get("liquid")}


def _parse_rating_date(s):
    from target_flow import parse_rating_date
    return parse_rating_date(s)


def _winsorise(x: pd.Series) -> pd.Series:
    lo, hi = x.quantile(WINSOR[0]), x.quantile(WINSOR[1])
    return x.clip(lower=lo, upper=hi)


def _zscore(x: pd.Series) -> pd.Series:
    sd = x.std(ddof=1)
    return (x - x.mean()) / sd if sd and sd > 0 else x * 0.0


def _design_matrix(df: pd.DataFrame) -> np.ndarray:
    """Intercept, log market cap, trailing yield, trailing beta, sector dummies."""
    # Every column is coerced to float explicitly: the measurement frame is built
    # by transposing a dict-of-dicts, which yields object dtype, and lstsq fails
    # on object arrays rather than casting silently.
    num = lambda s: pd.to_numeric(s, errors="coerce").astype(float)
    cols = [np.ones(len(df))]
    cols.append(np.log(num(df["market_cap"]).clip(lower=1.0)).to_numpy())
    cols.append(num(df["dividend_yield"]).fillna(0.0).to_numpy())
    b = num(df["beta"])
    cols.append(b.fillna(b.mean() if b.notna().any() else 1.0).to_numpy())
    sectors = sorted(df["sector"].dropna().unique())
    for s in sectors[1:]:                      # drop first level; intercept carries it
        cols.append((df["sector"] == s).astype(float).to_numpy())
    return np.column_stack(cols)


def residualise(df: pd.DataFrame, ycol: str) -> pd.Series:
    """Cross-sectional OLS residual. Mean is zero by construction, which is what
    makes the EW liquid universe the embedded benchmark (memo, Measurement)."""
    yv = pd.to_numeric(df[ycol], errors="coerce").astype(float)
    ok = yv.notna()
    sub = df.loc[ok]
    if len(sub) < 20:
        return pd.Series(np.nan, index=df.index)
    X = _design_matrix(sub)
    y = yv.loc[ok].to_numpy()
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = pd.Series(np.nan, index=df.index)
    resid.loc[ok] = y - X @ coef
    return resid


def top_decile_by_class(scores: pd.Series, min_names: int = DECILE_MIN_NAMES) -> set:
    """Smallest top set of DISCRETE score classes reaching >= min_names.

    Tie classes are kept whole (memo, S1c). Taking a fixed 10% would split a score
    class arbitrarily, which on a 1-10 integer score is a coin toss deciding
    membership -- so the bucket is defined by classes, and its size floats."""
    if scores.dropna().empty:
        return set()
    order = sorted(scores.dropna().unique(), reverse=True)
    chosen, n = [], 0
    for cls in order:
        chosen.append(cls)
        n += int((scores == cls).sum())
        if n >= min_names:
            break
    return set(scores.index[scores.isin(chosen)])


# --------------------------------------------------------------------------
# scheme selection -- one formation week (prev merge -> curr merge)
# --------------------------------------------------------------------------

def select(prev: dict, curr: dict) -> dict:
    """Return {scheme: set(tickers)}, the S1c retention band, and the week's
    feature frame.

    The frame covers the FULL current liquid universe (register rows 3 and
    8(e)): S1c and the style fit are level constructions on the week's
    universe, so a name need not appear in the prior snapshot to join them.
    Week-on-week quantities for a name without a prior-snapshot pairing read
    as no-event (S1a/S1b False, revision 0.0), never as an exclusion."""
    p, c = liquid_map(prev), liquid_map(curr)
    p_asof = dt.date.fromisoformat(prev["as_of"])
    c_asof = dt.date.fromisoformat(curr["as_of"])

    rows = []
    for t, b in c.items():
        a = p.get(t) or {}
        ca, cb = a.get("analyst_consensus"), b.get("analyst_consensus")
        label_up = (ca in RANK and cb in RANK and RANK[cb] < RANK[ca])
        label_dn = (ca in RANK and cb in RANK and RANK[cb] > RANK[ca])
        rd = _parse_rating_date(b.get("last_rating_date"))
        # Register row 7: inclusive lower bound.
        confirmed = bool(rd and p_asof <= rd <= c_asof)
        bt_p, bt_n = a.get("best_analyst_price_target"), b.get("best_analyst_price_target")
        bt_pct = ((bt_n / bt_p - 1.0) * 100.0
                  if isinstance(bt_p, (int, float)) and isinstance(bt_n, (int, float)) and bt_p
                  else None)
        ins = b.get("insider_signal")
        rows.append({
            "ticker": t,
            "s1a": label_up and confirmed,
            "s1b": bt_pct is not None and bt_pct > 0,
            "bt_pct": bt_pct if bt_pct is not None else 0.0,
            "rating_ind": (1.0 if (label_up and confirmed)
                           else (-1.0 if (label_dn and confirmed) else 0.0)),
            "insider": (1.0 if ins == "Positive" else (-1.0 if ins == "Negative" else 0.0)),
            "no_net_selling": ins != "Negative",
            "smart_score": b.get("smart_score"),
            "tr_3m_pct": b.get("tr_3m_pct"),
            "market_cap": b.get("market_cap") or 1.0,
            "dividend_yield": b.get("dividend_yield"),
            "sector": b.get("sector") or "—",
            "adv_usd": b.get("adv_usd") or 0.0,
            "symbol": b.get("norgate_symbol") or t,
            "anchor": b.get("anchor_date"),
        })
    df = pd.DataFrame(rows).set_index("ticker")
    if df.empty:
        return {"picks": {s: set() for s in SCHEMES}, "bands": {}, "frame": df}

    picks = {}
    picks["S1a"] = set(df.index[df["s1a"]])
    picks["S1b"] = set(df.index[df["s1b"]])
    picks["S1c"] = top_decile_by_class(df["smart_score"])

    # S3: S1a AND 3m TR above the liquid-universe median AND no net insider selling.
    med_tr = df["tr_3m_pct"].median()
    picks["S3"] = set(df.index[df["s1a"] & (df["tr_3m_pct"] > med_tr) & df["no_net_selling"]])

    # Register row 3 retention: a ranked construction re-enters a maturing name
    # while it sits inside twice the entry threshold. For the class-built S1c
    # bucket that is the smallest top set of score classes reaching twice the
    # entry floor; S2's band comes from its composite in build_s2.
    bands = {"S1c": top_decile_by_class(df["smart_score"],
                                        min_names=2 * DECILE_MIN_NAMES)}
    return {"picks": picks, "bands": bands, "frame": df, "median_tr_3m": med_tr}


def build_s2(df: pd.DataFrame, beta: pd.Series) -> tuple[set, set]:
    """S2 entry and retention-band sets from the residualised composite.

    Equal-weight composite of three winsorised z-scores, residualised BEFORE
    ranking -- construction-level neutralisation is S2's defining feature, so
    the decile is chosen on style-neutral signal rather than on a signal that
    happens to load on size or yield. Entry = top decile; band = top two
    deciles (register row 3: twice the entry threshold). The formation
    universe is every liquid name with a computable trailing beta -- a
    formation-time quantity, so the future never selects the universe."""
    work = df.copy()
    work["beta"] = beta.reindex(work.index)
    work = work.loc[work["beta"].notna()]
    if work.empty:
        return set(), set()
    comp = (_zscore(_winsorise(work["rating_ind"]))
            + _zscore(_winsorise(work["bt_pct"]))
            + _zscore(_winsorise(work["insider"]))) / 3.0
    work["_comp"] = comp
    resid = residualise(work, "_comp")
    if resid.dropna().empty:
        return set(), set()
    return (set(resid.index[resid >= resid.quantile(0.90)]),
            set(resid.index[resid >= resid.quantile(0.80)]))


# --------------------------------------------------------------------------
# costs
# --------------------------------------------------------------------------

def cost_bps(adv_usd: float) -> float:
    for floor, bps in COST_TIERS:
        if adv_usd >= floor:
            return bps
    return COST_TIERS[-1][1]


def round_trip_cost_frac(adv_usd: float, flat_bps: float | None = None) -> float:
    """Entry plus exit, as a fraction. Retained names pay nothing at roll, which
    the caller applies by charging only names that actually traded."""
    bps = flat_bps if flat_bps is not None else cost_bps(adv_usd)
    return 2.0 * bps / 10_000.0


# --------------------------------------------------------------------------
# inference
# --------------------------------------------------------------------------

def block_bootstrap_ci(series: np.ndarray, block: int = COHORT_WEEKS,
                       draws: int = BOOTSTRAP_DRAWS, alpha: float = 0.05,
                       rng: np.random.Generator | None = None) -> tuple:
    """Moving-block bootstrap on the weekly cohort-return series.

    Block length is the cohort overlap (see REGISTER_CANDIDATES (a)) -- with
    4-week overlapping cohorts a name contributes to four consecutive weekly
    returns, so blocks shorter than 4 would resample mechanically dependent
    observations as if independent and understate the interval."""
    x = np.asarray([v for v in series if v is not None and not math.isnan(v)], dtype=float)
    n = len(x)
    if n < block + 1:
        return (None, None, n)
    rng = rng or np.random.default_rng(20260810)
    n_blocks = int(math.ceil(n / block))
    starts_max = n - block
    means = np.empty(draws)
    for i in range(draws):
        idx = rng.integers(0, starts_max + 1, size=n_blocks)
        sample = np.concatenate([x[s:s + block] for s in idx])[:n]
        means[i] = sample.mean()
    return (float(np.quantile(means, alpha / 2)),
            float(np.quantile(means, 1 - alpha / 2)), n)


def bh_fdr(pvals: dict, q: float = FDR_Q) -> dict:
    """Benjamini-Hochberg over the graded menu. The memo applies the FDR to the
    null leg only, across N = 5 -- the menu IS the multiple-testing budget."""
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    passed, kmax = {k: False for k in pvals}, 0
    for i, (_, p) in enumerate(items, start=1):
        if p <= i / m * q:
            kmax = i
    for i, (k, _) in enumerate(items, start=1):
        passed[k] = i <= kmax
    return passed


def effective_n(net: dict, horizon: str) -> float | None:
    """Effective independent scheme count from realised net-return
    correlations: N_eff = (sum lambda)^2 / sum lambda^2 over the eigenvalues
    of the correlation matrix (the em-rotation-lab convention). Context only
    -- the DSR itself is computed at nominal N = 5 per the memo."""
    df = pd.DataFrame({s: pd.Series(net[s][horizon], dtype=float)
                       for s in SCHEMES}).dropna()
    if len(df) < 8:
        return None
    df = df.loc[:, df.std(ddof=1) > 0]
    if df.shape[1] < 2:
        return None
    corr = df.corr().to_numpy()
    if np.isnan(corr).any():
        return None
    lam = np.clip(np.linalg.eigvalsh(corr), 0.0, None)
    denom = float((lam ** 2).sum())
    return float(lam.sum() ** 2 / denom) if denom > 0 else None


def realised_moments(x) -> tuple[float, float]:
    """Skewness and RAW kurtosis of the realised net series for the DSR
    (register row 9(j)). Gaussian fallback when the series is too short for
    the higher moments to mean anything."""
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if x.size < 8:
        return 0.0, 3.0
    s = pd.Series(x)
    sk, ku = float(s.skew()), float(s.kurt()) + 3.0   # pandas kurt() is EXCESS kurtosis
    if not (math.isfinite(sk) and math.isfinite(ku)):
        return 0.0, 3.0
    return sk, ku


def deflated_sharpe(sr: float, n_obs: int, n_trials: int,
                    skew: float = 0.0, kurt: float = 3.0) -> float | None:
    """DSR at nominal N (memo: nominal N = 5; effective N reported as context)."""
    if n_obs < 8 or sr is None:
        return None
    e = 0.5772156649
    z = (1 - e) * _norm_ppf(1 - 1.0 / n_trials) + e * _norm_ppf(1 - 1.0 / (n_trials * math.e))
    sr0 = z * math.sqrt(max(1e-12, (1 - skew * sr + (kurt - 1) / 4 * sr ** 2) / (n_obs - 1)))
    denom = math.sqrt(max(1e-12, (1 - skew * sr + (kurt - 1) / 4 * sr ** 2) / (n_obs - 1)))
    return _norm_cdf((sr - sr0) / denom)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Acklam's rational approximation; adequate for a reported statistic."""
    if not 0 < p < 1:
        return float("nan")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


# --------------------------------------------------------------------------
# panel measurement
# --------------------------------------------------------------------------

def risk_frame(frame: pd.DataFrame, anchor: dt.date, get_series,
               market: pd.Series) -> pd.DataFrame:
    """Trailing beta and idiosyncratic drift for every liquid name, from data
    at or before the anchor ONLY.

    Formation-time knowledge: S2's construction and the retention chain need
    this on every formation week, including weeks whose forward windows have
    not matured -- gating formation on measurability would let the future
    select the universe (register row 9(g)). A short history is refused,
    never shrunk (register row 6(b))."""
    mkt = R._closes(market)
    out = {}
    for t, row in frame.iterrows():
        s = get_series(row["symbol"])
        if s is None or len(s) < R.MIN_TRAILING:
            continue
        risk = R.trailing_risk(s, mkt, anchor)
        if risk["beta"] is None:
            continue
        out[t] = {"beta": risk["beta"], "drift": risk["drift_per_session"]}
    if not out:
        return pd.DataFrame(columns=["beta", "drift"])
    return pd.DataFrame(out).T.astype(float)


def measure_week(frame: pd.DataFrame, risk: pd.DataFrame, anchor: dt.date,
                 horizon: str, get_series, market: pd.Series, sessions,
                 data_asof: dt.date, delisted_known=None) -> pd.DataFrame:
    """One formation week: raw -> drift-adjusted -> style-neutral, FULL universe.

    The residual is fitted on every liquid name with a computable adjusted return,
    never on the selected subset (register row 8(e)) -- that is what makes the
    weekly residual mean zero and the EW universe the embedded benchmark. A scheme's
    alpha is then simply the mean residual over its picks.

    On a DELISTED window the market leg covers the name's lived sessions, not
    the full horizon (register row 9(h)): the drift leg is already prorated
    per session, and every removed component must cover the same window.
    """
    mkt = R._closes(market)
    out = {}
    for t in risk.index:
        s = get_series(frame.at[t, "symbol"])
        if s is None:
            continue
        fr = R.forward_return(s, anchor, horizon, data_asof,
                              delisted_known=(delisted_known or {}).get(t))
        if fr["ret_frac"] is None:
            continue
        if fr["delisted"]:
            m_ret = R.market_return_between(mkt, fr["entry_date"], fr["exit_date"])
        else:
            m_ret = R.forward_return(mkt, fr["entry_date"], horizon, data_asof)["ret_frac"]
        if m_ret is None:
            continue
        n_sess = R.sessions_between(fr["entry_date"], fr["exit_date"], sessions)
        adj = R.drift_adjust(fr["ret_frac"], m_ret, float(risk.at[t, "beta"]),
                             float(risk.at[t, "drift"]), n_sess)
        out[t] = {"raw": fr["ret_frac"], "adj": adj, "delisted": fr["delisted"]}
    if not out:
        return pd.DataFrame()
    res = pd.DataFrame(out).T
    joined = frame.join(res, how="inner")
    joined["raw"] = pd.to_numeric(joined["raw"], errors="coerce")
    joined["adj"] = pd.to_numeric(joined["adj"], errors="coerce")
    joined["beta"] = risk["beta"].reindex(joined.index)
    joined["drift"] = risk["drift"].reindex(joined.index)
    joined["neutral"] = residualise(joined, "adj")
    return joined


def cohort_cost(cohort: set, frame: pd.DataFrame, held: set,
                flat_bps: float | None = None) -> tuple[float, set]:
    """Mean round-trip cost across the cohort, and the names actually charged.

    Charged on actual trades only (memo, Measurement), at holding-EPISODE
    level (register row 9(f)): a name already held through any of the four
    live cohorts -- including the one maturing at this roll -- is not sold
    and rebought when it re-enters, so it pays nothing. One continuous
    holding episode pays exactly one round trip, charged to the cohort that
    opened it. `held` is the union of the prior four cohorts."""
    if not cohort:
        return 0.0, set()
    charged = {t for t in cohort if t not in held}
    tot = sum(round_trip_cost_frac(
        float(frame.at[t, "adv_usd"]) if t in frame.index else 0.0, flat_bps)
        for t in charged)
    return tot / len(cohort), charged


def run_panel(merges: list[dict], get_series, market: pd.Series, sessions,
              data_asof: dt.date, horizons=None, delisted_known=None) -> dict:
    """Assemble weekly cohorts and their measured returns for every scheme.

    Selection and cohort formation run on EVERY formation week from
    formation-time information alone; measurement then fills in whichever
    horizons have matured. Horizon series are aligned on weeks with a
    measured PRIMARY window, so cross-horizon comparisons read the same
    weeks (register row 9(g)).

    Cohort mechanics per register row 3: 4-week overlapping Jegadeesh-Titman
    cohorts; ranked constructions (S1c, S2) re-enter a maturing name without
    a round trip while it sits inside twice the entry threshold; event
    buckets (S1a, S1b, S3) re-enter only on a fresh qualifying event. Costs
    are episode-netted (cohort_cost); the 0/5/10/20 bps sweep is accumulated
    in the same pass from the same charged names."""
    horizons = horizons or list(R.HORIZONS)
    net = {s: {h: [] for h in horizons} for s in SCHEMES}
    gross = {s: {h: [] for h in horizons} for s in SCHEMES}
    net_sweep = {b: {s: [] for s in SCHEMES} for b in COST_SWEEP_BPS}
    turnover = {s: [] for s in SCHEMES}
    weeks, universe_resid, week_data = [], [], []
    hist = {s: deque(maxlen=COHORT_WEEKS) for s in SCHEMES}

    for prev, curr in zip(merges, merges[1:]):
        sel = select(prev, curr)
        frame = sel["frame"]
        if frame.empty:
            continue
        anchor = dt.date.fromisoformat(frame["anchor"].iloc[0]) if frame["anchor"].iloc[0] \
            else dt.date.fromisoformat(curr["as_of"])
        risk = risk_frame(frame, anchor, get_series, market)
        if risk.empty:
            continue
        wr = {h: measure_week(frame, risk, anchor, h, get_series, market,
                              sessions, data_asof, delisted_known) for h in horizons}
        base = wr.get(PRIMARY, pd.DataFrame())
        base_ok = not base.empty

        s2_entry, s2_band = build_s2(frame.loc[frame.index.intersection(risk.index)],
                                     risk["beta"])
        entries = dict(sel["picks"])
        entries["S2"] = s2_entry
        bands = {"S1c": sel.get("bands", {}).get("S1c", set()), "S2": s2_band}

        weeks.append(curr["as_of"])
        universe_resid.append(float(base["neutral"].mean()) if base_ok else float("nan"))
        cohorts = {}
        for s in SCHEMES:
            matured = hist[s][0] if len(hist[s]) == COHORT_WEEKS else set()
            held = set().union(*hist[s]) if hist[s] else set()
            cohort = set(entries.get(s) or set())
            if s in bands:
                cohort |= (matured & bands[s])      # band re-entry, no round trip
            cohorts[s] = cohort
            cost, charged = cohort_cost(cohort, frame, held)
            turnover[s].append(len(charged) / len(cohort) if cohort else float("nan"))
            for h in horizons:
                w = wr[h]
                q = (cohort & set(w.index)) if (base_ok and not w.empty) else set()
                g = float(w.loc[list(q), "neutral"].mean()) if q else float("nan")
                gross[s][h].append(g)
                net[s][h].append(g - cost if q else float("nan"))
            qp = (cohort & set(base.index)) if base_ok else set()
            gp = float(base.loc[list(qp), "neutral"].mean()) if qp else float("nan")
            for b in COST_SWEEP_BPS:
                cb, _ = cohort_cost(cohort, frame, held, flat_bps=b)
                net_sweep[b][s].append(gp - cb if qp else float("nan"))
            hist[s].append(cohort)
        week_data.append({"asof": curr["as_of"], "anchor": anchor.isoformat(),
                          "wr": wr, "cohorts": cohorts})

    return {"weeks": weeks, "net": net, "gross": gross, "net_sweep": net_sweep,
            "turnover": turnover, "universe_resid": universe_resid,
            "week_data": week_data}


def _mean_or(x, default=None):
    """Mean over the non-NaN entries, or `default` when none exist. Avoids
    numpy's all-NaN slice warning on the accruing panel's unmatured weeks."""
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    return float(x.mean()) if x.size else default


def null_pvalue(panel: dict, scheme: str, horizon: str,
                draws: int = NULL_DRAWS, rng=None) -> float | None:
    """Drift-matched random-entry null (register rows 8(b) and 9(i)).

    Each realised cohort name is replaced by a random liquid name from the
    SAME weekly decile of trailing idiosyncratic drift, preserving cohort
    size and week. This is the leg the BH-FDR applies to: it asks whether the
    scheme beats a portfolio that shares its drift exposure but carries none
    of its information. An unmatched draw would test against a portfolio with
    the universe's average drift -- a weaker null that would flatter any
    scheme that happens to select high-drift names.

    The null consumes the cohorts the panel actually measured (including
    band re-entries) rather than re-running selection -- the prior code
    re-selected and never built S2's picks, leaving S2's null p-value
    silently at 1.0. The null portfolio is UNCOSTED while the observed
    statistic is net of the scheme's costs: the stricter comparison."""
    obs = _mean_or(panel["net"][scheme][horizon])
    if obs is None:
        return None
    rng = rng or np.random.default_rng(20260811)
    cells = []
    for wd in panel["week_data"]:
        w = wd["wr"].get(horizon, pd.DataFrame())
        base = wd["wr"].get(PRIMARY, pd.DataFrame())
        if w.empty or base.empty or len(w) < 20:   # too thin to match on deciles
            continue
        picks = wd["cohorts"].get(scheme, set()) & set(w.index)
        if not picks:
            continue
        dec = pd.qcut(w["drift"].rank(method="first"), 10, labels=False)
        vals = w["neutral"].to_numpy(dtype=float)
        want = pd.Series(sorted(picks)).map(dec).value_counts().to_dict()
        pools = {d: np.flatnonzero((dec == d).to_numpy()) for d in want}
        cells.append((vals, pools, want))
    if not cells:
        return None
    means = np.empty(draws)
    for i in range(draws):
        wk = []
        for vals, pools, want in cells:
            take = []
            for d, k in want.items():
                pool = pools[d]
                if len(pool) == 0:
                    continue
                take.append(rng.choice(pool, size=min(int(k), len(pool)), replace=False))
            if take:
                wk.append(np.nanmean(vals[np.concatenate(take)]))
        means[i] = np.nanmean(wk) if wk else np.nan
    good = means[~np.isnan(means)]
    if good.size == 0:
        return None
    return float((good >= obs).mean())


# --------------------------------------------------------------------------
# guards
# --------------------------------------------------------------------------

def accrual_state(merges: list[dict], today: dt.date) -> dict:
    caps = [dt.date.fromisoformat(m["as_of"]) for m in merges]
    matured = sum(1 for d in caps
                  if today >= R.horizon_end(d, PRIMARY))
    return {"captures": len(caps), "matured": matured,
            "first": caps[0].isoformat() if caps else None,
            "last": caps[-1].isoformat() if caps else None}


def gate(state: dict, force_interim: bool) -> tuple[bool, str]:
    if state["captures"] < MIN_SNAPSHOTS:
        return False, (f"{state['captures']} captures; the interim read unlocks at "
                       f"{MIN_SNAPSHOTS}. Refusing -- a read below the bar is not a "
                       "weaker result, it is a look at data the pre-registration "
                       "has not opened.")
    if not force_interim and not (state["captures"] >= VERDICT_CAPTURES
                                  and state["matured"] >= VERDICT_COHORTS):
        return False, (f"{state['captures']} captures / {state['matured']} matured "
                       f"cohorts; a VERDICT needs >= {VERDICT_CAPTURES} and "
                       f">= {VERDICT_COHORTS}. Re-run with --force-interim for an "
                       "interim read, which carries NO verdict.")
    return True, ""


# --------------------------------------------------------------------------
# self-test -- runs offline, no NDU, no merges required
# --------------------------------------------------------------------------

def selftest() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ok = True

    def check(name, cond, extra=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{extra}]" if extra else ""))

    print("[analyse] selftest -- construction, measurement, inference, guards\n")

    print("decile by tie class (S1c)")
    s = pd.Series([10] * 40 + [9] * 30 + [8] * 35 + [7] * 100, index=[f"T{i}" for i in range(205)])
    sel = top_decile_by_class(s, min_names=90)
    check("tie classes kept whole -- never splits a score class",
          len(sel) == 105 and all(s[t] >= 8 for t in sel), f"{len(sel)} names, classes 10/9/8")
    check("stops at the first class reaching the floor", 7 not in {s[t] for t in sel})
    band = top_decile_by_class(s, min_names=180)
    check("retention band is the 2x floor and contains the entry set (row 3)",
          sel <= band and len(band) == 205, f"{len(band)} names")

    print("\nwinsorisation and z-scores (S2)")
    x = pd.Series([-1000.0] + [0.0] * 98 + [1000.0])
    w = _winsorise(x)
    check("1st/99th clipped, tails no longer dominate", abs(w.max()) <= 1000 and w.std() < x.std(),
          f"sd {x.std():.1f} -> {w.std():.1f}")
    check("z of a constant series does not divide by zero",
          _zscore(pd.Series([5.0] * 10)).abs().sum() == 0)

    print("\nstyle neutralisation")
    rng = np.random.default_rng(7)
    n = 300
    df = pd.DataFrame({
        "market_cap": np.exp(rng.normal(23, 1.5, n)),
        "dividend_yield": rng.uniform(0, 4, n),
        "beta": rng.normal(1.0, 0.3, n),
        "sector": rng.choice(["Tech", "Health", "Fin"], n),
    }, index=[f"N{i}" for i in range(n)])
    # a return that is PURELY a size tilt plus noise
    df["y"] = 0.02 * np.log(df["market_cap"]) + rng.normal(0, 0.001, n)
    resid = residualise(df, "y")
    check("weekly residual mean is zero -- the EW universe is the embedded benchmark",
          abs(resid.mean()) < 1e-10, f"mean {resid.mean():.2e}")
    check("a pure size tilt is removed, not reported as alpha",
          resid.std() < df["y"].std() / 5, f"sd {df['y'].std():.4f} -> {resid.std():.4f}")

    print("\ncost tiers")
    check("mega-cap tier", cost_bps(500e6) == 3.0)
    check("mid tier", cost_bps(50e6) == 8.0)
    check("thin tier", cost_bps(15e6) == 15.0)
    check("below the ADV floor still pays the widest tier, never zero", cost_bps(1e6) == 15.0)
    check("round trip is two-way", abs(round_trip_cost_frac(500e6) - 0.0006) < 1e-12)
    check("flat sweep overrides the tier", abs(round_trip_cost_frac(500e6, 20.0) - 0.004) < 1e-12)

    print("\nblock bootstrap")
    rng2 = np.random.default_rng(11)
    flat = rng2.normal(0.0, 0.02, 60)
    lo, hi, nn = block_bootstrap_ci(flat, rng=np.random.default_rng(3))
    check("CI on zero-mean noise straddles zero", lo < 0 < hi, f"[{lo:.4f}, {hi:.4f}] n={nn}")
    shifted = flat + 0.05
    lo2, hi2, _ = block_bootstrap_ci(shifted, rng=np.random.default_rng(3))
    check("CI on a strong positive mean excludes zero", lo2 > 0, f"[{lo2:.4f}, {hi2:.4f}]")
    check("too few observations refuses rather than inventing an interval",
          block_bootstrap_ci(np.array([0.1, 0.2]))[0] is None)

    print("\nBenjamini-Hochberg over the N = 5 menu")
    res = bh_fdr({"S1a": 0.001, "S1b": 0.20, "S1c": 0.50, "S2": 0.04, "S3": 0.90})
    check("clear winner passes", res["S1a"])
    check("borderline passes under step-up at q = 0.10", res["S2"], "p=0.04 vs 2/5*0.10=0.04")
    check("weak results fail", not res["S1b"] and not res["S1c"] and not res["S3"])
    allbad = bh_fdr({s: 0.9 for s in SCHEMES})
    check("nothing passes when nothing is significant", not any(allbad.values()))

    print("\naccrual guards")
    today = dt.date(2026, 8, 8)
    few = [{"as_of": "2026-07-09"}, {"as_of": "2026-07-18"}]
    st = accrual_state(few, today)
    passed, why = gate(st, force_interim=True)
    check("refuses below 8 captures even with --force-interim", not passed, why.split(";")[0])
    many = [{"as_of": (dt.date(2026, 1, 5) + dt.timedelta(weeks=i)).isoformat()} for i in range(10)]
    st2 = accrual_state(many, today)
    passed2, why2 = gate(st2, force_interim=False)
    check("refuses a VERDICT below 26 captures / 20 cohorts", not passed2)
    passed3, _ = gate(st2, force_interim=True)
    check("allows an interim read at >= 8 captures", passed3)
    st3 = accrual_state([{"as_of": (dt.date(2026, 1, 5) + dt.timedelta(weeks=i)).isoformat()}
                         for i in range(30)], today)
    check("verdict opens only when both bars are met",
          gate(st3, force_interim=False)[0], f"{st3['captures']} caps / {st3['matured']} matured")

    print("\nmaturity arithmetic (month and year boundaries, per the vault date rule)")
    check("31 Jan + 1m clamps to 28 Feb, never 3 Mar",
          R.horizon_end(dt.date(2026, 1, 31), "1m") == dt.date(2026, 2, 28))
    check("leap year clamps to 29 Feb",
          R.horizon_end(dt.date(2024, 1, 31), "1m") == dt.date(2024, 2, 29))
    check("year boundary rolls correctly",
          R.horizon_end(dt.date(2025, 12, 15), "1m") == dt.date(2026, 1, 15))
    check("3m from 30 Nov lands 28 Feb",
          R.horizon_end(dt.date(2025, 11, 30), "3m") == dt.date(2026, 2, 28))

    print("\nS1a direction (long-only)")
    prev = {"as_of": "2026-07-31", "records": [
        {"ticker": "UP", "liquid": True, "analyst_consensus": "Hold"},
        {"ticker": "DN", "liquid": True, "analyst_consensus": "Strong Buy"},
        {"ticker": "OLD", "liquid": True, "analyst_consensus": "Hold"}]}
    curr = {"as_of": "2026-08-07", "records": [
        {"ticker": "UP", "liquid": True, "analyst_consensus": "Strong Buy",
         "last_rating_date": "Aug 05, 2026", "market_cap": 1e10, "sector": "Tech"},
        {"ticker": "DN", "liquid": True, "analyst_consensus": "Hold",
         "last_rating_date": "Aug 05, 2026", "market_cap": 1e10, "sector": "Tech"},
        {"ticker": "OLD", "liquid": True, "analyst_consensus": "Strong Buy",
         "last_rating_date": "Jun 01, 2026", "market_cap": 1e10, "sector": "Tech"}]}
    sel2 = select(prev, curr)["picks"]
    check("a confirmed upgrade selects", "UP" in sel2["S1a"])
    check("a downgrade never selects -- the menu is long-only", "DN" not in sel2["S1a"])
    check("an upgrade with a stale rating date is not confirmed", "OLD" not in sel2["S1a"])
    curr["records"].append({"ticker": "NEW", "liquid": True, "analyst_consensus": "Hold",
                            "smart_score": 10, "market_cap": 1e10, "sector": "Tech"})
    sel3 = select(prev, curr)
    check("an unpaired name joins the universe frame with no-event defaults (row 3/8(e))",
          "NEW" in sel3["frame"].index and "NEW" not in sel3["picks"]["S1a"]
          and "NEW" not in sel3["picks"]["S1b"])
    check("the S1c retention band is exposed and contains the entry bucket",
          "S1c" in sel3["bands"] and sel3["picks"]["S1c"] <= sel3["bands"]["S1c"])

    print("\nEND-TO-END on synthetic paths with a KNOWN answer")
    # Build a market and 200 names with known betas and known idiosyncratic drift.
    # Half the names are given a genuine one-month excess; the scheme picks exactly
    # those. If the loop is correct it recovers the injected excess after stripping
    # beta and drift -- and recovers ZERO for a scheme picking on a pure beta tilt.
    idx = pd.bdate_range("2025-01-01", periods=420)
    rng3 = np.random.default_rng(99)
    # The market must carry real variance or beta is unidentifiable: cov/var on a
    # constant-return market divides by a floating-point zero and returns noise.
    mkt = pd.Series(100.0 * np.cumprod(1 + rng3.normal(0.0004, 0.008, len(idx))), index=idx)
    names, series = [], {}
    INJECT = 0.03                       # 3% one-month excess on the treated half
    anchor = idx[300].date()
    for i in range(200):
        beta = 0.6 + 1.2 * rng3.random()
        drift = rng3.normal(0.0002, 0.0001)
        s = R._synthetic("2025-01-01", len(idx), drift, beta=beta, market=mkt)
        treated = i % 2 == 0
        if treated:                     # add the excess strictly AFTER the anchor
            bump = pd.Series(1.0, index=s.index)
            bump.loc[s.index > pd.Timestamp(anchor)] = 1.0 + INJECT
            s = s * bump
        names.append({"ticker": f"X{i}", "symbol": f"X{i}", "treated": treated,
                      "beta_true": beta, "market_cap": float(np.exp(rng3.normal(23, 1.0))),
                      "dividend_yield": float(rng3.uniform(0, 3)),
                      "sector": ["Tech", "Health", "Fin"][i % 3],
                      "adv_usd": 500e6, "anchor": anchor.isoformat()})
        series[f"X{i}"] = s
    frame = pd.DataFrame(names).set_index("ticker")
    sessions = [d.date() for d in idx]
    data_asof = idx[-1].date()
    get = lambda sym: series.get(sym)          # noqa: E731
    risk = risk_frame(frame, anchor, get, mkt)
    wr = measure_week(frame, risk, anchor, "1m", get, mkt, sessions, data_asof)
    check("every name measured -- no silent drops", len(wr) == 200, f"{len(wr)} of 200")
    check("trailing beta recovered from the path",
          abs((wr["beta"] - frame.loc[wr.index, "beta_true"]).abs().mean()) < 0.02,
          f"mean abs err {(wr['beta'] - frame.loc[wr.index, 'beta_true']).abs().mean():.4f}")
    check("weekly residual mean is zero across the FULL universe",
          abs(wr["neutral"].mean()) < 1e-10, f"{wr['neutral'].mean():.2e}")
    treated_alpha = wr.loc[wr.index[frame.loc[wr.index, "treated"]], "neutral"].mean()
    untreated_alpha = wr.loc[wr.index[~frame.loc[wr.index, "treated"]], "neutral"].mean()
    check("injected 3% excess is recovered on the treated half",
          abs(treated_alpha - INJECT / 2) < 0.006, f"{treated_alpha:.4f} (half of {INJECT} "
          f"because the residual is centred on the whole universe)")
    check("untreated half carries the mirror image, not alpha of its own",
          abs(treated_alpha + untreated_alpha) < 1e-10,
          f"{treated_alpha:.4f} vs {untreated_alpha:.4f}")
    # The guard that matters: a scheme selecting purely on HIGH BETA must score zero.
    # Drawn across the WHOLE universe -- treatment is assigned independently of beta,
    # so a beta-only selection should land half on each side and net to nothing. (An
    # earlier version of this check intersected with the untreated half, which merely
    # re-measured the mirror image and would have "failed" a correct loop.)
    hb = wr.loc[wr.index[wr["beta"] > wr["beta"].median()], "neutral"]
    check("a pure high-beta selection earns no alpha -- guard 2 of the memo holds",
          abs(hb.mean()) < 0.005, f"{hb.mean():.4f} on {len(hb)} names")

    print("\ndelisted window: every adjustment component covers the lived sessions (row 9(h))")
    # A name that IS the market, truncated eleven sessions after the anchor:
    # raw, beta leg and drift leg then cover the same part-window, so its
    # adjusted alpha is exactly zero. Under a full-horizon market leg it would
    # instead carry the market's post-delisting move with the sign flipped.
    dl = mkt.iloc[:311].copy()
    dlf = pd.DataFrame([{"ticker": "DLST", "symbol": "DLST", "market_cap": 1e10,
                         "dividend_yield": 1.0, "sector": "Tech", "adv_usd": 500e6,
                         "anchor": anchor.isoformat()}]).set_index("ticker")
    ser2 = {"DLST": dl}
    risk2 = risk_frame(dlf, anchor, lambda sym: ser2.get(sym), mkt)
    wr2 = measure_week(dlf, risk2, anchor, "1m", lambda sym: ser2.get(sym), mkt,
                       sessions, data_asof)
    check("a name stopping mid-window while the feed runs on is flagged delisted",
          len(wr2) == 1 and bool(wr2.at["DLST", "delisted"]))
    check("a market-mirroring delisted name earns exactly zero adjusted alpha",
          abs(float(wr2.at["DLST", "adj"])) < 1e-12,
          f"{float(wr2.at['DLST', 'adj']):.2e}")

    print("\ndrift-matched null (register row 8(b))")
    # A null that ignores drift is a weaker null. Build a universe where high-drift
    # names carry a real excess, then have the "scheme" pick exactly the top drift
    # decile: a matched null must find that unremarkable, an unmatched one would
    # call it a discovery.
    nn = 300
    d_rng = np.random.default_rng(5)
    drift_v = d_rng.normal(0, 0.0004, nn)
    neutral_v = 40.0 * drift_v + d_rng.normal(0, 0.002, nn)      # alpha IS the drift
    wr2 = pd.DataFrame({"drift": drift_v, "neutral": neutral_v},
                       index=[f"D{i}" for i in range(nn)])
    dec2 = pd.qcut(wr2["drift"].rank(method="first"), 10, labels=False)
    top = set(wr2.index[dec2 == 9])
    obs2 = wr2.loc[list(top), "neutral"].mean()
    r2 = np.random.default_rng(17)
    matched, unmatched = [], []
    pool_top = np.flatnonzero((dec2 == 9).to_numpy())
    vals2 = wr2["neutral"].to_numpy()
    for _ in range(400):
        matched.append(vals2[r2.choice(pool_top, len(top), replace=False)].mean())
        unmatched.append(vals2[r2.choice(nn, len(top), replace=False)].mean())
    p_matched = float(np.mean(np.array(matched) >= obs2))
    p_unmatched = float(np.mean(np.array(unmatched) >= obs2))
    check("a drift-MATCHED null finds a pure drift pick unremarkable",
          p_matched > 0.10, f"p = {p_matched:.3f}")
    check("an UNMATCHED null would have called the same pick a discovery",
          p_unmatched < 0.01, f"p = {p_unmatched:.3f} -- this is what 8(b) prevents")

    print("\ncost accounting (episode netting, register row 9(f))")
    f2 = pd.DataFrame({"adv_usd": [500e6, 500e6, 15e6]}, index=["A", "B", "C"])
    c_all_new, ch = cohort_cost({"A", "B"}, f2, set())
    check("a fresh cohort pays a full round trip",
          abs(c_all_new - 0.0006) < 1e-12 and ch == {"A", "B"})
    c_retained, ch = cohort_cost({"A", "B"}, f2, {"A", "B"})
    check("a fully retained cohort pays nothing at the roll",
          c_retained == 0.0 and not ch)
    c_mixed, ch = cohort_cost({"A", "C"}, f2, {"A"})
    check("only the new name pays, and at its own tier",
          abs(c_mixed - (2 * 15 / 10_000) / 2) < 1e-12 and ch == {"C"}, f"{c_mixed:.6f}")
    c_older, ch = cohort_cost({"A"}, f2, {"A", "B", "C"})
    check("a name held via ANY live cohort pays nothing -- episode, not week-on-week",
          c_older == 0.0 and not ch)
    c_flat, _ = cohort_cost({"A", "B"}, f2, set(), flat_bps=20.0)
    check("flat sweep overrides the tier in the same accounting",
          abs(c_flat - 0.004) < 1e-12, f"{c_flat:.4f}")

    print(f"\n[analyse] selftest {'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


# --------------------------------------------------------------------------

def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true", help="synthetic checks, runs offline")
    ap.add_argument("--force-interim", action="store_true",
                    help="interim read once >= 8 captures exist; carries NO verdict")
    ap.add_argument("--register", action="store_true",
                    help="print the implementation choices needing a register row, and stop")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if a.register:
        print("[analyse] implementation choices in force -- register row 8 (2026-08-08):")
        print(REGISTER_ROW8)
        print("[analyse] and register row 9 (2026-08-13, pre-flight):")
        print(REGISTER_ROW9)
        return 0

    merges = load_merges()
    if not merges:
        raise SystemExit("[analyse] no merged_*.json -- run the weekly pipeline first")
    state = accrual_state(merges, dt.date.today())
    print(f"[analyse] {state['captures']} captures {state['first']} -> {state['last']}; "
          f"{state['matured']} with a matured {PRIMARY} window")

    passed, why = gate(state, a.force_interim)
    if not passed:
        print(f"[analyse] REFUSED: {why}")
        print("[analyse] The gate is a hard refusal, not a warning: the value of a "
              "pre-registration is exactly that it binds when the data is tempting.")
        return 1

    print("[analyse] gate open. Implementation choices in force (register rows 8 and 9):")
    print(REGISTER_ROW8)
    print(REGISTER_ROW9)

    import exchange_calendars as xcals
    import norgate as ng

    n = ng.connect()
    market = R.market_series(n)
    data_asof = ng.last_completed_session()
    cal = xcals.get_calendar("XNYS")
    first = dt.date.fromisoformat(merges[0]["as_of"]) - dt.timedelta(days=400)
    sessions = [d.date() for d in cal.sessions_in_range(
        pd.Timestamp(first), pd.Timestamp(data_asof))]

    # --- symbol resolution, delisting-aware (register row 9(k)) -------------
    # Every panel symbol resolved at capture, so a live-feed miss here means
    # the name has since delisted (the plain symbol moves to the delisted
    # database, EA-202608 style) or, later, been reassigned. Silently
    # dropping such a name is the survivorship gap guard 1 exists to prevent.
    earliest: dict[str, tuple[str, dt.date]] = {}
    for m in merges:
        for r in m["records"]:
            if not r.get("liquid"):
                continue
            t = r["ticker"]
            sym = r.get("norgate_symbol") or t
            d = dt.date.fromisoformat(r.get("anchor_date") or m["as_of"])
            if t not in earliest or d < earliest[t][1]:
                earliest[t] = (sym, d)
    cache, delisted_known, unresolved = {}, {}, []
    for t, (sym, d0) in sorted(earliest.items()):
        try:
            s = R.stock_series(sym, n)
        except Exception:                          # noqa: BLE001 -- feed quirk
            s = None
        if s is None or s.loc[:pd.Timestamp(d0)].empty:
            dsym = ng.delisted_symbol_for(n, t, d0)
            if dsym is not None:
                ds = R.stock_series(dsym, n)
                if ds is not None:
                    s = ds
                    delisted_known[t] = True
        if s is None:
            unresolved.append(t)
        cache[sym] = s
    if delisted_known:
        print(f"[analyse] {len(delisted_known)} name(s) resolved through the delisted "
              f"database: {', '.join(sorted(delisted_known))}")
    if unresolved:
        print(f"[analyse] WARNING: {len(unresolved)} liquid name(s) resolve on neither "
              f"the live nor the delisted feed and DROP from measurement: "
              f"{', '.join(unresolved)}")
        print("[analyse] Every dropped name is a survivorship hole. Investigate "
              "(norgate.py --probe, delisted database) before trusting this read.")

    def get_series(sym: str):
        return cache.get(sym)

    print(f"[analyse] measuring {len(merges) - 1} formation weeks against a feed "
          f"through {data_asof}...")
    panel = run_panel(merges, get_series, market, sessions, data_asof,
                      delisted_known=delisted_known)
    n_measured = sum(1 for v in panel["universe_resid"] if not math.isnan(v))
    print(f"[analyse] {len(panel['weeks'])} formation weeks, {n_measured} with a "
          f"measured {PRIMARY} window; universe residual mean "
          f"{_mean_or(panel['universe_resid'], float('nan')):.2e} "
          "(must be ~0 -- the EW universe is the embedded benchmark)")

    verdict_ok = (state["captures"] >= VERDICT_CAPTURES
                  and state["matured"] >= VERDICT_COHORTS)
    pvals, out = {}, {}
    for s in SCHEMES:
        net = np.asarray(panel["net"][s][PRIMARY], dtype=float)
        xs = net[~np.isnan(net)]
        pt = float(xs.mean()) if xs.size else float("nan")
        lo, hi, nobs = block_bootstrap_ci(net)
        p = null_pvalue(panel, s, PRIMARY)
        pvals[s] = 1.0 if p is None else p
        signs = {h: _mean_or(panel["net"][s][h], float("nan")) for h in R.HORIZONS}
        sign_stable = (not math.isnan(pt) and pt != 0
                       and all(np.sign(v) == np.sign(pt) for v in signs.values()
                               if not math.isnan(v)))
        sd = float(xs.std(ddof=1)) if xs.size > 1 else float("nan")
        sr = pt / sd if sd and not math.isnan(sd) and sd > 0 else None
        sk, ku = realised_moments(net)
        sweep = {f"{b:g}": _mean_or(panel["net_sweep"][b][s]) for b in COST_SWEEP_BPS}
        out[s] = {"alpha_1m": pt, "ci95": [lo, hi], "n_cohorts": nobs,
                  "null_p": p, "sign_stable": sign_stable,
                  "by_horizon": signs, "sharpe": sr,
                  "skew": sk, "kurtosis": ku,
                  "dsr": (deflated_sharpe(sr, nobs or 0, len(SCHEMES), skew=sk, kurt=ku)
                          if sr is not None else None),
                  "cost_sweep_alpha_1m": sweep,
                  "turnover": _mean_or(panel["turnover"][s], float("nan"))}

    fdr = bh_fdr(pvals)
    n_eff = effective_n(panel["net"], PRIMARY)
    print(f"\n{'scheme':<7}{'alpha 1m':>10}{'95% CI':>22}{'null p':>9}"
          f"{'FDR':>6}{'sign':>6}{'turn':>7}")
    for s in SCHEMES:
        o = out[s]
        ci = (f"[{o['ci95'][0]:+.4f},{o['ci95'][1]:+.4f}]"
              if o["ci95"][0] is not None else "        n/a")
        keep = (o["ci95"][0] is not None and o["ci95"][0] > 0
                and fdr[s] and o["sign_stable"])
        out[s]["keep"] = keep if verdict_ok else None
        print(f"{s:<7}{o['alpha_1m']:>+10.4f}{ci:>22}"
              f"{(o['null_p'] if o['null_p'] is not None else float('nan')):>9.3f}"
              f"{('yes' if fdr[s] else 'no'):>6}"
              f"{('yes' if o['sign_stable'] else 'no'):>6}"
              f"{o['turnover']:>7.2f}")

    # Memo, Measurement: report the flat 0/5/10/20 bps sweep alongside the
    # tiered headline. No friction-free headline -- the 0 bps column is the
    # sweep's lower bound, never the number that leads.
    hdr = "".join(f"{'net@' + f'{b:g}' + 'bp':>12}" for b in COST_SWEEP_BPS)
    print(f"\n{'scheme':<7}{hdr}   (flat sweep, 1m)")
    for s in SCHEMES:
        cells = "".join(
            f"{v:>+12.4f}" if v is not None else f"{'n/a':>12}"
            for v in (out[s]["cost_sweep_alpha_1m"][f"{b:g}"] for b in COST_SWEEP_BPS))
        print(f"{s:<7}{cells}")
    print("[analyse] N_eff (realised net-return correlations, context; DSR is at "
          f"nominal N = {len(SCHEMES)}) = " + (f"{n_eff:.2f}" if n_eff else "n/a"))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().isoformat()
    payload = {"generated": stamp, "captures": state["captures"],
               "matured_cohorts": state["matured"], "verdict_eligible": verdict_ok,
               "register_rows": [8, 9], "n_eff": n_eff,
               "delisted_resolved": sorted(delisted_known),
               "unresolved_dropped": unresolved,
               "schemes": out, "weeks": panel["weeks"]}
    (OUT_DIR / f"analysis_{stamp}.json").write_text(
        json.dumps(payload, indent=2, default=float), encoding="utf-8")

    if not verdict_ok:
        print("\n[analyse] INTERIM READ -- NO VERDICT. The KEEP column is withheld, not "
              "computed-and-hidden: below 26 captures and 20 matured cohorts the "
              "time-block bootstrap holds too few independent blocks to be reliable, "
              "so a KEEP here would be a number without the property it claims.")
    print(f"[analyse] wrote {(OUT_DIR / f'analysis_{stamp}.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
