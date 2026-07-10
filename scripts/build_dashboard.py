#!/usr/bin/env python
"""build_dashboard.py -- turn the latest merged snapshot into the private local
dashboard's data file (data/dashboard/dashboard_data.json).

PRIVATE / LOCAL ONLY. The output carries per-name TipRanks values, so it lives
under data/ (gitignored) and the dashboard is served locally (npx serve .). It is
never built into a committed docs/ page and never published -- personal-use
licence firewall.

Honest layering: this fills the Panel State + Accrual tabs from ONE snapshot. The
Revision Monitor needs >= 2 snapshots (week-on-week deltas); the Findings tab is
locked until the analyse.py accrual threshold. The build reports which are live.

    python scripts/build_dashboard.py
"""
from __future__ import annotations

import datetime as dt   # Python datetime: months are 1-indexed (Jan == 1)
import json
import statistics as stats
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAP_DIR = ROOT / "data" / "snapshots"
MERGE_DIR = ROOT / "data" / "merged"
OUT_DIR = ROOT / "data" / "dashboard"
MIN_SNAPSHOTS = 8
MATURE_DAYS = 31
CONSENSUS_ORDER = ["Strong Buy", "Moderate Buy", "Hold", "Moderate Sell", "Strong Sell"]


def _latest_merge():
    files = sorted(MERGE_DIR.glob("merged_*.json")) if MERGE_DIR.exists() else []
    if not files:
        raise FileNotFoundError("no merged_*.json -- run merge_norgate.py first")
    return json.loads(files[-1].read_text(encoding="utf-8"))


def _median(xs):
    xs = [x for x in xs if x is not None]
    return round(stats.median(xs), 2) if xs else None


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    merge = _latest_merge()
    liquid = [r for r in merge["records"] if r.get("liquid")]

    ss = [r.get("smart_score") for r in liquid]
    cons = Counter(r.get("analyst_consensus") for r in liquid if r.get("analyst_consensus"))
    buy = sum(cons.get(k, 0) for k in ("Strong Buy", "Moderate Buy"))
    up_all = [r.get("analyst_price_target_upside_pct") for r in liquid]
    up_best = [r.get("best_analyst_upside_pct") for r in liquid]

    ss_int = [int(round(x)) for x in ss if x is not None]
    ss_hist = {str(b): sum(1 for v in ss_int if v == b) for b in range(1, 11)}

    sect: dict = {}
    for r in liquid:
        s = r.get("sector") or "—"
        d = sect.setdefault(s, {"n": 0, "ss": [], "buy": 0})
        d["n"] += 1
        if r.get("smart_score") is not None:
            d["ss"].append(r["smart_score"])
        if r.get("analyst_consensus") in ("Strong Buy", "Moderate Buy"):
            d["buy"] += 1
    by_sector = sorted(
        [{"sector": s, "n": d["n"],
          "mean_ss": round(stats.mean(d["ss"]), 2) if d["ss"] else None,
          "pct_buy": round(100 * d["buy"] / d["n"], 1) if d["n"] else None}
         for s, d in sect.items()],
        key=lambda x: -x["n"])

    def tally(field):
        return dict(Counter(r.get(field) for r in liquid if r.get(field)))

    sentiment = {"insider": tally("insider_signal"),
                 "hedgefund": tally("hedgefund_signal"),
                 "investor": tally("investor_sentiment")}

    def _bvc_gap(r):
        """Best-analyst vs consensus target gap, % -- crude dispersion proxy
        (consensus targets lose informativeness when disagreement is high)."""
        bt, at = r.get("best_analyst_price_target"), r.get("analyst_price_target")
        if bt is None or at is None or at <= 0:
            return None
        return round((bt / at - 1.0) * 100.0, 1)

    table = [{
        "ticker": r["ticker"], "company": r.get("company"), "sector": r.get("sector"),
        "smart_score": r.get("smart_score"), "consensus": r.get("analyst_consensus"),
        "best_consensus": r.get("best_analyst_consensus"),
        "upside": r.get("analyst_price_target_upside_pct"),
        "best_upside": r.get("best_analyst_upside_pct"),
        "tr_3m": round(r["tr_3m_pct"], 1) if r.get("tr_3m_pct") is not None else None,
        "vol_ann": round(r["vol_ann_pct"], 1) if r.get("vol_ann_pct") is not None else None,
        "bvc_gap": _bvc_gap(r),
        "insider": r.get("insider_signal"), "hedgefund": r.get("hedgefund_signal"),
        "div_yield": r.get("dividend_yield"),
        "mktcap_b": round((r.get("market_cap") or 0) / 1e9, 1),
        "idx": "500" if "S&P 500" in r.get("member_of", []) else "400",
    } for r in liquid]

    # --- view-layer lens (ungraded; mirrors the frozen S3 gate inputs) ---------
    # Trap profile = 3m TR below the liquid-universe cross-sectional median, OR
    # negative insider signal, OR Smart Score <= 4. Upside-per-sigma and the
    # sector-relative percentile are DISPLAY normalisations (Da & Schaumburg:
    # implied upside ranks within sector, not across the market). Nothing here
    # is graded -- the graded read is analyse.py against the frozen menu.
    med_tr3m = _median([row["tr_3m"] for row in table])
    for row in table:
        u, v = row["best_upside"], row["vol_ann"]
        row["upside_per_vol"] = round(u / v, 2) if (u is not None and v and v > 0) else None
        gate_price = (row["tr_3m"] is not None and med_tr3m is not None
                      and row["tr_3m"] >= med_tr3m)
        gate_insider = (row["insider"] or "") != "Negative"
        gate_ss = row["smart_score"] is None or row["smart_score"] > 4
        row["lens_pass"] = bool(gate_price and gate_insider and gate_ss)
    sec_vals: dict = {}
    for row in table:
        if row["upside_per_vol"] is not None:
            sec_vals.setdefault(row["sector"] or "—", []).append(row["upside_per_vol"])
    for row in table:
        v, vs = row["upside_per_vol"], sec_vals.get(row["sector"] or "—", [])
        if v is None or len(vs) < 2:
            row["sector_pct"] = None
        else:
            row["sector_pct"] = round(100.0 * sum(1 for x in vs if x < v) / (len(vs) - 1))
    n_lens = sum(1 for row in table if row["lens_pass"])

    snaps = sorted(SNAP_DIR.glob("snapshot_*.json")) if SNAP_DIR.exists() else []
    snap_dates = sorted(p.stem.replace("snapshot_", "") for p in snaps)
    today = dt.date.today()
    matured = sum(1 for d in snap_dates if (today - dt.date.fromisoformat(d)).days >= MATURE_DAYS)
    revision_available = len(snap_dates) >= 2

    payload = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "as_of": merge["as_of"],
        "universe": {
            "snapshot_total": merge["counts"]["snapshot"],
            "matched": merge["counts"]["matched"],
            "liquid": merge["counts"]["liquid"],
            "sp500": sum(1 for r in liquid if "S&P 500" in r.get("member_of", [])),
            "sp400": sum(1 for r in liquid if "S&P MidCap 400" in r.get("member_of", [])),
            "indices": merge["target_indices"],
        },
        "kpis": {
            "median_smart_score": _median(ss),
            "pct_buy": round(100 * buy / len(liquid), 1) if liquid else None,
            "median_upside": _median(up_all),
            "median_best_upside": _median(up_best),
            "n_liquid": len(liquid),
        },
        "smart_score_hist": ss_hist,
        "consensus": {k: cons.get(k, 0) for k in CONSENSUS_ORDER},
        "by_sector": by_sector,
        "sentiment": sentiment,
        "lens": {"median_tr_3m": med_tr3m, "n_pass": n_lens,
                 "definition": ("view-only, ungraded: hide 3m TR < universe median, "
                                "negative insider, or Smart Score <= 4")},
        "table": table,
        "accrual": {"snapshots": len(snap_dates), "dates": snap_dates,
                    "matured": matured, "min_snapshots": MIN_SNAPSHOTS, "mature_days": MATURE_DAYS},
        "revision": {"available": revision_available,
                     "prev_as_of": snap_dates[-2] if revision_available else None,
                     "note": ("Ready." if revision_available
                              else "Week-on-week revision tracking lights up at the next capture.")},
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "dashboard_data.json"
    out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"[dashboard] as_of {payload['as_of']}: {len(liquid)} liquid names, "
          f"{len(by_sector)} sectors, lens-pass {n_lens} (median 3m TR "
          f"{med_tr3m if med_tr3m is not None else 'n/a'}%) -> {out.relative_to(ROOT)} "
          f"({out.stat().st_size // 1024} KB)")
    print(f"[dashboard] live: Panel State + Accrual | Revision="
          f"{'ON' if revision_available else 'accruing'} | Findings locked "
          f"({len(snap_dates)}/{MIN_SNAPSHOTS})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
