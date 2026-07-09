# CLAUDE.md — tipranks-signal

Inherits the vault `C:\dev\CLAUDE.md`. Project-specific rules below; this file overrides where it conflicts.

## What this project is

A forward-tested study of TipRanks analyst signals (rating changes, best-analyst price targets, Smart Score) as cross-sectional predictors of forward equity returns, joined to a Norgate point-in-time, survivorship-free, liquid US universe. Context is **Personal** — single-stock signal work is off-book for Navigo (see the event-studies close-out). No capital is at risk; this is signal research.

## IP firewall (hard rule)

- TipRanks (Ultimate) and Norgate (Platinum) are BOTH personal-use licences. No API entitlement on either; data arrives by manual weekly Excel export (TipRanks) and the local NDU proxy (Norgate).
- Vendor VALUES never enter version control. `data/` is gitignored in full: raw `.xlsx` exports are archived to OneDrive, and the derived snapshots / merges / caches stay local-only. Verify with `git check-ignore -v data/` before committing anything.
- If this project is ever given a git remote it MUST be private. Only code, docs, and OUR own aggregate derived numbers (alphas, hit rates — never per-name TipRanks values) may ever be published.
- TipRanks numbers are leads, not facts: every signal is re-tested on our own Norgate return data before it informs anything. Human-in-the-loop; nothing auto-trades.

## The three ways this study could be silently wrong (state before building analysis)

1. **Signal-side survivorship / look-ahead.** The website shows only current scores; TipRanks recomputes them and drops coverage on dead names. Guard: FORWARD snapshots only — each weekly panel is frozen at capture and never revised. Never backfill a past date from today's site.
2. **Beta + high-drift-name selection masquerading as alpha.** Strong-Buy / high-Smart-Score names cluster on high-beta growth. Guard: the headline is DRIFT-ADJUSTED alpha (the same collapse that took event-studies' leads from 14 to 1), not raw forward return.
3. **Turnover + micro-cap spreads.** Revision signals churn and the max-upside names are un-tradeable. Guard: liquid universe (price + ADV floors, S&P 500/400 point-in-time) and realistic tiered costs on every rebalance.

## Data + dates

- Norgate access follows the proven vault pattern (`event-studies/scripts/norgate_universe.py`): `norgatedata` over local NDU, `index_constituent_timeseries` for point-in-time membership, TOTALRETURN `price_timeseries`, freshness gated on the actual last bar date (not `last_quoted_date`, unpopulated for live symbols on this feed). NDU is local-only — no CI pulls.
- Dates via libraries only (`datetime`, `exchange_calendars` XNYS). Python months are 1-indexed (stated in code). The month- and year-boundary checks in `ingest.py --selftest` must stay green.

## Build

```
python scripts/ingest.py  --export "<OneDrive>\Main\tipranks-signal\tipranks_YYYY-MM-DD.csv" --asof YYYY-MM-DD
python scripts/norgate.py --check                    # gate the feed
python scripts/merge_norgate.py --asof YYYY-MM-DD
# analyse.py runs only once forward bars accrue (pre-registered — see RESEARCH_MEMO.md)
```

Its own git repo if remoted (PRIVATE). Separate approvals for commit and push per vault. British/Singapore English, no contractions in code, comments, and commits.
