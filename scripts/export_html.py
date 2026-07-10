"""Build a single-file HTML snapshot of the monitor for private discussion.

Inlines dashboard_data.json and every pre-built price series into the template
so the output opens with a double-click (Plotly still loads from its CDN, so
an internet connection is needed to render charts). The file lands in
data/exports/ — inside the gitignored data/ tree, because it contains
per-name vendor values (TipRanks / Norgate, personal-use licences) which
never enter version control. The page chrome labels it as a private,
do-not-forward snapshot (see exportBadge() in template.html).

Dates are read from the data file as-is; no date arithmetic happens here.
(Python months are 1-indexed; not that it matters — nothing is computed.)
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> None:
    tpl = (ROOT / "template.html").read_text(encoding="utf-8")
    data_path = ROOT / "data" / "dashboard" / "dashboard_data.json"
    if not data_path.exists():
        sys.exit("dashboard_data.json missing — run scripts/build_dashboard.py first.")
    data = json.loads(data_path.read_text(encoding="utf-8"))

    prices = {}
    pdir = ROOT / "data" / "dashboard" / "prices"
    if pdir.exists():
        for f in sorted(pdir.glob("*.json")):
            prices[f.stem] = json.loads(f.read_text(encoding="utf-8"))

    # </ must not appear verbatim inside an inline <script> block
    esc = lambda s: s.replace("</", "<\\/")
    blob = (
        "<script>window.__EXPORT_DATA__="
        + esc(json.dumps(data, separators=(",", ":")))
        + ";window.__EXPORT_PRICES__="
        + esc(json.dumps(prices, separators=(",", ":")))
        + ";</script>"
    )

    anchor = "<script>\nconst $ = s => document.querySelector(s);"
    if anchor not in tpl:
        sys.exit("Injection anchor not found — template drifted; update export_html.py.")
    out_html = tpl.replace(anchor, blob + "\n" + anchor, 1)

    outdir = ROOT / "data" / "exports"
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"tipranks_monitor_{data['as_of']}.html"
    out.write_text(out_html, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB, {len(prices)} price series inlined)")


if __name__ == "__main__":
    main()
