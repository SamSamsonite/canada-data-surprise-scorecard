"""
render.py - build docs/index.html from data/processed/scorecard.json.

Charts are generated here as raw SVG strings rather than by a plotting
library. That is a deliberate choice and worth defending:

  * A JavaScript charting library puts the numbers out of reach of anyone
    browsing with JavaScript off, and usually renders to <canvas>, which is
    opaque to a screen reader.
  * Server-side SVG is just markup. It can carry role="img", a <title>, and a
    <desc>, it prints, it scales to any zoom level without going fuzzy, and it
    needs no runtime dependency at all.
  * The whole page ends up self-contained: one HTML file, inline CSS, inline
    SVG, zero network requests, zero JavaScript.

Colours come from validate_colours.PALETTE so that the palette the page uses
and the palette that gets contrast-checked cannot drift apart.

A NOTE ON THE WRITTEN SENTENCES
-------------------------------
Every "what this shows" and takeaway sentence on the page is generated from
the statistics rather than typed in by hand. This page rebuilds itself every
month without a human reading it, so a hardcoded sentence like "revisions
usually run upward" would eventually sit above a chart showing the opposite.
Generating the prose from the same numbers that draw the bars means the words
and the picture cannot disagree.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import sys
from pathlib import Path

from validate_colours import PALETTE, contrast

ROOT = Path(__file__).resolve().parent.parent
PROCESSED = ROOT / "data" / "processed"
DOCS = ROOT / "docs"


class RenderError(RuntimeError):
    """Raised when there is not enough data to render an honest page."""


def esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def signed(value: float, places: int = 1) -> str:
    """Format with an explicit sign, because direction is the whole point.

    Exact zero gets no sign - "+0.0" implies an upward move that did not
    happen, and in this data an exact zero is common rather than incidental.
    """
    if value == 0:
        return f"{0:.{places}f}"
    return f"{value:+.{places}f}"


def pp(value: float, places: int = 1) -> str:
    """Format a percentage-point quantity."""
    return f"{abs(value):.{places}f}"


# --------------------------------------------------------------------------
# SVG primitives
# --------------------------------------------------------------------------

# The charts are drawn on a fixed viewBox and then scaled to the container
# width by CSS. This is what makes them survive 200% browser zoom without a
# horizontal scrollbar: the SVG has no intrinsic pixel width to overflow.
CHART_W = 760


def _nice_ticks(lo: float, hi: float, target: int = 4) -> list[float]:
    """Pick round tick values covering [lo, hi].

    Round numbers only (0.1, 0.2, 0.5, 1, 2, 5 ...) because axis labels that
    read 0.37 make a reader do arithmetic they should not have to do.
    """
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / target
    magnitude = 10 ** (len(f"{int(abs(raw))}") - 1) if abs(raw) >= 1 else 0.01
    for mult in (0.01, 0.02, 0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10, 20, 50):
        step = mult
        if step >= raw:
            break
    ticks = []
    t = step * int(lo / step) - step
    while t <= hi + step:
        if lo - 1e-9 <= t <= hi + 1e-9:
            ticks.append(round(t, 4))
        t += step
    return ticks or [lo, hi]


def _column_path(x: float, y_end: float, w: float, baseline: float, r: float = 4.0) -> str:
    """A column with a rounded data-end and a square baseline end.

    Rounding only the end that carries the value keeps the mark's meaning at
    the baseline exact - a rounded baseline would visually shorten the bar.
    """
    up = y_end < baseline
    r = min(r, w / 2, abs(baseline - y_end))
    if up:
        return (
            f"M{x:.2f},{baseline:.2f} L{x:.2f},{y_end + r:.2f} "
            f"Q{x:.2f},{y_end:.2f} {x + r:.2f},{y_end:.2f} "
            f"L{x + w - r:.2f},{y_end:.2f} "
            f"Q{x + w:.2f},{y_end:.2f} {x + w:.2f},{y_end + r:.2f} "
            f"L{x + w:.2f},{baseline:.2f} Z"
        )
    return (
        f"M{x:.2f},{baseline:.2f} L{x:.2f},{y_end - r:.2f} "
        f"Q{x:.2f},{y_end:.2f} {x + r:.2f},{y_end:.2f} "
        f"L{x + w - r:.2f},{y_end:.2f} "
        f"Q{x + w:.2f},{y_end:.2f} {x + w:.2f},{y_end - r:.2f} "
        f"L{x + w:.2f},{baseline:.2f} Z"
    )


def diverging_columns(
    *,
    chart_id: str,
    rows: list[tuple[str, float]],
    title: str,
    desc: str,
    unit: str = "percentage points",
    above_label: str = "above",
    below_label: str = "below",
) -> str:
    """A column chart of signed values around a zero baseline.

    Direction is encoded three times over: by which side of the baseline the
    column sits on (position), by hue (blue above, orange below), and by the
    sign in the data table underneath. Position is the primary channel, so the
    chart still reads correctly in greyscale or for a reader who cannot
    distinguish the two hues.
    """
    if not rows:
        raise RenderError(f"{chart_id}: no rows to plot")

    left, right, top, bottom = 52, 16, 18, 54
    height = 300
    plot_w = CHART_W - left - right
    plot_h = height - top - bottom

    values = [v for _, v in rows]
    vmax = max(max(values), 0.0)
    vmin = min(min(values), 0.0)
    # Pad the range slightly so the tallest column does not touch the frame,
    # and keep the scale symmetric so a +0.4 and a -0.4 look equally big.
    span = max(abs(vmax), abs(vmin)) * 1.18 or 0.1
    lo, hi = -span, span

    def y(v: float) -> float:
        return top + plot_h * (hi - v) / (hi - lo)

    baseline = y(0.0)
    band = plot_w / len(rows)
    bar_w = min(24.0, band * 0.62)

    parts: list[str] = []
    title_id, desc_id = f"{chart_id}-t", f"{chart_id}-d"
    parts.append(
        f'<svg class="chart" viewBox="0 0 {CHART_W} {height}" role="img" '
        f'aria-labelledby="{title_id} {desc_id}" xmlns="http://www.w3.org/2000/svg">'
    )
    parts.append(f'<title id="{title_id}">{esc(title)}</title>')
    parts.append(f'<desc id="{desc_id}">{esc(desc)}</desc>')

    # Gridlines and y labels.
    for t in _nice_ticks(lo, hi, 4):
        gy = y(t)
        if abs(t) < 1e-9:
            continue
        parts.append(
            f'<line x1="{left}" y1="{gy:.2f}" x2="{CHART_W - right}" y2="{gy:.2f}" '
            f'class="grid"/>'
        )
        parts.append(
            f'<text x="{left - 8}" y="{gy + 4:.2f}" class="tick" text-anchor="end">'
            f"{t:+.1f}</text>"
        )

    # Columns.
    hi_i = max(range(len(rows)), key=lambda i: values[i])
    lo_i = min(range(len(rows)), key=lambda i: values[i])
    for i, (label, v) in enumerate(rows):
        x = left + band * i + (band - bar_w) / 2
        cls = "above" if v >= 0 else "below"
        if v == 0:
            # A zero column would be invisible. Draw a short tick on the
            # baseline so the month is not silently missing from the chart.
            parts.append(
                f'<rect x="{x:.2f}" y="{baseline - 1:.2f}" width="{bar_w:.2f}" '
                f'height="2" class="zero-mark"/>'
            )
        else:
            parts.append(
                f'<path d="{_column_path(x, y(v), bar_w, baseline)}" class="{cls}"/>'
            )
        # Label only the two extremes - a number on every column is noise.
        if i in (hi_i, lo_i) and v != 0:
            ty = y(v) - 7 if v > 0 else y(v) + 15
            parts.append(
                f'<text x="{x + bar_w / 2:.2f}" y="{ty:.2f}" class="pointlabel" '
                f'text-anchor="middle">{signed(v)}</text>'
            )

    # Zero baseline, drawn last so it sits on top of the columns.
    parts.append(
        f'<line x1="{left}" y1="{baseline:.2f}" x2="{CHART_W - right}" '
        f'y2="{baseline:.2f}" class="axis"/>'
    )

    # X labels: thin them out so they never collide or overlap.
    step = max(1, round(len(rows) / 9))
    for i, (label, _) in enumerate(rows):
        if i % step:
            continue
        parts.append(
            f'<text x="{left + band * i + band / 2:.2f}" y="{height - bottom + 20}" '
            f'class="tick" text-anchor="middle">{esc(label)}</text>'
        )

    parts.append(
        f'<text x="{left}" y="{height - 8}" class="axistitle">'
        f"{esc(unit)} &#8212; {esc(above_label)} above the line, "
        f"{esc(below_label)} below</text>"
    )
    parts.append("</svg>")
    return "".join(parts)


def emphasis_bars(
    *,
    chart_id: str,
    rows: list[tuple[str, float, bool]],
    title: str,
    desc: str,
    unit: str,
) -> str:
    """Horizontal bars where exactly one bar is the point and the rest are context.

    The data-visualisation guidance calls this the "emphasis" form, and it is
    the right one here: the question is not "how do these three compare in
    general" but "did the advance estimate beat the naive rules". One bar
    carries the accent hue, the others recede to neutral grey.
    """
    # The bar is capped at 24px and sits inside a taller slot, so the leftover
    # band reads as air rather than the bars filling their rows edge to edge.
    slot_h, bar_h, gap = 40, 24, 14
    left, right, top = 210, 70, 10
    height = top + len(rows) * (slot_h + gap) + 18
    plot_w = CHART_W - left - right
    vmax = max(v for _, v, _ in rows) * 1.1 or 1.0

    title_id, desc_id = f"{chart_id}-t", f"{chart_id}-d"
    parts = [
        f'<svg class="chart" viewBox="0 0 {CHART_W} {height}" role="img" '
        f'aria-labelledby="{title_id} {desc_id}" xmlns="http://www.w3.org/2000/svg">',
        f'<title id="{title_id}">{esc(title)}</title>',
        f'<desc id="{desc_id}">{esc(desc)}</desc>',
    ]
    for i, (label, value, is_focus) in enumerate(rows):
        slot_top = top + i * (slot_h + gap)
        y0 = slot_top + (slot_h - bar_h) / 2
        mid = slot_top + slot_h / 2 + 5
        w = plot_w * value / vmax
        parts.append(
            f'<text x="{left - 12}" y="{mid:.2f}" class="rowlabel" '
            f'text-anchor="end">{esc(label)}</text>'
        )
        # 4px rounded data-end, square at the baseline.
        r = min(4.0, w)
        parts.append(
            f'<path d="M{left},{y0:.2f} L{left + max(0, w - r):.2f},{y0:.2f} '
            f'Q{left + w:.2f},{y0:.2f} {left + w:.2f},{y0 + r:.2f} '
            f'L{left + w:.2f},{y0 + bar_h - r:.2f} '
            f'Q{left + w:.2f},{y0 + bar_h:.2f} {left + max(0, w - r):.2f},{y0 + bar_h:.2f} '
            f'L{left},{y0 + bar_h:.2f} Z" class="{"focus" if is_focus else "context"}"/>'
        )
        parts.append(
            f'<text x="{left + w + 10:.2f}" y="{mid:.2f}" '
            f'class="pointlabel">{value:.2f}</text>'
        )
    parts.append(
        f'<text x="{left}" y="{height - 2}" class="axistitle">{esc(unit)}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def step_line(
    *, chart_id: str, rows: list[tuple[str, float]], title: str, desc: str, unit: str
) -> str:
    """A single-series line. Used only for the policy-rate context strip.

    One series, so no legend box - the heading already says what is plotted.
    """
    left, right, top, bottom = 52, 16, 16, 46
    height = 210
    plot_w = CHART_W - left - right
    plot_h = height - top - bottom
    values = [v for _, v in rows]
    lo, hi = min(values), max(values)
    pad = (hi - lo) * 0.15 or 0.5
    lo, hi = lo - pad, hi + pad

    def y(v: float) -> float:
        return top + plot_h * (hi - v) / (hi - lo)

    def x(i: int) -> float:
        return left + plot_w * i / max(1, len(rows) - 1)

    title_id, desc_id = f"{chart_id}-t", f"{chart_id}-d"
    parts = [
        f'<svg class="chart" viewBox="0 0 {CHART_W} {height}" role="img" '
        f'aria-labelledby="{title_id} {desc_id}" xmlns="http://www.w3.org/2000/svg">',
        f'<title id="{title_id}">{esc(title)}</title>',
        f'<desc id="{desc_id}">{esc(desc)}</desc>',
    ]
    for t in _nice_ticks(lo, hi, 4):
        gy = y(t)
        parts.append(
            f'<line x1="{left}" y1="{gy:.2f}" x2="{CHART_W - right}" y2="{gy:.2f}" class="grid"/>'
        )
        parts.append(
            f'<text x="{left - 8}" y="{gy + 4:.2f}" class="tick" text-anchor="end">{t:.2f}</text>'
        )
    pts = " ".join(f"{x(i):.2f},{y(v):.2f}" for i, (_, v) in enumerate(rows))
    parts.append(f'<polyline points="{pts}" class="line"/>')
    # End marker plus a direct label, so the current value is readable without
    # tracing the line back to an axis.
    lx, ly = x(len(rows) - 1), y(values[-1])
    parts.append(f'<circle cx="{lx:.2f}" cy="{ly:.2f}" r="5" class="endmark"/>')
    parts.append(
        f'<text x="{lx - 10:.2f}" y="{ly - 12:.2f}" class="pointlabel" '
        f'text-anchor="end">{values[-1]:.2f}%</text>'
    )
    step = max(1, round(len(rows) / 8))
    for i, (label, _) in enumerate(rows):
        if i % step and i != len(rows) - 1:
            continue
        parts.append(
            f'<text x="{x(i):.2f}" y="{height - bottom + 20}" class="tick" '
            f'text-anchor="middle">{esc(label)}</text>'
        )
    parts.append(f'<text x="{left}" y="{height - 6}" class="axistitle">{esc(unit)}</text>')
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------------------
# Page components
# --------------------------------------------------------------------------


def data_table(caption: str, headers: list[str], rows: list[list[str]]) -> str:
    """A real <table> holding the numbers behind a chart.

    Wrapped in <details> by the caller. This is the accessible twin of every
    chart on the page: anyone using a screen reader, or anyone who simply
    wants the figures, gets them without relying on the picture.
    """
    head = "".join(f'<th scope="col">{esc(h)}</th>' for h in headers)
    body = []
    for r in rows:
        cells = f'<th scope="row">{esc(r[0])}</th>' + "".join(
            f"<td>{esc(c)}</td>" for c in r[1:]
        )
        body.append(f"<tr>{cells}</tr>")
    return (
        f'<div class="tablewrap"><table><caption>{esc(caption)}</caption>'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    )


def figure(
    *,
    heading: str,
    what: str,
    why: str,
    takeaway: str,
    svg: str,
    legend: str,
    table_summary: str,
    table_html: str,
    extra: str = "",
) -> str:
    """One chart block: heading, plain-language framing, chart, table.

    The takeaway sits ABOVE the chart on purpose. If someone cannot see the
    chart at all - screen reader, images-off, or just skimming - the finding
    reaches them first, and the chart becomes supporting evidence rather than
    the only carrier of the point.
    """
    return f"""
<section class="figure">
  <h3>{esc(heading)}</h3>
  <p class="takeaway">{esc(takeaway)}</p>
  <dl class="framing">
    <dt>What this shows</dt><dd>{esc(what)}</dd>
    <dt>Why it matters</dt><dd>{esc(why)}</dd>
  </dl>
  {extra}
  {legend}
  {svg}
  <details>
    <summary>Show the numbers behind this chart</summary>
    <p class="tablenote">{esc(table_summary)}</p>
    {table_html}
  </details>
</section>"""


def legend_two(above: str, below: str) -> str:
    """A legend for the diverging pair.

    Present because two series are on screen, and identity must never rest on
    colour-matching alone. Each swatch has a shape and a written label beside
    it, and the words repeat the positional rule.
    """
    return (
        '<p class="legend">'
        '<span class="key"><span class="swatch above" aria-hidden="true"></span>'
        f"{esc(above)}</span>"
        '<span class="key"><span class="swatch below" aria-hidden="true"></span>'
        f"{esc(below)}</span></p>"
    )


def stat_tile(value: str, label: str, note: str) -> str:
    return (
        f'<div class="tile"><p class="tilevalue">{esc(value)}</p>'
        f'<p class="tilelabel">{esc(label)}</p>'
        f'<p class="tilenote">{esc(note)}</p></div>'
    )


# --------------------------------------------------------------------------
# CSS
# --------------------------------------------------------------------------


def build_css() -> str:
    lightv = PALETTE["light"]
    darkv = PALETTE["dark"]

    def block(p: dict) -> str:
        return "\n".join(
            f"    --{k.replace('_', '-')}: {v};" for k, v in p.items()
        )

    return f"""
/* Colours come from src/validate_colours.py, which computes every contrast
   ratio quoted in the README. Changing a value here without re-running that
   script would invalidate the accessibility claims. */
:root {{
  color-scheme: light dark;
{block(lightv)}
  --maxw: 54rem;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
{block(darkv)}
  }}
}}

*, *::before, *::after {{ box-sizing: border-box; }}

html {{
  /* No fixed pixel font size, so the page honours the reader's own browser
     font-size setting instead of overriding it. */
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  line-height: 1.6;
  background: var(--page);
  color: var(--text-primary);
  -webkit-text-size-adjust: 100%;
}}
body {{ margin: 0; padding: 0 1rem 4rem; }}
.wrap {{ max-width: var(--maxw); margin: 0 auto; }}

h1, h2, h3 {{ line-height: 1.25; text-wrap: balance; }}
h1 {{ font-size: clamp(1.6rem, 1.2rem + 2vw, 2.4rem); margin: 2rem 0 0.5rem; }}
h2 {{ font-size: clamp(1.25rem, 1.05rem + 1vw, 1.6rem); margin: 3rem 0 0.75rem;
     padding-top: 1.25rem; border-top: 2px solid var(--grid); }}
h3 {{ font-size: clamp(1.05rem, 0.98rem + 0.5vw, 1.25rem); margin: 0 0 0.5rem; }}
p {{ margin: 0 0 1rem; }}

a {{ color: var(--above); }}
a:focus-visible, summary:focus-visible, details:focus-visible {{
  outline: 3px solid var(--above);
  outline-offset: 2px;
}}

.lede {{ font-size: 1.12rem; color: var(--text-primary); }}
.sub {{ color: var(--text-secondary); }}
.meta {{ color: var(--text-secondary); font-size: 0.92rem; }}

/* ---- key numbers -------------------------------------------------- */
/* Two-up, then four-up. Explicit breakpoints rather than auto-fit, because
   auto-fit leaves a lone orphan tile on its own row at in-between widths -
   including at 200% zoom, which is exactly where it must not look broken. */
.tiles {{
  display: grid; gap: 1rem; margin: 1.5rem 0 0;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}}
@media (max-width: 24rem) {{
  .tiles {{ grid-template-columns: minmax(0, 1fr); }}
}}
@media (min-width: 48rem) {{
  .tiles {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }}
}}
.tile {{
  background: var(--surface); border: 1px solid var(--grid);
  border-radius: 10px; padding: 1rem 1.1rem;
}}
.tilevalue {{
  font-size: 2rem; font-weight: 600; margin: 0 0 0.15rem;
  color: var(--text-primary); letter-spacing: -0.01em;
}}
.tilelabel {{ margin: 0 0 0.35rem; font-weight: 600; }}
.tilenote {{ margin: 0; font-size: 0.88rem; color: var(--text-secondary); }}

/* ---- figures ------------------------------------------------------ */
.figure {{
  background: var(--surface); border: 1px solid var(--grid);
  border-radius: 12px; padding: 1.25rem; margin: 1.5rem 0;
}}
.takeaway {{
  font-size: 1.05rem; font-weight: 600; margin: 0 0 0.85rem;
  color: var(--text-primary);
}}
.framing {{ margin: 0 0 1rem; font-size: 0.95rem; }}
.framing dt {{
  font-weight: 600; color: var(--text-secondary);
  font-size: 0.78rem; letter-spacing: 0.06em; text-transform: uppercase;
}}
.framing dd {{ margin: 0.1rem 0 0.6rem; color: var(--text-secondary); }}
.caveat {{
  font-size: 0.95rem; color: var(--text-secondary);
  border-left: 3px solid var(--axis); padding: 0.1rem 0 0.1rem 0.85rem;
  margin: 0 0 1rem;
}}
.caveat strong {{ color: var(--text-primary); }}

.chart {{ width: 100%; height: auto; display: block; overflow: visible; }}

.legend {{ display: flex; flex-wrap: wrap; gap: 1.25rem; margin: 0 0 0.5rem;
           font-size: 0.9rem; color: var(--text-secondary); }}
.key {{ display: inline-flex; align-items: center; gap: 0.45rem; }}
.swatch {{ width: 0.85rem; height: 0.85rem; border-radius: 3px; flex: none; }}
.swatch.above {{ background: var(--above); }}
.swatch.below {{ background: var(--below); }}

/* SVG mark styles. Kept in CSS rather than inline attributes so that light
   and dark mode swap with a single custom-property change. */
.chart .above {{ fill: var(--above); }}
.chart .below {{ fill: var(--below); }}
.chart .focus {{ fill: var(--above); }}
.chart .context {{ fill: var(--neutral); }}
.chart .zero-mark {{ fill: var(--axis); }}
.chart .grid {{ stroke: var(--grid); stroke-width: 1; }}
.chart .axis {{ stroke: var(--axis); stroke-width: 1.5; }}
.chart .line {{ fill: none; stroke: var(--above); stroke-width: 2;
                stroke-linejoin: round; stroke-linecap: round; }}
.chart .endmark {{ fill: var(--above); stroke: var(--surface); stroke-width: 2; }}
.chart text {{ font-family: inherit; }}
.chart .tick {{ font-size: 12px; fill: var(--text-muted);
                font-variant-numeric: tabular-nums; }}
.chart .rowlabel {{ font-size: 13px; fill: var(--text-secondary); }}
.chart .pointlabel {{ font-size: 12.5px; font-weight: 600; fill: var(--text-primary);
                      font-variant-numeric: tabular-nums; }}
.chart .axistitle {{ font-size: 11.5px; fill: var(--text-muted); }}

/* ---- tables ------------------------------------------------------- */
details {{ margin-top: 1rem; border-top: 1px solid var(--grid); padding-top: 0.75rem; }}
summary {{ cursor: pointer; font-weight: 600; font-size: 0.92rem;
           color: var(--text-secondary); }}
summary::marker {{ color: var(--text-muted); }}
.tablenote {{ font-size: 0.9rem; color: var(--text-secondary); margin: 0.75rem 0 0.5rem; }}
/* Wide tables scroll inside their own box so the PAGE never scrolls
   sideways - a requirement at 200% zoom. */
.tablewrap {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem;
         font-variant-numeric: tabular-nums; }}
caption {{ text-align: left; color: var(--text-secondary); font-size: 0.88rem;
           padding-bottom: 0.5rem; }}
th, td {{ text-align: right; padding: 0.4rem 0.6rem;
          border-bottom: 1px solid var(--grid); white-space: nowrap; }}
th[scope="row"] {{ text-align: left; font-weight: 500; }}
thead th {{ color: var(--text-secondary); font-size: 0.82rem; }}

/* ---- glossary & footer -------------------------------------------- */
.steps {{ padding-left: 1.2rem; }}
.steps li {{ margin-bottom: 0.5rem; color: var(--text-secondary); }}
.steps strong {{ color: var(--text-primary); }}
.glossary dt {{ font-weight: 600; margin-top: 0.85rem; }}
.glossary dd {{ margin: 0.15rem 0 0; color: var(--text-secondary); }}
.callout {{ background: var(--surface); border: 1px solid var(--grid);
            border-left: 4px solid var(--below); border-radius: 8px;
            padding: 1rem 1.1rem; margin: 2rem 0; }}
/* The callout carries its own left rule, so its heading drops the section
   border it would otherwise inherit from h2. */
.callout h2 {{ border-top: none; padding-top: 0; margin-top: 0;
               font-size: 1.15rem; }}
footer {{ margin-top: 3rem; padding-top: 1.25rem; border-top: 2px solid var(--grid);
          color: var(--text-secondary); font-size: 0.9rem; }}

/* Nothing on this page animates, but a reader who has asked for reduced
   motion should be insulated from anything a browser might add. */
@media (prefers-reduced-motion: reduce) {{
  *, *::before, *::after {{
    animation-duration: 0.001ms !important; animation-iteration-count: 1 !important;
    transition-duration: 0.001ms !important; scroll-behavior: auto !important;
  }}
}}
@media print {{
  .figure, .tile {{ break-inside: avoid; border-color: #999; }}
  details {{ display: block; }}
  details > summary {{ display: none; }}
}}
"""


# --------------------------------------------------------------------------
# Prose generated from the numbers
# --------------------------------------------------------------------------


def describe_advance(stats: dict, paired: dict, directional: dict) -> str:
    a = stats["advance"]
    beat_rw = paired.get("skill_vs_rw")
    verdict = (
        "better than simply repeating last month's number"
        if beat_rw is not None and beat_rw > 0
        else "no better than simply repeating last month's number"
    )
    return (
        f"Across {a['n']} months, StatCan's advance estimate missed the first "
        f"official figure by {a['mae']:.2f} percentage points on average, which is "
        f"{verdict}."
    )


def describe_headline(stats: dict, paired: dict, rb: dict) -> str:
    """The opening paragraph, in ordinary language, stating the finding.

    Written as a branch rather than a fixed sentence because the page rebuilds
    itself monthly. If the revision bias later stops being distinguishable
    from zero, or the advance estimate stops beating the naive rule, this
    paragraph changes with the data instead of quietly becoming false.
    """
    a, r = stats["advance"], stats["revision"]
    skill = paired.get("skill_vs_rw")

    if skill is not None and skill > 0:
        forecast = (
            f"The forecast turns out to be the reassuring part: the advance estimate "
            f"misses the official figure by {a['mae']:.2f} percentage points on average, "
            f"and beats a rule as simple as repeating last month's number by "
            f"{skill * 100:.0f}%."
        )
    else:
        forecast = (
            f"The advance estimate misses the official figure by {a['mae']:.2f} "
            f"percentage points on average, and does not beat a rule as simple as "
            f"repeating last month's number."
        )

    if r["mean_differs_from_zero"]:
        direction = "up" if r["mean"] > 0 else "down"
        opposite = "understated" if r["mean"] > 0 else "overstated"
        # Whether this reads as "systematically" or as "on average, though the
        # evidence is not decisive" depends on the robustness check. Claiming
        # the strong version when the weak one is what the data supports would
        # be the single easiest way to discredit this whole page.
        if rb.get("applicable") and not rb.get("later_significant"):
            strength = (
                f"On average, the number everyone reacts to on release day has "
                f"{opposite} how the economy actually did &#8212; though, as the "
                f"revisions section explains, that lean rests heavily on the "
                f"post-pandemic recovery months and is suggestive rather than settled."
            )
        else:
            strength = (
                f"In other words, the number everyone reacts to on release day "
                f"systematically {opposite} how the economy actually did."
            )
        revision = (
            f"The unsettling part is what happens afterwards. The published figure "
            f"moves by {r['mae']:.2f} points on average once it is revised &#8212; more "
            f"than the advance estimate was ever wrong by &#8212; and it moves "
            f"<strong>{direction}</strong> more often than not, by an average of "
            f"{pp(r['mean'], 2)} points. {strength}"
        )
    else:
        revision = (
            f"Afterwards, the published figure still moves by {r['mae']:.2f} points on "
            f"average once it is revised, though on this sample the revisions do not "
            f"lean clearly in either direction."
        )

    return f"{forecast} {revision}"


def describe_robustness(rb: dict) -> str:
    """State plainly whether the revision bias survives dropping the oldest months.

    Included because the headline result does NOT survive cleanly on this
    sample, and a page that reported only the full-sample t-statistic would be
    overclaiming. Saying so is the difference between a finding and a
    coincidence dressed up as one.
    """
    if not rb.get("applicable"):
        return ""
    if rb["full_significant"] and not rb["later_significant"]:
        return (
            f"That lean is not evenly spread across the sample. Dropping the earliest "
            f"{rb['drop_first']} months &#8212; the post-pandemic recovery, when GDP was "
            f"moving fast and source data was unusually incomplete &#8212; the average "
            f"revision is still upward at {signed(rb['later_mean'], 2)} points, but it is "
            f"no longer large enough relative to its spread to rule out chance "
            f"(t = {rb['later_t']:+.2f}, n = {rb['later_n']}). The direction is "
            f"consistent; the strength of the evidence for it is not. Treat the upward "
            f"lean as suggestive rather than settled."
        )
    if rb["full_significant"] and rb["later_significant"]:
        return (
            f"The lean survives a robustness check: dropping the earliest "
            f"{rb['drop_first']} months of the sample, the average revision is still "
            f"{signed(rb['later_mean'], 2)} points and still distinguishable from zero "
            f"(t = {rb['later_t']:+.2f}, n = {rb['later_n']})."
        )
    same = "the same direction" if rb["sign_agrees"] else "the opposite direction"
    return (
        f"Dropping the earliest {rb['drop_first']} months, the average revision is "
        f"{signed(rb['later_mean'], 2)} points (t = {rb['later_t']:+.2f}, "
        f"n = {rb['later_n']}) &#8212; {same} as the full sample, and likewise too "
        f"small relative to its spread to call."
    )


def describe_revision(stats: dict) -> str:
    r = stats["revision"]
    direction = "upward" if r["mean"] > 0 else "downward"
    if r["mean_differs_from_zero"]:
        lean = (
            f"and they lean {direction}: the average revision is "
            f"{signed(r['mean'], 2)} points, which is large enough relative to its "
            f"spread to be unlikely to be chance (t = {r['t_stat']:+.2f})"
        )
    else:
        lean = (
            f"and they do not clearly lean either way: the average revision is "
            f"{signed(r['mean'], 2)} points, too small relative to its spread to "
            f"distinguish from zero (t = {r['t_stat']:+.2f})"
        )
    return (
        f"Revisions move the published figure by {r['mae']:.2f} percentage points "
        f"on average, {lean}."
    )


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------


def build_page(data: dict) -> str:
    records = data["records"]
    stats = data["stats"]
    paired = data["paired"]
    directional = data["directional"]
    coverage = data["coverage"]

    adv_rows = [(r["label"], r["surprise"]) for r in records if r["surprise"] is not None]
    rev_rows = [(r["label"], r["revision"]) for r in records if r["revision"] is not None]
    if not adv_rows or not rev_rows:
        raise RenderError("Nothing to plot - refusing to write an empty page.")

    a, r = stats["advance"], stats["revision"]

    # The robustness caveat is rendered as a marked-up aside rather than folded
    # into the takeaway sentence, so a reader skimming the finding cannot miss
    # the qualification attached to it.
    rb_text = describe_robustness(data.get("robustness", {}))
    robustness_html = (
        f'<p class="caveat"><strong>Read this with the finding above:</strong> '
        f"{rb_text}</p>"
        if rb_text
        else ""
    )

    # ---- charts -------------------------------------------------------
    chart_surprise = diverging_columns(
        chart_id="c-surprise",
        rows=adv_rows,
        title="Advance estimate error by month",
        desc=(
            "Column chart. Each column is one month: how far the first official "
            "GDP figure came in above or below StatCan's own advance estimate, in "
            "percentage points. Columns above the zero line mean the economy did "
            "better than the advance estimate said; below means worse. "
            f"Average absolute miss {a['mae']:.2f} points over {a['n']} months. "
            "The full figures are in the data table below this chart."
        ),
        above_label="stronger than the advance estimate",
        below_label="weaker",
    )

    chart_revision = diverging_columns(
        chart_id="c-revision",
        rows=rev_rows,
        title="Revision to the first published figure, by month",
        desc=(
            "Column chart. Each column is one month: how far the current, revised "
            "GDP figure sits above or below the number first published, in "
            "percentage points. Columns above the zero line were revised up; below "
            f"were revised down. Average absolute revision {r['mae']:.2f} points "
            f"over {r['n']} months. The full figures are in the data table below."
        ),
        above_label="revised up",
        below_label="revised down",
    )

    score_rows = [
        ("StatCan advance estimate", paired["advance_mae"], True),
        ("Naive: repeat last month", paired["naive_rw_mae"], False),
        ("Naive: 3-month average", paired["naive_ma3_mae"], False),
    ]
    chart_score = emphasis_bars(
        chart_id="c-score",
        rows=score_rows,
        title="Average miss: advance estimate against two naive rules",
        desc=(
            "Horizontal bar chart comparing average absolute error in percentage "
            f"points over the same {paired['n']} months. StatCan's advance estimate "
            f"{paired['advance_mae']:.2f}; repeating last month's figure "
            f"{paired['naive_rw_mae']:.2f}; a three-month average "
            f"{paired['naive_ma3_mae']:.2f}. Shorter is better."
        ),
        unit="average absolute error, percentage points (shorter is better)",
    )

    # ---- policy rate context -----------------------------------------
    months_shown = {rec["month"] for rec in records}
    rate_rows = [
        (dt.date(int(p["month"][:4]), int(p["month"][5:7]), 1).strftime("%b %Y"), p["rate"])
        for p in data["policy_rate"]
        if p["month"] >= min(months_shown)
    ]
    chart_rate = step_line(
        chart_id="c-rate",
        rows=rate_rows,
        title="Bank of Canada target for the overnight rate",
        desc=(
            "Line chart of the Bank of Canada's policy interest rate over the same "
            f"period, ending at {rate_rows[-1][1]:.2f} percent in {rate_rows[-1][0]}. "
            "Shown as background context only; it is not scored anywhere on this page."
        ),
        unit="percent, month-end value",
    )

    # ---- tables -------------------------------------------------------
    main_table = data_table(
        "Every month in the scorecard. Blank cells mean the figure was not "
        "available for that month.",
        [
            "Month",
            "Advance estimate (%)",
            "First print (%)",
            "Latest revised (%)",
            "Surprise (pp)",
            "Revision (pp)",
        ],
        [
            [
                rec["label"],
                signed(rec["advance"]) if rec["advance"] is not None else "—",
                signed(rec["first_print"]),
                signed(rec["revised"]) if rec["revised"] is not None else "—",
                signed(rec["surprise"]) if rec["surprise"] is not None else "—",
                signed(rec["revision"]) if rec["revision"] is not None else "—",
            ]
            for rec in records
        ],
    )

    score_table = data_table(
        "Average absolute error over the months where all three could be "
        "compared. Lower is better.",
        ["Method", "Average miss (pp)", "Months compared"],
        [
            ["StatCan advance estimate", f"{paired['advance_mae']:.2f}", str(paired["n"])],
            ["Naive: repeat last month", f"{paired['naive_rw_mae']:.2f}", str(paired["n"])],
            ["Naive: 3-month average", f"{paired['naive_ma3_mae']:.2f}", str(paired["n"])],
        ],
    )

    rate_table = data_table(
        "Bank of Canada target for the overnight rate, month-end.",
        ["Month", "Policy rate (%)"],
        [[m, f"{v:.2f}"] for m, v in rate_rows],
    )

    # ---- headline numbers --------------------------------------------
    within_01 = sum(1 for _, v in adv_rows if abs(v) <= 0.1)
    rev_up = sum(1 for _, v in rev_rows if v > 0)
    rev_down = sum(1 for _, v in rev_rows if v < 0)
    biggest = max(adv_rows, key=lambda kv: abs(kv[1]))

    # The revision bias leads, because it is the larger effect and the only one
    # that is statistically distinguishable from zero.
    tiles = "".join(
        [
            stat_tile(
                f"{signed(r['mean'], 2)} pp",
                "Average revision, and it leans up",
                # Deliberately not claiming significance on the tile. The
                # full-sample t-statistic does not survive dropping the
                # recovery months, and a stat tile is exactly where an
                # over-strong claim would get screenshotted out of context.
                f"{rev_up} up, {rev_down} down"
                + (
                    " over the full sample; see the caveat below."
                    if data.get("robustness", {}).get("applicable")
                    and not data["robustness"].get("later_significant")
                    else f". Unlikely to be chance (t = {r['t_stat']:+.2f})."
                    if r["mean_differs_from_zero"]
                    else ". Too small to call."
                ),
            ),
            stat_tile(
                f"{r['mae']:.2f} pp",
                "Typical size of a revision",
                f"Bigger than the advance estimate's average miss of {a['mae']:.2f} pp.",
            ),
            stat_tile(
                f"{a['mae']:.2f} pp",
                "Average advance-estimate miss",
                f"Over {a['n']} months, with {within_01} of them within 0.1 points.",
            ),
            stat_tile(
                f"{signed(biggest[1])} pp",
                "Largest single miss",
                f"{biggest[0]}.",
            ),
        ]
    )

    # ---- the summary paragraph ---------------------------------------
    skill_rw = paired.get("skill_vs_rw")
    if skill_rw is not None and skill_rw > 0:
        skill_sentence = (
            f"The advance estimate does beat both naive rules, cutting the average "
            f"miss by {skill_rw * 100:.0f}% against simply repeating last month's number."
        )
    else:
        skill_sentence = (
            "The advance estimate does not beat the naive rules on this sample, "
            "which is a genuine finding rather than a bug."
        )

    lede = (
        f"Every month, Statistics Canada tells you how the economy did &#8212; three "
        f"times, and not always the same thing. "
        f"{describe_headline(stats, paired, data.get('robustness', {}))}"
    )

    generated = data["generated"]
    period = f"{records[0]['label']} to {records[-1]['label']}"

    parse_note = ""
    if coverage["parse_failures"]:
        items = "".join(
            f"<li>{esc(f['release_date'])}: {esc('; '.join(f['problems']))}</li>"
            for f in coverage["parse_failures"]
        )
        parse_note = (
            '<div class="callout"><h2>Releases this page could not read</h2>'
            "<p>These StatCan bulletins used wording the parser did not recognise, "
            "so they are excluded from every figure above. They are listed here "
            "rather than dropped silently, because quietly discarding the "
            "awkwardly-worded months would bias the results toward the tidy ones.</p>"
            f"<ul>{items}</ul></div>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Canada Data Surprise Scorecard &#8212; how well was Canadian economic data anticipated?</title>
<meta name="description" content="Scoring Statistics Canada's advance GDP estimates against the official first print, and measuring how much the published figures change afterwards. Built from free, open data.">
<style>{build_css()}</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>Canada Data Surprise Scorecard</h1>
  <p class="lede">{lede}</p>
  <p class="meta">Monthly real GDP by industry, {esc(period)}.
     Sources: Statistics Canada and the Bank of Canada, both open-licensed.
     Last rebuilt {esc(generated)}.</p>
</header>

<main>
  <h2>How a GDP number gets published</h2>
  <p>The same month's growth is reported three separate times, and the differences
     between them are what this page measures.</p>
  <ol class="steps">
    <li><strong>The advance estimate.</strong> Roughly a month before the official
        figure, StatCan publishes a preliminary number based on incomplete data. It
        appears inside the previous month's release.</li>
    <li><strong>The first print.</strong> The first official figure. This is the one
        that makes headlines and that markets trade on.</li>
    <li><strong>The revisions.</strong> As fuller source data arrives, the figure is
        quietly corrected &#8212; for years afterwards.</li>
  </ol>

  <h2>The headline numbers</h2>
  <div class="tiles">{tiles}</div>

  <h2>The part nobody watches: what happens after the headline</h2>
  {figure(
      heading="Revisions to the first published figure",
      what=(
          "For each month, how far today's revised GDP figure sits from the number "
          "that was first published."
      ),
      why=(
          "Markets, newsrooms and policymakers react to the first print. If that "
          "number moves materially afterwards, decisions were made on a figure that "
          "no longer exists."
      ),
      takeaway=describe_revision(stats),
      extra=robustness_html,
      svg=chart_revision,
      legend=legend_two(
          "Above the line: revised up, the month was better than first reported",
          "Below the line: revised down",
      ),
      table_summary=(
          "Positive means the figure was later revised upward. This is the full "
          "scorecard table; the revision column is the one plotted here."
      ),
      table_html=main_table,
  )}

  <h2>How good is the advance estimate?</h2>
  {figure(
      heading="Advance estimate against the first official figure",
      what=(
          "For each month, the gap between StatCan's early advance estimate of GDP "
          "growth and the official figure it published a month later."
      ),
      why=(
          "The advance estimate is the first hard signal anyone gets about the "
          "month. If it is reliably close, decisions can be made a month earlier "
          "than they otherwise could."
      ),
      takeaway=describe_advance(stats, paired, directional),
      svg=chart_surprise,
      legend=legend_two(
          "Above the line: economy stronger than the advance estimate said",
          "Below the line: weaker",
      ),
      table_summary=(
          "Positive means the official figure came in above the advance estimate."
      ),
      table_html=main_table,
  )}

  {figure(
      heading="Does the advance estimate beat just guessing?",
      what=(
          "The advance estimate's average miss, next to two rules that use no "
          "judgement at all: repeat last month's growth, or average the last three "
          "months."
      ),
      why=(
          "A forecast is only worth producing if it beats a rule anyone could apply "
          "in their head. This is the benchmark every other number on the page is "
          "measured against."
      ),
      takeaway=(
          f"{skill_sentence} Both naive rules are computed only from figures that "
          f"had actually been published at the time, so they are not given "
          f"information from the future."
      ),
      svg=chart_score,
      legend="",
      table_summary=(
          "Average absolute error in percentage points, lower is better, over the "
          f"{paired['n']} months where all three methods could be compared."
      ),
      table_html=score_table,
  )}

  <h2>What the Bank of Canada was doing</h2>
  {figure(
      heading="Policy interest rate over the same period",
      what="The Bank of Canada's target for the overnight rate, month by month.",
      why=(
          "Context, not a score. It shows what the interest-rate backdrop was while "
          "these forecast errors were being made."
      ),
      takeaway=(
          f"The policy rate stood at {rate_rows[-1][1]:.2f}% in {rate_rows[-1][0]}, "
          f"against {rate_rows[0][1]:.2f}% at the start of the period shown. "
          "Nothing on this page scores the Bank of Canada."
      ),
      svg=chart_rate,
      legend="",
      table_summary="Month-end value of the target for the overnight rate.",
      table_html=rate_table,
  )}

  {parse_note}

  <h2>What the words mean</h2>
  <dl class="glossary">
    <dt>Advance estimate</dt>
    <dd>A preliminary figure Statistics Canada publishes for the following month,
        inside each monthly GDP release, based on incomplete source data. It is an
        official, on-the-record forecast from the agency itself.</dd>
    <dt>First print</dt>
    <dd>The first official figure for a month. This is the number that appears in
        headlines and that markets react to on release day.</dd>
    <dt>Revision</dt>
    <dd>A later change to an already-published figure, as more complete source data
        arrives. Revisions continue for years after the first print.</dd>
    <dt>Percentage point (pp)</dt>
    <dd>The unit for a gap between two percentages. If the advance estimate said
        growth of 0.2% and the official figure was 0.5%, the miss is 0.3 percentage
        points.</dd>
    <dt>Naive benchmark</dt>
    <dd>A deliberately simple rule &#8212; here, repeating last month's figure, or
        averaging the last three. Any forecast that cannot beat it is not adding
        information.</dd>
  </dl>

  <h2>How this was built, and what it cannot tell you</h2>
  <p>Every number above comes from two free, openly-licensed sources: the
     <a href="https://www150.statcan.gc.ca/t1/wds/rest/">Statistics Canada Web Data
     Service</a> (table 36-10-0434-01, monthly GDP by industry, seasonally adjusted,
     chained 2017 dollars) and the
     <a href="https://www.bankofcanada.ca/valet/docs">Bank of Canada Valet API</a>.
     No paid or licensed forecast data is used anywhere.</p>
  <p>The advance estimates and first prints are not available from the API at all.
     Statistics Canada's data service returns only current, fully-revised values, so
     it cannot tell you what a figure said on the day it was released. Those numbers
     were recovered from <a href="https://www150.statcan.gc.ca/n1/dai-quo/index-eng.htm">The
     Daily</a>, StatCan's official release bulletin, which states both figures on the
     record in every monthly GDP release.</p>
  <ul>
    <li><strong>Rounding limits the precision.</strong> StatCan publishes these
        changes to one decimal place, so revisions smaller than 0.1 percentage
        points are invisible to this method.</li>
    <li><strong>The sample is short.</strong> {a['n']} months is enough to describe
        what happened but not enough to settle whether a small average bias is real.
        Where a figure is too small to distinguish from zero, the page says so.</li>
    <li><strong>Recent months are under-revised.</strong> The most recent months have
        had less time to be revised than older ones, which mechanically shrinks their
        revisions.</li>
    <li><strong>CPI and the Labour Force Survey are not scored here.</strong>
        Neither publishes an advance estimate, so the central comparison on this page
        does not exist for them. Adding them would have meant a different, weaker
        chart rather than the same one with more data.</li>
  </ul>
</main>

<footer>
  <p>Built by a fourth-year Bachelor of Commerce student at the DeGroote School of
     Business, McMaster University, as a free-data rebuild of the ideas in the
     Bloomberg Market Concepts Economic Indicators module. No Bloomberg data is
     used anywhere in this project.</p>
  <p>Contains information licensed under the
     <a href="https://www.statcan.gc.ca/en/reference/licence">Statistics Canada Open
     Licence</a>. Bank of Canada data used under the
     <a href="https://www.bankofcanada.ca/terms/">Bank of Canada Terms of Use</a>.
     This page is not endorsed by, or affiliated with, either institution.</p>
  <p>Rebuilt automatically each month. Page generated {esc(generated)}.</p>
</footer>
</div>
</body>
</html>
"""


def main() -> int:
    path = PROCESSED / "scorecard.json"
    if not path.exists():
        raise RenderError(f"Missing {path}. Run `python src/analyze.py` first.")
    data = json.loads(path.read_text(encoding="utf-8"))

    page = build_page(data)

    # A page this size means something has gone wrong upstream; better to fail
    # than to publish a shell.
    if len(page) < 12_000:
        raise RenderError(f"Rendered page is only {len(page)} bytes - refusing to publish.")

    DOCS.mkdir(parents=True, exist_ok=True)
    out = DOCS / "index.html"
    out.write_text(page, encoding="utf-8")
    print(f"Wrote {out} ({len(page):,} bytes)")

    # Re-state the contrast headroom so a colour regression is visible in the
    # build log even if nobody opens the README.
    for mode in ("light", "dark"):
        p = PALETTE[mode]
        print(
            f"  {mode}: body text {contrast(p['text_primary'], p['surface']):.1f}:1, "
            f"chart marks {contrast(p['above'], p['surface']):.1f}:1 / "
            f"{contrast(p['below'], p['surface']):.1f}:1"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
