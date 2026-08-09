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

IMPLEMENTATION CHOICES NOT FIXED BY THE MEMO -- see REGISTER_CANDIDATES below.
The memo names "episode-block bootstrap" without fixing a block length, names the
"drift-matched random-entry null" without fixing how drift is matched, and names an
"insider axis (+1/0/-1)" without mapping the vendor's strings onto it. Those are
decided here, stated here, and must be registered before any read is taken -- the
row-6 precedent: the only moment such choices provably cannot be tuned to a result
is before a forward window has matured.

Reads data/merged/ (which carries Norgate returns) and pulls price history, so
unlike the diagnostics this DOES touch forward returns. That is the point of it,
and it is why the accrual guards below are hard refusals rather than warnings.

    python scripts/analyse.py --selftest      # synthetic; runs offline, no NDU
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

REGISTER_CANDIDATES = """
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

def load_merges() -> list[dict]:
    files = sorted(MERGE_DIR.glob("merged_*.json")) if MERGE_DIR.exists() else []
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
    cols = [np.ones(len(df))]
    cols.append(np.log(df["market_cap"].clip(lower=1.0)).to_numpy())
    cols.append(df["dividend_yield"].fillna(0.0).to_numpy())
    cols.append(df["beta"].fillna(df["beta"].mean()).to_numpy())
    sectors = sorted(df["sector"].dropna().unique())
    for s in sectors[1:]:                      # drop first level; intercept carries it
        cols.append((df["sector"] == s).astype(float).to_numpy())
    return np.column_stack(cols)


def residualise(df: pd.DataFrame, ycol: str) -> pd.Series:
    """Cross-sectional OLS residual. Mean is zero by construction, which is what
    makes the EW liquid universe the embedded benchmark (memo, Measurement)."""
    ok = df[ycol].notna()
    sub = df.loc[ok]
    if len(sub) < 20:
        return pd.Series(np.nan, index=df.index)
    X = _design_matrix(sub)
    y = sub[ycol].to_numpy()
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
    """Return {scheme: set(tickers)} plus the week's feature frame."""
    p, c = liquid_map(prev), liquid_map(curr)
    p_asof = dt.date.fromisoformat(prev["as_of"])
    c_asof = dt.date.fromisoformat(curr["as_of"])
    paired = [t for t in c if t in p]

    rows = []
    for t in paired:
        a, b = p[t], c[t]
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
        return {"picks": {s: set() for s in SCHEMES}, "frame": df}

    picks = {}
    picks["S1a"] = set(df.index[df["s1a"]])
    picks["S1b"] = set(df.index[df["s1b"]])
    picks["S1c"] = top_decile_by_class(df["smart_score"])

    # S3: S1a AND 3m TR above the liquid-universe median AND no net insider selling.
    med_tr = df["tr_3m_pct"].median()
    picks["S3"] = set(df.index[df["s1a"] & (df["tr_3m_pct"] > med_tr) & df["no_net_selling"]])
    return {"picks": picks, "frame": df, "median_tr_3m": med_tr}


def build_s2(df: pd.DataFrame, beta: pd.Series) -> set:
    """Equal-weight composite of three winsorised z-scores, residualised, top decile.

    S2's defining feature is construction-level neutralisation: the composite is
    residualised BEFORE ranking, so the decile is chosen on style-neutral signal
    rather than on a signal that happens to load on size or yield."""
    work = df.copy()
    work["beta"] = beta.reindex(work.index)
    comp = (_zscore(_winsorise(work["rating_ind"]))
            + _zscore(_winsorise(work["bt_pct"]))
            + _zscore(_winsorise(work["insider"]))) / 3.0
    work["_comp"] = comp
    resid = residualise(work, "_comp")
    if resid.dropna().empty:
        return set()
    cut = resid.quantile(0.90)
    return set(resid.index[resid >= cut])


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
        print("[analyse] implementation choices NOT fixed by RESEARCH_MEMO.md:")
        print(REGISTER_CANDIDATES)
        print("[analyse] Register these before any read is taken (row-6 precedent).")
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

    print("[analyse] gate open. Implementation choices in force (register them "
          "before quoting any number):")
    print(REGISTER_CANDIDATES)
    print("[analyse] Panel measurement requires NDU and is run from here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
