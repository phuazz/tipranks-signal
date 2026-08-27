"""emit_state.py — publish the accrual state in the STATE_CONTRACT shape.

WHAT THIS IS FOR
----------------
A private consumer (the command centre) renders this study's accrual state
beside signals from seven other projects. Until now it did that by reaching INTO
this repo and reading data/public/aggregates.json from its own side, and by
counting the local snapshot folder itself. This writes `data/public/state.json`
beside the aggregates it describes.

THE IP FIREWALL IS THE BOUNDARY HERE, AND IT IS ENFORCED
----------------------------------------------------------
This repo HAS a remote, and its entire `data/` tree is gitignored on purpose:
the per-name TipRanks content is licensed third-party IP and nothing derived
from it at name level may ever be committed. The emission therefore has two
constraints that the sibling emitters do not:

  1. AGGREGATE FIELDS ONLY. Counts and dates. No ticker, no per-name rating,
     target or score, ever — not even one. The consumer's contract permits this
     source's values in private artefacts only, and even there only in
     aggregate.
  2. The emission must live where it CANNOT be committed. The emitter refuses to
     write unless git confirms the output path is ignored. That check is the
     firewall expressed as code rather than as a convention: `data/` being
     ignored is the only thing standing between this file and a commit, and a
     .gitignore is editable.

WHY THE SNAPSHOT CROSS-CHECK IS A HARD FAILURE
------------------------------------------------
`accrual.snapshots` is a count the aggregates file asserts about itself; the
local `data/snapshots/` folder is the ground truth it was built from. If they
disagree, either the aggregates are stale or a capture went missing — and the
accrual count is the whole state, since it is what decides whether the study has
reached its interim read. A mismatch is refused rather than reconciled, because
either number could be the wrong one and picking is guessing.

WHAT IT IS NOT
--------------
  * NOT a verdict, and not a signal to act on. The study is accruing; the state
    says so. Its evidence grade travels with it for exactly that reason.
  * NOT load-bearing here. Nothing in this repo reads state.json.

Usage:
    python scripts/emit_state.py           # write data/public/state.json
    python scripts/emit_state.py --check   # validate and print, write nothing
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent
AGGREGATES = ROOT / "data" / "public" / "aggregates.json"
SNAPSHOT_DIR = ROOT / "data" / "snapshots"
OUT = ROOT / "data" / "public" / "state.json"

CONTRACT_VERSION = "1"
SOURCE = "tipranks-signal"
SIGNAL = "tipranks_accrual"
LICENCE = "tipranks"

# Accrual ladder: counts below the interim bar, at it, and at the verdict bar.
VERDICT_SNAPSHOTS = 26


class EmitError(Exception):
    """A required input was missing or malformed. Never emit a guess."""


def assert_output_is_gitignored() -> None:
    """Refuse to write unless git confirms the output path is ignored.

    This repo has a remote, and the whole `data/` tree is gitignored because the
    content behind it is licensed third-party IP. That .gitignore line is the
    only thing between this emission and a commit, and a .gitignore is editable.
    Asserting it here turns the firewall from a convention into a precondition.
    """
    try:
        r = subprocess.run(["git", "check-ignore", "-q", str(OUT)],
                           cwd=str(ROOT), capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmitError(f"could not verify the output path is gitignored: {exc}") from exc
    if r.returncode != 0:
        raise EmitError(
            f"{OUT.relative_to(ROOT).as_posix()} is NOT gitignored — refusing to write. "
            "This repo has a remote and its data/ tree is excluded because the content "
            "behind it is licensed third-party IP. Restore the ignore rule before "
            "emitting anything here."
        )


def require(obj, path: str, kind=None):
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise EmitError(f"missing key `{part}` at pointer `{path}`")
        cur = cur[part]
    if cur is None:
        raise EmitError(f"pointer `{path}` is null")
    if kind is not None and not isinstance(cur, kind):
        want = kind.__name__ if isinstance(kind, type) else "/".join(k.__name__ for k in kind)
        raise EmitError(f"pointer `{path}` is {type(cur).__name__}, expected {want}")
    return cur


def load_aggregates():
    if not AGGREGATES.exists():
        raise EmitError(f"source file not found: {AGGREGATES}")
    try:
        return json.loads(AGGREGATES.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EmitError(f"aggregates.json is not valid JSON: {exc}") from exc


def local_snapshot_count() -> int | None:
    """Ground truth for the accrual count. None when the folder is absent, which
    is a different thing from zero and must not be read as agreement."""
    if not SNAPSHOT_DIR.exists():
        return None
    return len(list(SNAPSHOT_DIR.glob("snapshot_*.json")))


def accrual_state(snapshots: int, min_snapshots: int) -> str:
    if snapshots >= VERDICT_SNAPSHOTS:
        return "verdict-ready"
    return "accruing" if snapshots < min_snapshots else "interim-readable"


def build() -> dict:
    assert_output_is_gitignored()
    agg = load_aggregates()

    as_of = require(agg, "as_of", str)
    snapshots = require(agg, "accrual.snapshots", int)
    min_snaps = require(agg, "accrual.min_snapshots", int)
    liquid = require(agg, "universe.liquid", int)

    local_n = local_snapshot_count()
    if local_n is None:
        raise EmitError(
            f"local snapshot folder {SNAPSHOT_DIR} is absent — the accrual count "
            "cannot be corroborated, and it is the whole state"
        )
    if local_n != snapshots:
        raise EmitError(
            f"accrual.snapshots {snapshots} != local snapshot count {local_n} — "
            "either the aggregates are stale or a capture went missing. Refusing "
            "to pick one; the accrual count decides whether the study has reached "
            "its interim read."
        )

    state = accrual_state(snapshots, min_snaps)

    return {
        "contract_version": CONTRACT_VERSION,
        "emitted_by": SOURCE,
        "emitted_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "signals": {
            SIGNAL: {
                "as_of": as_of,
                "state": state,
                "value": snapshots,
                # House rule: every figure at or above 1,000 is comma-grouped.
                "zone": f"{snapshots} of {min_snaps} to the interim read · "
                        f"{liquid:,}-name liquid panel",
                "role": "view-only",
                "horizon": "1w-3m cohorts",
                "evidence_grade": "accruing",
                "licence": LICENCE,
                "action_hint": "none",
                "source_file": "data/public/aggregates.json",
                "computed_at": agg.get("generated_utc"),
                "cadence": "weekly",
            }
        },
    }


def unchanged(payload: dict) -> bool:
    """Same emission as the one on disk, apart from the run's own timestamp?"""
    if not OUT.exists():
        return False
    try:
        prev = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    strip = lambda d: {k: v for k, v in d.items() if k != "emitted_at"}
    return strip(prev) == strip(payload)


def emit(log=print) -> bool:
    """Write the emission. Returns True on success, False on any failure. NEVER
    raises: the capture routine must not fail because a convenience for one
    private reader could not be written."""
    try:
        payload = build()
    except EmitError as exc:
        log(f"emit_state: FAILED — {exc}")
        log("emit_state: nothing written; the previous state.json is left as it was.")
        return False
    except Exception as exc:  # noqa: BLE001 — a convenience must never break the product
        log(f"emit_state: FAILED unexpectedly — {type(exc).__name__}: {exc}")
        return False

    try:
        if unchanged(payload):
            log("emit_state: state unchanged since the last emission — leaving it as it is.")
            return True
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
    except OSError as exc:
        log(f"emit_state: could not write {OUT}: {exc}")
        return False

    s = payload["signals"][SIGNAL]
    log(f"emit_state: {s['state']} @ {s['as_of']} — {s['zone']}")
    return True


def main(argv: list[str]) -> int:
    if "--check" in argv:
        try:
            s = build()["signals"][SIGNAL]
        except EmitError as exc:
            print(f"emit_state: FAILED — {exc}", file=sys.stderr)
            return 1
        print(f"emit_state: {s['state']} @ {s['as_of']} — {s['zone']}")
        print("emit_state: --check, nothing written.")
        return 0
    return 0 if emit() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
