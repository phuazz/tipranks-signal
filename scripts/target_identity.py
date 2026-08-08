#!/usr/bin/env python
"""target_identity.py -- do best-analyst targets REVERT to exact prior levels?

An UNGRADED diagnostic built to press the open S1b question in RESEARCH_MEMO.md:
the exported best-analyst target can move because TipRanks re-picks WHICH analyst
is "best" (an identity switch, nobody acted) rather than because an analyst
revised. The memo records that timing evidence cannot separate the two, because a
nightly re-pick and continuous analyst flow produce the same overnight pattern.

This asks a different question, one timing cannot reach. A genuine revision is a
decision: an analyst moves a target and it stays moved. An identity switch is a
selection flipping between a fixed set of candidates, so the exported value
RETURNS to a level it held before -- not approximately, exactly. ULS is the case
that prompted this: best target 111.71 -> 111.05 -> 111.71 -> 111.05 across four
weekly captures, alternating between two levels to the cent, with Last Rating
Date frozen at Jul 14 throughout and the daily log showing it rock-steady at
111.05 across 08-01 to 08-04. Three alternating revisions of $0.66 by a single
analyst is not a plausible reading; two analysts alternately selected is.

The inference rests on a CONTROL, not on a raw count. Exact reversion could in
principle happen by chance, or because an analyst genuinely moved a target back.
So the same statistic is computed separately for names whose Last Rating Date
CHANGED over the span and names whose rating date was STATIC. Under the identity
hypothesis, reversion should concentrate where no analyst acted. If reversion is
just as common when a rating date moved, the pattern is not diagnostic and the
question stays open on these fields.

Known limitation in the floor's role, stated rather than smoothed: the de-minimis
floor selects WHICH NAMES are examined (a name qualifies as a mover if any single
move clears it) but reversion is then detected across the whole raw series, so a
qualifying name can be marked reverted on a sub-floor pair. That asymmetry is why
the result must be read across floors and not at one setting.

Reads data/snapshots/ and data/daily/ ONLY. Both are TipRanks-only captures taken
BEFORE the Norgate merge, so no return field is touched and no forward return can
leak into a pre-registered read. Default output is aggregates only and therefore
safe to quote in the memo; --names additionally lists example tickers, which are
vendor values and must stay local.

    python scripts/target_identity.py                  # both streams
    python scripts/target_identity.py --stream weekly
    python scripts/target_identity.py --floor 0        # every move, no de-minimis
    python scripts/target_identity.py --names 12       # local inspection only

Ungraded: no KEEP claim can rest on this (no-promotion rule). Python months are
1-indexed (parse_rating_date, reused from target_flow.py).
"""
from __future__ import annotations

import argparse
import pathlib
import json
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from capture import _snap_key                    # noqa: E402
from target_flow import parse_rating_date        # noqa: E402

SNAP_DIR = ROOT / "data" / "snapshots"
DAILY_DIR = ROOT / "data" / "daily"

DEFAULT_FLOOR_PCT = 0.25
CENTS = 4          # targets are currency; compare at 4dp, never on raw float equality


def load_stream(stream: str):
    """Return [(label, {ticker: record})] in capture order."""
    if stream == "weekly":
        paths = sorted(SNAP_DIR.glob("snapshot_*.json"))
        key = lambda p: p.stem.replace("snapshot_", "")
    else:
        paths = sorted((p for p in DAILY_DIR.glob("snapshot_*.json") if _snap_key(p)),
                       key=_snap_key)
        key = lambda p: p.stem.replace("snapshot_", "")
    out = []
    for p in paths:
        d = json.loads(p.read_text(encoding="utf-8"))
        out.append((key(p), {r["ticker"]: r for r in d["records"] if r.get("ticker")}))
    return out


def classify(seq, floor: float):
    """One name's target series -> (moves, reverted, rating_static).

    `seq` is [(target, rating_date)] in capture order, gaps already dropped.
    A move counts only past the de-minimis floor, so a penny wiggle neither
    creates a move nor breaks a flat run. Reversion is an EXACT return to a level
    the series held earlier -- the signature a decision does not leave.
    """
    vals = [round(t, CENTS) for t, _ in seq]
    moves = 0
    for a, b in zip(vals, vals[1:]):
        if a and abs(b / a - 1.0) * 100.0 >= floor:
            moves += 1
    reverted = False
    seen = set()
    prev = None
    for v in vals:
        if prev is not None and v != prev and v in seen:
            reverted = True          # left this level earlier, and came back to it
        seen.add(v)
        prev = v
    rds = [parse_rating_date(rd) for _, rd in seq]
    rating_static = len({r for r in rds if r is not None}) <= 1
    return moves, reverted, rating_static


def run(stream: str, floor: float, n_names: int) -> None:
    caps = load_stream(stream)
    if len(caps) < 3:
        print(f"[identity] {stream}: need >= 3 captures to see a reversion, "
              f"found {len(caps)}")
        return

    print(f"\n=== {stream.upper()} stream: {len(caps)} captures, "
          f"{caps[0][0]} -> {caps[-1][0]}, floor {floor}% ===")

    universe = set(caps[0][1])
    for _, recs in caps[1:]:
        universe &= set(recs)

    buckets = {(True, True): [], (True, False): [],       # (rating_static, reverted)
               (False, True): [], (False, False): []}
    movers = 0
    total_moves = 0
    for t in sorted(universe):
        seq = []
        for _, recs in caps:
            r = recs[t]
            bt = r.get("best_analyst_price_target")
            if not isinstance(bt, (int, float)) or not bt:
                seq = None
                break
            seq.append((bt, r.get("last_rating_date")))
        if not seq:
            continue
        moves, reverted, static = classify(seq, floor)
        if moves == 0:
            continue
        movers += 1
        total_moves += moves
        buckets[(static, reverted)].append((t, moves))

    if not movers:
        print("  no name moved past the floor")
        return

    print(f"  {len(universe):,} names present in every capture; "
          f"{movers:,} moved at least once ({total_moves:,} moves)")

    def cell(static, rev):
        rows = buckets[(static, rev)]
        return len(rows), sum(m for _, m in rows)

    print(f"\n  {'':<26}{'names':>8}{'moves':>8}{'reverted %':>12}")
    for static, label in ((True, "rating date STATIC"), (False, "rating date CHANGED")):
        nr, mr = cell(static, True)
        nn, mn = cell(static, False)
        tot = nr + nn
        share = f"{nr / tot:.0%}" if tot else "n/a"
        print(f"  {label:<26}{tot:>8,}{mr + mn:>8,}{share:>12}")
        print(f"    of which reverted{'':<7}{nr:>8,}{mr:>8,}")

    nr_s, _ = cell(True, True)
    nn_s, _ = cell(True, False)
    nr_c, _ = cell(False, True)
    nn_c, _ = cell(False, False)
    p_static = nr_s / (nr_s + nn_s) if (nr_s + nn_s) else None
    p_changed = nr_c / (nr_c + nn_c) if (nr_c + nn_c) else None
    print()
    if p_static is not None and p_changed is not None:
        if p_changed == 0:
            print(f"  Reversion runs {p_static:.0%} where no analyst acted and 0% where one did.")
        else:
            print(f"  Reversion is {p_static / p_changed:.2f}x more common where the rating "
                  f"date never moved ({p_static:.0%}) than where it did ({p_changed:.0%}).")
        # Tested, not eyeballed. A two-proportion z on a small arm is exactly the
        # place a ratio flatters itself -- the weekly stream's static arm has run
        # as low as 22 names, where 1.6x is three reversions.
        n1, x1 = nr_s + nn_s, nr_s
        n2, x2 = nr_c + nn_c, nr_c
        pool = (x1 + x2) / (n1 + n2)
        se = (pool * (1 - pool) * (1 / n1 + 1 / n2)) ** 0.5
        z = (p_static - p_changed) / se if se else 0.0
        # Two-sided normal tail without scipy: Abramowitz & Stegun 7.1.26 erf.
        t = 1.0 / (1.0 + 0.3275911 * abs(z) / 2 ** 0.5)
        erf = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
                    - 0.284496736) * t + 0.254829592) * t * 2.718281828 ** (-(abs(z) / 2 ** 0.5) ** 2)
        pval = 1 - erf
        verdict = ("discriminates" if pval < 0.05 and p_static > p_changed
                   else "does NOT discriminate at 5%")
        print(f"  Two-proportion z = {z:.2f}, p = {pval:.4f} "
              f"(n = {n1:,} static / {n2:,} changed) -- {verdict}.")
        if min(n1, n2) < 50:
            print(f"  CAUTION: smallest arm is {min(n1, n2)} names. Too thin to lean on.")
        print("  The identity hypothesis predicts a ratio well above 1; a ratio near 1")
        print("  means the pattern does not discriminate and the question stays open.")
    else:
        print("  One arm is empty -- no comparison can be drawn from this stream yet.")

    if n_names:
        ex = sorted(buckets[(True, True)], key=lambda x: -x[1])[:n_names]
        print(f"\n  [--names] reverting, rating date static (LOCAL ONLY, vendor values):")
        print("   ", ", ".join(f"{t}({m})" for t, m in ex) or "none")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stream", choices=["weekly", "daily", "both"], default="both")
    ap.add_argument("--floor", type=float, default=DEFAULT_FLOOR_PCT,
                    help=f"de-minimis move, %% of prior target (default {DEFAULT_FLOOR_PCT})")
    ap.add_argument("--names", type=int, default=0,
                    help="list N example tickers -- local inspection only, never quoted")
    a = ap.parse_args()

    for s in (["weekly", "daily"] if a.stream == "both" else [a.stream]):
        run(s, a.floor, a.names)

    print("\n[identity] Signal-side only: data/snapshots/ and data/daily/ are TipRanks")
    print("[identity] captures taken before the Norgate merge, so no return field is read.")
    print("[identity] Ungraded -- no KEEP claim can rest on this (no-promotion rule).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
