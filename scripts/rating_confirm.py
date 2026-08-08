#!/usr/bin/env python
"""rating_confirm.py -- does a consensus rating change carry a dated rating behind it?

An UNGRADED diagnostic on the daily event log, built to settle one question raised
by the 2026-08-08 off-panel build: over a 7-day window, 52 of 65 consensus rating
changes (80%) were confirmed by a Last Rating Date inside the window; over the
1-day 08-07 -> 08-08 window, 0 of 10 were. Two readings make opposite predictions
and this sweep separates them.

  (a) COMPOSITION PHANTOMS. The consensus label moves because the analyst panel
      changed -- somebody dropped or initiated coverage -- and no analyst acted.
      build_dashboard.py already names this as the thing date-confirmation guards
      against. Phantoms arrive at a roughly constant rate, while genuine actions
      accumulate with elapsed time, so the confirmed SHARE should climb with
      window length and be near zero at daily resolution. This reading argues for
      the weekly window on S1a.

  (b) ANCHORING ARTEFACT. Last Rating Date is a DATE and the capture stamp is an
      INSTANT, so confirmation is tested on the window's ET calendar-date span --
      an inclusive test whose width is set by where the window falls relative to
      ET midnight, not by how long it is. RESEARCH_MEMO.md already records this
      biting once: re-anchoring from Singapore-day to ET calendar dates moved the
      dated share from 54% to 59-70%. If the effect is anchoring, the confirmed
      share should track the SPAN width and not elapsed hours.

The two are separable because span width and elapsed hours are not collinear
across all pairs: a short window crossing ET midnight has a 2-day span, and a
long window can share one. So the table reports both, and the summary groups
pairs by span width so elapsed time can be read WITHIN a fixed span.

Reads data/daily/ ONLY. That stream carries no Norgate merge, so this touches no
forward return and can leak no performance information into a pre-registered
read. Output is aggregates only; no per-name vendor values are printed.

    python scripts/rating_confirm.py
    python scripts/rating_confirm.py --min-changes 5   # hide thin pairs from the summary

This is a diagnostic and can never become a KEEP claim -- see the no-promotion
rule in RESEARCH_MEMO.md. Python months are 1-indexed (parse_rating_date, reused
from target_flow.py rather than reimplemented).
"""
from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from capture import _snap_key                                      # noqa: E402
from target_flow import capture_instant_et, parse_rating_date      # noqa: E402
from pipeline import CONSENSUS_ORDER                               # noqa: E402

DAILY_SNAP_DIR = ROOT / "data" / "daily"

# Strong Buy .. Strong Sell, index 0..4. A FALL in index is an upgrade.
RANK = {c: i for i, c in enumerate(CONSENSUS_ORDER)}


def load(path: Path) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    return {r["ticker"]: r for r in d["records"] if r.get("ticker")}


def compare(ra: dict, rb: dict, a_et: dt.datetime, b_et: dt.datetime):
    """One window. Returns (paired, upgrades, downgrades, confirmed_up, confirmed_down)."""
    paired = up = down = cu = cd = 0
    for ticker, rec_b in rb.items():
        rec_a = ra.get(ticker)
        if rec_a is None:
            continue
        ca, cb = rec_a.get("analyst_consensus"), rec_b.get("analyst_consensus")
        if ca not in RANK or cb not in RANK:
            continue
        paired += 1
        if RANK[cb] == RANK[ca]:
            continue
        # Same confirmation test as the view layer: a dated rating action inside
        # the window's ET calendar-date span would explain the label move.
        rd = parse_rating_date(rec_b.get("last_rating_date"))
        confirmed = bool(rd and a_et.date() <= rd <= b_et.date())
        if RANK[cb] < RANK[ca]:
            up += 1
            cu += confirmed
        else:
            down += 1
            cd += confirmed
    return paired, up, down, cu, cd


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-changes", type=int, default=3,
                    help="minimum label changes for a pair to enter the summary (default 3)")
    args = ap.parse_args()

    if not DAILY_SNAP_DIR.exists():
        print(f"[confirm] no daily event log at {DAILY_SNAP_DIR}")
        return 1
    paths = sorted((p for p in DAILY_SNAP_DIR.glob("snapshot_*.json") if _snap_key(p)),
                   key=lambda p: _snap_key(p))
    if len(paths) < 2:
        print(f"[confirm] need at least two daily snapshots, found {len(paths)}")
        return 1

    snaps = {p: load(p) for p in paths}
    ets = {p: capture_instant_et(_snap_key(p)) for p in paths}

    pairs = list(itertools.combinations(paths, 2))
    print(f"[confirm] consensus rating changes across the daily event log")
    print(f"[confirm] {len(paths)} snapshots -> {len(pairs)} ordered pairs, "
          f"all times US Eastern\n")

    hdr = (f"{'window (ET)':<30} {'hrs':>6} {'span':>5} {'paired':>7} "
           f"{'chg':>4} {'up':>4} {'dn':>4} {'conf':>5} {'rate':>6}")
    print(hdr)
    print("-" * len(hdr))

    rows = []
    for a, b in pairs:
        a_et, b_et = ets[a], ets[b]
        paired, up, down, cu, cd = compare(snaps[a], snaps[b], a_et, b_et)
        chg, conf = up + down, cu + cd
        hrs = (b_et - a_et).total_seconds() / 3600.0
        span = (b_et.date() - a_et.date()).days + 1          # inclusive, in ET days
        rate = conf / chg if chg else None
        label = f"{a_et.strftime('%m-%d %H:%M')} -> {b_et.strftime('%m-%d %H:%M')}"
        print(f"{label:<30} {hrs:>6.1f} {span:>5} {paired:>7,} {chg:>4} "
              f"{up:>4} {down:>4} {conf:>5} "
              f"{(f'{rate:.0%}' if rate is not None else 'n/a'):>6}")
        rows.append({"hrs": hrs, "span": span, "chg": chg, "conf": conf, "rate": rate})

    useful = [r for r in rows if r["chg"] >= args.min_changes]
    print(f"\n[confirm] {len(useful)} of {len(rows)} pairs carry >= {args.min_changes} "
          f"label changes and enter the summary.\n")

    # --- reading 1: confirmed share against elapsed time ---------------------
    print("[confirm] confirmed share by ELAPSED time (reading (a): phantoms):")
    buckets = [(0, 24, "< 1 day"), (24, 48, "1-2 days"), (48, 96, "2-4 days"),
               (96, 1e9, "4+ days")]
    for lo, hi, name in buckets:
        sel = [r for r in useful if lo <= r["hrs"] < hi]
        c, n = sum(r["conf"] for r in sel), sum(r["chg"] for r in sel)
        print(f"  {name:<10} {len(sel):>2} pairs  {n:>4} changes  {c:>4} confirmed  "
              f"{(f'{c / n:.0%}' if n else 'n/a'):>5}")

    # --- reading 2: confirmed share against ET span width --------------------
    print("\n[confirm] confirmed share by ET DATE SPAN (reading (b): anchoring):")
    for sp in sorted({r["span"] for r in useful}):
        sel = [r for r in useful if r["span"] == sp]
        c, n = sum(r["conf"] for r in sel), sum(r["chg"] for r in sel)
        print(f"  span {sp:>2}d   {len(sel):>2} pairs  {n:>4} changes  {c:>4} confirmed  "
              f"{(f'{c / n:.0%}' if n else 'n/a'):>5}")

    # --- the separating test: elapsed time WITHIN a fixed span ---------------
    # If confirmation is an anchoring artefact, holding span fixed should flatten
    # the elapsed-time gradient. If phantoms are the story, it should survive.
    print("\n[confirm] elapsed time WITHIN a fixed ET span (this is what separates them):")
    any_split = False
    for sp in sorted({r["span"] for r in useful}):
        sel = sorted((r for r in useful if r["span"] == sp), key=lambda r: r["hrs"])
        if len(sel) < 2 or sel[-1]["hrs"] - sel[0]["hrs"] < 6:
            continue
        any_split = True
        mid = len(sel) // 2
        for half, name in ((sel[:mid], "shorter"), (sel[mid:], "longer ")):
            c, n = sum(r["conf"] for r in half), sum(r["chg"] for r in half)
            lo, hi = half[0]["hrs"], half[-1]["hrs"]
            print(f"  span {sp}d {name}  {lo:>5.1f}-{hi:>5.1f}h  {n:>4} changes  "
                  f"{c:>4} confirmed  {(f'{c / n:.0%}' if n else 'n/a'):>5}")
    if not any_split:
        print("  no span holds enough elapsed-time variation yet — more captures needed.")

    print("\n[confirm] 'conf' = label changes whose Last Rating Date falls inside the")
    print("[confirm] window's ET date span, the same test the view layer applies.")
    print("[confirm] Ungraded diagnostic: no KEEP claim can rest on it (no-promotion")
    print("[confirm] rule, RESEARCH_MEMO.md). Daily log only — no merge, no forward return.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
