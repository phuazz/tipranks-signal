#!/usr/bin/env python
"""target_flow.py -- when do best-analyst target revisions actually arrive?

An UNGRADED diagnostic on the daily event log, built to settle one open item in
RESEARCH_MEMO.md: the 460 best-target moves observed across 2026-08-01 ->
2026-08-03 were either a periodic vendor recomputation (artefact) or genuine
analyst flow. The two hypotheses make opposite predictions about the days in
between -- a weekly batch leaves ordinary weekday windows near-empty; continuous
flow does not.

Why the times are converted: capture stamps are Singapore local, but the vendor,
the analysts and the exchange all run on US Eastern, and sell-side notes are
published outside session hours. A window that looks like "one Singapore day"
can contain a whole overnight publication cycle or none at all, which is the
axis that separates the hypotheses. Conversion is via zoneinfo -- DST is derived,
never assumed, and no offset is hand-rolled.

Reads data/daily/ ONLY. That stream carries no Norgate merge, so this touches no
forward return and can leak no performance information into a pre-registered
read. Output is aggregates only; no per-name vendor values are printed, so the
result is safe to quote in the memo.

    python scripts/target_flow.py                 # all consecutive windows
    python scripts/target_flow.py --floor 0.5     # sensitivity on the de-minimis floor

This is a diagnostic and can never become a KEEP claim -- see the no-promotion
rule in RESEARCH_MEMO.md. Python months are 1-indexed (parse_rating_date).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from capture import _snap_key  # noqa: E402 -- reuse the one snapshot-naming contract

DAILY_SNAP_DIR = ROOT / "data" / "daily"
SGT = ZoneInfo("Asia/Singapore")
ET = ZoneInfo("America/New_York")

# De-minimis floor, matching the one applied in the view layer on 2026-07-19:
# below this a "move" is a penny wiggle on a large target, not a revision.
DEFAULT_FLOOR_PCT = 0.25

# US regular session, ET. Used only to label a window as containing an overnight
# / pre-market stretch or sitting wholly inside one session.
OPEN_ET = dt.time(9, 30)
CLOSE_ET = dt.time(16, 0)

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}  # 1-indexed, stated
_RATING_RE = re.compile(r"([A-Z][a-z]{2})\s+(\d{1,2}),\s+(\d{4})")


def parse_rating_date(val):
    """'Jul 24, 2026' -> date. None on anything unparseable -- an unreadable
    stamp must not silently count as a confirmation."""
    if not isinstance(val, str):
        return None
    m = _RATING_RE.search(val)
    if not m:
        return None
    mon = _MONTHS.get(m.group(1))
    if not mon:
        return None
    try:
        return dt.date(int(m.group(3)), mon, int(m.group(2)))
    except ValueError:
        return None


def capture_instant_et(key) -> dt.datetime:
    """(date, 'HHMM') Singapore capture stamp -> aware ET datetime."""
    d, hhmm = key
    hh, mm = (int(hhmm[:2]), int(hhmm[2:])) if hhmm else (0, 0)
    return dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=SGT).astimezone(ET)


def window_kind(a_et: dt.datetime, b_et: dt.datetime) -> str:
    """Does the window contain non-session time (when notes are published)?"""
    if a_et.date() != b_et.date():
        return "overnight"
    if a_et.time() >= OPEN_ET:
        return "intra-session" if b_et.time() <= CLOSE_ET else "session+close"
    return "pre-open"


def load(path: Path) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    return {r["ticker"]: r for r in d["records"] if r.get("ticker")}


def compare(ra: dict, rb: dict, a_et: dt.datetime, b_et: dt.datetime, floor: float):
    """One window. Returns (paired, {ticker: pct move}, raises, cuts, dated)."""
    paired = raises = cuts = dated = 0
    movers = {}
    for ticker, rec_b in rb.items():
        rec_a = ra.get(ticker)
        if rec_a is None:
            continue
        pa = rec_a.get("best_analyst_price_target")
        pb = rec_b.get("best_analyst_price_target")
        if not isinstance(pa, (int, float)) or not isinstance(pb, (int, float)) or not pa:
            continue
        paired += 1
        pct = (pb - pa) / pa * 100.0
        if abs(pct) < floor:
            continue
        movers[ticker] = round(pct, 4)
        if pct > 0:
            raises += 1
        else:
            cuts += 1
        # A rating action inside the window's ET calendar-date span would
        # explain the move. Span widths differ between rows, so this share is
        # read alongside the elapsed hours, never on its own.
        rd = parse_rating_date(rec_b.get("last_rating_date"))
        if rd and a_et.date() <= rd <= b_et.date():
            dated += 1
    return paired, movers, raises, cuts, dated


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--floor", type=float, default=DEFAULT_FLOOR_PCT,
                    help="de-minimis move size, %% of the prior target "
                         f"(default {DEFAULT_FLOOR_PCT})")
    args = ap.parse_args()

    if not DAILY_SNAP_DIR.exists():
        print(f"[flow] no daily event log at {DAILY_SNAP_DIR}")
        return 1
    paths = sorted((p for p in DAILY_SNAP_DIR.glob("snapshot_*.json") if _snap_key(p)),
                   key=lambda p: _snap_key(p))
    if len(paths) < 2:
        print(f"[flow] need at least two daily snapshots, found {len(paths)}")
        return 1

    print(f"[flow] best-analyst target moves, floor {args.floor}% of prior level")
    print(f"[flow] {len(paths)} snapshots -> {len(paths) - 1} windows, all times US Eastern\n")

    hdr = (f"{'window (ET)':<34} {'kind':<13} {'hrs':>5} {'paired':>7} "
           f"{'moves':>6} {'raises':>7} {'cuts':>6} {'dated':>7}")
    print(hdr)
    print("-" * len(hdr))

    windows = []
    for a_path, b_path in zip(paths, paths[1:]):
        a_et = capture_instant_et(_snap_key(a_path))
        b_et = capture_instant_et(_snap_key(b_path))
        paired, movers, raises, cuts, dated = compare(
            load(a_path), load(b_path), a_et, b_et, args.floor)
        moves = len(movers)
        kind = window_kind(a_et, b_et)
        hrs = (b_et - a_et).total_seconds() / 3600.0
        label = (f"{a_et.strftime('%m-%d %H:%M')} -> {b_et.strftime('%m-%d %H:%M')} "
                 f"{b_et.strftime('%a')}")
        print(f"{label:<34} {kind:<13} {hrs:>5.1f} {paired:>7,} {moves:>6,} "
              f"{raises:>7,} {cuts:>6,} "
              f"{(f'{dated / moves:.0%}' if moves else 'n/a'):>7}")
        windows.append((label, kind, moves, movers))

    # Two adjacent windows returning the same count is worth one check: the same
    # names at the same magnitudes would mean a duplicated snapshot, not flow.
    print("\n[flow] adjacent-window overlap (guards against a duplicated snapshot):")
    for (la, _, _, ma), (lb, _, _, mb) in zip(windows, windows[1:]):
        if not ma or not mb:
            continue
        shared = set(ma) & set(mb)
        identical = sum(1 for t in shared if ma[t] == mb[t])
        flag = "  <-- DUPLICATE?" if identical and identical == len(shared) else ""
        print(f"  {la[:14]} vs {lb[:14]}: {len(ma)} / {len(mb)} moves, "
              f"{len(shared)} shared names, {identical} identical magnitudes{flag}")

    # The headline contrast: windows containing an overnight publication cycle
    # against windows that sit inside a session.
    night = [m for _, k, m, _ in windows if k in ("overnight", "pre-open")]
    day = [m for _, k, m, _ in windows if k in ("intra-session", "session+close")]
    if night and day:
        print(f"\n[flow] windows containing an overnight stretch: "
              f"{', '.join(f'{m:,}' for m in night)} moves")
        print(f"[flow] windows inside a session:                  "
              f"{', '.join(f'{m:,}' for m in day)} moves")
        print("[flow] A weekly recomputation predicts near-empty weekday windows.")
    print("\n[flow] 'dated' = share of moves whose Last Rating Date falls inside the")
    print("[flow] window's ET date span. Ungraded diagnostic: no KEEP claim can rest")
    print("[flow] on it (no-promotion rule, RESEARCH_MEMO.md).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
