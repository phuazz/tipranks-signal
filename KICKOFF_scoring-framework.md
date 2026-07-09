# KICKOFF — tipranks-signal scoring & selection framework

**Context:** Personal · project `C:\dev\tipranks-signal` (private repo).
**Model:** Claude Fable 5 (research-correctness tier).
**Mode:** interview-first — resolve the open questions in §7 one at a time with Zhenghao, then freeze. **Do not write code or run anything this session.**

**Read first:** `RESEARCH_MEMO.md` (the pre-registration this extends), `README.md`, `CLAUDE.md`, and the event-studies rig — `C:\dev\event-studies\scripts\{validate_leads,confirm_lead,discovery_scan}.py` — whose drift-adjusted-alpha / block-bootstrap / Benjamini-Hochberg-FDR machinery this reuses rather than rebuilds.

---

## 1. Objective

Design and **freeze** the *adjudication framework*: a fixed grammar of candidate filter/scoring schemes plus the evaluation harness that the accruing weekly panel will use to grade which schemes carry **drift-adjusted, cost-surviving** forward-return edge on the liquid universe.

This session designs the machine that *decides* — not the answer. With one snapshot, any scorer is an unfalsified hypothesis; freezing a clever-looking one now is the overfit the whole pre-registration exists to prevent. The realistic prize is a **disciplined, evidence-graded weekly shortlist** for Zhenghao and Eileen — not a market-beating systematic book (retail analyst data, net of costs, usually fails that bar; the win is finding out rigorously).

## 2. Hard constraints (do not drift)

- **Forward-only.** One snapshot exists (2026-07-09). Nothing is validated on it; nothing is backfilled. The framework is frozen BEFORE the data can argue back.
- **Drift-adjusted alpha is the headline, never raw return** — the beta + high-drift-name-selection trap that took event-studies 14→1.
- **Long-only** is the deployable target (personal book). A market-neutral variant is a research read only, if at all.
- **Costs + turnover always.** Tiered one-way costs by liquidity bucket on every rebalance; revision signals churn, so a holding-period / rebalance-band is part of each scheme, not an afterthought (Barber-Lehavy-McNichols-Trueman 2001).
- **Style-neutralise** every score against size / sector / dividend yield / beta.
- **Fixed grammar = the multiple-testing budget.** Freeze the full menu up front; grade with BH-FDR + a deflated Sharpe (DSR) over the true trial count.
- **Reuse, do not rebuild,** the event-studies harness.
- **IP firewall:** personal-use data, private repo, derived numbers only.

## 3. Priors (established — do not re-derive; do test)

1. **Revisions > levels** — screen on *upgraded this week* / *best-analyst target raised*, not "Strong Buy" (80.6% of the universe already is).
2. **Best-analyst-weighted > consensus** — TipRanks' one genuine edge over vanilla I/B/E/S.
3. **Confirmation gates beat raw signals** — the value-trap anti-pattern (Strong Buy + extreme upside + falling price). Require corroboration.
4. **Turnover kills; rank cross-sectionally** (deciles), not on absolute thresholds — the universe drifts bullish/bearish together.

## 4. Proposed candidate grammar (starting point — refine in the interview)

- **S1 — single-signal decile sorts** (baselines): (a) consensus rating change, (b) best-analyst target revision, (c) Smart-Score level.
- **S2 — style-neutralised composite z-score:** revision + best-analyst + insider, residualised.
- **S3 — gated / confirmation:** revision AND price-not-falling AND a second axis (insider buy / Smart-Score rising).
- **S4 — Smart-Score-as-is decile** — does TipRanks' own 8-factor composite beat our reconstruction?

Each: long-only, liquid universe, weekly cross-sectional rank, drift-adjusted, net of tiered costs, vs an equal-weight benchmark, FDR across the menu.

## 5. Evaluation harness to specify

- **Forward returns:** TOTALRETURN from the anchor close over {1w, 1m, 3m}, delisting-aware.
- **Drift adjustment:** trailing beta×market + idiosyncratic drift removed (event-studies method) + style residualisation.
- **Cost model:** bps by liquidity bucket; explicit turnover accounting; report a 0/5/10/20 bps sweep.
- **Benchmark:** equal-weight liquid universe (primary) + SPY.
- **Inference:** block-bootstrap CI, drift-matched random-entry null, BH-FDR, DSR over the frozen trial count; walk-forward as snapshots accrue.
- **Decision / graduation rule:** what a scheme must clear to become the live shortlist (per-scheme, on the 1-month primary horizon, out of sample, net of costs, CI excluding zero) + a paper-track period before capital.

## 6. Session deliverable

An extension to `RESEARCH_MEMO.md`: the frozen scheme grammar (§4 refined), the harness spec (§5), the benchmark + cost + multiple-testing budget + graduation rule, and register rows. **No code, no backtest, no results** — those are mechanical work (Opus tier) against the frozen design once the panel matures (~8 captures). Update the ledger row when frozen.

## 7. Open questions for the interview (resolve one at a time, then freeze)

1. **Shortlist shape:** top-decile vs a fixed top-N (e.g. 20); rebalance weekly vs monthly; maximum holding / turnover band.
2. **Long-only only, or also a market-neutral research variant?**
3. **Rating-change definition:** consensus-label transition vs Last-Rating-Date movement vs both; best-analyst target-revision threshold (percentage? percentile?).
4. **Confirmation axes for S3:** which corroborators; AND-gate vs weighted; "price-not-falling" definition (above 50/200-day? 3-month return above universe median?).
5. **Smart Score:** use as-is (S4) and/or decompose into the exposed sub-signals (analyst / insider / hedge-fund / news / blogger)? Guard the double-counting risk if both.
6. **Style-neutralisation method:** cross-sectional regression residuals vs sector-neutral deciles vs both.
7. **Cost levels + liquidity buckets;** the turnover / holding rule baked into each scheme.
8. **The precise "outperform" bar:** drift-adjusted alpha CI>0 on 1m OOS net — and must it also beat the equal-weight universe, or only be positive alpha?
9. **Multiple-testing budget:** freeze N total schemes; FDR level (e.g. 0.10); DSR trial-count bookkeeping.
10. **Graduation to live:** clearance bar + paper-track length before it informs a real decision.

## 8. Out of scope this session

Picking a "winning" scorer; running any backtest on one snapshot; deploying capital; touching the dashboard beyond noting that the Findings tab will host the eventual read.
