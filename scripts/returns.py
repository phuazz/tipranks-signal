#!/usr/bin/env python
"""returns.py -- the risk and return layer for the pre-registered analysis.

Everything analyse.py measures rests on three quantities the weekly merge does
NOT store, because they are only knowable after the capture date:

    forward TOTALRETURN   over {1 week, 1 month, 3 months} from the anchor close,
                          delisting-aware
    trailing beta         versus the market, estimated on data ending at or
                          before the anchor
    idiosyncratic drift   the trailing residual mean, per session

The last two are what turn a raw forward return into the DRIFT-ADJUSTED alpha
that RESEARCH_MEMO.md fixes as the headline. Guard 2 of the study -- "beta plus
high-drift-name selection masquerading as alpha" -- lives or dies here.

Design notes, all deliberate:

* PURE FUNCTIONS FIRST. Every calculation takes plain price series and returns
  numbers; only the thin wrappers at the bottom touch Norgate. That is what
  lets --selftest run offline, and it is why this layer can be written and
  verified NOW, while four captures exist and no result can be seen. Code
  written after the panel is readable is code the panel can influence.

* LOOK-AHEAD. Trailing estimation slices with .loc[:anchor] and forward
  measurement slices with .loc[anchor:]. The anchor bar belongs to BOTH: it is
  the last observation used to estimate risk and the entry price for the
  forward window. Nothing else crosses.

* DELISTING. If the price series ends before the horizon does, the name
  realises its return to the final bar and is flagged, per the memo -- it does
  not become a survivorship gap and it is not silently dropped.

* Dates go through datetime / dateutil / exchange_calendars only. Python months
  are 1-indexed. Month- and year-boundary cases are asserted in --selftest, per
  the vault rule; relativedelta clamps 31 Jan + 1 month to end-of-February
  rather than overflowing, which is the behaviour the horizons want.

    python scripts/returns.py --selftest        # offline; no NDU, no vendor data

IMPLEMENTATION CHOICES NOT FIXED BY THE MEMO (recorded here, and flagged for a
register row before analyse.py grades anything):
    - trailing window 252 sessions, minimum 120 (roughly one year, half required)
    - market proxy SPY, TOTALRETURN
    - horizons snapped to the last XNYS session on or before the target date
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# Horizon set is frozen by RESEARCH_MEMO.md: 1 week context, 1 month PRIMARY,
# 3 months context. Keys are used as column names downstream -- do not rename.
HORIZONS = {
    "1w": relativedelta(weeks=1),
    "1m": relativedelta(months=1),
    "3m": relativedelta(months=3),
}
PRIMARY_HORIZON = "1m"

TRAILING_SESSIONS = 252     # ~1 year of daily bars for beta / drift
MIN_TRAILING = 120          # below this the estimate is refused, not fudged
MARKET_SYMBOL = "SPY"
SESSIONS_PER_YEAR = 252
# A live name carries a bar within a few sessions of the feed's last one.
# Only used when the delisted database has not been consulted -- see
# forward_return(delisted_known=...).
STALE_TOLERANCE_SESSIONS = 3


# --- dates ----------------------------------------------------------------

def horizon_end(anchor: dt.date, horizon: str) -> dt.date:
    """Calendar end of a forward window. relativedelta clamps overflow, so
    31 January + 1 month is 28 (or 29) February, never 3 March."""
    if horizon not in HORIZONS:
        raise KeyError(f"unknown horizon {horizon!r}; expected {sorted(HORIZONS)}")
    return anchor + HORIZONS[horizon]


def snap_to_session(d: dt.date, sessions) -> dt.date | None:
    """Last trading session on or before d. Sessions come from XNYS, never
    from a weekday guess -- holidays are not weekends."""
    prior = [s for s in sessions if s <= d]
    return prior[-1] if prior else None


def snap_back(d: dt.date, business_days: int) -> dt.date:
    """d shifted back by N business days. Used only for the delisting staleness
    tolerance, where erring long is safe -- holidays make this slightly more
    lenient than a true session count, which is the harmless direction."""
    return (pd.Timestamp(d) - pd.tseries.offsets.BDay(business_days)).date()


# --- pure calculations ----------------------------------------------------

def _closes(series: pd.Series) -> pd.Series:
    s = pd.Series(series).dropna().astype(float)
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def forward_return(closes: pd.Series, anchor: dt.date, horizon: str,
                   data_asof: dt.date, delisted_known: bool | None = None,
                   stale_tolerance_sessions: int = STALE_TOLERANCE_SESSIONS) -> dict:
    """Forward TOTALRETURN from the anchor close, delisting-aware.

    `data_asof` -- the last session the FEED carries, not this symbol's last
    bar -- is required, and it is the whole point of the signature. A series
    that stops before its horizon ends has two completely different meanings:

        the name DELISTED       -> realise the return to the final bar; it is
                                   final and will never change (memo: a
                                   delisting is not a survivorship gap)
        the horizon is UNMATURED -> return None; the window is still running

    Both look identical from the price series alone. Conflating them is the
    single most damaging error available here: this study accrues forward, so
    at any run most names have unmatured horizons, and reading those as
    delistings would fill the panel with truncated partial returns that are
    systematically short. `data_asof` is what separates them.

    Pass `delisted_known` when the Norgate delisted database has been consulted
    -- it overrides the staleness heuristic with the authoritative answer.

    ret_frac is a fraction, not a percentage."""
    s = _closes(closes)
    a_ts = pd.Timestamp(anchor)
    at_or_before = s.loc[:a_ts]
    if at_or_before.empty:
        return {"ret_frac": None, "entry_date": None, "exit_date": None,
                "delisted": False, "reason": "no bar at or before anchor"}
    entry_ts = at_or_before.index[-1]
    entry = float(at_or_before.iloc[-1])
    if entry <= 0:
        return {"ret_frac": None, "entry_date": entry_ts.date(), "exit_date": None,
                "delisted": False, "reason": "non-positive entry price"}

    end_date = horizon_end(entry_ts.date(), horizon)
    sym_last = s.index[-1].date()

    if delisted_known is None:
        # Heuristic: a live name has a bar within a few sessions of the feed's
        # last one. Deliberately conservative -- a name wrongly called live is
        # merely excluded as unmatured, whereas one wrongly called delisted
        # injects a short partial return into the panel.
        stale_cut = snap_back(data_asof, stale_tolerance_sessions)
        delisted = sym_last < stale_cut and sym_last < end_date
    else:
        delisted = bool(delisted_known) and sym_last < end_date

    if not delisted:
        if data_asof < end_date:
            return {"ret_frac": None, "entry_date": entry_ts.date(), "exit_date": None,
                    "delisted": False, "reason": "horizon not matured"}
        # Matured horizon, name not delisted, yet the series stops well short of
        # the window end. That is contradictory -- a live name does not go dark
        # for weeks. Refuse: returning the truncated window would hand back a
        # systematically short return dressed as a full one.
        if sym_last < snap_back(end_date, stale_tolerance_sessions):
            return {"ret_frac": None, "entry_date": entry_ts.date(), "exit_date": None,
                    "delisted": False,
                    "reason": f"series ends {sym_last} before a matured horizon "
                              f"ending {end_date}, and not flagged delisted"}

    window = s.loc[entry_ts:pd.Timestamp(end_date)]
    if len(window) < 2:
        return {"ret_frac": None, "entry_date": entry_ts.date(), "exit_date": None,
                "delisted": delisted, "reason": "no bars after anchor"}

    exit_ts = window.index[-1]
    return {"ret_frac": float(window.iloc[-1]) / entry - 1.0,
            "entry_date": entry_ts.date(), "exit_date": exit_ts.date(),
            "delisted": delisted, "reason": "delisted in window" if delisted else "ok"}


def trailing_risk(stock_closes: pd.Series, market_closes: pd.Series, anchor: dt.date,
                  sessions: int = TRAILING_SESSIONS, minimum: int = MIN_TRAILING) -> dict:
    """Beta and per-session idiosyncratic drift from data ENDING at the anchor.

    beta  = cov(stock, market) / var(market) on daily fractional returns
    drift = mean(stock) - beta * mean(market)          (per session, residual)

    Returns None for both when fewer than `minimum` overlapping sessions exist.
    A short history is a refusal, not a shrunk estimate -- a fudged beta would
    silently weaken the very adjustment guard 2 depends on."""
    s = _closes(stock_closes).loc[:pd.Timestamp(anchor)]
    m = _closes(market_closes).loc[:pd.Timestamp(anchor)]
    sr = s.pct_change().dropna().tail(sessions)
    mr = m.pct_change().dropna().tail(sessions)
    joined = pd.concat([sr, mr], axis=1, join="inner").dropna()
    joined.columns = ["stock", "market"]
    n = len(joined)
    if n < minimum:
        return {"beta": None, "drift_per_session": None, "n_sessions": n,
                "reason": f"only {n} overlapping sessions, need {minimum}"}
    mvar = float(joined["market"].var(ddof=1))
    if mvar <= 0:
        return {"beta": None, "drift_per_session": None, "n_sessions": n,
                "reason": "market variance is zero"}
    beta = float(joined["stock"].cov(joined["market"]) / mvar)
    drift = float(joined["stock"].mean() - beta * joined["market"].mean())
    return {"beta": beta, "drift_per_session": drift, "n_sessions": n, "reason": "ok"}


def drift_adjust(ret_frac: float, market_ret_frac: float, beta: float,
                 drift_per_session: float, n_sessions: int) -> float:
    """The headline transform: strip beta x market and the name's own trailing
    drift, so what remains is not just high-beta, high-drift name selection.

    n_sessions is the realised length of the forward window in trading days --
    the drift is per session, so a delisted name that only ran nine days has
    only nine days of drift removed, not a full month's."""
    return ret_frac - beta * market_ret_frac - drift_per_session * n_sessions


def sessions_between(entry: dt.date, exit_: dt.date, sessions) -> int:
    """Trading sessions strictly after entry, up to and including exit."""
    return len([s for s in sessions if entry < s <= exit_])


# --- Norgate wrappers (thin; everything above is testable without them) ----

def _norgate():
    import norgate  # noqa: PLC0415 -- deliberately lazy so --selftest runs offline
    return norgate


def market_series(n=None) -> pd.Series:
    ng = _norgate()
    n = n or ng.connect()
    df = ng._price(n, MARKET_SYMBOL, "TOTALRETURN")
    if len(df) == 0:
        raise RuntimeError(f"no bars for market proxy {MARKET_SYMBOL}")
    return df["Close"].astype(float)


def stock_series(sym: str, n=None) -> pd.Series | None:
    ng = _norgate()
    n = n or ng.connect()
    df = ng._price(n, sym, "TOTALRETURN")
    if len(df) == 0:
        return None
    return df["Close"].astype(float)


# --- self-test ------------------------------------------------------------

def _synthetic(start: str, days: int, daily: float, seed: int | None = None,
               beta: float = None, market: pd.Series = None) -> pd.Series:
    """A deterministic price path. With beta/market given, builds a stock whose
    returns are beta x market + a constant drift, so the estimator has a known
    answer to recover."""
    idx = pd.bdate_range(start, periods=days)
    if market is None:
        rets = np.full(days, daily)
    else:
        mr = market.pct_change().fillna(0.0).to_numpy()[:days]
        rets = beta * mr + daily
    return pd.Series(100.0 * np.cumprod(1.0 + rets), index=idx)


def selftest() -> int:
    fails = []

    def check(label, cond, detail=""):
        print(f"  {'PASS' if cond else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
        if not cond:
            fails.append(label)

    print("[returns] date arithmetic (Python months are 1-indexed)")
    # Month boundary: 31 January clamps into February, never overflows to March.
    check("31 Jan 2026 + 1m -> 28 Feb 2026",
          horizon_end(dt.date(2026, 1, 31), "1m") == dt.date(2026, 2, 28),
          str(horizon_end(dt.date(2026, 1, 31), "1m")))
    # Leap year: the same clamp lands on the 29th.
    check("31 Jan 2028 + 1m -> 29 Feb 2028 (leap)",
          horizon_end(dt.date(2028, 1, 31), "1m") == dt.date(2028, 2, 29),
          str(horizon_end(dt.date(2028, 1, 31), "1m")))
    # Year boundary: month and year both roll.
    check("31 Dec 2025 + 1m -> 31 Jan 2026",
          horizon_end(dt.date(2025, 12, 31), "1m") == dt.date(2026, 1, 31),
          str(horizon_end(dt.date(2025, 12, 31), "1m")))
    check("30 Nov 2025 + 3m -> 28 Feb 2026",
          horizon_end(dt.date(2025, 11, 30), "3m") == dt.date(2026, 2, 28),
          str(horizon_end(dt.date(2025, 11, 30), "3m")))
    check("31 Dec 2025 + 1w -> 7 Jan 2026",
          horizon_end(dt.date(2025, 12, 31), "1w") == dt.date(2026, 1, 7),
          str(horizon_end(dt.date(2025, 12, 31), "1w")))

    print("[returns] forward return")
    flat = _synthetic("2026-01-02", 200, 0.0)
    up = _synthetic("2026-01-02", 200, 0.001)
    feed_end = up.index[-1].date()          # the feed's last session: 2026-10-09
    r = forward_return(flat, dt.date(2026, 2, 2), "1m", data_asof=feed_end)
    check("flat series -> zero return", r["ret_frac"] is not None and abs(r["ret_frac"]) < 1e-12)
    r = forward_return(up, dt.date(2026, 2, 2), "1m", data_asof=feed_end)
    # 2 Feb -> 2 Mar, about 20 business days at 10 bps a day.
    check("0.1%/day compounds positive over 1m",
          r["ret_frac"] is not None and 0.015 < r["ret_frac"] < 0.025,
          f"{r['ret_frac']:.4f}" if r["ret_frac"] is not None else "None")
    check("anchor is the entry, not a later bar",
          r["entry_date"] == dt.date(2026, 2, 2), str(r["entry_date"]))

    print("[returns] delisting versus unmatured -- the distinction data_asof exists for")
    short = _synthetic("2026-01-02", 30, 0.001)          # stops 2026-02-12
    # Feed has run months past this name's last bar: a genuine delisting.
    r = forward_return(short, dt.date(2026, 2, 2), "3m", data_asof=feed_end)
    check("name stopping while the feed runs on -> delisted", r["delisted"] is True, r["reason"])
    check("delisted name still realises a return", r["ret_frac"] is not None,
          f"{r['ret_frac']:.4f}" if r["ret_frac"] is not None else "None")
    # SAME series, but the feed itself only reaches 2026-02-12: nothing has
    # delisted, the window is simply still running. This is the case that was
    # silently wrong before data_asof was required.
    r = forward_return(short, dt.date(2026, 2, 2), "3m", data_asof=dt.date(2026, 2, 12))
    check("same series, feed only just there -> unmatured, NOT delisted",
          r["ret_frac"] is None and r["reason"] == "horizon not matured", r["reason"])
    # A live name whose horizon has not run out yet.
    r = forward_return(up, dt.date(2026, 9, 15), "3m", data_asof=feed_end)
    check("live name, horizon still running -> None",
          r["ret_frac"] is None and r["reason"] == "horizon not matured", r["reason"])
    # Delisted AND unmatured: the return is final, so it is realised.
    r = forward_return(short, dt.date(2026, 2, 2), "3m", data_asof=dt.date(2026, 3, 2),
                       delisted_known=True)
    check("delisted before an unmatured horizon still realises (return is final)",
          r["ret_frac"] is not None and r["delisted"] is True, r["reason"])
    # Contradictory state: asserted live, yet the series goes dark for months
    # before a matured horizon. Must refuse rather than hand back the truncated
    # window as if it were a full one.
    r = forward_return(short, dt.date(2026, 2, 2), "3m", data_asof=feed_end,
                       delisted_known=False)
    check("live name gone dark before a matured horizon is refused",
          r["ret_frac"] is None and "not flagged delisted" in r["reason"], r["reason"])
    # A live name missing only the last day or two (holiday, no trade) is fine.
    nearly = _synthetic("2026-01-02", 45, 0.001)         # stops 2026-03-04
    r = forward_return(nearly, dt.date(2026, 2, 2), "1m", data_asof=dt.date(2026, 3, 6),
                       delisted_known=False)
    check("a bar or two short of the window end is tolerated",
          r["ret_frac"] is not None, r["reason"])
    r = forward_return(up, dt.date(2020, 1, 1), "1m", data_asof=feed_end)
    check("anchor before the series starts is refused", r["ret_frac"] is None)

    print("[returns] beta and drift recovery")
    rng = np.random.default_rng(20260806)
    mkt_rets = rng.normal(0.0004, 0.01, 400)
    mkt = pd.Series(100.0 * np.cumprod(1.0 + mkt_rets), index=pd.bdate_range("2025-01-01", periods=400))
    true_beta, true_drift = 1.35, 0.0006
    stk = _synthetic("2025-01-01", 400, true_drift, beta=true_beta, market=mkt)
    tr = trailing_risk(stk, mkt, dt.date(2026, 6, 30))
    check("beta recovered", tr["beta"] is not None and abs(tr["beta"] - true_beta) < 1e-6,
          f"{tr['beta']:.6f}" if tr["beta"] else "None")
    check("drift recovered", tr["drift_per_session"] is not None
          and abs(tr["drift_per_session"] - true_drift) < 1e-9,
          f"{tr['drift_per_session']:.8f}" if tr["drift_per_session"] else "None")
    short_hist = _synthetic("2026-06-01", 40, 0.0)
    tr_short = trailing_risk(short_hist, mkt, dt.date(2026, 7, 20))
    check("short history refuses rather than fudges", tr_short["beta"] is None, tr_short["reason"])

    print("[returns] drift adjustment")
    # A pure beta ride with no idiosyncratic drift must adjust to zero alpha.
    adj = drift_adjust(ret_frac=1.35 * 0.02, market_ret_frac=0.02,
                       beta=1.35, drift_per_session=0.0, n_sessions=21)
    check("pure beta ride -> zero adjusted alpha", abs(adj) < 1e-12, f"{adj:.2e}")
    # A name that merely repeated its own trailing drift earns nothing.
    adj = drift_adjust(ret_frac=0.0006 * 21, market_ret_frac=0.0,
                       beta=1.0, drift_per_session=0.0006, n_sessions=21)
    check("own trailing drift repeated -> zero adjusted alpha", abs(adj) < 1e-12, f"{adj:.2e}")
    adj = drift_adjust(0.05, 0.02, 1.35, 0.0006, 21)
    check("genuine outperformance survives adjustment", adj > 0, f"{adj:.4f}")
    # Drift is per session, so a shortened window removes proportionally less.
    a_full = drift_adjust(0.05, 0.02, 1.0, 0.001, 21)
    a_part = drift_adjust(0.05, 0.02, 1.0, 0.001, 9)
    check("delisted window removes less drift than a full one", a_part > a_full,
          f"{a_part:.4f} > {a_full:.4f}")

    print(f"\n[returns] {'ALL CHECKS PASSED' if not fails else str(len(fails)) + ' FAILED: ' + ', '.join(fails)}")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true",
                    help="run the offline correctness checks (no NDU, no vendor data)")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
