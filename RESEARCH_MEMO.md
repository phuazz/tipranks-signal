# tipranks-signal — Research Memo (pre-registration)

**Context:** Personal. **Status:** open (scaffold + pre-registration, 2026-07-09). **Owner:** Zhenghao. No capital at risk.

This memo is the pre-registration. The universe, signals, return measure, adjustment, inference, and decision rule are fixed HERE before results exist, so findings cannot be reverse-engineered from the data. Amend by adding a dated register row, never by silent edit.

## Question

Do TipRanks analyst signals — primarily consensus rating *changes* and best-analyst price-target *revisions*, secondarily the Smart Score — carry drift-adjusted, cost-surviving forward-return information on a liquid, point-in-time US universe?

## Prior (so we do not re-run known results)

Analyst rating LEVELS are largely priced in and turnover-heavy: Barber, Lehavy, McNichols & Trueman (2001) found gross abnormal returns to consensus levels that transaction costs erased. Post-recommendation drift and the value of rating *changes* over *levels* is the more durable finding (Womack 1996; Jegadeesh & Kim). TipRanks' one genuine edge over vanilla consensus is analyst-quality weighting (the best-analyst target). The design follows the prior: revision-first, best-analyst-weighted, costs central.

## The three ways this could be silently wrong

1. **Signal-side survivorship / look-ahead.** The site shows only current, recomputed scores and drops coverage on dead names. GUARD: forward snapshots only, frozen at capture, never backfilled.
2. **Beta + high-drift-name selection as fake alpha.** Strong-Buy / high-Smart-Score clusters on high-beta growth. GUARD: headline is drift-adjusted alpha (trailing beta × market + idiosyncratic drift removed); raw return only alongside.
3. **Turnover + micro-cap spreads.** Revision signals churn; max-upside names are un-tradeable. GUARD: price ≥ $5 and 21d median dollar volume ≥ $10m on a point-in-time S&P 500/400 universe; tiered costs on every rebalance.

## Fixed design

- **Universe.** In-universe = point-in-time member of S&P 500 or S&P MidCap 400 on the capture date (Norgate, delisted included); liquid = the price and ADV floors above. Widening to SmallCap 600 requires a register amendment.
- **Signals.** (a) consensus rating change vs the prior weekly snapshot [PRIMARY]; (b) best-analyst price-target revision; (c) Smart Score decile.
- **Return.** TOTALRETURN from the anchor close over {1 week, 1 month, 3 month} forward windows; delisting-aware (a name that delists mid-window realises its final return, not a survivorship gap).
- **Headline metric.** Drift-adjusted alpha; episode-block bootstrap CI + drift-matched random-entry null; hit rate; turnover.
- **Costs.** Tiered one-way costs by liquidity bucket, applied on every rebalance; report a 0/5/10/20 bps sweep. No friction-free headline.
- **Style controls.** Capture size (market cap), sector, and trailing dividend yield as controls (not signals). The headline alpha is checked and, if needed, neutralised against them, so an analyst-signal edge is not a disguised yield / value / size tilt.

## Decision rule

A signal is KEPT only if its drift-adjusted alpha is positive net of costs with a bootstrap CI excluding zero on the PRIMARY horizon (1 month), on the liquid universe, and the sign is stable across the 1-week and 3-month horizons. Otherwise it is recorded and dropped. A levels-only edge that dies on cost is expected (see prior) and is not a surprise to be explained away.

## Register

| # | date | change | reason |
|---|------|--------|--------|
| 0 | 2026-07-09 | Pre-registration frozen; scaffold built (ingest + norgate + merge); analyse pre-registered, not runnable | Forward accrual begins |
| 1 | 2026-07-09 | Schema: capture trailing dividend yield (+ size, sector) as style controls, not signals; export column set fixed against the TipRanks Add-Columns menu | Enable style-tilt neutralisation of the headline alpha |
| 2 | 2026-07-09 | First snapshot filed: real CSV export (26 cols, 1,987 names, Mid+Large+Mega, US primary) confirmed the schema; ingest now supports CSV + multi-page; **Last Rating Date IS exported**, so the primary rating-change signal is available from snapshot one (both via the date field and week-on-week) | Forward panel now accruing |

## Open

- ~~Confirm the TipRanks export column schema~~ — RESOLVED 2026-07-09 (26-column CSV; `COLUMN_MAP` locked to real headers; Last Rating Date present).
- ≥ 8 weekly snapshots before a first honest read (a matured 1-month window on enough captures).
- **Scoring / selection framework** — to be designed next (Fable 5, interview-first) BEFORE the panel matures: see `KICKOFF_scoring-framework.md`. It freezes the fixed scheme grammar + evaluation harness + graduation rule that `analyse.py` will implement.
- Merge performance: first full-universe merge (~1,987 names) pulls Norgate per name; if the weekly run is slow, cache price/membership or bound history — optimise only if it bites.
