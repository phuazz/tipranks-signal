# tipranks-signal — Research Memo (pre-registration)

**Context:** Personal. **Status:** open (scaffold + pre-registration 2026-07-09; scoring & selection framework frozen 2026-07-10). **Owner:** Zhenghao. No capital at risk.

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

## Scoring & selection framework (frozen 2026-07-10)

Designed interview-first per `KICKOFF_scoring-framework.md` and frozen before the second snapshot exists. This section extends the Fixed design and strengthens the Decision rule (register rows 3–5). The graded menu below IS the multiple-testing budget: five schemes, and nothing else is ever graded without a register amendment.

### Graded menu (N = 5)

| # | Scheme | Frozen construction |
|---|--------|---------------------|
| S1a | Rating-change bucket [PRIMARY] | Week-on-week consensus-label improvement between frozen snapshots, counted only if Last Rating Date falls inside the inter-snapshot window (guards composition-change phantoms, where the label moves because coverage changed rather than because an analyst acted). Equal-weight event bucket. |
| S1b | Best-analyst target-revision bucket | Best-analyst target LEVEL raised versus the prior snapshot; upside moves driven purely by price, with the target unchanged, do not qualify. Equal-weight event bucket; within-bucket rank by revision as % of the prior target feeds the shortlist view only. No absolute thresholds (prior 4). |
| S1c | Smart-Score decile | Top-decile bucket on the discrete 1–10 score: tie classes kept whole, bucket = the smallest top set of score classes reaching ≥ 90 names (~10% of the 901-name liquid universe). Tests the vendor claim as-is. S4 is merged here; the TipRanks-versus-our-reconstruction question is read off the S1c-versus-S2 grades, not a duplicate portfolio. |
| S2 | Residualised composite | Equal-weight composite of three cross-sectional z-scores (weights fixed by design, never optimised): signed confirmed rating-change indicator (+1 upgrade / −1 downgrade / 0), best-analyst target revision % of prior target (0 for non-revisers), insider axis (+1 / 0 / −1). Components winsorised at the 1st/99th percentiles; composite residualised against log market cap, sector, trailing dividend yield and trailing beta (construction-level neutralisation is S2's defining feature); top decile. |
| S3 | Gated revision | S1a AND 3-month total return above the liquid-universe cross-sectional median (Norgate TOTALRETURN at capture) AND no net insider selling per the snapshot insider field. Pure AND-gate, for maximum contrast with S2's weighting; insider buys lift shortlist rank but never gate. |

All schemes: long-only, liquid universe (901 names at snapshot 1), weekly cross-sectional formation.

### Shared construction conventions (not trials)

- **Cohorts.** Weekly entries into 4-week overlapping cohorts (Jegadeesh–Titman); about a quarter of the book rolls each week; matches the 1-month primary horizon.
- **Retention.** Ranked constructions (S1c, S2, and every top-20 view): a maturing name re-enters without a round trip while it sits inside twice the entry threshold (top two deciles; top-40 for a top-20 view), and exits only below it. Event buckets (S1a, S1b, S3): cohorts run to 4-week maturity and re-enter only on a fresh qualifying event. No hard turnover cap; no maximum holding age; realised turnover reported.
- **Top-20 view.** Per scheme, the human shortlist: ranked by within-scheme signal magnitude, maximum 5 names per sector. Reported with a same-sign consistency check only — never graded, no trial.
- **Ungraded diagnostics** (can never be promoted — see bookkeeping): top-minus-bottom spread, bottom-decile alpha, Smart-Score-rising sort, S1c sub-axis attribution (analyst / insider / hedge-fund / news / blogger), raw drift-adjusted alpha, raw returns versus the EW universe and SPY.

### Measurement (headline)

- Forward TOTALRETURN from the anchor close over {1 week context · 1 month PRIMARY · 3 months context}, delisting-aware.
- Drift adjustment per the event-studies rig (trailing beta × market plus idiosyncratic drift removed), then **style neutralisation at the measurement level**: realised abnormal returns residualised cross-sectionally each week against log market cap, sector, trailing dividend yield and trailing beta. Headline = style-neutral drift-adjusted NET alpha, unconditionally (raw drift-adjusted reported alongside).
- **Benchmark embedded.** The weekly cross-sectional residual mean is zero by construction, so the equal-weight liquid universe has adjusted alpha ≡ 0: adjusted alpha > 0 IS outperformance of the EW universe. Raw comparisons versus the EW universe and SPY are context only.
- **Costs.** One-way, by 21-day median dollar volume: ≥ $100m → 3 bps; $25–100m → 8 bps; $10–25m → 15 bps. These tiers are judgement estimates, conservative for personal-order size — NOT measured spreads. Charged on actual trades only (retained names pay nothing at roll). Sensitivity: 0/5/10/20 bps flat sweep. Scale note: a fully-churning 4-week book at the mid tier pays ≈ 2%/yr; retention reduces this.

### KEEP bar (per scheme, 1-month primary, net of tiered costs)

1. 95% time-blocked bootstrap CI on the style-neutral drift-adjusted net alpha excludes zero; AND
2. survives the drift-matched random-entry null within Benjamini–Hochberg FDR q = 0.10 across the five-scheme menu (the FDR applies to this leg only); AND
3. sign stability: the 1-week and 3-month point estimates share the sign of the 1-month estimate.

The deflated Sharpe ratio (DSR) is computed at nominal N = 5; effective N (from realised scheme-return correlations, the em-rotation-lab convention) is reported as context. Failing schemes are recorded and dropped, per the pre-registered decision rule.

### Budget bookkeeping (frozen verbatim)

- **Ratchet.** Any scheme added later by register amendment raises N permanently.
- **Clock-start.** A late-added scheme is graded only on data accrued after its registration date.
- **No promotion.** An ungraded diagnostic can never become a KEEP claim on already-seen data; it must be registered as a new scheme and accrue fresh data.

### Verdicts, graduation, demotion

- Interim reads unlock at 8 captures and carry NO verdict. Verdict eligibility: ≥ 26 weekly captures AND ≥ 20 matured 1-month cohorts (below that the time-block bootstrap holds too few independent blocks to be reliable).
- **Graduation** to the live shortlist requires: KEEP, AND net alpha ≥ +25 bps/month at the point estimate, AND DSR ≥ 0.90 — THEN a 13-week paper track of the deployable top-20 view, passing on net alpha ≥ 0 with behavioural consistency (turnover within band, no sector-cap breach, same sign as the graded decile). Sign consistency, not a fresh significance test: 13 weeks cannot support one and it would smuggle in a trial.
- **Live** means: informs the weekly human shortlist read by Zhenghao and Eileen. Nothing auto-trades; any capital decision sits outside this framework (personal, human-in-the-loop).
- **Re-grade quarterly** on the cumulative panel thereafter; a graduated scheme falling below the KEEP bar is demoted to monitor status by register row.

## Register

| # | date | change | reason |
|---|------|--------|--------|
| 0 | 2026-07-09 | Pre-registration frozen; scaffold built (ingest + norgate + merge); analyse pre-registered, not runnable | Forward accrual begins |
| 1 | 2026-07-09 | Schema: capture trailing dividend yield (+ size, sector) as style controls, not signals; export column set fixed against the TipRanks Add-Columns menu | Enable style-tilt neutralisation of the headline alpha |
| 2 | 2026-07-09 | First snapshot filed: real CSV export (26 cols, 1,987 names, Mid+Large+Mega, US primary) confirmed the schema; ingest now supports CSV + multi-page; **Last Rating Date IS exported**, so the primary rating-change signal is available from snapshot one (both via the date field and week-on-week) | Forward panel now accruing |
| 3 | 2026-07-10 | Scoring & selection framework frozen (see section): graded menu fixed at N = 5 — S1a / S1b / S1c / S2 / S3, with S4 merged into S1c; JT 4-week overlapping cohorts + retention conventions; top-20 as ungraded human view (max 5/sector); ADV-tiered costs 3/8/15 bps one-way (judgement estimates) + 0/5/10/20 bps sweep | Adjudication framework fixed before the panel can argue back; the grammar IS the multiple-testing budget |
| 4 | 2026-07-10 | Headline strengthened: style-neutralised (log size / sector / trailing yield / trailing beta) drift-adjusted NET alpha, unconditionally; raw drift-adjusted reported alongside; EW-universe outperformance embedded by construction (weekly residual mean ≡ 0) | Removes the conditional-neutralisation degree of freedom in the pre-registered "checked and, if needed, neutralised" |
| 5 | 2026-07-10 | Decision rule extended: KEEP = 95% time-blocked bootstrap CI + drift-matched random-entry null within BH-FDR q = 0.10 over N = 5 + 1w/3m sign stability; ratchet / clock-start / no-promotion bookkeeping; verdicts from ≥ 26 captures & ≥ 20 matured cohorts; graduation = KEEP + ≥ 25 bps/month + DSR ≥ 0.90 + 13-week top-20 paper track; quarterly re-grade with demotion by register row | Inference, budget and graduation discipline fixed before results exist |

## Open

- ~~Confirm the TipRanks export column schema~~ — RESOLVED 2026-07-09 (26-column CSV; `COLUMN_MAP` locked to real headers; Last Rating Date present).
- ≥ 8 weekly snapshots before a first honest read (a matured 1-month window on enough captures).
- ~~**Scoring / selection framework** — to be designed next (Fable 5, interview-first) BEFORE the panel matures~~ — RESOLVED 2026-07-10: frozen above (register rows 3–5), per the `KICKOFF_scoring-framework.md` interview.
- `analyse.py` to implement the frozen framework (mechanical work, separate session) before the 8-capture first read; interim reads carry no verdict — verdict eligibility starts at ≥ 26 captures.
- **S1b mechanics decision before `analyse.py` freezes** — the exported best-analyst target can move because TipRanks re-picks the "best analyst" (identity switch), not because any analyst acted: the first WoW window (9 days, 2026-07-09 → 2026-07-18) shows 365 target raises / 208 cuts against only 47 label changes, 96% of which were date-confirmed. Decide whether S1b adopts a Last-Rating-Date confirmation guard mirroring S1a, and a de-minimis threshold on the target move (observed: a −0.01% penny wiggle on a ~$319 target read as a "cut" in the view layer until a ≥ 0.25%-of-prior-level floor was applied there on 2026-07-19); adopting either for the graded scheme is a dated register amendment — cheap now, impossible to add honestly once accrual matures.
- Merge performance: first full-universe merge (~1,987 names) pulls Norgate per name; if the weekly run is slow, cache price/membership or bound history — optimise only if it bites.
