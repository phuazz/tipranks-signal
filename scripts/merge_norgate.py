#!/usr/bin/env python
"""merge_norgate.py -- join a frozen TipRanks snapshot to the Norgate point-in-time
liquid universe, producing an analysis-ready panel.

For each name in snapshot_<asof>.json, as of the last session on/before the
capture date (survivorship-bias-free):
  - resolve the TipRanks ticker to a live Norgate symbol (misses are FLAGGED,
    never silently dropped);
  - test point-in-time membership of the target indices (S&P 500 / MidCap 400);
  - read the as-of close (return anchor) and trailing median dollar volume;
  - apply the price + ADV liquidity floors.

The signal (from the snapshot) and the universe/liquidity (from Norgate) are BOTH
known at the capture date -- a clean point-in-time join. Forward returns are
measured LATER by analyse.py, once bars accrue. That separation is the forward
test.

    python scripts/merge_norgate.py --asof 2026-07-09
"""
from __future__ import annotations

import argparse
import datetime as dt   # Python datetime: months are 1-indexed
import json
import sys
from pathlib import Path

import norgate as ng     # local helper (same scripts/ folder is on sys.path)

ROOT = Path(__file__).resolve().parents[1]
SNAP_DIR = ROOT / "data" / "snapshots"
MERGE_DIR = ROOT / "data" / "merged"

PRICE_FLOOR_USD = 5.0             # drop sub-$5 names (spread + optionability)
ADV_FLOOR_USD = 10_000_000.0     # 21d median dollar volume >= $10m


def load_snapshot(asof: dt.date) -> dict:
    pth = SNAP_DIR / f"snapshot_{asof.isoformat()}.json"
    if not pth.exists():
        raise FileNotFoundError(f"no snapshot for {asof}: {pth}. Run ingest.py first.")
    return json.loads(pth.read_text(encoding="utf-8"))


def merge(asof: dt.date, price_floor=PRICE_FLOOR_USD, adv_floor=ADV_FLOOR_USD) -> Path:
    snap = load_snapshot(asof)
    n = ng.connect()
    ng.hard_check(n)

    out_records = []
    n_matched = n_member = n_liquid = 0
    for rec in snap["records"]:
        ticker = rec["ticker"]
        sym = ng.resolve_symbol(n, ticker)
        row = dict(rec)
        if sym is None:
            row.update({"norgate_symbol": None, "matched": False, "member_of": [],
                        "in_universe": False, "close_tr": None, "adv_usd": None,
                        "liquid": False, "anchor_date": None,
                        "tr_3m_pct": None, "vol_ann_pct": None})
            out_records.append(row)
            continue
        n_matched += 1
        members = ng.member_asof(n, sym, asof)
        liq = ng.liquidity_asof(n, sym, asof)
        in_universe = len(members) > 0
        liquid = (in_universe
                  and liq["close_tr"] is not None and liq["close_tr"] >= price_floor
                  and liq["adv_usd"] is not None and liq["adv_usd"] >= adv_floor)
        if in_universe:
            n_member += 1
        if liquid:
            n_liquid += 1
        row.update({"norgate_symbol": sym, "matched": True, "member_of": members,
                    "in_universe": in_universe, "close_tr": liq["close_tr"],
                    "adv_usd": liq["adv_usd"], "liquid": liquid,
                    "anchor_date": liq["anchor_date"],
                    "tr_3m_pct": liq.get("tr_3m_pct"), "vol_ann_pct": liq.get("vol_ann_pct")})
        out_records.append(row)

    payload = {
        "as_of": asof.isoformat(),
        "built_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "target_indices": list(ng.TARGET_INDICES),
        "price_floor_usd": price_floor,
        "adv_floor_usd": adv_floor,
        "counts": {"snapshot": len(snap["records"]), "matched": n_matched,
                   "in_universe": n_member, "liquid": n_liquid},
        "records": out_records,
    }
    MERGE_DIR.mkdir(parents=True, exist_ok=True)
    out = MERGE_DIR / f"merged_{asof.isoformat()}.json"
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    c = payload["counts"]
    print(f"[merge] {c['snapshot']} snapshot -> {c['matched']} matched, "
          f"{c['in_universe']} in-universe, {c['liquid']} liquid -> {out.relative_to(ROOT)}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--asof", required=True, help="capture date YYYY-MM-DD of the snapshot")
    ap.add_argument("--price-floor", type=float, default=PRICE_FLOOR_USD)
    ap.add_argument("--adv-floor", type=float, default=ADV_FLOOR_USD)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    asof = dt.date.fromisoformat(args.asof)
    merge(asof, args.price_floor, args.adv_floor)
    return 0


if __name__ == "__main__":
    sys.exit(main())
