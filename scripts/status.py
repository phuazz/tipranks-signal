#!/usr/bin/env python
"""status.py -- accrual status for the tipranks-signal forward study.

Prints how many weekly snapshots have been captured, the latest universe counts,
and how far the panel is from the analyse.py threshold. Pure stdlib -- does NOT
need NDU running.

    python scripts/status.py
"""
from __future__ import annotations

import datetime as dt   # Python datetime: months are 1-indexed (Jan == 1)
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAP_DIR = ROOT / "data" / "snapshots"
MERGE_DIR = ROOT / "data" / "merged"
MIN_SNAPSHOTS = 8          # keep in step with analyse.py
MATURE_DAYS = 31           # ~1 calendar month -> the primary (1m) forward window has closed


def _load(dir_: Path, prefix: str) -> list[dict]:
    out = []
    if dir_.exists():
        for p in sorted(dir_.glob(f"{prefix}_*.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001 - skip a corrupt file, keep counting
                pass
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    snaps = _load(SNAP_DIR, "snapshot")
    merges = _load(MERGE_DIR, "merged")
    today = dt.date.today()

    print("tipranks-signal - accrual status")
    print("=" * 44)
    if not snaps:
        print("No snapshots yet. Run ingest.py on your first export.")
        return 0

    dates = sorted(dt.date.fromisoformat(s["as_of"]) for s in snaps)
    print(f"snapshots captured           : {len(snaps)}  ({dates[0]} -> {dates[-1]})")
    matured = sum(1 for d in dates if (today - d).days >= MATURE_DAYS)
    print(f"with a closed ~1m window     : {matured}  (>= {MATURE_DAYS} calendar days old)")

    if merges:
        m = merges[-1]
        c = m["counts"]
        print(f"latest merge {m['as_of']}      : {c['snapshot']} names, "
              f"{c['in_universe']} in-universe, {c['liquid']} liquid")
    else:
        print("merges                       : none yet -- run merge_norgate.py (NDU running)")

    filled = min(len(snaps), MIN_SNAPSHOTS)
    bar = "#" * filled + "-" * (MIN_SNAPSHOTS - filled)
    need = max(0, MIN_SNAPSHOTS - len(snaps))
    print("-" * 44)
    print(f"toward first analysis        : [{bar}] {len(snaps)}/{MIN_SNAPSHOTS} captures")
    if need:
        print(f"analyse.py runnable in ~{need} more weekly capture(s), once "
              f"{MIN_SNAPSHOTS} have a matured 1m window.")
    else:
        print(f"threshold met ({len(snaps)} captures, {matured} with a matured 1m window) "
              "-- analyse.py can implement the pre-registered read.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
