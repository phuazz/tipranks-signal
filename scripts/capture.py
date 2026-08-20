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
    python scripts/capture.py --latest --asof 2026-07-30      # newest CSV in Downloads or the OneDrive project folder
    python scripts/capture.py --file ... --validate-only      # guards only, touch nothing
    python scripts/capture.py --latest --push                 # also commit + push the public page
    python scripts/capture.py --latest --daily                # daily event log entry (~15s, panel untouched)

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
from zoneinfo import ZoneInfo

SGT = ZoneInfo("Asia/Singapore")   # capture stamps are Singapore wall-clock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ingest  # noqa: E402 -- reuse the locked column contract

ONEDRIVE = Path.home() / "OneDrive" / "Main" / "tipranks-signal"
DOWNLOADS = Path.home() / "Downloads"
# --latest searches both, newest wins: the browser saves to Downloads, but an
# export moved to the project's OneDrive folder before capture is just as valid
# a source and was previously invisible to --latest.
LATEST_DIRS = (DOWNLOADS, ONEDRIVE)
# Filed archives live in ONEDRIVE under this prefix (see the filing steps in
# main()). They must never be offered back as a fresh export -- re-capturing a
# filed file would be a silent duplicate of a capture already in the panel.
FILED_PREFIX = "tipranks_"
SNAP_DIR = ROOT / "data" / "snapshots"
EXPORT_DIR = ROOT / "data" / "exports"
# --- daily event log ------------------------------------------------------
# A SEPARATE stream from the frozen weekly panel. Its purpose is event-time
# precision: the export stamps Last Rating Date, so S1a rating events are
# already dated to the day, but TARGET revisions carry no date and are smeared
# across the capture window. Daily captures narrow that smear and expose
# intra-week reversals the weekly panel nets out. Nothing here is graded, and
# a missed day costs only precision on events inside the gap -- which is
# exactly why this lives outside data/snapshots/ and cannot contaminate it.
DAILY_SNAP_DIR = ROOT / "data" / "daily"
DAILY_ONEDRIVE = ONEDRIVE / "daily"

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


def _snap_key(p: Path):
    """(date, time) sort key from snapshot_<date>[_<HHMM>].json. Daily-log
    entries carry the capture time so several a day can coexist and order
    correctly; weekly panel files have an empty time component."""
    s = p.stem.replace("snapshot_", "")
    try:
        return (dt.date.fromisoformat(s[:10]), s[11:] if len(s) > 10 else "")
    except ValueError:
        return None


def _prior_snapshot(asof_key, snap_dir: Path = SNAP_DIR, fallback_dir: Path | None = None):
    """Newest snapshot strictly BEFORE asof_key (a (date, time) tuple), so a
    re-run compares against the right baseline rather than against itself.
    Daily mode searches the daily log first and falls back to the weekly panel
    until the log has its own history."""
    for d_dir in [snap_dir] + ([fallback_dir] if fallback_dir else []):
        best = None
        for p in d_dir.glob("snapshot_*.json") if d_dir.exists() else []:
            k = _snap_key(p)
            if k is None or k >= asof_key:
                continue
            if best is None or k > best[0]:
                best = (k, p)
        if best is not None:
            snap = json.loads(best[1].read_text(encoding="utf-8"))
            snap["_date"] = best[0][0]
            snap["_key"] = best[0]
            return snap
    return None


def validate(csv_path: Path, asof: dt.date, force: bool,
             snap_dir: Path = SNAP_DIR, fallback_dir: Path | None = None,
             snap_name: str | None = None, asof_time: str = ""):
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

    prior = _prior_snapshot((asof, asof_time), snap_dir, fallback_dir)
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
        # source_sha256 is {filename: sha} -- compare against the VALUES.
        same = _sha256(csv_path) in set((prior.get("source_sha256") or {}).values())
        checks.append((not same, "export is fresh",
                       "content differs from the prior export" if not same
                       else "IDENTICAL to the previous export -- re-export before filing"))
    else:
        checks.append((True, "prior capture", "none found -- first capture, comparisons skipped"))

    # --- dates -------------------------------------------------------------
    checks.append((asof <= today, "as-of not in the future",
                   f"{asof.isoformat()} ({asof.strftime('%A')}); today is {today.isoformat()}"))
    if prior is not None:
        # Daily entries compare on (date, time), so a same-day later capture is
        # correctly "after" the earlier one rather than a duplicate.
        newer = (asof, asof_time) > prior["_key"]
        checks.append((newer, "capture is after the prior one",
                       f"{asof.isoformat()}{' ' + asof_time if asof_time else ''} > "
                       f"{prior['_date'].isoformat()}"
                       f"{' ' + prior['_key'][1] if prior['_key'][1] else ''}"))
    existing = snap_dir / (snap_name or f"snapshot_{asof.isoformat()}.json")
    checks.append((force or not existing.exists(), "not already captured",
                   "new entry" if not existing.exists()
                   else ("overwriting (--force)" if force else "ALREADY CAPTURED -- pass --force to overwrite")))

    fatal = sum(1 for ok, _, _ in checks if not ok)
    return checks, fatal


def _session_bounds(cal, sessions: list[dt.date]) -> dict:
    """{session date: (open, close)} in SGT, read off the exchange calendar.

    Derived, never hard-coded. The US open lands at 21:30 SGT under EDT but
    22:30 under EST, and the close moves with it -- 04:00 SGT the next morning
    in summer, 05:00 in winter, 02:00 after a 13:00 ET early close. A fixed
    clock constant is right for about eight months of the year and silently
    wrong for the rest, which is the failure mode this study cannot see.
    """
    def sgt(ts):
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts
        return ts.tz_convert(SGT).to_pydatetime()

    return {s: (sgt(cal.session_open(s)), sgt(cal.session_close(s))) for s in sessions}


def _capture_instant(d: dt.date, hhmm: str | None):
    """SGT instant of a capture, or None when the entry carries no time.
    Python months are 1-indexed; the date is taken from the parsed key, never
    re-derived."""
    if not hhmm:
        return None
    return dt.datetime(d.year, d.month, d.day, int(hhmm[:2]), int(hhmm[2:]), tzinfo=SGT)


def _anchor_session(d: dt.date, sessions: list[dt.date], hhmm: str | None = None,
                    bounds: dict | None = None):
    """The US session a capture reflects: the last one that had CLOSED at the
    capture instant, judged against that session's own close.

    An untimed entry falls back to the calendar-day rule -- the last session on
    or before the previous Singapore day -- which is the best available reading
    when the clock time was never recorded.
    """
    inst = _capture_instant(d, hhmm)
    if inst is not None and bounds:
        closed = [s for s in sessions if bounds[s][1] <= inst]
        return closed[-1] if closed else None
    cutoff = d - dt.timedelta(days=1)
    prior = [s for s in sessions if s <= cutoff]
    return prior[-1] if prior else None


def _slot(d: dt.date, hhmm: str | None, sessions: list[dt.date], bounds: dict):
    """Which trialled slot a capture belongs to, by the US session date rather
    than a Singapore clock reading: "intraday" when a US session was live at the
    capture instant, "post-close" when the last session had already closed.
    None for an untimed entry -- the slot is not inferable without a time.
    """
    inst = _capture_instant(d, hhmm)
    if inst is None:
        return None
    live = any(o <= inst < c for o, c in (bounds[s] for s in sessions))
    return "intraday" if live else "post-close"


def daily_coverage() -> None:
    """Report which US sessions the daily log covers and where the gaps are.
    Gaps are expected: they cost event-dating precision inside the gap and
    nothing else, so this reports honestly rather than failing."""
    if not DAILY_SNAP_DIR.exists():
        return
    keys = sorted(k for k in (_snap_key(p) for p in DAILY_SNAP_DIR.glob("snapshot_*.json"))
                  if k is not None)
    if not keys:
        return
    dates = [k[0] for k in keys]
    import exchange_calendars as xcals
    cal = xcals.get_calendar("XNYS")
    lo = (dates[0] - dt.timedelta(days=10)).isoformat()
    hi = dates[-1].isoformat()
    sessions = [s.date() for s in cal.sessions_in_range(lo, hi)]
    bounds = _session_bounds(cal, sessions)
    covered = sorted({a for a in (_anchor_session(d, sessions, hhmm, bounds)
                                  for d, hhmm in keys) if a})
    if not covered:
        return
    in_range = [s for s in sessions if covered[0] <= s <= covered[-1]]
    missed = [s for s in in_range if s not in set(covered)]
    # Slot split: a post-close capture sees one completed US session; an
    # intraday one straddles the live session and catches flow early. Both are
    # being trialled -- report the mix. Classified by the session's own
    # open/close, so the split holds across DST and early closes.
    slots = [_slot(d, hhmm, sessions, bounds) for d, hhmm in keys]
    timed = [s for s in slots if s]
    pre = sum(1 for s in timed if s == "post-close")
    print(f"\n[daily] event log: {len(keys)} capture(s), "
          f"{dates[0].isoformat()} -> {dates[-1].isoformat()} (Singapore capture dates)")
    if timed:
        print(f"[daily] slots: {pre} post-close (no US session live) / "
              f"{len(timed) - pre} intraday (US session live); {len(keys) - len(timed)} untimed")
    print(f"[daily] US sessions covered: {len(covered)} of {len(in_range)} in range "
          f"({len(covered) / len(in_range):.0%}); latest anchor {covered[-1].isoformat()}")
    print("[daily] uncovered sessions: "
          + (", ".join(s.isoformat() for s in missed) if missed else "none"))


def run(cmd: list[str], label: str) -> None:
    print(f"\n[capture] {label}: {' '.join(str(c) for c in cmd[1:])}")
    res = subprocess.run(cmd, cwd=ROOT)
    if res.returncode != 0:
        sys.exit(f"[capture] ABORTED -- {label} failed (exit {res.returncode})")


def latest_export():
    """Newest candidate export across LATEST_DIRS, or None.

    Already-filed archives are excluded by name: offering one back would
    re-capture a file the panel already holds, and the freshness guard would
    only catch it if the content happened to be identical.
    """
    cands = [p for d in LATEST_DIRS if d.is_dir()
             for p in d.glob("*.csv")
             if not p.name.startswith(FILED_PREFIX)]
    cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


def selftest() -> int:
    """Slot and anchor rules against known sessions. These are the cases a
    Singapore clock reading gets wrong, so they are asserted rather than
    eyeballed: the DST flip, the post-midnight window while New York is still
    open, an early close, and the month and year boundaries the vault rule
    requires of any date logic."""
    import exchange_calendars as xcals
    cal = xcals.get_calendar("XNYS")
    sessions = [s.date() for s in cal.sessions_in_range("2026-06-01", "2027-02-01")]
    bounds = _session_bounds(cal, sessions)

    # (label, Singapore capture date, HHMM, expected slot, expected anchor)
    # Python months are 1-indexed.
    cases = [
        ("EDT morning, last session closed 04:00 SGT",
         dt.date(2026, 8, 20), "0738", "post-close", dt.date(2026, 8, 19)),
        ("EDT, minutes before the 21:30 SGT open",
         dt.date(2026, 8, 20), "2125", "post-close", dt.date(2026, 8, 19)),
        ("EDT, minutes after the open",
         dt.date(2026, 8, 20), "2135", "intraday", dt.date(2026, 8, 19)),
        # Past midnight in Singapore, New York still trading. The old clock rule
        # read this as post-close and anchored to a session that had not closed.
        ("EDT, past midnight with the session live",
         dt.date(2026, 8, 21), "0230", "intraday", dt.date(2026, 8, 19)),
        # Under EST the open moves to 22:30 SGT, so 22:00 is no longer intraday.
        ("EST, before the 22:30 SGT open",
         dt.date(2026, 12, 9), "2200", "post-close", dt.date(2026, 12, 8)),
        ("EST, after the open",
         dt.date(2026, 12, 9), "2240", "intraday", dt.date(2026, 12, 8)),
        ("EST, past midnight with the session live",
         dt.date(2026, 12, 10), "0430", "intraday", dt.date(2026, 12, 8)),
        # Thanksgiving Friday closes 13:00 ET = 02:00 SGT, three hours early.
        ("early close, already shut at 03:30 SGT",
         dt.date(2026, 11, 28), "0330", "post-close", dt.date(2026, 11, 27)),
        ("early close, still live at 01:30 SGT",
         dt.date(2026, 11, 28), "0130", "intraday", dt.date(2026, 11, 25)),
        # Month boundary: the Singapore date rolls into September while the
        # anchor session stays in August.
        ("month boundary, 1 Sep capture anchors to 31 Aug",
         dt.date(2026, 9, 1), "0800", "post-close", dt.date(2026, 8, 31)),
        # Year boundary: 1 Jan is an XNYS holiday, so the anchor reaches back
        # across both the year end and the closed session.
        ("year boundary, 2 Jan capture anchors to 31 Dec",
         dt.date(2027, 1, 2), "0800", "post-close", dt.date(2026, 12, 31)),
    ]

    failures = 0
    for label, d, hhmm, want_slot, want_anchor in cases:
        got_slot = _slot(d, hhmm, sessions, bounds)
        got_anchor = _anchor_session(d, sessions, hhmm, bounds)
        ok = got_slot == want_slot and got_anchor == want_anchor
        failures += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            print(f"        want {want_slot}/{want_anchor}, got {got_slot}/{got_anchor}")

    # An untimed entry must still resolve, by the calendar-day fallback.
    fallback = _anchor_session(dt.date(2026, 8, 20), sessions, None, bounds)
    ok = fallback == dt.date(2026, 8, 19)
    failures += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  untimed entry falls back to the calendar-day rule")

    if failures:
        print(f"[selftest] {failures} FAILED")
        return 1
    print("[selftest] OK -- slot and anchor rules hold across DST, early closes, "
          "and the month and year boundaries")
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    if "--selftest" in sys.argv:
        return selftest()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="path to the screener export CSV")
    src.add_argument("--latest", action="store_true",
                     help="use the newest CSV in Downloads or the OneDrive tipranks-signal folder "
                          "(already-filed tipranks_*.csv archives are ignored)")
    ap.add_argument("--asof", help="capture date YYYY-MM-DD (default: today)")
    ap.add_argument("--validate-only", action="store_true", help="run the guards and stop")
    ap.add_argument("--force", action="store_true", help="allow overwriting an existing capture date")
    ap.add_argument("--push", action="store_true", help="commit and push the public page afterwards")
    ap.add_argument("--daily", action="store_true",
                    help="file into the daily event log (data/daily/): validate and ingest only, "
                         "no merge / dashboard / publish. The frozen weekly panel is untouched.")
    ap.add_argument("--selftest", action="store_true",
                    help="check the slot and anchor date rules and stop (touches nothing)")
    a = ap.parse_args()

    if a.latest:
        csv_path = latest_export()
        if csv_path is None:
            searched = " or ".join(str(d) for d in LATEST_DIRS)
            sys.exit(f"[capture] no CSV found in {searched}")
        print(f"[capture] newest CSV in {csv_path.parent}: {csv_path.name} "
              f"({dt.datetime.fromtimestamp(csv_path.stat().st_mtime):%Y-%m-%d %H:%M})")
    else:
        csv_path = Path(a.file)
    if not csv_path.exists():
        sys.exit(f"[capture] file not found: {csv_path}")

    asof = dt.date.fromisoformat(a.asof) if a.asof else dt.date.today()

    snap_dir = DAILY_SNAP_DIR if a.daily else SNAP_DIR
    fallback = SNAP_DIR if a.daily else None
    # The export's own mtime is the true capture moment; it keys daily entries
    # so several captures a day coexist and order correctly.
    stamp = dt.datetime.fromtimestamp(csv_path.stat().st_mtime)
    asof_time = f"{stamp:%H%M}" if a.daily else ""
    snap_name = f"snapshot_{asof.isoformat()}_{asof_time}.json" if a.daily else None
    print(f"\n[capture] integrity report ({'daily event log' if a.daily else 'weekly panel'}) "
          f"-- {csv_path.name} as of {asof.isoformat()} ({asof.strftime('%A')})"
          + (f" captured {stamp:%H:%M} SGT" if a.daily else ""))
    checks, fatal = validate(csv_path, asof, a.force, snap_dir, fallback, snap_name, asof_time)
    width = max(len(lbl) for _, lbl, _ in checks)
    for ok, lbl, detail in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {lbl.ljust(width)}  {detail}")
    if fatal:
        sys.exit(f"\n[capture] ABORTED -- {fatal} check(s) failed. Nothing was filed or ingested.")
    print(f"\n[capture] all {len(checks)} checks passed.")
    if a.validate_only:
        print("[capture] --validate-only: stopping before filing.")
        return 0

    # --- daily event log: validate + ingest only, then stop ----------------
    if a.daily:
        DAILY_ONEDRIVE.mkdir(parents=True, exist_ok=True)
        filed = DAILY_ONEDRIVE / f"tipranks_{asof.isoformat()}_{asof_time}.csv"
        if csv_path.resolve() != filed.resolve():
            shutil.copy2(csv_path, filed)
        print(f"[capture] filed -> {filed}")
        ingest.ingest([filed], asof, out_dir=DAILY_SNAP_DIR, out_name=snap_name,
                      captured_at=stamp.isoformat(timespec="minutes"))
        daily_coverage()
        print(f"\n[capture] DONE -- daily log entry {asof.isoformat()} recorded. "
              f"The frozen weekly panel is untouched.")
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
    # Register row 7 (2026-08-08): the weekly panel used to record only a DATE, so
    # the confirmation window's boundary could not be tested against the capture
    # instant. The daily log already carried it; the weekly stream now does too.
    ingest_cmd = [py, "scripts/ingest.py", "--export", str(filed), "--asof",
                  asof.isoformat(), "--captured-at", stamp.isoformat(timespec="minutes")]
    if a.force:
        # capture --force passed the "not already captured" check above; the
        # ingest freeze guard still refuses anything but a latest-date correction.
        ingest_cmd.append("--force-overwrite")
    run(ingest_cmd, "1/6 ingest")
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
