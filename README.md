# tipranks-signal

Forward-tested study of **TipRanks analyst signals** as cross-sectional predictors of forward US-equity returns, joined to a **Norgate** point-in-time, survivorship-free, liquid universe.

**Context:** Personal. Single-stock signal work is off-book for Navigo (see the event-studies close-out in `C:\dev\STUDIES_LEDGER.md`). No capital at risk — this is signal research.

## Status

First snapshot captured 2026-07-09 (1,987 names; Mid+Large+Mega, US primary). Ingest and the Norgate merge run on live data; the forward-return analysis is pre-registered but **not yet runnable** (it needs weekly snapshots to accrue). The scoring & selection framework was **frozen 2026-07-10** — five graded schemes, shared construction conventions, KEEP bar and graduation rule (`RESEARCH_MEMO.md`, register rows 3–5); `analyse.py` implements it in a later, mechanical session. See `RESEARCH_MEMO.md`.

Remote: **private** repo at `github.com/phuazz/tipranks-signal` (code and docs only — `data/` is gitignored in full and never pushed, per the IP firewall).

## Why this shape

There is **no API** on either licence (TipRanks Ultimate, Norgate Platinum — both personal-use). History cannot be pulled, so each week's screener is frozen at capture and the panel accrues going forward. That is the honest design: zero signal-side look-ahead by construction. Known priors (Barber–Lehavy–McNichols–Trueman 2001; Womack 1996) say rating *levels* are largely priced in and turnover-heavy, while rating and target *revisions* and their drift carry the edge — so the primary signal is the revision, not the level, and the headline is drift-adjusted alpha, not raw return.

## Data sources

- **TipRanks (Ultimate):** weekly manual Excel export of the Analyst screener → normalised snapshot. Raw `.xlsx` archived to OneDrive; never committed.
- **Norgate (Platinum, local NDU):** point-in-time S&P 500 / MidCap 400 membership (survivorship-free, delisted included), TOTALRETURN prices, dollar-volume liquidity. Local-only, no CI pulls.

## Weekly operator routine

1. In TipRanks → Screeners → **Stock Screener**, load the saved screen `tipranks-signal weekly` (Market Cap = Mid+Large+Mega, US primary; the signal filters left as Any). Export (CSV or Excel) and save to `OneDrive\Main\tipranks-signal\tipranks_YYYY-MM-DD.csv`. If a future export exceeds one page, drop each page into a dated folder instead.
2. `python scripts/ingest.py --export "C:\Users\phuaz\OneDrive\Main\tipranks-signal\tipranks_YYYY-MM-DD.csv" --asof YYYY-MM-DD`  (or pass the folder of page-files)
3. `python scripts/merge_norgate.py --asof YYYY-MM-DD`  (NDU running)
4. `python scripts/build_dashboard.py`  → refresh the local monitor; `python scripts/status.py` for accrual.

A few minutes weekly. `analyse.py` runs later, once the forward windows mature.

## Setup / checks

```
pip install -r requirements.txt
python scripts/ingest.py --selftest        # date + parsing + column-mapping checks
python scripts/norgate.py --check          # NDU feed gate (needs NDU running)
```

## Dashboard (private, local — a monitor, not a verdict)

Never published, never Navigo-facing (personal-use firewall). The values are fetched at runtime from `data/` (gitignored); `template.html` carries no data.

```
python scripts/build_dashboard.py     # writes data/dashboard/dashboard_data.json (gitignored)
npx serve .                           # open http://localhost:PORT/template.html
python scripts/export_html.py        # one-file snapshot -> data/exports/ (gitignored)
```

The export inlines the data and all price series into a single HTML file that opens by double-click (charts need internet for the Plotly CDN). It contains per-name vendor values, so it is for person-to-person discussion only — never hosted, never forwarded onward; the page carries that label. The shareable public layer remains the aggregate findings once verdicts exist. For anywhere-access, copy the dated export to `OneDrive\Main\tipranks-signal\` alongside the raw CSVs (done for 2026-07-09) — OneDrive is private storage, not publication.

Tabs: **Panel State** (current cross-section — Smart Score, consensus mix, sector, flow signals, the **Sector Leaders** board (vol-scaled best-analyst upside per sector, top three labelled), and the liquid-universe table with the view-only lens: trap-profile filter + sector-relative Best↑/σ ranking, ungraded; click any row or leader dot for a PCC-style price chart with 50d/200d averages and analyst-target lines, built per lens-passed name into `data/dashboard/prices/`) and **Accrual** are live now; **Revision Monitor** lights up at snapshot 2 (week-on-week upgrades / target raises / score deltas — the useful part; sector revision breadth staged there too); **Findings** (the drift-adjusted-alpha read) stays locked until ~8 captures; **Literature** is static reference — the research map behind the frozen design and the panel lens.

## Open issues

- Schema confirmed 2026-07-09 (26-column CSV; `COLUMN_MAP` locked). The two unmapped columns (Volume, Avg. Volume (3M)) are skipped by design — Norgate supplies liquidity.
- Ticker → Norgate symbol resolution is best-effort (class shares); the merge **flags** misses rather than dropping them — review the unmatched list on the first merge.

_Last updated: 2026-07-10._
