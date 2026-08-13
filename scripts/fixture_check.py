#!/usr/bin/env python
"""fixture_check.py -- end-to-end verification of the frozen analysis path on
SYNTHETIC fixture panels with a known planted answer.

Complements the unit-level selftests: those prove each mechanism in isolation;
this writes real merged_*.json files to a temporary directory, loads them
through analyse.load_merges, and runs the full pre-registered path -- select ->
risk_frame -> measure_week -> run_panel -> bootstrap CI -> drift-matched null
-> BH-FDR -> KEEP -- asserting that the planted result comes back.

Fixture A (deterministic, closed form). Every name IS the market before the
plant, so beta is exactly 1, trailing drift exactly 0, and the style fit
collapses to mean-centring. A single one-day excess J on 90 treated names,
placed inside exactly one formation week's 1-month window and after every
trailing window, must come back as 0.9 x J x (1 + M) to 1e-12 -- and as
exactly zero everywhere else. Also planted: a mid-window delisting whose
adjusted alpha must be exactly zero (the market leg covers its lived sessions,
register row 9(h)); an S2 walker that re-enters through the retention band and
exits below it (row 3); episode netting cost schedules; the 0/5/10/20 sweep;
the degenerate drift-matched null (all-equal drift -> the null resamples the
cohort itself and must report p = 1.0 exactly); and the maturity exclusion of
the unmatured tail. The KEEP verdict on a one-burst alpha must be False --
the bar exists to refuse exactly this.

Fixture C (stochastic, seeded). Noisy paths, dispersed betas, a persistent
one-day excess planted on a ROTATING treated set carried by S1b, decoy schemes
with no excess. The drift-matched null gets honest cells here; S1b must come
back KEEP = True at verdict scale with the planted magnitude recovered, and
the decoys must fail. Deterministic via fixed seeds.

Vendor values never enter version control; every number below is fabricated.

    python scripts/fixture_check.py            # ~1-3 minutes, offline, no NDU
"""
from __future__ import annotations

import datetime as dt   # Python datetime: months are 1-indexed (Jan == 1)
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import analyse as A     # noqa: E402
import returns as R     # noqa: E402

FAILS: list[str] = []


def check(label: str, cond, extra: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  [{extra}]" if extra else ""))
    if not cond:
        FAILS.append(label)


def close(a, b, tol=1e-12):
    return a is not None and b is not None and abs(a - b) <= tol


def write_merges(tmp: Path, merges: list[dict]) -> Path:
    d = tmp / "merged"
    d.mkdir(parents=True, exist_ok=True)
    for m in merges:
        (d / f"merged_{m['as_of']}.json").write_text(json.dumps(m), encoding="utf-8")
    return d


def rd_str(d: dt.date) -> str:
    return d.strftime("%b %d, %Y")


# ---------------------------------------------------------------------------
# fixture A -- deterministic, closed form
# ---------------------------------------------------------------------------

def fixture_a(tmp: Path) -> None:
    print("\nFIXTURE A -- deterministic closed form (exact recovery)")
    U, TREAT, J = 900, 90, 0.02
    idx = pd.bdate_range("2024-06-03", periods=520)
    t_arr = np.arange(len(idx))
    mr = 0.0004 * (1.0 + 0.5 * np.sin(t_arr / 7.0))      # deterministic, non-constant
    mkt_full = pd.Series(100.0 * np.cumprod(1.0 + mr), index=idx)

    n_merges = 30
    a_idx = [300 + 5 * k for k in range(n_merges)]        # merge k's anchor session
    a_date = [idx[i].date() for i in a_idx]
    sess_dates = [d.date() for d in idx]

    def snap_idx(d: dt.date) -> int:
        return max(i for i, s in enumerate(sess_dates) if s <= d)

    end25 = snap_idx(R.horizon_end(a_date[25], "1m"))     # window-end session of week 25
    end24 = snap_idx(R.horizon_end(a_date[24], "1m"))
    D = end25                                             # plant on week 25's last session
    assert end24 < D, "fixture construction: the plant leaks into week 24's window"
    data_asof = R.horizon_end(a_date[25], "1m")           # weeks 26-29 stay unmatured
    cut = snap_idx(data_asof)

    tickers = [f"T{i:03d}" for i in range(U)]
    treated = set(tickers[:TREAT])
    walker, parked, delist = "T100", "T089", "T890"
    delist_end = a_idx[3] + 8                             # delists 8 sessions into week 3

    series = {}
    for i, t in enumerate(tickers):
        px = mkt_full.copy()
        if t in treated:
            px.iloc[D:] *= (1.0 + J)                      # one-day excess, week 25 only
        end = delist_end if t == delist else cut
        series[t] = px.iloc[:end + 1]
    market = mkt_full.iloc[:cut + 1]

    def bt_level(i: int, k: int, t: str) -> float:
        if t == parked:
            # Parked out of S2 for merges 1-4 (flat target, Neutral insider) so
            # the walker has a genuine 90th slot to enter; raises resume at 5.
            return 100.0 if k <= 4 else 100.0 * (1.01 ** (k - 4))
        if t in treated:
            return 100.0 * (1.01 ** k)                    # raised every merge -> S1b
        if t == walker:
            # Distinctly BELOW the block throughout -- winsorisation clips
            # anything above the block back onto it, and exact composite ties
            # break on float rounding, so the walk runs strictly under: +0.9%
            # for entry (merges 1-4), +0.5% for the band leg (5-8), cuts after.
            steps = [1.009 if j <= 4 else (1.005 if j <= 8 else 0.9998)
                     for j in range(1, k + 1)]
            return 200.0 * float(np.prod(steps)) if k else 200.0
        if t == delist:
            return 50.0 if k < 3 else 51.0                # one raise, at merge 3
        return 100.0 * ((1.0 - (i - 89) * 1e-6) ** k)     # distinct tiny cuts (never S1b)

    merges = []
    for k in range(n_merges):
        recs = []
        for i, t in enumerate(tickers):
            if t == delist and k >= 4:
                continue                                  # left the export post-delisting
            up_week = (k % 2 == 1)
            # The walker mirrors the treated block's rating and insider fields
            # for merges 1-4 so its composite TIES the block (entry); from
            # merge 5 it reverts to a no-event name and only its target level
            # steers the walk down through the band and out.
            flips = t in treated or (t == walker and k <= 4)
            recs.append({
                "ticker": t, "liquid": True, "norgate_symbol": t,
                "anchor_date": a_date[k].isoformat(),
                "analyst_consensus": ("Strong Buy" if up_week else "Hold") if flips else "Hold",
                "last_rating_date": (rd_str(a_date[k]) if (flips and up_week)
                                     else rd_str(dt.date(2024, 1, 2))),
                "best_analyst_price_target": bt_level(i, k, t),
                "smart_score": 10 if t in treated else (9 if 90 <= i < 270 else 5),
                "insider_signal": ("Positive"
                                   if ((t in treated and not (t == parked and k <= 4))
                                       or (t == walker and k <= 4))
                                   else "Neutral"),
                # Continuous, distinct values: a knife-edge two-value design
                # broke when the delisting turned the universe count odd and
                # the median landed ON the block's value.
                "tr_3m_pct": 10.0 if t in treated else (-(i - 89) / 100.0),
                "market_cap": 1e10, "dividend_yield": 1.0, "sector": "Tech",
                "adv_usd": 500e6,
            })
        merges.append({"as_of": a_date[k].isoformat(), "records": recs})

    mdir = write_merges(tmp / "A", merges)
    loaded = A.load_merges(mdir)
    check("loader reads the fixture panel", len(loaded) == 30)

    # --- the coded accrual gate, on fixture accrual ------------------------
    today = data_asof
    st6 = A.accrual_state(loaded[:6], today)
    check("gate refuses 6 captures even with --force-interim",
          not A.gate(st6, force_interim=True)[0])
    st8 = A.accrual_state(loaded[:8], today)
    check("gate allows an interim (no-verdict) read at 8 captures",
          A.gate(st8, force_interim=True)[0] and not A.gate(st8, force_interim=False)[0])
    st30 = A.accrual_state(loaded, today)
    check("verdict opens at 30 captures / >= 20 matured cohorts",
          A.gate(st30, force_interim=False)[0],
          f"{st30['captures']} caps / {st30['matured']} matured")

    get = lambda sym: series.get(sym)                     # noqa: E731
    panel = A.run_panel(loaded, get, market, sess_dates, data_asof)

    check("29 formation weeks formed (selection never waits on maturity)",
          len(panel["weeks"]) == 29)
    resid = np.asarray(panel["universe_resid"], dtype=float)
    check("weeks 26-29 excluded as unmatured, 25 measured",
          np.isnan(resid[25:]).all() and (~np.isnan(resid[:25])).all())
    check("universe residual mean exactly zero on every measured week",
          float(np.nanmax(np.abs(resid[:25]))) < 1e-12,
          f"max {float(np.nanmax(np.abs(resid[:25]))):.1e}")

    # Closed-form planted alpha. The measured universe is 899 names by week 25
    # -- the delisted name left the export at merge 4 -- so the mean-centring
    # factor is (1 - 90/899), not 0.9 of a nominal 900. The first run of this
    # fixture asserted 0.9 and FAILED against a correct pipeline; the fixture
    # was wrong, which is exactly the direction this check is meant to cut.
    m25 = float(np.prod(1.0 + mr[a_idx[25] + 1: end25 + 1])) - 1.0
    G = (1.0 - 90.0 / 899.0) * J * (1.0 + m25)
    rt = 2 * 3 / 10_000                                   # 3 bp tier, round trip
    zero_idx = [1, 3] + list(range(5, 24))                # w3 and w5 pay episode entries

    s1b = np.asarray(panel["net"]["S1b"]["1m"], dtype=float)
    check("S1b week 1 pays exactly one round trip (90 names, all new)",
          close(s1b[0], -rt), f"{s1b[0]:.6f}")
    check("S1b week 3: only the delisted entrant pays, over the 91-name cohort",
          close(s1b[2], -rt / 91), f"{s1b[2]:.7f}")
    check("S1b week 5: only the un-parked block name pays its episode entry",
          close(s1b[4], -rt / 91), f"{s1b[4]:.7f}")
    check("S1b weeks 2, 4, 6-24 exactly zero (held book, zero alpha planted)",
          bool(np.max(np.abs(s1b[zero_idx])) < 1e-12))
    check("planted alpha recovered EXACTLY on week 25: (1-90/899)*J*(1+M)",
          close(s1b[24], G), f"{s1b[24]:.10f} vs {G:.10f}")
    check("unmatured tail is NaN, never a number", bool(np.isnan(s1b[25:]).all()))

    s1c = np.asarray(panel["net"]["S1c"]["1m"], dtype=float)
    same = [i for i in range(25) if i not in (2, 4)]
    check("S1c identical to S1b except weeks 3/5 (never held the delisting or parked name)",
          bool(np.max(np.abs(s1c[same] - s1b[same])) < 1e-12)
          and abs(s1c[2]) < 1e-12 and abs(s1c[4]) < 1e-12)

    s1a = np.asarray(panel["net"]["S1a"]["1m"], dtype=float)
    even_nan = all(np.isnan(s1a[k]) for k in range(1, 24, 2))
    check("S1a fires on confirmed-upgrade weeks only (event bucket)",
          even_nan and close(s1a[0], -rt) and close(s1a[24], G))
    s3 = np.asarray(panel["net"]["S3"]["1m"], dtype=float)
    check("S3 equals S1a under the fixture's gate fields",
          close(s3[0], -rt) and close(s3[24], G))

    # delisting: week 3, S1b cohort carries the delisted name
    wd3 = panel["week_data"][2]
    wr3 = wd3["wr"]["1m"]
    check("delisted name measured in its cohort, flagged delisted",
          delist in wd3["cohorts"]["S1b"] and delist in wr3.index
          and bool(wr3.at[delist, "delisted"]))
    check("delisted name's adjusted alpha exactly zero (market leg covers lived window)",
          abs(float(wr3.at[delist, "adj"])) < 1e-12,
          f"{float(wr3.at[delist, 'adj']):.1e}")
    n3 = len(wd3["cohorts"]["S1b"])
    check("week 3 S1b cohort is 91 names (89 block + walker + the delisting)",
          n3 == 91, f"{n3}")

    # retention band walk (S2): entry weeks 1-4, band-held 5-8, exits at 9
    coh = [wd["cohorts"]["S2"] for wd in panel["week_data"]]
    check("S2 walker enters at the top decile (weeks 1-4)",
          all(walker in coh[k] for k in range(0, 4)))
    check("S2 walker re-enters through the BAND at maturity (weeks 5-8), no trade",
          all(walker in coh[k] for k in range(4, 8)))
    check("S2 walker exits below the band (week 9 on)",
          all(walker not in coh[k] for k in range(8, 25)))
    tv = np.asarray(panel["turnover"]["S2"], dtype=float)
    tv_zero = [i for i in range(1, 25) if i != 4]
    check("S2 turnover: week 1 all-new, week 5 the un-parked name only, else zero",
          close(tv[0], 1.0) and close(tv[4], 1.0 / 91)
          and bool(np.nanmax(np.abs(tv[tv_zero])) < 1e-12), f"w5 {tv[4]:.4f}")

    # inference at verdict scale
    verdict = {}
    pvals = {}
    for s in A.SCHEMES:
        net = np.asarray(panel["net"][s]["1m"], dtype=float)
        lo, hi, nobs = A.block_bootstrap_ci(net)
        p = A.null_pvalue(panel, s, "1m", draws=400)
        pvals[s] = 1.0 if p is None else p
        verdict[s] = {"lo": lo, "p": p, "n": nobs}
    check("degenerate drift ties -> the null resamples the cohort itself, p = 1.0 exactly",
          verdict["S1b"]["p"] == 1.0 and verdict["S1c"]["p"] == 1.0,
          f"S1b p = {verdict['S1b']['p']}")
    check("one-burst alpha: 95% CI does not exclude zero, KEEP correctly refused",
          verdict["S1b"]["lo"] is not None and verdict["S1b"]["lo"] <= 0,
          f"lo = {verdict['S1b']['lo']:.5f} on n = {verdict['S1b']['n']}")

    # sweep, exact
    sw0 = A._mean_or(panel["net_sweep"][0.0]["S1b"])
    sw20 = A._mean_or(panel["net_sweep"][20.0]["S1b"])
    exp0 = G / 25                                         # 0 bp: no costs anywhere
    exp20 = (G - (2 * 20 / 10_000) * (1 + 2 / 91)) / 25   # w1 full + w3, w5 at 1/91
    check("flat sweep at 0 bp: exactly the gross planted mean",
          close(sw0, exp0), f"{sw0:.10f} vs {exp0:.10f}")
    check("flat sweep at 20 bp: exactly gross minus the episode round trips",
          close(sw20, exp20), f"{sw20:.10f} vs {exp20:.10f}")

    ne = A.effective_n(panel["net"], "1m")
    check("near-identical books collapse to N_eff ~ 1 (em-rotation-lab convention)",
          ne is not None and abs(ne - 1.0) < 1e-3,
          f"{ne:.4f}" if ne is not None else "None")


# ---------------------------------------------------------------------------
# fixture C -- stochastic, persistent planted alpha, KEEP must fire
# ---------------------------------------------------------------------------

def fixture_c(tmp: Path) -> None:
    print("\nFIXTURE C -- stochastic persistent alpha (KEEP must fire, decoys must fail)")
    rng = np.random.default_rng(20260813)
    U, R_TREAT, DELTA = 500, 60, 0.02
    idx = pd.bdate_range("2024-06-03", periods=520)
    mr = rng.normal(0.0004, 0.009, len(idx))
    mkt_full = pd.Series(100.0 * np.cumprod(1.0 + mr), index=idx)

    n_merges = 30
    a_idx = [300 + 5 * k for k in range(n_merges)]
    a_date = [idx[i].date() for i in a_idx]
    sess_dates = [d.date() for d in idx]
    data_asof = R.horizon_end(a_date[25], "1m")
    cut = max(i for i, s in enumerate(sess_dates) if s <= data_asof)

    tickers = [f"Z{i:03d}" for i in range(U)]
    beta = rng.uniform(0.7, 1.3, U)
    eps = rng.normal(0.0, 0.012, (U, len(idx)))
    treated_by_week = {k: set(rng.choice(tickers, R_TREAT, replace=False))
                      for k in range(1, n_merges)}
    upgrades_by_week = {}
    for k in range(1, n_merges):
        pool = [t for t in tickers if t not in treated_by_week[k]]
        upgrades_by_week[k] = set(rng.choice(pool, 30, replace=False))

    bump = np.zeros((U, len(idx)))
    for k in range(1, n_merges):
        d = a_idx[k] + 2                                  # excess lands 2 sessions in
        for t in treated_by_week[k]:
            bump[tickers.index(t), d] = DELTA

    series = {}
    for i, t in enumerate(tickers):
        rets = beta[i] * mr + eps[i] + bump[i]
        px = pd.Series(100.0 * np.cumprod(1.0 + rets), index=idx)
        series[t] = px.iloc[:cut + 1]
    market = mkt_full.iloc[:cut + 1]

    score_static = rng.integers(1, 11, U)                 # S1c decoy: static classes
    insider = rng.choice(["Positive", "Neutral", "Negative"], U, p=[0.3, 0.3, 0.4])

    level = {t: 100.0 for t in tickers}
    merges = []
    for k in range(n_merges):
        if k > 0:
            for t in treated_by_week.get(k, ()):
                level[t] *= 1.02                          # S1b carrier: raised targets
        recs = []
        for i, t in enumerate(tickers):
            up = k > 0 and t in upgrades_by_week.get(k, ())
            recs.append({
                "ticker": t, "liquid": True, "norgate_symbol": t,
                "anchor_date": a_date[k].isoformat(),
                "analyst_consensus": "Strong Buy" if up else "Hold",
                "last_rating_date": rd_str(a_date[k]) if up else rd_str(dt.date(2024, 1, 3)),
                "best_analyst_price_target": level[t],
                "smart_score": int(score_static[i]),
                "insider_signal": str(insider[i]),
                "tr_3m_pct": float(rng.normal(2, 8)),
                "market_cap": float(np.exp(rng.normal(23, 1.0))),
                "dividend_yield": float(rng.uniform(0, 3)),
                "sector": ["Tech", "Health", "Fin", "Ind"][i % 4],
                "adv_usd": 500e6,
            })
        merges.append({"as_of": a_date[k].isoformat(), "records": recs})

    mdir = write_merges(tmp / "C", merges)
    loaded = A.load_merges(mdir)
    get = lambda sym: series.get(sym)                     # noqa: E731
    panel = A.run_panel(loaded, get, market, sess_dates, data_asof)

    out, pvals = {}, {}
    for s in A.SCHEMES:
        net = np.asarray(panel["net"][s]["1m"], dtype=float)
        xs = net[~np.isnan(net)]
        lo, hi, nobs = A.block_bootstrap_ci(net)
        p = A.null_pvalue(panel, s, "1m", draws=400)
        pvals[s] = 1.0 if p is None else p
        signs = {h: A._mean_or(panel["net"][s][h], float("nan")) for h in R.HORIZONS}
        pt = float(xs.mean()) if xs.size else float("nan")
        stable = (not np.isnan(pt) and pt != 0
                  and all(np.sign(v) == np.sign(pt) for v in signs.values()
                          if not np.isnan(v)))
        out[s] = {"pt": pt, "lo": lo, "p": p, "stable": stable, "n": nobs}
    fdr = A.bh_fdr(pvals)
    keep = {s: (out[s]["lo"] is not None and out[s]["lo"] > 0
                and fdr[s] and out[s]["stable"]) for s in A.SCHEMES}

    o = out["S1b"]
    expected = 0.9 * DELTA                                 # planted, less the 10% centring
    check("planted persistent alpha recovered on the carrier (within noise)",
          abs(o["pt"] - (expected - 0.0006)) < 0.006,
          f"{o['pt']:.4f} vs {expected - 0.0006:.4f} planted")
    check("carrier passes the drift-matched null decisively", o["p"] is not None
          and o["p"] <= 0.02, f"p = {o['p']}")
    check("carrier KEEP = True at verdict scale -- the full bar, end to end",
          keep["S1b"], f"lo = {o['lo']:.4f}, stable = {o['stable']}")
    check("decoy S1a (upgrades drawn from untreated names) does not KEEP",
          not keep["S1a"], f"alpha {out['S1a']['pt']:.4f}")
    check("decoy S1c (static random classes) does not KEEP",
          not keep["S1c"], f"alpha {out['S1c']['pt']:.4f}")
    ne = A.effective_n(panel["net"], "1m")
    check("N_eff computed on the realised correlation structure, 1 < N_eff <= 5",
          ne is not None and 1.0 < ne <= 5.0 + 1e-9, f"{ne:.2f}" if ne else "n/a")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    print("[fixture] end-to-end synthetic fixture panels -- full frozen path")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fixture_a(tmp)
        fixture_c(tmp)
    print(f"\n[fixture] {'ALL CHECKS PASSED' if not FAILS else str(len(FAILS)) + ' FAILED: ' + '; '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
