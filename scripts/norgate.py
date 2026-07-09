#!/usr/bin/env python
"""norgate.py -- point-in-time, survivorship-free liquid US universe + liquidity
for the tipranks-signal study.

Thin data layer over the local Norgate Data Updater (NDU), reusing the access
pattern proven in `event-studies/scripts/norgate_universe.py` and the vault
`norgate-breadth-series` discipline. It provides exactly what the signal merge
needs and nothing else:

  1. connect() / hard_check -- refuse to serve on a down or stale feed, and
     enforce that the delisted database and point-in-time membership are actually
     present (a survivors-only fallback is worse than no join).
  2. member_asof -- was a symbol a POINT-IN-TIME member of a target index on the
     last session on/before an as-of date (survivorship-bias-free; delisted names
     answer honestly).
  3. liquidity_asof -- the as-of close and trailing median dollar volume, for the
     price + ADV floors that keep the study on tradeable names.

Adjustment choice (stated once): forward RETURNS are measured on TOTALRETURN
close (what an investor earns). Dollar VOLUME for the liquidity floor uses the
CAPITAL-adjusted close x volume (split-consistent, no dividend inflation) -- a
coarse tradeability gate, not a return measure.

Dates: sessions come from exchange_calendars ('XNYS'); no hand-computed
weekdays. Python datetime months are 1-indexed. NDU is LOCAL-ONLY -- never pulled
in CI. data/ is gitignored (vendor values stay local).

    python scripts/norgate.py --check            # feed gate only
    python scripts/norgate.py --probe AAPL       # membership + liquidity as-of today
"""
from __future__ import annotations

import argparse
import datetime as dt   # Python datetime: months are 1-indexed (Jan == 1)
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache" / "norgate"       # data/ is gitignored
PRICE_DIR = CACHE / "prices"

REQUIRED_DBS = ("US Equities", "US Equities Delisted", "US Indices")

# Liquid analyst universe: large + mid. Analyst signal is structurally strongest
# where coverage is deep and names are tradeable. Widen to add "S&P SmallCap 600"
# only with a pre-registration amendment (see RESEARCH_MEMO.md).
TARGET_INDICES = ("S&P 500", "S&P MidCap 400")

BENCHMARKS = ("AAPL", "SPY", "MSFT")
PRICE_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def nd():
    """Lazy import so the module stays importable without the package present."""
    import norgatedata
    return norgatedata


def connect():
    """Assert NDU is up and the three required databases are present."""
    n = nd()
    try:
        ok = bool(n.status())
    except Exception as exc:  # noqa: BLE001 -- NDU throws bare errors when down
        raise RuntimeError(f"Norgate NDU status() raised: {exc}") from exc
    if not ok:
        raise RuntimeError(
            "Norgate Data Updater (NDU) is not running / not reachable. Start NDU, "
            "let it finish syncing, then re-run. The Python package only proxies "
            "the local NDU database."
        )
    dbs = set(n.databases())
    missing = [d for d in REQUIRED_DBS if d not in dbs]
    if missing:
        raise RuntimeError(
            f"Required Norgate databases missing: {missing}. Present: {sorted(dbs)}. "
            "Platinum-level US Stocks is required for delisted securities and "
            "point-in-time membership."
        )
    return n


def last_completed_session(now=None) -> dt.date:
    """Most recent NYSE session whose CLOSE is already in the past (tz-aware).

    Run intraday before the US close, this correctly returns the PRIOR session,
    so the freshness gate does not false-fail from a non-US timezone (e.g. SGT
    afternoon, when today's US session has not opened yet). NDU only holds bars
    for sessions that have actually closed."""
    import exchange_calendars as xcals
    cal = xcals.get_calendar("XNYS")
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    start = (now - pd.Timedelta(days=15)).date().isoformat()   # survives long closures
    sessions = cal.sessions_in_range(start, now.date().isoformat())
    if len(sessions) == 0:
        raise RuntimeError("no NYSE sessions found in the lookback window")
    last = sessions[-1]
    if cal.session_close(last) > now:      # today's session has not closed yet
        if len(sessions) < 2:
            raise RuntimeError("no completed NYSE session in the lookback window")
        last = sessions[-2]
    return last.date() if hasattr(last, "date") else dt.date.fromisoformat(str(last)[:10])


def _price(n, sym: str, adjustment: str) -> pd.DataFrame:
    """TOTALRETURN/CAPITAL OHLCV for one symbol; empty frame on unknown/no-bar."""
    adj = getattr(n.StockPriceAdjustmentType, adjustment)
    try:
        df = n.price_timeseries(
            sym,
            stock_price_adjustment_setting=adj,
            padding_setting=n.PaddingType.NONE,
            timeseriesformat="pandas-dataframe",
        )
    except Exception:  # noqa: BLE001 -- unknown symbol / no bars
        return pd.DataFrame(columns=PRICE_COLUMNS)
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    out = df.copy()
    if getattr(out.index, "tz", None) is not None:
        out.index = out.index.tz_localize(None)
    for col in PRICE_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    out = out[PRICE_COLUMNS].sort_index()
    out.index.name = "Date"
    return out


def _last_bar_date(n, sym: str):
    """Authoritative freshness signal: the actual last bar date (not
    last_quoted_date, which is unpopulated for live symbols on this feed)."""
    df = _price(n, sym, "TOTALRETURN")
    if len(df) == 0:
        return None
    d = df.index[-1]
    return d.date() if hasattr(d, "date") else dt.date.fromisoformat(str(d)[:10])


def hard_check(n=None) -> dict:
    """Feed-fresh + delisted-present + membership-works gate. Raises on failure.
    Freshness is measured against the last COMPLETED US session as of now (not a
    capture date), so a backfill run still requires an up-to-date feed."""
    n = n or connect()
    exp = last_completed_session()

    # --- freshness (actual last bar, per the NDU last_quoted_date quirk) ---
    bench = {}
    for s in BENCHMARKS:
        last = _last_bar_date(n, s)
        if last is None:
            raise RuntimeError(
                f"feed not ready: {s} has no price bars yet -- the US Equities price "
                "database is still downloading. Let NDU finish, then re-run."
            )
        bench[s] = last
    if min(bench.values()) < exp:
        raise RuntimeError(
            f"feed stale: oldest benchmark last bar {min(bench.values())} < last NYSE "
            f"session {exp}. Let NDU finish syncing, then re-run."
        )

    # --- HARD STOP 1: delisted database must be non-empty ---
    delisted = list(n.database_symbols("US Equities Delisted"))
    if len(delisted) == 0:
        raise RuntimeError(
            "HARD STOP: 'US Equities Delisted' returned zero symbols. A survivors-only "
            "join is worse than none. STOP."
        )

    # --- HARD STOP 2: point-in-time membership must be available ---
    try:
        probe = n.index_constituent_timeseries(
            "AAPL", "S&P 500", timeseriesformat="pandas-dataframe")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"HARD STOP: index membership query raised {exc}") from exc
    if probe is None or len(probe) == 0 or int(probe[probe.columns[0]].sum()) == 0:
        raise RuntimeError("HARD STOP: point-in-time membership unavailable (AAPL vs S&P 500).")

    return {
        "checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "last_completed_session": exp.isoformat(),
        "benchmark_last_dates": {k: v.isoformat() for k, v in bench.items()},
        "delisted_symbol_count": len(delisted),
    }


def _member_series(n, sym: str, index_name: str) -> pd.Series:
    try:
        df = n.index_constituent_timeseries(sym, index_name, timeseriesformat="pandas-dataframe")
    except Exception:  # noqa: BLE001
        return pd.Series(dtype="int8")
    if df is None or len(df) == 0:
        return pd.Series(dtype="int8")
    s = df[df.columns[0]].astype("int8")
    if getattr(s.index, "tz", None) is not None:
        s.index = s.index.tz_localize(None)
    return s.sort_index()


def member_asof(n, sym: str, asof: dt.date, indices=TARGET_INDICES) -> list[str]:
    """Target indices of which `sym` was a POINT-IN-TIME member on the last
    session on/before `asof`. Empty list = not in the liquid universe then."""
    ts = pd.Timestamp(asof)
    hits = []
    for idx in indices:
        s = _member_series(n, sym, idx)
        if len(s) == 0:
            continue
        upto = s.loc[:ts]
        if len(upto) and int(upto.iloc[-1]) == 1:
            hits.append(idx)
    return hits


def liquidity_asof(n, sym: str, asof: dt.date, window: int = 21) -> dict:
    """As-of TOTALRETURN close (return anchor) and trailing median dollar volume
    (CAPITAL close x volume -- split-consistent tradeability gate)."""
    ts = pd.Timestamp(asof)
    tr = _price(n, sym, "TOTALRETURN").loc[:ts]
    cap = _price(n, sym, "CAPITAL").loc[:ts]
    if len(tr) == 0:
        return {"matched": False, "close_tr": None, "adv_usd": None, "anchor_date": None}
    anchor_date = tr.index[-1].date()
    close_tr = float(tr["Close"].iloc[-1])
    adv_usd = None
    if len(cap):
        dv = (cap["Close"].astype(float) * cap["Volume"].astype(float)).dropna()
        if len(dv):
            adv_usd = float(dv.tail(window).median())
    return {"matched": True, "close_tr": close_tr, "adv_usd": adv_usd,
            "anchor_date": anchor_date.isoformat()}


def resolve_symbol(n, ticker: str) -> str | None:
    """Best-effort TipRanks ticker -> live Norgate symbol (must have price bars).
    Tries the raw ticker and common class-share spellings; returns None on a miss
    so the merge can FLAG it rather than silently drop it."""
    cands = [ticker, ticker.replace(".", "-"), ticker.replace("-", "."),
             ticker.replace(".", " "), ticker.replace("/", ".")]
    seen = []
    for c in cands:
        c = c.strip().upper()
        if c in seen:
            continue
        seen.append(c)
        if _last_bar_date(n, c) is not None:
            return c
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="run the feed gate only")
    ap.add_argument("--probe", metavar="TICKER", help="membership + liquidity as-of today")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    n = connect()
    proof = hard_check(n)
    print("[gate] PASS")
    print(json.dumps(proof, indent=1))

    if args.probe:
        sym = resolve_symbol(n, args.probe) or args.probe.upper()
        today = dt.date.today()
        print(f"[probe] {args.probe} -> Norgate {sym!r}")
        print(json.dumps({"member_of": member_asof(n, sym, today),
                          "liquidity": liquidity_asof(n, sym, today)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
