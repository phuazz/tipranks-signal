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


def _prev_merge():
    files = sorted(MERGE_DIR.glob("merged_*.json")) if MERGE_DIR.exists() else []
    return json.loads(files[-2].read_text(encoding="utf-8")) if len(files) >= 2 else None


def _parse_rating_date(s):
    """TipRanks exports 'May 27, 2026' (sometimes abbreviated). Date library only."""
    if not s:
        return None
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


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
        "most_accurate": r.get("most_accurate_analyst"),
        "most_profitable": r.get("most_profitable_analyst"),
        "last_rating": r.get("last_rating_date"),
        "news": r.get("news_sentiment"),
        "investor": r.get("investor_sentiment"),
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

    # --- ticker -> record maps shared by the revision and chart layers --------
    sym_by_ticker = {r["ticker"]: r.get("norgate_symbol") for r in liquid}
    rec_by_ticker = {r["ticker"]: r for r in liquid}
    # (the chart loop runs after the revision layer below, so strong week-on-week
    # revisions qualify for price charts alongside lens-passed names)
    n_charts = 0

    snaps = sorted(SNAP_DIR.glob("snapshot_*.json")) if SNAP_DIR.exists() else []
    snap_dates = sorted(p.stem.replace("snapshot_", "") for p in snaps)
    today = dt.date.today()
    matured = sum(1 for d in snap_dates if (today - dt.date.fromisoformat(d)).days >= MATURE_DAYS)
    revision_available = len(snap_dates) >= 2

    # --- week-on-week revision layer (the pre-registered primary, view form) ---
    # Frozen definitions applied at the VIEW layer: a rating change is a WoW
    # consensus-label move between frozen snapshots, CONFIRMED only when the
    # exported Last Rating Date falls inside the inter-snapshot window (guards
    # composition-change phantoms); a target revision is a change in the
    # best-analyst target LEVEL (price-driven upside moves do not qualify).
    # Ungraded here -- analyse.py grades the frozen menu.
    revision = {"available": False, "note": "Week-on-week revision tracking lights up at the next capture."}
    prev = _prev_merge()
    row_by_ticker = {row["ticker"]: row for row in table}
    for row in table:
        row["rev_rank"] = 0
        row["rev_badge"] = None
        row["rev_note"] = None
    if prev is not None:
        prev_asof = dt.date.fromisoformat(prev["as_of"])
        now_asof = dt.date.fromisoformat(merge["as_of"])
        prev_liq = {r["ticker"]: r for r in prev["records"] if r.get("liquid")}
        now_all = {r["ticker"]: r for r in merge["records"]}
        rank = {c: i for i, c in enumerate(CONSENSUS_ORDER)}  # 0 = Strong Buy
        rows, n_conf_up, n_conf_dn = [], 0, 0
        for t, r in ((t, r) for t, r in rec_by_ticker.items() if t in prev_liq):
            p = prev_liq[t]
            pc, nc = p.get("analyst_consensus"), r.get("analyst_consensus")
            label_dir = 0
            if pc in rank and nc in rank and pc != nc:
                label_dir = 1 if rank[nc] < rank[pc] else -1
            rd = _parse_rating_date(r.get("last_rating_date"))
            confirmed = rd is not None and prev_asof < rd <= now_asof
            # de-minimis floor: a target move counts only at >= 0.25% of the prior
            # level -- deliberate analyst changes are >= ~1%; penny wiggles are noise
            bt_p, bt_n = p.get("best_analyst_price_target"), r.get("best_analyst_price_target")
            bt_pct = None
            if bt_p and bt_n and abs(bt_n / bt_p - 1.0) >= 0.0025:
                bt_pct = round((bt_n / bt_p - 1.0) * 100.0, 2)
            ct_p, ct_n = p.get("analyst_price_target"), r.get("analyst_price_target")
            ct_pct = None
            if ct_p and ct_n and abs(ct_n / ct_p - 1.0) >= 0.0025:
                ct_pct = round((ct_n / ct_p - 1.0) * 100.0, 2)
            ss_p, ss_n = p.get("smart_score"), r.get("smart_score")
            ss_d = (ss_n - ss_p) if (ss_p is not None and ss_n is not None and ss_n != ss_p) else None
            ins_flip = (p.get("insider_signal"), r.get("insider_signal")) \
                if (p.get("insider_signal") or r.get("insider_signal")) and p.get("insider_signal") != r.get("insider_signal") else None
            news_flip = (p.get("news_sentiment"), r.get("news_sentiment")) \
                if (p.get("news_sentiment") or r.get("news_sentiment")) and p.get("news_sentiment") != r.get("news_sentiment") else None
            if not (label_dir or bt_pct is not None or ss_d is not None or ins_flip or news_flip):
                continue
            score = 0
            if label_dir > 0:
                score += 5 if confirmed else 3
                n_conf_up += 1 if confirmed else 0
            if label_dir < 0:
                score -= 5 if confirmed else 3
                n_conf_dn += 1 if confirmed else 0
            if bt_pct is not None:
                score += 4 if bt_pct > 0 else -4
            if ss_d is not None:
                score += 1 if ss_d > 0 else -1
            row = row_by_ticker.get(t)
            note_bits = []
            if label_dir:
                note_bits.append(f"{pc} → {nc}" + (" (confirmed in window)" if confirmed else " (no rating date in window)"))
            if bt_pct is not None:
                note_bits.append(f"best target {'+' if bt_pct >= 0 else ''}{bt_pct}%")
            if ss_d is not None:
                ss_show = int(ss_d) if float(ss_d).is_integer() else ss_d
                note_bits.append(f"Smart Score {'+' if ss_d > 0 else ''}{ss_show}")
            if ins_flip:
                note_bits.append(f"insider {ins_flip[0] or '—'} → {ins_flip[1] or '—'}")
            if news_flip:
                note_bits.append(f"news {news_flip[0] or '—'} → {news_flip[1] or '—'}")
            if row is not None:
                row["rev_rank"] = score
                row["rev_badge"] = "up" if score > 0 else ("down" if score < 0 else None)
                row["rev_note"] = " · ".join(note_bits)
            rows.append({
                "ticker": t, "company": r.get("company"), "sector": r.get("sector"),
                "prev_cons": pc, "now_cons": nc, "dir": label_dir, "confirmed": confirmed,
                "last_rating": r.get("last_rating_date"),
                "bt_prev": bt_p, "bt_now": bt_n, "bt_pct": bt_pct, "ct_pct": ct_pct,
                "ss_prev": ss_p, "ss_now": ss_n, "ss_d": ss_d,
                "ins_prev": ins_flip[0] if ins_flip else None, "ins_now": ins_flip[1] if ins_flip else None,
                "news_prev": news_flip[0] if news_flip else None, "news_now": news_flip[1] if news_flip else None,
                "rev_rank": score,
                "lens_pass": row["lens_pass"] if row is not None else None,
                "chart": row.get("chart", False) if row is not None else False,
                "upside_per_vol": row["upside_per_vol"] if row is not None else None,
            })
        entered = sorted(t for t in row_by_ticker if t not in prev_liq)
        left = []
        for t in sorted(prev_liq):
            if t in row_by_ticker:
                continue
            nr = now_all.get(t)
            if nr is None:
                left.append({"ticker": t, "reason": "absent from this week's export"})
            elif nr.get("norgate_symbol") is None:
                left.append({"ticker": t, "reason": "unmatched on the feed — delisted in the window; the final return realises at analyse time"})
            else:
                left.append({"ticker": t, "reason": "lost index membership or liquidity floor"})
        sect_rev: dict = {}
        for rv in rows:
            s = rv["sector"] or "—"
            d = sect_rev.setdefault(s, {"up": 0, "down": 0, "raise": 0, "cut": 0})
            d["up"] += 1 if rv["dir"] > 0 else 0
            d["down"] += 1 if rv["dir"] < 0 else 0
            d["raise"] += 1 if (rv["bt_pct"] or 0) > 0 else 0
            d["cut"] += 1 if (rv["bt_pct"] or 0) < 0 else 0
        sector_n = Counter((row["sector"] or "—") for row in table if row["ticker"] in prev_liq)
        breadth = sorted(
            [{"sector": s, "n": sector_n.get(s, 0), "net_upgrades": d["up"] - d["down"],
              "raises": d["raise"], "cuts": d["cut"]} for s, d in sect_rev.items()],
            key=lambda x: -(x["net_upgrades"]))
        raises = [rv["bt_pct"] for rv in rows if (rv["bt_pct"] or 0) > 0]
        cuts = [rv["bt_pct"] for rv in rows if (rv["bt_pct"] or 0) < 0]
        raises_conf = sum(1 for rv in rows if (rv["bt_pct"] or 0) > 0 and rv["confirmed"])
        cuts_conf = sum(1 for rv in rows if (rv["bt_pct"] or 0) < 0 and rv["confirmed"])
        revision = {
            "available": True, "prev_as_of": prev["as_of"], "as_of": merge["as_of"],
            "window_days": (now_asof - prev_asof).days,
            "pairs": len([t for t in rec_by_ticker if t in prev_liq]),
            "upgrades": sum(1 for rv in rows if rv["dir"] > 0),
            "downgrades": sum(1 for rv in rows if rv["dir"] < 0),
            "confirmed_up": n_conf_up, "confirmed_down": n_conf_dn,
            "target_raises": len(raises), "target_cuts": len(cuts),
            "target_raises_confirmed": raises_conf, "target_cuts_confirmed": cuts_conf,
            "median_raise_pct": _median(raises), "median_cut_pct": _median(cuts),
            "score_up": sum(1 for rv in rows if (rv["ss_d"] or 0) > 0),
            "score_down": sum(1 for rv in rows if (rv["ss_d"] or 0) < 0),
            "rows": sorted(rows, key=lambda x: -x["rev_rank"]),
            "entered": entered, "left": left, "breadth": breadth,
        }

    # --- per-name price history (lens-passed OR strongly-revised names) -------
    # Display layer only: ~1y TOTALRETURN + 50/200-session averages, one small
    # JSON each, fetched by the template on row click. |revision score| >= 8
    # (label change and target move in agreement) earns a chart even off-lens --
    # the revision leaders are exactly the names worth a look. Skipped
    # gracefully when NDU is down; the weekly flow runs post-merge with NDU up.
    price_dir = OUT_DIR / "prices"
    try:
        import norgate as ng
        ndu = ng.connect()
        price_dir.mkdir(parents=True, exist_ok=True)
        for old in price_dir.glob("*.json"):
            old.unlink()
        asof_date = dt.date.fromisoformat(merge["as_of"])
        for row in table:
            if not (row["lens_pass"] or abs(row.get("rev_rank", 0)) >= 8):
                continue
            sym = sym_by_ticker.get(row["ticker"])
            if not sym:
                continue
            hist = ng.chart_history(ndu, sym, asof_date)
            if hist is None:
                continue
            rec = rec_by_ticker[row["ticker"]]
            hist.update({"ticker": row["ticker"], "company": row.get("company"),
                         "as_of": merge["as_of"],
                         "best_target": rec.get("best_analyst_price_target"),
                         "cons_target": rec.get("analyst_price_target")})
            safe = row["ticker"].replace("/", "-")
            (price_dir / f"{safe}.json").write_text(
                json.dumps(hist, separators=(",", ":")), encoding="utf-8")
            row["chart"] = True
            n_charts += 1
    except Exception as exc:  # noqa: BLE001 -- chart build is optional display
        print(f"[dashboard] price charts skipped ({exc})", file=sys.stderr)
    for row in table:
        row.setdefault("chart", False)
    if revision.get("available"):
        for rv in revision["rows"]:
            rv["chart"] = bool(row_by_ticker.get(rv["ticker"], {}).get("chart"))

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
        "revision": revision,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "dashboard_data.json"
    out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"[dashboard] as_of {payload['as_of']}: {len(liquid)} liquid names, "
          f"{len(by_sector)} sectors, lens-pass {n_lens} (median 3m TR "
          f"{med_tr3m if med_tr3m is not None else 'n/a'}%), {n_charts} price charts "
          f"-> {out.relative_to(ROOT)} ({out.stat().st_size // 1024} KB)")
    print(f"[dashboard] live: Panel State + Accrual | Revision="
          f"{'ON' if revision.get('available') else 'accruing'} | Findings locked "
          f"({len(snap_dates)}/{MIN_SNAPSHOTS})")
    if revision.get("available"):
        print(f"[dashboard] revision {revision['prev_as_of']} -> {revision['as_of']} "
              f"({revision['window_days']}d window, {revision['pairs']} pairs): "
              f"{revision['upgrades']} upgrades ({revision['confirmed_up']} confirmed) / "
              f"{revision['downgrades']} downgrades ({revision['confirmed_down']} confirmed); "
              f"best-target raises {revision['target_raises']} (median "
              f"{revision['median_raise_pct']}%) / cuts {revision['target_cuts']}; "
              f"score up {revision['score_up']} / down {revision['score_down']}; "
              f"entered {len(revision['entered'])}, left {len(revision['left'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
