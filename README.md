# tipranks-signal

Forward-tested study of **TipRanks analyst signals** as cross-sectional predictors of forward US-equity returns, joined to a **Norgate** point-in-time, survivorship-free, liquid universe.

**Context:** Personal. Single-stock signal work is off-book for Navigo (see the event-studies close-out in `C:\dev\STUDIES_LEDGER.md`). No capital at risk — this is signal research.

## Status

First snapshot captured 2026-07-09 (1,987 names; Mid+Large+Mega, US primary). Ingest and the Norgate merge run on live data; the forward-return analysis is pre-registered but **not yet runnable** (it needs weekly snapshots to accrue). The scoring & selection framework was **frozen 2026-07-10** — five graded schemes, shared construction conventions, KEEP bar and graduation rule (`RESEARCH_MEMO.md`, register rows 3–5); `analyse.py` implements it in a later, mechanical session. See `RESEARCH_MEMO.md`.

Second snapshot filed 2026-07-18 (1,993 exported → 900 liquid; GTLS delisted in-window — its final return realises at analyse time, delisting-aware by design). The **Revision Monitor is live**: confirmed week-on-week upgrades / downgrades, best-analyst target revisions (identity-switch caveat disclosed), Smart-Score deltas and sector revision breadth; the panel's default order is now the week-on-week revision score.

Remote: **public** repo at `github.com/phuazz/tipranks-signal` (made public 2026-07-18 by owner decision, after a full-history scan). What is public is code, docs, OUR aggregate derived numbers, and two GitHub Pages surfaces — the **public page** at `phuazz.github.io/tipranks-signal` (`docs/index.html`, built by `scripts/pipeline.py` — aggregate panel state, revision-flow counts, methodology; a build-time leak guard fails the build if any ticker or per-name field reaches the output), and the **monitor shell** at `phuazz.github.io/tipranks-signal/monitor/` (`docs/monitor/index.html` — the monitor's code with no data in it; see below). Per-name TipRanks / Norgate values never enter version control (`data/` is gitignored in full), and the monitor's data files and HTML exports are never committed or hosted. A mandatory local pre-commit hook blocks `data/`, `*.csv`, `*.xlsx` and monitor exports outright, and independently rejects a `docs/monitor/index.html` over 150 KB; on a fresh clone, install it with `python scripts/install_hooks.py`.

## Why this shape

There is **no API** on either licence (TipRanks Ultimate, Norgate Platinum — both personal-use). History cannot be pulled, so each week's screener is frozen at capture and the panel accrues going forward. That is the honest design: zero signal-side look-ahead by construction. Known priors (Barber–Lehavy–McNichols–Trueman 2001; Womack 1996) say rating *levels* are largely priced in and turnover-heavy, while rating and target *revisions* and their drift carry the edge — so the primary signal is the revision, not the level, and the headline is drift-adjusted alpha, not raw return.

## Data sources

- **TipRanks (Ultimate):** weekly manual Excel export of the Analyst screener → normalised snapshot. Raw `.xlsx` archived to OneDrive; never committed.
- **Norgate (Platinum, local NDU):** point-in-time S&P 500 / MidCap 400 membership (survivorship-free, delisted included), TOTALRETURN prices, dollar-volume liquidity. Local-only, no CI pulls.

## Weekly operator routine

1. In TipRanks → Screeners → **Stock Screener**, load the saved screen `tipranks-signal weekly` (Market Cap = Mid+Large+Mega, US primary; the signal filters left as Any). **Set Rows to the maximum before exporting** — a silently truncated export is the one failure a forward-only panel cannot undo. Export to CSV.
2. `python scripts/capture.py --latest --asof YYYY-MM-DD` (NDU running) — one command for everything else.

`capture.py` is the guard layer. It validates the export *before* anything is filed or ingested — column contract (imported from `ingest.py`, never copied), row count and ticker overlap against the prior capture, a content hash against the previous export to catch a stale re-submit, and date sanity — aborts on any failure, then files the raw CSV to OneDrive and runs ingest → feed gate → merge → dashboard → HTML export (+ OneDrive copy) → public page. Flags: `--file <path>` instead of `--latest`, `--validate-only` to run the guards alone, `--force` to overwrite a captured date, `--push` to commit and push the public aggregates. `python scripts/status.py` reports accrual.

**Capture dates are the Singapore download day.** The merge anchors every name on the last US session *on or before* that date (stored per record as `anchor_date`), so a Thursday-SGT capture anchors on the Wednesday US close — the signal is observed after that close, so there is no look-ahead by construction.
`python scripts/pipeline.py` rebuilds both Pages surfaces — the public aggregate page (`docs/index.html`) and the data-less monitor shell (`docs/monitor/index.html`); commit and push `docs/` to publish. `capture.py` runs it as its last step, so a normal weekly capture already refreshes both.

A few minutes weekly. `analyse.py` runs later, once the forward windows mature.

## Setup / checks

```
pip install -r requirements.txt
python scripts/ingest.py --selftest        # date + parsing + column-mapping checks
python scripts/norgate.py --check          # NDU feed gate (needs NDU running)
```

## Dashboard (a monitor, not a verdict) — hosted code, local data

The monitor's **values** are private and stay that way. Its **code** is now hosted, because iterating on the page through a local build-and-serve loop was the binding constraint on progress.

**Hosted:** `phuazz.github.io/tipranks-signal/monitor/`. That page is `template.html` plus `monitor_shell.html`, built by `scripts/pipeline.py` — 80 KB, zero vendor values, `noindex`. On first visit it asks for the week's export; drop in `tipranks_monitor_YYYY-MM-DD.html` (from `data/exports/`, or from `OneDrive\Main\tipranks-signal\` on any signed-in device) and it renders the full panel, charts included. The file is read with the FileReader API and cached in that browser's IndexedDB, so later visits restore it with no file at all. Nothing is uploaded — the page is static and has no backend. A "Load data" button swaps weeks; "Clear stored data" wipes the cache. `dashboard_data.json` also loads, without the price charts.

Two guards keep the boundary honest, and both are tested in each direction: `pipeline.py` aborts the build if any ticker from any merge appears in the shell, if a data payload is inlined, or if the output exceeds 150 KB; the pre-commit hook independently refuses a staged `docs/monitor/index.html` over 150 KB. Feeding the real 2.7 MB export to the builder aborts on the ticker guard and writes nothing.

**Local (unchanged):**

```
python scripts/build_dashboard.py     # writes data/dashboard/dashboard_data.json (gitignored)
npx serve .                           # open http://localhost:PORT/template.html
python scripts/export_html.py         # one-file snapshot -> data/exports/ (gitignored)
```

The export inlines the data and all price series into a single HTML file that opens by double-click (charts need internet for the Plotly CDN). It contains per-name vendor values, so it is for person-to-person discussion only — never hosted, never forwarded onward; the page carries that label, and the hosted shell carries the equivalent one once data is loaded. The shareable public layer remains the aggregate findings once verdicts exist. Copying the dated export to `OneDrive\Main\tipranks-signal\` alongside the raw CSVs is how it reaches another device — OneDrive is private storage, not publication.

Tabs: **Panel State** (current cross-section — Smart Score, consensus mix, sector, flow signals, the **Sector Leaders** board (vol-scaled best-analyst upside per sector, top three labelled), and the liquid-universe table with the view-only lens: trap-profile filter + revision-score default order, ungraded; click any row or leader dot for a PCC-style price chart with 50d/200d averages and analyst-target lines, built into `data/dashboard/prices/` for lens-passed and strongly-revised names); **Revision Monitor** (live from snapshot 2 — confirmed week-on-week upgrades / downgrades, best-analyst target revisions with the identity-switch caveat, Smart-Score deltas, sector revision breadth, and the full revision table); **Accrual** is live; **Findings** (the drift-adjusted-alpha read) stays locked until ~8 captures; **Literature** is static reference — the research map behind the frozen design and the panel lens.

## Open issues

- Schema confirmed 2026-07-09 (26-column CSV; `COLUMN_MAP` locked). The two unmapped columns (Volume, Avg. Volume (3M)) are skipped by design — Norgate supplies liquidity.
- Ticker → Norgate symbol resolution is best-effort (class shares); the merge **flags** misses rather than dropping them — review the unmatched list on the first merge.

_Last updated: 2026-07-31._
