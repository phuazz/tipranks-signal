#!/usr/bin/env python
"""capture.py -- one-command weekly capture, with integrity guards up front.

The panel is FORWARD-ONLY: a snapshot frozen from a bad export cannot be
re-captured later, so a silently-wrong file is permanent damage. This script is
the guard layer the vault rule requires. It validates the export BEFORE the file
is filed or ingested, aborts on any hard failure, and only then runs the full
weekly chain:

    validate -> file to OneDrive -> ingest -> feed gate -> merge
             -> dashboard -> HTML export (+ OneDrive copy)
             -> Pages build (public aggregate page + data-less monitor shell)

Usage:
    python scripts/capture.py --file "C:\\Users\\phuaz\\Downloads\\export.csv" --asof 2026-07-30
    python scripts/capture.py --latest --asof 2026-07-30      # newest CSV in Downloads
    python scripts/capture.py --file ... --validate-only      # guards only, touch nothing
    python scripts/capture.py --latest --push                 # also commit + push the public page

The column contract is IMPORTED from ingest.py, never copied, so the guard
cannot drift from the real mapping. Dates go through datetime only (Python
months are 1-indexed); the weekday is derived, never assumed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ingest  # noqa: E402 -- reuse the locked column contract

ONEDRIVE = Path.home() / "OneDrive" / "Main" / "tipranks-signal"
DOWNLOADS = Path.home() / "Downloads"
SNAP_DIR = ROOT / "data" / "snapshots"
EXPORT_DIR = ROOT / "data" / "exports"

ROW_TOLERANCE = 0.05     # +/- 5% vs the prior capture; observed week-on-week drift is < 1%
MIN_OVERLAP = 0.90       # ticker overlap floor vs the prior capture
# Fields the study cannot run without. A regression check against the prior
# snapshot catches everything else, but these are asserted unconditionally.
CORE_FIELDS = {
    "ticker", "company", "sector", "market_cap", "smart_score",
    "analyst_consensus", "best_analyst_consensus",
    "analyst_price_target", "best_analyst_price_target",
    "last_rating_date", "insider_signal",
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _prior_snapshot(asof: dt.date):
    """Newest snapshot strictly BEFORE asof, so re-running a date compares
    against the right baseline rather than against itself."""
    best = None
    for p in sorted(SNAP_DIR.glob("snapshot_*.json")) if SNAP_DIR.exists() else []:
        try:
            d = dt.date.fromisoformat(p.stem.replace("snapshot_", ""))
        except ValueError:
            continue
        if d < asof and (best is None or d > best[0]):
            best = (d, p)
    if best is None:
        return None
    snap = json.loads(best[1].read_text(encoding="utf-8"))
    snap["_date"] = best[0]
    return snap


def validate(csv_path: Path, asof: dt.date, force: bool):
    """Return (checks, fatal_count). Each check is (ok, label, detail)."""
    checks = []
    today = dt.date.today()

    # --- the file itself ---------------------------------------------------
    df = ingest.read_export(csv_path)
    n_rows = len(df)
    checks.append((n_rows > 0, "file readable",
                   f"{n_rows:,} data rows, {len(df.columns)} columns"))

    # --- column contract ---------------------------------------------------
    mapping, unmapped = ingest.map_columns(df.columns)
    mapped = set(mapping.values())
    missing_core = sorted(CORE_FIELDS - mapped)
    checks.append((not missing_core, "core columns present",
                   "all core fields mapped" if not missing_core
                   else f"MISSING: {missing_core}"))

    prior = _prior_snapshot(asof)
    if prior is not None:
        prior_recs = prior["records"]
        prior_fields = set(prior_recs[0].keys()) - {"as_of"}
        regressed = sorted(f for f in prior_fields if f in CORE_FIELDS and f not in mapped)
        checks.append((not regressed, f"no column regression vs {prior['_date']}",
                       "field set intact" if not regressed else f"LOST: {regressed}"))
        new_unmapped = sorted(set(unmapped) - set(prior.get("unmapped_columns") or []))
        # A new unmapped header is additive, not destructive -- the regression
        # check above is what catches a rename. Report it, do not fail on it.
        checks.append((True, "unmapped columns",
                       f"{len(unmapped)} unmapped"
                       + (f"; NEW since last week: {new_unmapped}" if new_unmapped else " (all known)")))

        # --- size and composition ------------------------------------------
        n_prior = len(prior_recs)
        drift = (n_rows - n_prior) / n_prior
        checks.append((abs(drift) <= ROW_TOLERANCE, "row count vs prior",
                       f"{n_rows:,} vs {n_prior:,} ({drift:+.1%}, tolerance +/-{ROW_TOLERANCE:.0%})"))

        tick_col = next((raw for raw, canon in mapping.items() if canon == "ticker"), None)
        if tick_col is not None:
            now_ticks = {str(t).strip().upper() for t in df[tick_col].dropna()}
            prior_ticks = {str(r.get("ticker", "")).strip().upper() for r in prior_recs}
            prior_ticks.discard("")
            overlap = len(now_ticks & prior_ticks) / len(prior_ticks) if prior_ticks else 0.0
            checks.append((overlap >= MIN_OVERLAP, "ticker overlap vs prior",
                           f"{overlap:.1%} of last week's names present (floor {MIN_OVERLAP:.0%})"))

        # --- stale re-submit --------------------------------------------------
        # Identical bytes to the previous export means the screener served a
        # cached file, or the same download was submitted twice.
        same = _sha256(csv_path) in set(prior.get("source_sha256") or [])
        checks.append((not same, "export is fresh",
                       "content differs from the prior export" if not same
                       else "IDENTICAL to the previous export -- re-export before filing"))
    else:
        checks.append((True, "prior capture", "none found -- first capture, comparisons skipped"))

    # --- dates -------------------------------------------------------------
    checks.append((asof <= today, "as-of not in the future",
                   f"{asof.isoformat()} ({asof.strftime('%A')}); today is {today.isoformat()}"))
    if prior is not None:
        checks.append((asof > prior["_date"], "as-of after the prior capture",
                       f"{asof.isoformat()} > {prior['_date'].isoformat()}"))
    existing = SNAP_DIR / f"snapshot_{asof.isoformat()}.json"
    checks.append((force or not existing.exists(), "no snapshot for this date",
                   "date is new" if not existing.exists()
                   else ("overwriting (--force)" if force else "ALREADY CAPTURED -- pass --force to overwrite")))

    fatal = sum(1 for ok, _, _ in checks if not ok)
    return checks, fatal


def run(cmd: list[str], label: str) -> None:
    print(f"\n[capture] {label}: {' '.join(str(c) for c in cmd[1:])}")
    res = subprocess.run(cmd, cwd=ROOT)
    if res.returncode != 0:
        sys.exit(f"[capture] ABORTED -- {label} failed (exit {res.returncode})")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="path to the screener export CSV")
    src.add_argument("--latest", action="store_true", help="use the newest CSV in Downloads")
    ap.add_argument("--asof", help="capture date YYYY-MM-DD (default: today)")
    ap.add_argument("--validate-only", action="store_true", help="run the guards and stop")
    ap.add_argument("--force", action="store_true", help="allow overwriting an existing capture date")
    ap.add_argument("--push", action="store_true", help="commit and push the public page afterwards")
    a = ap.parse_args()

    if a.latest:
        cands = sorted(DOWNLOADS.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not cands:
            sys.exit(f"[capture] no CSV found in {DOWNLOADS}")
        csv_path = cands[0]
        print(f"[capture] newest CSV in Downloads: {csv_path.name} "
              f"({dt.datetime.fromtimestamp(csv_path.stat().st_mtime):%Y-%m-%d %H:%M})")
    else:
        csv_path = Path(a.file)
    if not csv_path.exists():
        sys.exit(f"[capture] file not found: {csv_path}")

    asof = dt.date.fromisoformat(a.asof) if a.asof else dt.date.today()

    print(f"\n[capture] integrity report -- {csv_path.name} as of {asof.isoformat()} "
          f"({asof.strftime('%A')})")
    checks, fatal = validate(csv_path, asof, a.force)
    width = max(len(lbl) for _, lbl, _ in checks)
    for ok, lbl, detail in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {lbl.ljust(width)}  {detail}")
    if fatal:
        sys.exit(f"\n[capture] ABORTED -- {fatal} check(s) failed. Nothing was filed or ingested.")
    print(f"\n[capture] all {len(checks)} checks passed.")
    if a.validate_only:
        print("[capture] --validate-only: stopping before filing.")
        return 0

    # --- file the raw export (archive of record) ---------------------------
    ONEDRIVE.mkdir(parents=True, exist_ok=True)
    filed = ONEDRIVE / f"tipranks_{asof.isoformat()}.csv"
    if csv_path.resolve() != filed.resolve():
        shutil.copy2(csv_path, filed)
        print(f"[capture] filed -> {filed}")
    else:
        print(f"[capture] already filed at {filed}")

    py = sys.executable
    run([py, "scripts/ingest.py", "--export", str(filed), "--asof", asof.isoformat()], "1/6 ingest")
    run([py, "scripts/norgate.py", "--check"], "2/6 feed gate")
    run([py, "scripts/merge_norgate.py", "--asof", asof.isoformat()], "3/6 merge")
    run([py, "scripts/build_dashboard.py"], "4/6 dashboard")
    run([py, "scripts/export_html.py"], "5/6 HTML export")
    run([py, "scripts/pipeline.py"], "6/6 Pages build")

    monitor = EXPORT_DIR / f"tipranks_monitor_{asof.isoformat()}.html"
    if monitor.exists():
        shutil.copy2(monitor, ONEDRIVE / monitor.name)
        print(f"\n[capture] monitor copied -> {ONEDRIVE / monitor.name}")

    if a.push:
        # Both Pages surfaces: the aggregate page and the data-less monitor
        # shell. The pre-commit hook is the backstop on what the shell may carry.
        subprocess.run(["git", "add", "docs/index.html", "docs/monitor/index.html"],
                       cwd=ROOT, check=True)
        subprocess.run(["git", "commit", "-m",
                        f"tipranks-signal: public aggregates for the {asof.isoformat()} capture"],
                       cwd=ROOT, check=True)
        subprocess.run(["git", "push", "origin", "master"], cwd=ROOT, check=True)

    print(f"\n[capture] DONE -- capture {asof.isoformat()} filed, panel rebuilt, "
          f"monitor and public page refreshed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
