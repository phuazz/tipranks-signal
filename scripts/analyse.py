#!/usr/bin/env python
"""analyse.py -- the PRE-REGISTERED forward-return analysis (NOT yet runnable).

Runs only once enough frozen weekly merges have matured forward windows. The
method is fixed here so results cannot be reverse-engineered from the data:

  universe   in-universe AND liquid names only (price + ADV floors already applied
             in merge_norgate.py)
  signals    (a) consensus rating CHANGE since the prior snapshot   [PRIMARY]
             (b) best-analyst price-target revision
             (c) Smart Score level, in deciles
  return     TOTALRETURN from the anchor close over {1w, 1m, 3m} forward windows;
             delisting-aware (a name that delists mid-window realises its final
             return, not a survivorship gap)
  headline   DRIFT-ADJUSTED alpha: subtract each name's trailing market beta x
             market return and its trailing idiosyncratic drift, so the number is
             not just beta + high-drift-name selection (the collapse that took
             event-studies from 14 leads to 1). Raw forward return is reported
             alongside, never as the headline.
  inference  episode-block bootstrap CI + a drift-matched random-entry null;
             hit rate; turnover and tiered costs netted on every rebalance
  decision   see RESEARCH_MEMO.md -- a signal is kept only if drift-adjusted alpha
             is positive net of costs with a CI excluding zero on the PRIMARY
             horizon, on the liquid universe, sign-stable across 1w/3m.

Guarded: refuses to run until >= MIN_SNAPSHOTS merges exist (a matured 1m window
on enough captures), and prints how many are available.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MERGE_DIR = ROOT / "data" / "merged"
MIN_SNAPSHOTS = 8      # ~2 months of weekly captures before a first honest read


def main() -> int:
    merges = sorted(MERGE_DIR.glob("merged_*.json")) if MERGE_DIR.exists() else []
    print(f"[analyse] {len(merges)} merged snapshot(s) available; "
          f"need >= {MIN_SNAPSHOTS} with matured forward windows.")
    print("[analyse] Pre-registered and NOT YET RUNNABLE -- see the docstring and "
          "RESEARCH_MEMO.md. Accrue weekly snapshots first, then this is implemented "
          "against the frozen design.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
