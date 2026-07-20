#!/usr/bin/env python
"""pipeline.py -- build the PUBLIC page (docs/index.html) from the private panel.

The public layer carries AGGREGATES ONLY: crowd statistics over the liquid
universe, revision-flow counts, accrual state. Per-name vendor values
(TipRanks / Norgate) never appear -- that boundary is enforced here by a hard
leak guard: the build fails if any ticker ever seen in any merge appears as a
quoted string in the output, or if any per-name field name survives.

    python scripts/pipeline.py     # data/merged/*.json -> docs/index.html
                                   # (+ data/public/aggregates.json for dev)

House architecture: public_template.html is the source (fetch fallback for
`npx serve .` dev); this script inlines the aggregate JSON at the
/*__AGDATA__*/ marker and writes docs/index.html for GitHub Pages.
Python datetime throughout; months are 1-indexed.
"""
from __future__ import annotations

import datetime as dt
import json
import statistics as stats
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MERGE_DIR = ROOT / "data" / "merged"
SNAP_DIR = ROOT / "data" / "snapshots"
TEMPLATE = ROOT / "public_template.html"
OUT_HTML = ROOT / "docs" / "index.html"
DEV_JSON = ROOT / "data" / "public" / "aggregates.json"
MIN_SNAPSHOTS = 8
MATURE_DAYS = 31
CONSENSUS_ORDER = ["Strong Buy", "Moderate Buy", "Hold", "Moderate Sell", "Strong Sell"]


def _median(xs):
    xs = [x for x in xs if x is not None]
    return round(stats.median(xs), 2) if xs else None


def _parse_rating_date(s):
    if not s:
        return None
    for f in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s.strip(), f).date()
        except ValueError:
            continue
    return None


def _week_aggregates(liquid):
    cons = Counter(r.get("analyst_consensus") for r in liquid if r.get("analyst_consensus"))
    buy = sum(cons.get(k, 0) for k in ("Strong Buy", "Moderate Buy"))
    return {
        "liquid": len(liquid),
        "pct_buy": round(100 * buy / len(liquid), 1) if liquid else None,
        "median_upside": _median([r.get("analyst_price_target_upside_pct") for r in liquid]),
        "median_best_upside": _median([r.get("best_analyst_upside_pct") for r in liquid]),
        "median_smart_score": _median([r.get("smart_score") for r in liquid]),
    }


def _pair_counts(prev, curr):
    """Label / target revision counts between two adjacent merges (aggregate only)."""
    rank = {c: i for i, c in enumerate(CONSENSUS_ORDER)}
    prev_liq = {r["ticker"]: r for r in prev["records"] if r.get("liquid")}
    curr_liq = {r["ticker"]: r for r in curr["records"] if r.get("liquid")}
    p_asof = dt.date.fromisoformat(prev["as_of"])
    c_asof = dt.date.fromisoformat(curr["as_of"])
    up = dn = cu = cd = 0
    raises, cuts = [], []
    raises_conf = cuts_conf = 0
    breadth: dict = {}
    for t, r in curr_liq.items():
        p = prev_liq.get(t)
        if p is None:
            continue
        rd = _parse_rating_date(r.get("last_rating_date"))
        confirmed = rd is not None and p_asof < rd <= c_asof
        pc, nc = p.get("analyst_consensus"), r.get("analyst_consensus")
        d = breadth.setdefault(r.get("sector") or "—", {"up": 0, "down": 0, "raise": 0, "cut": 0})
        if pc in rank and nc in rank and pc != nc:
            if rank[nc] < rank[pc]:
                up += 1; cu += 1 if confirmed else 0; d["up"] += 1
            else:
                dn += 1; cd += 1 if confirmed else 0; d["down"] += 1
        bt_p, bt_n = p.get("best_analyst_price_target"), r.get("best_analyst_price_target")
        # de-minimis floor mirrors build_dashboard.py: moves < 0.25% are noise
        if bt_p and bt_n and abs(bt_n / bt_p - 1.0) >= 0.0025:
            chg = round((bt_n / bt_p - 1.0) * 100.0, 2)
            if chg > 0:
                raises.append(chg); raises_conf += 1 if confirmed else 0; d["raise"] += 1
            else:
                cuts.append(chg); cuts_conf += 1 if confirmed else 0; d["cut"] += 1
    pairs = sum(1 for t in curr_liq if t in prev_liq)
    return {
        "prev_as_of": prev["as_of"], "as_of": curr["as_of"],
        "window_days": (c_asof - p_asof).days, "pairs": pairs,
        "upgrades": up, "downgrades": dn, "confirmed_up": cu, "confirmed_down": cd,
        "target_raises": len(raises), "target_cuts": len(cuts),
        "target_raises_confirmed": raises_conf, "target_cuts_confirmed": cuts_conf,
        "median_raise_pct": _median(raises), "median_cut_pct": _median(cuts),
        "entered_n": sum(1 for t in curr_liq if t not in prev_liq),
        "left_n": sum(1 for t in prev_liq if t not in curr_liq),
        "breadth": sorted(
            [{"sector": s, "net_upgrades": d["up"] - d["down"],
              "raises": d["raise"], "cuts": d["cut"]} for s, d in breadth.items()],
            key=lambda x: -x["net_upgrades"]),
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    files = sorted(MERGE_DIR.glob("merged_*.json")) if MERGE_DIR.exists() else []
    if not files:
        raise FileNotFoundError("no merged_*.json -- run the weekly pipeline first")
    merges = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    latest = merges[-1]
    liquid = [r for r in latest["records"] if r.get("liquid")]

    ss_int = [int(round(r["smart_score"])) for r in liquid if r.get("smart_score") is not None]
    cons = Counter(r.get("analyst_consensus") for r in liquid if r.get("analyst_consensus"))
    sect: dict = {}
    for r in liquid:
        s = r.get("sector") or "—"
        d = sect.setdefault(s, {"n": 0, "ss": []})
        d["n"] += 1
        if r.get("smart_score") is not None:
            d["ss"].append(r["smart_score"])
    tally = lambda f: dict(Counter(r.get(f) for r in liquid if r.get(f)))

    weekly = []
    pair_by_asof = {}
    for i, m in enumerate(merges):
        wk = {"as_of": m["as_of"]}
        wk.update(_week_aggregates([r for r in m["records"] if r.get("liquid")]))
        if i > 0:
            pc = _pair_counts(merges[i - 1], m)
            pair_by_asof[m["as_of"]] = pc
            wk.update({"upgrades": pc["upgrades"], "downgrades": pc["downgrades"],
                       "target_raises": pc["target_raises"], "target_cuts": pc["target_cuts"]})
        weekly.append(wk)
    latest_pair = pair_by_asof.get(latest["as_of"])

    snaps = sorted(SNAP_DIR.glob("snapshot_*.json")) if SNAP_DIR.exists() else []
    snap_dates = sorted(p.stem.replace("snapshot_", "") for p in snaps)
    today = dt.date.today()
    matured = sum(1 for d in snap_dates if (today - dt.date.fromisoformat(d)).days >= MATURE_DAYS)

    agg = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "as_of": latest["as_of"],
        "universe": {
            "liquid": len(liquid),
            "sp500": sum(1 for r in liquid if "S&P 500" in r.get("member_of", [])),
            "sp400": sum(1 for r in liquid if "S&P MidCap 400" in r.get("member_of", [])),
        },
        "kpis": _week_aggregates(liquid) | {"pct_buy": round(
            100 * sum(cons.get(k, 0) for k in ("Strong Buy", "Moderate Buy")) / len(liquid), 1)},
        "smart_score_hist": {str(b): sum(1 for v in ss_int if v == b) for b in range(1, 11)},
        "consensus": {k: cons.get(k, 0) for k in CONSENSUS_ORDER},
        "by_sector": sorted(
            [{"sector": s, "n": d["n"],
              "mean_ss": round(stats.mean(d["ss"]), 2) if d["ss"] else None}
             for s, d in sect.items()], key=lambda x: -x["n"]),
        "sentiment": {"insider": tally("insider_signal"),
                      "hedgefund": tally("hedgefund_signal"),
                      "investor": tally("investor_sentiment")},
        "accrual": {"snapshots": len(snap_dates), "min_snapshots": MIN_SNAPSHOTS,
                    "matured": matured},
        "revision": ({"available": True} | latest_pair) if latest_pair else {"available": False},
        "weekly": weekly,
    }

    out_json = json.dumps(agg, separators=(",", ":"))

    # --- publication leak guard (hard fail) -----------------------------------
    # No ticker ever seen in ANY merge may appear as a quoted string, and no
    # per-name field name may survive into the public payload.
    all_tickers = set()
    for m in merges:
        all_tickers |= {r["ticker"] for r in m["records"] if r.get("ticker")}
    leaks = sorted(t for t in all_tickers if f'"{t}"' in out_json)
    if leaks:
        raise SystemExit(f"LEAK GUARD: ticker(s) in public payload: {leaks[:10]}")
    for banned in ('"ticker"', '"company"', "best_analyst_price_target",
                   "last_rating_date", '"rows"', '"table"'):
        if banned in out_json:
            raise SystemExit(f"LEAK GUARD: per-name field {banned} in public payload")

    tpl = TEMPLATE.read_text(encoding="utf-8")
    marker = "/*__AGDATA__*/null"
    if marker not in tpl:
        raise SystemExit("injection marker missing from public_template.html")
    html = tpl.replace(marker, "/*__AGDATA__*/" + out_json.replace("</", "<\\/"), 1)
    leaks_html = sorted(t for t in all_tickers if f'"{t}"' in html)
    if leaks_html:
        raise SystemExit(f"LEAK GUARD (html): {leaks_html[:10]}")

    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(html, encoding="utf-8")
    (OUT_HTML.parent / ".nojekyll").write_text("", encoding="utf-8")
    DEV_JSON.parent.mkdir(parents=True, exist_ok=True)
    DEV_JSON.write_text(out_json, encoding="utf-8")
    print(f"[pipeline] {len(weekly)} weekly rows, revision "
          f"{'ON' if latest_pair else 'pending'}; leak guard PASS "
          f"({len(all_tickers)} tickers screened) -> {OUT_HTML.relative_to(ROOT)} "
          f"({OUT_HTML.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
