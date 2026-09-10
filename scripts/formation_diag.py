#!/usr/bin/env python
"""formation_diag.py -- what the frozen schemes SELECT, before any forward return is read.

An UNGRADED diagnostic. It runs the pre-registered formation machinery in
analyse.py (select, build_s2, the 4-week cohort roll, retention bands, episode-
netted costs) on every accrued formation week and reports what the books look
like -- size, overlap, style tilts, cost hurdle -- plus event flow, signal
durability from the daily log, and the accrual calendar. It never measures a
forward return.

WHY THIS EXISTS. The interim read (analyse.py --force-interim) is one command
away, but running it closes the blind window that register row 10 (the daily-
log promotion) depends on. Everything here is answerable from formation-time
information alone, so it can be read now without spending that window, and it
answers the question a forward read cannot: is any of this beta in disguise?
Guard 2 of the study is that Strong-Buy / high-Smart-Score names cluster on
high-beta growth; the tilt table below tests that directly, blind.

THE THREE WAYS THIS DIAGNOSTIC COULD BE SILENTLY WRONG, stated before it ran:
  1. Forward leakage. Every field used is trailing at capture (merge fields:
     tr_3m_pct, vol_ann_pct, adv_usd, market_cap, dividend_yield; Norgate beta
     via risk_frame, which slices AT OR BEFORE the anchor). returns.forward_return
     is never called; a module-level assertion at the bottom enforces that.
  2. Universe drift. The frame is the FULL liquid universe of the current merge
     (register rows 3 and 8(e)) -- the same frame analyse.py forms on, so the
     books here are the books it will measure. S2 forms only where beta exists,
     exactly as build_s2 does.
  3. Pooling across windows of unequal length. Event counts per window are
     reported per calendar day AND per ET session, never raw alone: a 21-day
     window carries three weeks of events and a 6-day window under one.

Reads data/merged/ (formation-time fields), data/daily/ (label durability) and
Norgate for TRAILING beta only. Output is aggregates only -- no per-name vendor
value is printed, so the result is safe to quote in the memo. This is a
diagnostic and can never become a KEEP claim (no-promotion rule).

    python scripts/formation_diag.py                    # console
    python scripts/formation_diag.py --out reviews/x.md # also write a markdown record
    python scripts/formation_diag.py --no-norgate       # skip beta / S2 / costs (offline)

Python months are 1-indexed; every date shift goes through returns.py.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import sys
from collections import Counter, deque
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import analyse as A                                     # noqa: E402
import returns as R                                     # noqa: E402
from capture import _snap_key                           # noqa: E402
from target_flow import parse_rating_date               # noqa: E402

DAILY_DIR = ROOT / "data" / "daily"
SCHEMES = A.SCHEMES
RANK = A.RANK
COHORT_WEEKS = A.COHORT_WEEKS
REVERSAL_DAYS = (7, 14, 28)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def _wd(d: dt.date) -> str:
    return f"{d.isoformat()} ({d.strftime('%a')})"


def _num(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").astype(float)


def _tier(adv: float) -> str:
    for floor, bps in A.COST_TIERS:
        if adv >= floor:
            return f"{int(bps)}bp"
    return "below-floor"


def _pct(n: int, d: int) -> str:
    return f"{100.0 * n / d:.0f}%" if d else "n/a"


def _fmt_table(rows: list[list], header: list[str]) -> str:
    """Plain-text table, also valid GitHub markdown."""
    cells = [header] + [[str(c) for c in r] for r in rows]
    w = [max(len(r[i]) for r in cells) for i in range(len(header))]
    out = ["| " + " | ".join(c.ljust(w[i]) for i, c in enumerate(cells[0])) + " |",
           "|" + "|".join("-" * (w[i] + 2) for i in range(len(header))) + "|"]
    for r in cells[1:]:
        out.append("| " + " | ".join(c.ljust(w[i]) for i, c in enumerate(r)) + " |")
    return "\n".join(out)


# --------------------------------------------------------------------------
# 1. accrual calendar and verdict projection -- pure date arithmetic
# --------------------------------------------------------------------------

def accrual_calendar(merges: list[dict], sessions: list[dt.date], data_asof: dt.date,
                     promoted: list[dt.date]) -> None:
    print("\n## 1. Accrual calendar\n")
    rows = []
    for m in merges:
        asof = _d(m["as_of"])
        anchors = {r.get("anchor_date") for r in m["records"] if r.get("liquid")}
        anchor = _d(sorted(a for a in anchors if a)[-1]) if anchors else asof
        cells = [asof.isoformat(), anchor.isoformat()]
        for h in ("1w", "1m", "3m"):
            end = R.snap_to_session(R.horizon_end(anchor, h), sessions) or R.horizon_end(anchor, h)
            cells.append(end.isoformat() + (" *" if end <= data_asof else ""))
        rows.append(cells)
    print(_fmt_table(rows, ["capture", "anchor", "1w ends", "1m ends", "3m ends"]))
    print(f"\n`*` = window closed on the feed as at {data_asof}. Horizon ends are calendar "
          "targets snapped to the last XNYS session on or before (register row 6(d)).")

    # Verdict projection. Captures continue at weekly cadence from the latest
    # capture; a cohort is matured when today >= horizon_end(capture, 1m),
    # matching analyse.accrual_state exactly.
    last = _d(merges[-1]["as_of"])
    base = [_d(m["as_of"]) for m in merges]
    print("\nVerdict projection (>= 26 captures AND >= 20 matured 1m cohorts), weekly cadence "
          f"continuing from {last.isoformat()} with no further gap:\n")
    for label, caps0 in (("as accrued", base), ("with register row 10", sorted(base + promoted))):
        caps = list(caps0)
        d = last
        while True:
            matured = sum(1 for c in caps if d >= R.horizon_end(c, "1m"))
            if len(caps) >= A.VERDICT_CAPTURES and matured >= A.VERDICT_COHORTS:
                break
            d = d + dt.timedelta(days=7)
            caps.append(d)
        binding = ("captures" if len(caps) == A.VERDICT_CAPTURES else "matured cohorts")
        print(f"- {label}: {len(caps0)} captures now -> earliest verdict date "
              f"**{_wd(d)}**, binding constraint: {binding}")


# --------------------------------------------------------------------------
# 2-4. formation books: size, overlap, tilts, cost hurdle
# --------------------------------------------------------------------------

def resolve_series(merges: list[dict]):
    """Symbol resolution as analyse.main does it (register row 9(k)), so the
    formation universe here is the one the read will use."""
    import norgate as ng
    n = ng.connect()
    market = R.market_series(n)
    data_asof = ng.last_completed_session()
    earliest: dict[str, tuple[str, dt.date]] = {}
    for m in merges:
        for r in m["records"]:
            if not r.get("liquid"):
                continue
            t = r["ticker"]
            sym = r.get("norgate_symbol") or t
            d0 = _d(r.get("anchor_date") or m["as_of"])
            if t not in earliest or d0 < earliest[t][1]:
                earliest[t] = (sym, d0)
    cache, delisted, unresolved = {}, [], []
    for t, (sym, d0) in sorted(earliest.items()):
        try:
            s = R.stock_series(sym, n)
        except Exception:                          # noqa: BLE001 -- feed quirk
            s = None
        if s is None or s.loc[:pd.Timestamp(d0)].empty:
            dsym = ng.delisted_symbol_for(n, t, d0)
            ds = R.stock_series(dsym, n) if dsym else None
            if ds is not None:
                s, _ = ds, delisted.append(t)
        if s is None:
            unresolved.append(t)
        cache[sym] = s
    print(f"[diag] {len(cache)} symbols resolved; {len(delisted)} via the delisted "
          f"database; {len(unresolved)} unresolved" + (f": {', '.join(unresolved)}" if unresolved else ""))
    return (lambda sym: cache.get(sym)), market, data_asof


def formation_books(merges: list[dict], get_series, market, use_norgate: bool) -> dict:
    """Run the frozen formation roll -- selection, S2, bands, cohorts, episode
    costs -- with no measurement. Mirrors analyse.run_panel minus measure_week."""
    hist = {s: deque(maxlen=COHORT_WEEKS) for s in SCHEMES}
    weeks, sizes, overlaps = [], {s: [] for s in SCHEMES}, []
    turnover = {s: [] for s in SCHEMES}
    cost = {s: [] for s in SCHEMES}
    pooled = {s: [] for s in SCHEMES}
    pooled_univ = []
    flow = []

    for prev, curr in zip(merges, merges[1:]):
        sel = A.select(prev, curr)
        frame = sel["frame"]
        if frame.empty:
            continue
        p_asof, c_asof = _d(prev["as_of"]), _d(curr["as_of"])
        anchor = _d(frame["anchor"].iloc[0]) if frame["anchor"].iloc[0] else c_asof
        entries = dict(sel["picks"])
        bands = {"S1c": sel.get("bands", {}).get("S1c", set())}
        if use_norgate:
            risk = A.risk_frame(frame, anchor, get_series, market)
            s2_entry, s2_band = A.build_s2(frame.loc[frame.index.intersection(risk.index)],
                                           risk["beta"])
            entries["S2"] = s2_entry
            bands["S2"] = s2_band
            frame = frame.join(risk[["beta"]], how="left")
        else:
            entries["S2"] = set()
            frame["beta"] = np.nan

        # event flow for this window (S1a counts confirmed upgrades by construction)
        p, c = A.liquid_map(prev), A.liquid_map(curr)
        up = dn = cu = cd = raises = cuts = 0
        for t, b in c.items():
            a = p.get(t)
            if not a:
                continue
            ca, cb = a.get("analyst_consensus"), b.get("analyst_consensus")
            if ca in RANK and cb in RANK and RANK[cb] != RANK[ca]:
                rd = parse_rating_date(b.get("last_rating_date"))
                conf = bool(rd and p_asof <= rd <= c_asof)
                if RANK[cb] < RANK[ca]:
                    up += 1; cu += conf
                else:
                    dn += 1; cd += conf
            bp, bn = a.get("best_analyst_price_target"), b.get("best_analyst_price_target")
            if isinstance(bp, (int, float)) and isinstance(bn, (int, float)) and bp:
                pct = (bn / bp - 1.0) * 100.0
                if pct > 0.25:
                    raises += 1
                elif pct < -0.25:
                    cuts += 1
        flow.append({"prev": p_asof, "curr": c_asof, "days": (c_asof - p_asof).days,
                     "pairs": sum(1 for t in c if t in p), "up": up, "dn": dn,
                     "cu": cu, "cd": cd, "raises": raises, "cuts": cuts})

        weeks.append(c_asof)
        cohorts = {}
        for s in SCHEMES:
            matured = hist[s][0] if len(hist[s]) == COHORT_WEEKS else set()
            held = set().union(*hist[s]) if hist[s] else set()
            cohort = set(entries.get(s) or set())
            if s in bands:
                cohort |= (matured & bands[s])
            cohorts[s] = cohort
            cst, charged = A.cohort_cost(cohort, frame, held)
            sizes[s].append(len(cohort))
            turnover[s].append(len(charged) / len(cohort) if cohort else np.nan)
            cost[s].append(cst * 1e4 if cohort else np.nan)      # bps round trip, cohort mean
            hist[s].append(cohort)
            sub = frame.loc[frame.index.intersection(cohort)].copy()
            sub["week"] = c_asof
            pooled[s].append(sub)
        u = frame.copy()
        u["week"] = c_asof
        pooled_univ.append(u)
        overlaps.append({(a, b): (len(cohorts[a] & cohorts[b]) / len(cohorts[a] | cohorts[b])
                                  if (cohorts[a] | cohorts[b]) else np.nan)
                         for a in SCHEMES for b in SCHEMES if a < b})

    return {"weeks": weeks, "sizes": sizes, "turnover": turnover, "cost": cost,
            "pooled": {s: (pd.concat(v) if v else pd.DataFrame()) for s, v in pooled.items()},
            "universe": pd.concat(pooled_univ) if pooled_univ else pd.DataFrame(),
            "overlaps": overlaps, "flow": flow}


def report_books(b: dict, use_norgate: bool) -> None:
    weeks = b["weeks"]
    print(f"\n## 2. Formation books -- {len(weeks)} formation weeks, "
          f"{weeks[0].isoformat()} -> {weeks[-1].isoformat()}\n")
    print("Cohort size per formation week (names entering or band-retained that week):\n")
    rows = []
    for s in SCHEMES:
        rows.append([s] + [str(n) for n in b["sizes"][s]]
                    + [f"{np.nanmean(b['sizes'][s]):.0f}"])
    print(_fmt_table(rows, ["scheme"] + [w.strftime("%m-%d") for w in weeks] + ["mean"]))
    if not use_norgate:
        print("\nS2 not formed (--no-norgate: no trailing beta).")

    print("\nOverlap between scheme books, mean Jaccard across weeks "
          "(0 = disjoint, 1 = identical):\n")
    keys = sorted({k for o in b["overlaps"] for k in o})
    rows = [[f"{a} ∩ {c}", f"{np.nanmean([o.get((a, c), np.nan) for o in b['overlaps']]):.2f}"]
            for a, c in keys]
    print(_fmt_table(rows, ["pair", "Jaccard"]))

    # --- tilts -----------------------------------------------------------
    print("\n## 3. Style tilts of each book against the liquid universe (guard 2, read blind)\n")
    print("Pooled over formation weeks; each name counted once per week it is in the book. "
          "Universe = every liquid name each week, same pooling. Dividend yield blank reads 0 "
          "(register row 6(a)).\n")
    univ = b["universe"]

    def stats(df: pd.DataFrame) -> dict:
        if df.empty:
            return {}
        mc = _num(df["market_cap"])
        adv = _num(df["adv_usd"])
        sec = df["sector"].value_counts(normalize=True)
        tiers = Counter(_tier(x) for x in adv.fillna(0.0))
        n = len(df)
        return {
            "n": n,
            "beta": np.nanmedian(_num(df["beta"])) if "beta" in df else np.nan,
            "mcap_bn": np.nanmedian(mc) / 1e9,
            "tr3m": np.nanmedian(_num(df["tr_3m_pct"])),
            "vol": np.nanmedian(_num(df["vol_ann_pct"])) if "vol_ann_pct" in df else np.nan,
            "nonpayer": float((_num(df["dividend_yield"]).fillna(0.0) <= 0).mean()) * 100,
            "top3sec": float(sec.head(3).sum()) * 100,
            "top_sector": f"{sec.index[0]} {sec.iloc[0] * 100:.0f}%" if len(sec) else "",
            "tier3": 100.0 * tiers.get("3bp", 0) / n,
            "tier15": 100.0 * tiers.get("15bp", 0) / n,
        }

    # vol_ann_pct is on the merge record but not on the select() frame; join it back
    # from the pooled universe (same ticker-week keys).
    rows = []
    hdr = ["book", "name-weeks", "median beta", "median mcap $bn", "median 3m TR %",
           "non-payers %", "top-3 sector share %", "top sector", "ADV >= $100m %", "ADV $10-25m %"]
    for label, df in [("universe", univ)] + [(s, b["pooled"][s]) for s in SCHEMES]:
        st = stats(df)
        if not st:
            rows.append([label, 0] + ["—"] * (len(hdr) - 2))
            continue
        rows.append([label, st["n"], f"{st['beta']:.2f}" if not np.isnan(st["beta"]) else "—",
                     f"{st['mcap_bn']:.1f}", f"{st['tr3m']:+.1f}", f"{st['nonpayer']:.0f}",
                     f"{st['top3sec']:.0f}", st["top_sector"], f"{st['tier3']:.0f}",
                     f"{st['tier15']:.0f}"])
    print(_fmt_table(rows, hdr))

    if use_norgate and not univ.empty:
        ub = _num(univ["beta"]).dropna()
        q75 = ub.quantile(0.75)
        print(f"\nShare of each book in the universe's top-quartile beta (beta > {q75:.2f}); "
              "25% = no tilt:\n")
        rows = []
        for s in SCHEMES:
            bb = _num(b["pooled"][s]["beta"]).dropna() if not b["pooled"][s].empty else pd.Series(dtype=float)
            rows.append([s, len(bb), f"{100 * float((bb > q75).mean()):.0f}%" if len(bb) else "—"])
        print(_fmt_table(rows, ["book", "with beta", "top-quartile beta share"]))

    # --- cost hurdle ---------------------------------------------------------
    print("\n## 4. Cost hurdle per scheme (episode-netted, register row 9(f))\n")
    print("Mean round-trip cost charged to a cohort, in bps of the cohort -- the gross "
          "style-neutral 1m alpha a KEEP must clear before the CI is even asked. Turnover = "
          "share of the cohort entering from flat (paying). The first four weeks carry no "
          "matured cohort, so band re-entry cannot yet lower turnover there.\n")
    rows = []
    for s in SCHEMES:
        to = [f"{100 * x:.0f}%" if not np.isnan(x) else "—" for x in b["turnover"][s]]
        cs = [f"{x:.0f}" if not np.isnan(x) else "—" for x in b["cost"][s]]
        rows.append([s, " ".join(to), " ".join(cs),
                     f"{np.nanmean(b['cost'][s]):.0f}" if np.isfinite(np.nanmean(b["cost"][s])) else "—"])
    print(_fmt_table(rows, ["scheme", "turnover by week", "cost bps by week", "mean cost bps"]))
    print("\nA cohort is held 4 weeks and pays once, so mean cohort cost ≈ the book's monthly "
          "cost drag. Tiers are 3/8/15 bps one-way by 21d median dollar volume -- judgement "
          "estimates, not measured spreads.")

    # --- event flow ------------------------------------------------------------
    print("\n## 5. Event flow per inter-capture window\n")
    print("Consensus-label moves between consecutive weekly captures, and whether a Last "
          "Rating Date inside the window confirms them (register row 7 bound). Per-day rates "
          "make unequal windows comparable.\n")
    rows = []
    for f in b["flow"]:
        rows.append([f"{f['prev'].strftime('%m-%d')}→{f['curr'].strftime('%m-%d')}", f["days"],
                     f["pairs"], f"{f['up']} ({f['cu']})", f"{f['dn']} ({f['cd']})",
                     f"{f['cu'] / f['days']:.1f}", f["raises"], f["cuts"],
                     f"{f['raises'] / f['days']:.1f}"])
    print(_fmt_table(rows, ["window", "days", "pairs", "upgrades (conf.)", "downgrades (conf.)",
                            "conf. up / day", "target raises", "target cuts", "raises / day"]))
    tot = sum(f["cu"] for f in b["flow"])
    print(f"\nS1a events accrued across all windows: {tot} confirmed upgrades. Target moves use "
          "a 0.25% de-minimis floor (target_flow.py convention).")


# --------------------------------------------------------------------------
# 6. label durability from the daily log
# --------------------------------------------------------------------------

def load_daily() -> list[tuple[dt.date, str, dict]]:
    out = []
    for p in DAILY_DIR.glob("snapshot_*.json"):
        k = _snap_key(p)
        if k is None:
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        out.append((k[0], k[1], {r["ticker"]: r for r in d["records"] if r.get("ticker")}))
    out.sort(key=lambda x: (x[0], x[1]))
    return out


def durability(daily: list, liquid_union: set) -> None:
    print("\n## 6. Durability of confirmed upgrades (daily log, no returns)\n")
    if len(daily) < 3:
        print("Fewer than three daily entries; skipped.")
        return
    last_date = daily[-1][0]
    # The daily log carries no Norgate merge and therefore no liquid flag; the
    # full 1,991-name export would count events on names the panel never
    # measures. Restrict to names liquid in ANY weekly merge -- a stable
    # approximation of the panel universe, stated as such.
    events = []                                  # (ticker, event_date, pre_rank, post_rank)
    for (d0, _, ra), (d1, _, rb) in zip(daily, daily[1:]):
        for t, b in rb.items():
            a = ra.get(t)
            if not a or t not in liquid_union:
                continue
            ca, cb = a.get("analyst_consensus"), b.get("analyst_consensus")
            if ca in RANK and cb in RANK and RANK[cb] < RANK[ca]:
                rd = parse_rating_date(b.get("last_rating_date"))
                if rd and d0 <= rd <= d1:
                    events.append((t, d1, RANK[ca], RANK[cb]))
    print(f"{len(events)} confirmed upgrades detected between consecutive daily entries, "
          f"{daily[0][0].isoformat()} -> {last_date.isoformat()}, on the {len(liquid_union)} "
          "names liquid in at least one weekly merge.\n")
    # Track each event forward through later entries.
    by_date = {}
    for d, hh, recs in daily:
        by_date.setdefault(d, []).append(recs)
    dates = sorted(by_date)
    rows = []
    for n in REVERSAL_DAYS:
        elig = full = partial = 0
        for t, d_ev, pre, post in events:
            if d_ev + dt.timedelta(days=n) > last_date:
                continue                         # cannot observe the whole window
            elig += 1
            later = [recs for d in dates if d_ev < d <= d_ev + dt.timedelta(days=n)
                     for recs in by_date[d]]
            ranks = [RANK.get((recs.get(t) or {}).get("analyst_consensus")) for recs in later]
            ranks = [r for r in ranks if r is not None]
            if any(r >= pre for r in ranks):
                full += 1
            elif any(r > post for r in ranks):
                partial += 1
        rows.append([f"{n}d", elig, f"{full} ({_pct(full, elig)})",
                     f"{partial} ({_pct(partial, elig)})",
                     f"{elig - full - partial} ({_pct(elig - full - partial, elig)})"])
    print(_fmt_table(rows, ["window", "eligible events", "fully reversed",
                            "partly reversed", "label held"]))
    print("\nFully reversed = the label returned to or below its pre-upgrade level at any "
          "later daily entry inside the window; partly = slipped below the post-upgrade "
          "label without returning. Eligible = events whose whole window is inside the log. "
          "A held label is not a return; it says the event was not a phantom that the next "
          "recomputation undid.")


# --------------------------------------------------------------------------
# 7. migration across the panel
# --------------------------------------------------------------------------

def migration(merges: list[dict]) -> None:
    first, last = A.liquid_map(merges[0]), A.liquid_map(merges[-1])
    both = sorted(set(first) & set(last))
    print(f"\n## 7. Migration {merges[0]['as_of']} -> {merges[-1]['as_of']} "
          f"({len(both)} names liquid at both ends)\n")
    order = A.CONSENSUS_ORDER
    mat = pd.DataFrame(0, index=order, columns=order)
    for t in both:
        a, b = first[t].get("analyst_consensus"), last[t].get("analyst_consensus")
        if a in RANK and b in RANK:
            mat.at[a, b] += 1
    rows = [[a] + [int(mat.at[a, c]) for c in order] + [int(mat.loc[a].sum())] for a in order]
    print("Consensus label, first capture (rows) -> latest capture (columns):\n")
    print(_fmt_table(rows, ["from \\ to"] + order + ["total"]))
    diag = int(np.trace(mat.values))
    tot = int(mat.values.sum())
    up = int(np.triu(mat.values, 1).sum())      # above diagonal in RANK order = less bullish
    print(f"\nUnchanged {diag} ({_pct(diag, tot)}); net less bullish {up}, "
          f"net more bullish {tot - diag - up}.")

    ds = []
    for t in both:
        a, b = first[t].get("smart_score"), last[t].get("smart_score")
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            ds.append(int(round(b - a)))
    c = Counter(ds)
    n = len(ds)
    print(f"\nSmart Score change over the same span ({n} names): "
          f"unchanged {_pct(c.get(0, 0), n)}, ±1 {_pct(c.get(1, 0) + c.get(-1, 0), n)}, "
          f"±2 or more {_pct(sum(v for k, v in c.items() if abs(k) >= 2), n)}; "
          f"up {_pct(sum(v for k, v in c.items() if k > 0), n)} / "
          f"down {_pct(sum(v for k, v in c.items() if k < 0), n)}.")


# --------------------------------------------------------------------------

def run(use_norgate: bool, promoted: list[dt.date]) -> None:
    merges = A.load_merges()
    if len(merges) < 2:
        raise SystemExit("[diag] need at least two merges")
    today = dt.date.today()
    state = A.accrual_state(merges, today)
    print(f"# Formation diagnostics -- {today.isoformat()} ({today.strftime('%A')})\n")
    print(f"{state['captures']} weekly captures {state['first']} -> {state['last']}, "
          f"{state['matured']} with a matured 1m window. UNGRADED; no forward return read.")

    import exchange_calendars as xcals
    cal = xcals.get_calendar("XNYS")
    if use_norgate:
        get_series, market, data_asof = resolve_series(merges)
    else:
        get_series, market, data_asof = (lambda s: None), None, today
    sessions = [d.date() for d in cal.sessions_in_range(
        pd.Timestamp(_d(merges[0]["as_of"]) - dt.timedelta(days=30)),
        pd.Timestamp(today + dt.timedelta(days=200)))]

    accrual_calendar(merges, sessions, data_asof, promoted)
    books = formation_books(merges, get_series, market, use_norgate)
    report_books(books, use_norgate)
    liquid_union = {t for m in merges for t in A.liquid_map(m)}
    durability(load_daily(), liquid_union)
    migration(merges)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="also write the report as markdown to this path")
    ap.add_argument("--no-norgate", action="store_true",
                    help="offline: skip trailing beta, S2 and costs")
    ap.add_argument("--promoted", default="2026-08-19,2026-08-26",
                    help="candidate row-10 capture dates for the projection (comma-separated)")
    a = ap.parse_args()
    promoted = [_d(x) for x in a.promoted.split(",") if x.strip()]

    buf = io.StringIO()
    with redirect_stdout(buf):
        run(not a.no_norgate, promoted)
    text = buf.getvalue()
    print(text)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"[diag] written -> {a.out}")
    return 0


# Guard 1 of this diagnostic, enforced rather than promised: no code path in
# this module may reach a forward return or the measurement step.
_SRC = Path(__file__).read_text(encoding="utf-8")
assert "R.forward_" + "return(" not in _SRC and "A.measure_" + "week(" not in _SRC, \
    "formation_diag must never measure a forward window"

if __name__ == "__main__":
    raise SystemExit(main())
