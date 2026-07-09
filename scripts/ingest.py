#!/usr/bin/env python
"""ingest.py -- normalise one weekly TipRanks Excel export into a frozen,
point-in-time snapshot JSON.

Each run reads one or more manual export pages (TipRanks Ultimate has no API; the
Excel export is the sanctioned bulk pull, and the screener caps a page at 500
rows, so a large universe is exported as several pages and concatenated here),
maps columns to a stable schema, validates, and writes
data/snapshots/snapshot_<asof>.json. Snapshots are FROZEN at capture and never
revised -- that is the whole forward test: no past date is ever backfilled from a
later view of the site (guard against signal-side look-ahead).

IP firewall: the raw .xlsx lives in OneDrive; data/ is gitignored so vendor
values stay local. Only OUR aggregate derived numbers may ever be published.

Dates: as-of parsed with datetime (Python months are 1-indexed); weekday read
from the library, never from memory. `--selftest` covers a month boundary and a
year boundary.

    python scripts/ingest.py --export "<OneDrive>\\tipranks_2026-07-09.xlsx" --asof 2026-07-09
    python scripts/ingest.py --export "<...>_p1.xlsx" "<...>_p2.xlsx" --asof 2026-07-09   # pages
    python scripts/ingest.py --export "<OneDrive>\\tipranks_2026-07-09\\"  --asof 2026-07-09   # a folder of pages
    python scripts/ingest.py --selftest
"""
from __future__ import annotations

import argparse
import datetime as dt   # Python datetime: months are 1-indexed (Jan == 1)
import hashlib
import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SNAP_DIR = ROOT / "data" / "snapshots"            # data/ is gitignored
SCHEMA_VERSION = 1

# canonical field -> ordered list of lowercased header substrings (first match
# wins; each canonical is claimed once). Headers CONFIRMED against a real CSV
# export on 2026-07-09 (26 columns). Order matters: the best-analyst and the
# upside-% columns MUST precede the generic ones, or they collide (every header
# contains the next as a substring: "Analyst Price Target %" contains "Analyst
# Price Target"; "Best Analyst Consensus" contains "Analyst Consensus"; several
# contain "Price"). Unmapped columns (Volume, Avg. Volume (3M)) are logged and
# ignored -- liquidity comes from Norgate, not TipRanks.
COLUMN_MAP = {
    # identity
    "ticker":                          ["ticker", "symbol"],
    "company":                         ["name", "company"],
    # best-analyst (analyst-quality-weighted) signals -- MOST specific, first
    "best_analyst_price_target":       ["best price target"],            # "Best Price Target Upside" = $ value
    "best_analyst_upside_pct":         ["upside/downside"],              # "Top Analysts' Price Target - upside/downside"
    "best_analyst_consensus":          ["best analyst consensus"],
    "most_accurate_analyst":           ["most accurate analyst"],
    "most_profitable_analyst":         ["most profitable analyst"],
    # all-analyst signals -- upside % BEFORE the bare target, or it is swallowed
    "analyst_price_target_upside_pct": ["analyst price target %"],
    "analyst_price_target":            ["analyst price target"],
    "analyst_consensus":               ["analyst consensus"],           # after best_analyst_consensus
    "last_rating_date":                ["last rating date"],
    "smart_score":                     ["smart score"],
    # secondary sentiment / flow signals
    "investor_sentiment":              ["investor sentiment"],
    "insider_signal":                  ["insider"],
    "news_sentiment":                  ["news sentiment"],
    "hedgefund_signal":                ["hedge fund"],
    "blogger_sentiment":               ["blogger"],
    "media_buzz":                      ["media buzz"],
    # context + style controls (not signals; used to neutralise style tilts)
    "sector":                          ["sector"],
    "dividend_yield":                  ["dividend yield"],
    "pe_ratio":                        ["p/e ratio"],
    "market_cap":                      ["market cap"],
    "price":                           ["price"],
    "change_pct":                      ["change %"],
}
NUMERIC_FIELDS = {"best_analyst_price_target", "best_analyst_upside_pct",
                  "analyst_price_target_upside_pct", "analyst_price_target",
                  "smart_score", "dividend_yield", "pe_ratio", "market_cap",
                  "price", "change_pct"}
_MAG = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _to_number(val):
    """First numeric value in a TipRanks cell, sign- and magnitude-aware:
    '$1.35M' -> 1.35e6, '$4.94T' -> 4.94e12, '615.53% Upside' -> 615.53,
    '(0.64%)' -> -0.64, '293.86% Downside' -> -293.86, '-2.42%' -> -2.42, and a
    combined '$309.33 (51.54% Upside)' -> 309.33 (the target; upside is derived
    downstream). Returns None on blanks / 'n/a'."""
    if val is None:
        return None
    s = str(val).strip()
    if s == "" or s.lower() in {"n/a", "na", "-", "--", "nan"}:
        return None
    neg = (s.startswith("(") and s.endswith(")")) or "downside" in s.lower() or s.startswith("-")
    m = _NUM_RE.search(s)
    if not m:
        return None
    num = float(m.group(0).replace(",", ""))
    tail = s[m.end():].lstrip()                   # magnitude suffix right after the number
    num *= _MAG.get(tail[:1].upper(), 1.0) if tail else 1.0
    return -abs(num) if neg else num


def map_columns(cols):
    """Return (mapping {raw: canonical}, unmapped [raw...]). First match wins; a
    canonical field is claimed at most once."""
    mapping, claimed, unmapped = {}, set(), []
    for raw in cols:
        low = str(raw).strip().lower()
        hit = None
        for canon, subs in COLUMN_MAP.items():
            if canon in claimed:
                continue
            if any(sub in low for sub in subs):
                hit = canon
                break
        if hit:
            mapping[raw] = hit
            claimed.add(hit)
        else:
            unmapped.append(str(raw))
    return mapping, unmapped


def read_export(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"export not found: {path}")
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, encoding="utf-8-sig")   # TipRanks CSV carries a BOM
    else:
        df = pd.read_excel(path, engine="openpyxl")
    return df.dropna(how="all")


def build_records(df: pd.DataFrame, asof: dt.date):
    mapping, unmapped = map_columns(df.columns)
    canon = df.rename(columns=mapping)
    if "ticker" not in canon.columns:
        raise ValueError(
            f"no ticker/symbol column found. Columns seen: {list(df.columns)}. "
            "Extend COLUMN_MAP['ticker'] to match this export."
        )
    records = []
    for _, row in canon.iterrows():
        rec = {"as_of": asof.isoformat()}
        for canon_field in COLUMN_MAP:
            if canon_field in canon.columns:
                v = row[canon_field]
                if canon_field in NUMERIC_FIELDS:
                    rec[canon_field] = _to_number(v)
                else:
                    rec[canon_field] = None if pd.isna(v) else str(v).strip()
        tick = rec.get("ticker")
        if not tick or str(tick).strip().lower() == "nan":
            continue
        rec["ticker"] = str(tick).strip().upper()
        records.append(rec)
    return records, unmapped


def _dedupe(records):
    seen, out, dups = set(), [], []
    for r in records:
        t = r["ticker"]
        if t in seen:
            dups.append(t)
            continue
        seen.add(t)
        out.append(r)
    return out, dups


def expand_exports(paths) -> list[Path]:
    """Accept files and/or a directory of page-files. A directory expands to its
    sorted *.xlsx, so a multi-page export dropped in one folder just works."""
    files: list[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files.extend(sorted([*p.glob("*.csv"), *p.glob("*.xlsx")]))
        else:
            files.append(p)
    seen, out = set(), []
    for f in files:
        key = str(f.resolve()).lower()            # Windows paths are case-insensitive
        if key not in seen:
            seen.add(key)
            out.append(f)
    if not out:
        raise FileNotFoundError(f"no .xlsx export found in: {list(paths)}")
    return out


def ingest(exports, asof: dt.date) -> Path:
    files = expand_exports(exports)
    frames, shas = [], {}
    for f in files:
        if not f.exists():
            raise FileNotFoundError(f"export not found: {f}")
        frames.append(read_export(f))
        shas[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
    df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    records, unmapped = build_records(df, asof)
    records, dups = _dedupe(records)              # de-dupe overlap across pages
    weekday = asof.strftime("%A")                 # weekday from the library, never memory
    payload = {
        "schema_version": SCHEMA_VERSION,
        "as_of": asof.isoformat(),
        "as_of_weekday": weekday,
        "as_of_is_weekend": asof.weekday() >= 5,   # Mon=0 .. Sun=6
        "source_files": [f.name for f in files],
        "source_sha256": shas,
        "n_source_files": len(files),
        "n_rows": len(records),
        "unmapped_columns": unmapped,
        "duplicate_tickers_dropped": dups,
        "records": records,
    }
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    out = SNAP_DIR / f"snapshot_{asof.isoformat()}.json"
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    src = f"{len(files)} page-files" if len(files) > 1 else files[0].name
    print(f"[ingest] {len(records)} names from {src} -> {out.relative_to(ROOT)}  ({weekday})")
    if unmapped:
        print(f"[ingest] UNMAPPED columns (extend COLUMN_MAP): {unmapped}", file=sys.stderr)
    if payload["as_of_is_weekend"]:
        print("[ingest] NOTE: as-of is a weekend; signals reflect the last trading day.",
              file=sys.stderr)
    if dups:
        print(f"[ingest] de-duped {len(dups)} tickers overlapping across pages", file=sys.stderr)
    return out


def selftest() -> int:
    # --- date edge cases (library only; no memory weekdays) ---
    # month boundary: February only has 29 days in a leap year
    assert dt.date.fromisoformat("2024-02-29").month == 2
    rejected = False
    try:
        dt.date.fromisoformat("2023-02-29")       # not a leap year -> invalid
    except ValueError:
        rejected = True
    assert rejected, "2023-02-29 should be rejected (month/leap boundary)"
    # year boundary: 2024-12-31 -> 2025-01-01 is exactly one day
    assert (dt.date(2025, 1, 1) - dt.date(2024, 12, 31)).days == 1

    # --- number parsing ---
    assert _to_number("$1.35M") == 1.35e6
    assert _to_number("615.53% Upside") == 615.53
    assert _to_number("(0.64%)") == -0.64
    assert _to_number("293.86% Downside") == -293.86
    assert _to_number("$885.30M") == 885.30e6
    assert _to_number("$4.94T") == 4.94e12
    assert _to_number("$309.33 (51.54% Upside)") == 309.33   # combined cell -> take the target
    assert _to_number("-2.42%") == -2.42
    assert _to_number("$4,939,703,881,836") == 4939703881836.0   # full CSV market cap w/ commas
    assert _to_number("-1.45%") == -1.45
    assert _to_number("n/a") is None

    # --- column mapping + record build against the REAL export headers ---
    df = pd.DataFrame({
        "Ticker": ["nvda", "aapl"],
        "Name": ["Nvidia", "Apple"],
        "Price": ["$201.16", "$310.86"],
        "Change %": ["-1.45%", "-0.81%"],
        "Analyst Price Target %": ["53.77%", "4.61%"],
        "Analyst Price Target": ["$309.33", "$325.20"],
        "Analyst Consensus": ["Strong Buy", "Moderate Buy"],
        "Smart Score": ["9.0", "9.0"],
        "Market Cap": ["$4,939,703,881,836", "$4,602,870,851,543"],
        "Dividend Yield %": ["0.14%", "0.34%"],
        "P/E Ratio": ["31.12", "37.80"],
        "Best Price Target Upside": ["$310.00", "$330.71"],
        "Top Analysts' Price Target - upside/downside": ["54.10%", "6.39%"],
        "Best Analyst Consensus": ["Moderate Buy", "Hold"],
        "Last Rating Date": ["Jul 08, 2026", "Jul 09, 2026"],
        "Volume": ["41,527,325.00", "7,996,624.00"],
    })
    recs, unmapped = build_records(df, dt.date(2024, 12, 31))
    r0 = recs[0]
    assert r0["ticker"] == "NVDA"
    assert r0["company"] == "Nvidia"                              # from "Name"
    assert r0["price"] == 201.16                                  # not swallowed by a target column
    assert r0["change_pct"] == -1.45
    assert r0["analyst_price_target"] == 309.33                   # $ value
    assert r0["analyst_price_target_upside_pct"] == 53.77         # distinct % column
    assert r0["best_analyst_price_target"] == 310.00             # distinct from all-analyst target
    assert r0["best_analyst_upside_pct"] == 54.10
    assert r0["analyst_consensus"] == "Strong Buy"
    assert r0["best_analyst_consensus"] == "Moderate Buy"         # distinct from all-analyst consensus
    assert r0["last_rating_date"] == "Jul 08, 2026"
    assert r0["smart_score"] == 9.0
    assert r0["dividend_yield"] == 0.14
    assert r0["pe_ratio"] == 31.12
    assert r0["market_cap"] == 4939703881836.0                    # full CSV integer with commas
    assert "Volume" in unmapped                                  # deliberately not mapped
    print("[selftest] OK -- date, number, and mapping checks pass")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--export", nargs="+", metavar="PATH",
                    help="one or more .xlsx export pages, or a folder of page-files")
    ap.add_argument("--asof", help="capture date YYYY-MM-DD (default: today)")
    ap.add_argument("--selftest", action="store_true", help="run edge-case tests and exit")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    if args.selftest:
        return selftest()
    if not args.export:
        ap.error("--export is required (or use --selftest)")
    asof = dt.date.fromisoformat(args.asof) if args.asof else dt.date.today()
    ingest(args.export, asof)
    return 0


if __name__ == "__main__":
    sys.exit(main())
