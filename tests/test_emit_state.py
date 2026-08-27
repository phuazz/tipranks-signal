"""Tests for scripts/emit_state.py — the STATE_CONTRACT emission.

This repo has no CI, so these are a local guard. Run them by hand after touching
emit_state.py:

    python -m pytest tests/test_emit_state.py -q

Two things carry real risk here:

  1. THE IP FIREWALL. This repo HAS a remote, and its whole data/ tree is
     gitignored because the content behind it is licensed third-party IP. The
     emitter refuses to write unless git confirms the output path is ignored.
     A .gitignore is editable, so that guard is the firewall expressed as code,
     and it must be proven rather than assumed. A second test asserts that no
     per-name content reaches the emission even when the aggregates file is full
     of it.

  2. THE ACCRUAL COUNT. It is the whole state — it decides whether the study has
     reached its interim read. The aggregates file asserts a count about itself;
     the local snapshot folder is the ground truth it was built from. A
     disagreement is refused rather than reconciled, because either number could
     be the wrong one and picking is guessing.

Python datetime months are 1-indexed (January = 1).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import emit_state  # noqa: E402

REQUIRED = {"as_of", "state", "value", "zone", "role", "horizon",
            "evidence_grade", "licence", "action_hint", "source_file"}
OPTIONAL = {"computed_at", "cadence"}
SIGNAL = "tipranks_accrual"


def _aggregates(snapshots=6, min_snapshots=8, liquid=899, **over):
    d = {
        "generated_utc": "2026-08-12T10:55:38+00:00",
        "as_of": "2026-08-12",
        "universe": {"liquid": liquid, "sp500": 502, "sp400": 397},
        "kpis": {"liquid": liquid, "pct_buy": 78.9, "median_upside": 13.4},
        "accrual": {"snapshots": snapshots, "min_snapshots": min_snapshots, "matured": 1},
        # Per-name and per-sector content, present in the real file and which
        # must never reach the emission.
        "revision": {"upgrades": 21, "downgrades": 13,
                     "breadth": [{"sector": "Technology", "net_upgrades": 1},
                                 {"sector": "Utilities", "net_upgrades": 3}]},
        "by_sector": {"Technology": {"pct_buy": 81.2}},
    }
    d.update(over)
    return d


@pytest.fixture
def env(monkeypatch):
    box = {"agg": _aggregates(), "local": 6}
    monkeypatch.setattr(emit_state, "load_aggregates", lambda: box["agg"])
    monkeypatch.setattr(emit_state, "local_snapshot_count", lambda: box["local"])
    monkeypatch.setattr(emit_state, "assert_output_is_gitignored", lambda: None)
    return box


# --- 1. the IP firewall -----------------------------------------------------

def test_the_emitter_refuses_to_write_where_git_would_track_it(monkeypatch):
    """check-ignore returning non-zero means the path is NOT ignored."""
    class R:
        returncode = 1
    monkeypatch.setattr(emit_state.subprocess, "run", lambda *a, **k: R())
    with pytest.raises(emit_state.EmitError, match="NOT gitignored"):
        emit_state.assert_output_is_gitignored()


def test_an_ignored_path_passes(monkeypatch):
    class R:
        returncode = 0
    monkeypatch.setattr(emit_state.subprocess, "run", lambda *a, **k: R())
    emit_state.assert_output_is_gitignored()   # must not raise


def test_a_git_failure_is_refused_not_assumed_safe(monkeypatch):
    """If the check cannot run, the answer is unknown, and unknown must not be
    treated as ignored."""
    def boom(*a, **k):
        raise OSError("git missing")
    monkeypatch.setattr(emit_state.subprocess, "run", boom)
    with pytest.raises(emit_state.EmitError, match="could not verify"):
        emit_state.assert_output_is_gitignored()


def test_the_live_output_path_really_is_ignored():
    """Not a mock: asserts the actual repository state, so a .gitignore edit
    that exposed data/ would fail here."""
    emit_state.assert_output_is_gitignored()


def test_no_per_name_or_per_sector_content_reaches_the_emission(env):
    """The aggregates file is full of it. Aggregate counts and dates only."""
    rendered = json.dumps(emit_state.build())
    for leaked in ("Technology", "Utilities", "by_sector", "breadth",
                   "pct_buy", "median_upside", "upgrades", "downgrades"):
        assert leaked not in rendered, f"`{leaked}` reached the emission"


def test_the_emission_carries_the_restricted_licence(env):
    assert emit_state.build()["signals"][SIGNAL]["licence"] == "tipranks"


# --- 2. the accrual count ---------------------------------------------------

def test_a_count_mismatch_is_refused_not_reconciled(env):
    env["local"] = 5
    with pytest.raises(emit_state.EmitError, match="!="):
        emit_state.build()


def test_the_mismatch_message_names_both_numbers(env):
    env["local"] = 5
    with pytest.raises(emit_state.EmitError) as exc:
        emit_state.build()
    assert "6" in str(exc.value) and "5" in str(exc.value)


def test_an_absent_snapshot_folder_is_refused_not_read_as_agreement(env):
    """None is not zero. Treating a missing folder as a count would let the
    aggregates assert their own accrual with nothing to check them against."""
    env["local"] = None
    with pytest.raises(emit_state.EmitError, match="absent"):
        emit_state.build()


def test_a_matching_count_passes(env):
    assert emit_state.build()["signals"][SIGNAL]["value"] == 6


# --- the accrual ladder -----------------------------------------------------

@pytest.mark.parametrize("snapshots,expected", [
    (0, "accruing"), (6, "accruing"), (7, "accruing"),
    (8, "interim-readable"), (25, "interim-readable"),
    (26, "verdict-ready"), (40, "verdict-ready"),
])
def test_the_accrual_ladder(snapshots, expected):
    assert emit_state.accrual_state(snapshots, 8) == expected


def test_the_interim_bar_is_at_the_minimum_not_above_it(env):
    env["agg"], env["local"] = _aggregates(snapshots=8), 8
    assert emit_state.build()["signals"][SIGNAL]["state"] == "interim-readable"


def test_one_short_of_the_bar_is_still_accruing(env):
    env["agg"], env["local"] = _aggregates(snapshots=7), 7
    assert emit_state.build()["signals"][SIGNAL]["state"] == "accruing"


# --- the description --------------------------------------------------------

def test_the_panel_size_is_comma_grouped(env):
    """House rule: every figure at or above 1,000 is comma-grouped."""
    env["agg"] = _aggregates(liquid=1234)
    assert "1,234-name liquid panel" in emit_state.build()["signals"][SIGNAL]["zone"]


def test_a_panel_below_a_thousand_carries_no_separator(env):
    assert "899-name liquid panel" in emit_state.build()["signals"][SIGNAL]["zone"]


def test_the_zone_states_the_progress_toward_the_interim_read(env):
    assert emit_state.build()["signals"][SIGNAL]["zone"].startswith("6 of 8 to the interim read")


# --- shape ------------------------------------------------------------------

def test_emits_exactly_the_one_signal(env):
    assert set(emit_state.build()["signals"]) == {SIGNAL}


def test_the_block_carries_the_required_fields_and_nothing_unknown(env):
    block = emit_state.build()["signals"][SIGNAL]
    assert REQUIRED <= set(block), f"missing {REQUIRED - set(block)}"
    assert set(block) <= REQUIRED | OPTIONAL, f"unknown {set(block) - REQUIRED - OPTIONAL}"


def test_no_score_or_weight_field_is_emitted(env):
    banned = {"score", "weight", "composite", "rank"}
    assert not (banned & set(emit_state.build()["signals"][SIGNAL]))


def test_the_evidence_grade_says_it_is_accruing(env):
    """The study has no verdict. The grade travels with the state so nobody
    reads an accrual count as a result."""
    assert emit_state.build()["signals"][SIGNAL]["evidence_grade"] == "accruing"


@pytest.mark.parametrize("pointer", ["as_of", "accrual", "universe"])
def test_a_missing_top_level_key_stops_the_emission(env, pointer):
    del env["agg"][pointer]
    with pytest.raises(emit_state.EmitError, match=pointer):
        emit_state.build()


def test_a_missing_min_snapshots_stops_the_emission(env):
    del env["agg"]["accrual"]["min_snapshots"]
    with pytest.raises(emit_state.EmitError, match="min_snapshots"):
        emit_state.build()


# --- emit() must never raise ------------------------------------------------

def test_emit_returns_false_rather_than_raising(env, monkeypatch, tmp_path):
    monkeypatch.setattr(emit_state, "OUT", tmp_path / "state.json")
    env["local"] = 99
    lines = []
    assert emit_state.emit(log=lines.append) is False
    assert any("FAILED" in line for line in lines)


def test_a_failed_emit_leaves_the_previous_file_untouched(env, monkeypatch, tmp_path):
    out = tmp_path / "state.json"
    out.write_text('{"previous": "emission"}', encoding="utf-8")
    monkeypatch.setattr(emit_state, "OUT", out)
    env["local"] = 99
    assert emit_state.emit(log=lambda _: None) is False
    assert json.loads(out.read_text(encoding="utf-8")) == {"previous": "emission"}


def test_emit_writes_and_is_idempotent(env, monkeypatch, tmp_path):
    out = tmp_path / "state.json"
    monkeypatch.setattr(emit_state, "OUT", out)
    assert emit_state.emit(log=lambda _: None) is True
    first = out.read_text(encoding="utf-8")
    assert emit_state.emit(log=lambda _: None) is True
    assert out.read_text(encoding="utf-8") == first, "unchanged state was rewritten"
