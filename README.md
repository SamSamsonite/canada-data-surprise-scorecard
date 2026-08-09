# Canada Data Surprise Scorecard

**How well was Canadian economic data anticipated — and how much did it change after the fact?**

A static page, rebuilt automatically every month, that scores Statistics Canada's
monthly GDP releases against two things: the agency's own advance estimate, and the
figure that eventually replaced it after revision.

Built entirely from free, openly-licensed public data. No paid terminal, no licensed
consensus forecasts, no API keys.

**→ [View the page](https://YOUR-USERNAME.github.io/canada-data-surprise-scorecard/)**
*(replace with your Pages URL once published)*

---

## Why this exists

I'm a fourth-year Bachelor of Commerce student at McMaster's DeGroote School of
Business, working through the Bloomberg Market Concepts certification. The Economic
Indicators module covers the primacy of GDP, monitoring GDP, and forecasting GDP —
taught by showing you Bloomberg Terminal screens.

Watching a video about a monitor proves less than building one. So this is that module
rebuilt from open data: a working GDP monitor that scores forecast accuracy, running on
sources anyone can access for free.

**No Bloomberg data, screenshots, or exports appear anywhere in this project.**

## The idea

The obvious version of this project compares each data release to the "consensus
forecast." That data is paywalled and licensed, and republishing it isn't allowed. So
this scorecard is built on three comparisons that are free — and, I'd argue, more
interesting:

**1. Advance estimate vs. first official print.** Inside every monthly GDP release,
Statistics Canada publishes an advance estimate for the *following* month. That is an
official, on-the-record forecast from the agency itself. Scoring StatCan against StatCan
is clean, honest, and nobody else seems to publish it.

**2. First print vs. latest revised figure.** How much does economic data change after
everyone has already acted on it? Decisions get made on the first print, and the first
print is frequently wrong.

**3. A naive benchmark.** Two rules that use no judgement at all — repeat last month's
figure, or average the last three. Any forecast that can't beat these isn't adding
information. This gives every other number on the page something to be measured against.

## What it found

Covering June 2021 to May 2026 — 59 monthly releases, 49 with a matching advance estimate.

**The forecast is the boring part.** StatCan's advance estimate misses the first official
figure by an average of **0.11 percentage points**, lands within 0.1 points in 32 of 49
months, and calls the direction correctly in 27 of the 30 months where a direction
existed. It roughly **halves** the error of repeating last month's number (0.11 vs 0.23).

**The revisions are the interesting part.** The published figure moves by **0.15
percentage points** on average after the fact — *more than the advance estimate was ever
wrong by*. The number that gets traded on is, in a real sense, less settled than the
preliminary guess that preceded it.

**And the bit I had to argue myself down on.** Revisions lean upward: 29 up, 15 down, 15
unchanged, averaging +0.07 points (t = +2.55). That was nearly the headline — but the six
largest revisions all sit in the 2021 post-pandemic recovery. Dropping the earliest 12
months, the average is still positive (+0.04) but the t-statistic falls to +1.75 and stops
being distinguishable from zero. The direction is consistent; the evidence isn't decisive.
The page says exactly that, directly beneath the chart, and `analyze.py` computes the
robustness check on every run so the claim can't quietly outgrow the data.

Full write-up: [`notes/2026-08-first-look.md`](notes/2026-08-first-look.md).

## The hard part: the API cannot answer this question

This is the finding that shaped the whole project, and it's worth stating plainly.

**The Statistics Canada Web Data Service serves only current, fully-revised values.**
There is no endpoint that returns "what this figure said on the day it was published."
I verified this directly: the last 40 datapoints of the headline GDP vector carry only
two distinct `releaseTime` stamps, which tells you *which* months were touched at the
last revision but not what they previously said.

So a revision analysis built purely on the API is impossible. The values it returns have
already absorbed every revision — the original numbers are simply gone.

The recovery route is **[The Daily](https://www150.statcan.gc.ca/n1/dai-quo/index-eng.htm)**,
StatCan's official release bulletin. Every monthly GDP release states both numbers on the
record:

> Real gross domestic product (GDP) grew 0.1% in May …
> Advance information indicates that real GDP increased 0.2% in June.

Consecutive articles pair up exactly: the advance estimate for a month appears in one
release, and the first print for that same month appears in the next. The Daily is
permanently archived and carried under the Statistics Canada Open Licence, so parsing it
is both reproducible and redistributable.

Each article also links to its predecessor, so `fetch.py` walks that chain backwards at
one request per release, rather than guessing at release dates. (My first attempt
brute-forced candidate URLs and made several thousand requests, nearly all 404s.)

## How to run it

Python 3.11 or newer. One dependency.

```bash
git clone https://github.com/YOUR-USERNAME/canada-data-surprise-scorecard.git
cd canada-data-surprise-scorecard

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python src/validate_colours.py     # verify contrast ratios (exits non-zero on failure)
python src/fetch.py                # download raw data into data/raw/
python src/analyze.py              # compute the scorecard into data/processed/
python src/render.py               # build docs/index.html
```

Then open `docs/index.html` in a browser.

The raw responses are cached in `data/raw/` and committed to the repository, so the
analysis and rendering steps reproduce offline:

```bash
python src/fetch.py --offline      # use only cached data, make no network calls
```

The first full crawl takes a few minutes because it walks several years of The Daily
one article at a time, deliberately slowly. After that, cached articles are reused and
only the newest release is fetched.

## What's in here

```
├── README.md
├── LICENSE                     MIT for the code; data terms noted separately
├── requirements.txt
├── src/
│   ├── fetch.py                StatCan WDS + BoC Valet + The Daily → data/raw/
│   ├── analyze.py              surprises, revisions, benchmarks → data/processed/
│   ├── render.py               SVG charts + the HTML page → docs/
│   └── validate_colours.py     computes every contrast ratio and CVD separation
├── data/
│   ├── raw/                    cached API responses and Daily articles
│   └── processed/scorecard.json
├── notes/
│   ├── 2026-08-first-look.md   written analysis, one file per update
│   └── linkedin-post-draft.md
├── docs/index.html             the published page
└── .github/workflows/refresh.yml
```

## Data sources

| Source | What it provides | Licence |
|---|---|---|
| [StatCan Web Data Service](https://www150.statcan.gc.ca/t1/wds/rest/) | Table 36-10-0434-01, monthly GDP by industry, seasonally adjusted, chained 2017 dollars (vector 65201210) | [Statistics Canada Open Licence](https://www.statcan.gc.ca/en/reference/licence) |
| [The Daily](https://www150.statcan.gc.ca/n1/dai-quo/index-eng.htm) | Advance estimates and first prints, as originally published | Statistics Canada Open Licence |
| [Bank of Canada Valet](https://www.bankofcanada.ca/valet/docs) | Target for the overnight rate (V39079), context only | [Bank of Canada Terms of Use](https://www.bankofcanada.ca/terms/) |

Table and vector identifiers were verified against the live API rather than recalled —
StatCan renumbers tables, and `fetch.py` re-checks on every run that the cube is still
`CURRENT` and that the vector still resolves to the expected series title. If either
changes, the build fails rather than silently publishing the wrong series.

## Accessibility

This was treated as a requirement, not a finishing touch.

### Colour

**Nothing on this page is encoded by colour alone.** Direction is carried first by
position — above or below a zero baseline — then by hue, then by an explicit sign in
the data table beneath every chart. Remove colour entirely and every chart still reads.

The conventional red/green scheme for surprise charts is the worst available choice:
red/green colour blindness affects roughly 8% of men. This page uses a **blue/orange
diverging pair** instead.

`src/validate_colours.py` computes rather than assumes the numbers below. It simulates
protanopia and deuteranopia using the Machado, Oliveira & Fernandes (2009) transforms at
full severity and measures perceptual distance as Euclidean distance in OKLab (×100).
The target is ΔE ≥ 8.

| Mode | Blue vs orange, normal vision | Protanopia | Deuteranopia |
|---|---|---|---|
| Light | 31.6 | 26.6 | 29.5 |
| Dark | 27.2 | 22.4 | 25.3 |

Comfortably clear of the threshold in both modes.

### Contrast ratios (measured, WCAG 2.1)

Computed with the sRGB relative-luminance formula from the spec, against the surface each
colour actually sits on. Body text must clear 4.5:1; chart marks and UI lines 3:1.

| Role | Light mode | Dark mode | Required |
|---|---|---|---|
| Primary text | 17.96:1 | 16.24:1 | 4.5:1 |
| Secondary text | 8.78:1 | 10.36:1 | 4.5:1 |
| Muted text | 5.28:1 | 6.31:1 | 4.5:1 |
| Chart: above (blue) | 4.82:1 | 6.58:1 | 3:1 |
| Chart: below (orange) | 4.63:1 | 6.95:1 | 3:1 |
| Chart: neutral | 7.18:1 | 6.79:1 | 3:1 |
| Zero baseline | 3.39:1 | 3.44:1 | 3:1 |

Gridlines are decorative hairlines carrying no information and are deliberately
low-contrast; they are reported by the script but not gated.

This table is generated by running `python src/validate_colours.py`, which exits
non-zero if any gate fails — and the GitHub Action runs it before every publish. It has
already caught one real regression: my first choice of baseline colour measured 2.35:1
and had to be darkened.

### Everything else

- **Every chart has a real `<table>`**, in a `<details>` element directly beneath it —
  available to screen readers and to anyone who just wants the figures, without cluttering
  the visual page.
- **Every chart has a plain-language takeaway sentence above it.** If you can't see the
  chart at all, the finding still reaches you first.
- **Charts are server-side inline SVG** with `role="img"`, a `<title>`, and a descriptive
  `<desc>` — generated in Python, not by a JavaScript charting library. Nothing renders to
  `<canvas>`, which a screen reader cannot read.
- **No JavaScript at all.** The page is fully readable with JS disabled because there is
  none to disable.
- **Semantic HTML** with a single `<h1>` and a correct heading hierarchy.
- **Keyboard navigable**, with a visible focus ring; the only interactive elements are
  native `<details>` and links.
- **200% zoom without horizontal scrolling** — charts use a `viewBox` with no intrinsic
  pixel width, and wide tables scroll inside their own container so the page body never
  does.
- **Light and dark mode**, via `prefers-color-scheme`, with both palettes independently
  contrast-checked.
- **Respects `prefers-reduced-motion`.**
- Self-contained: inline CSS, inline SVG, no external requests, no fonts to download.

### Plain language

Jargon is defined at first use, and there's a glossary on the page covering *advance
estimate*, *first print*, *revision*, *percentage point*, and *naive benchmark*. Every
chart carries a "what this shows" and a "why it matters" line.

All of the interpretive sentences on the page are **generated from the statistics**
rather than hardcoded. The page rebuilds itself monthly without anyone reading it, so a
typed-in sentence like "revisions usually run upward" would eventually sit above a chart
showing the opposite.

## Methodology notes worth defending

**Benchmarks use vintage data, not revised data.** When the advance estimate for a month
was published, the most recent figure a forecaster actually had was the *first print* for
the previous month. Feeding revised values into the naive benchmark would hand it
information from the future and flatter it unfairly. Using first prints keeps the
comparison honest — and it's only possible because the vintages were recovered from The
Daily.

**Both sides are rounded to one decimal before differencing.** StatCan publishes these
changes to one decimal place. The "latest revised" figure is computed here from the
current level series and has full precision. Differencing a full-precision number against
a rounded one would manufacture revisions of up to 0.05 points that are pure artifact.

**The series is seasonally adjusted.** The published headline change is a seasonally
adjusted one, so the comparison uses the seasonally adjusted chained-2017-dollar vector.
Comparing against an unadjusted change would be comparing two different quantities.

**Direction accuracy excludes flat months.** Months where either figure is exactly 0.0%
are counted separately rather than scored — "flat" isn't a direction, and forcing it into
up-or-down would overstate accuracy.

**A t-statistic accompanies every average.** With roughly three dozen monthly
observations, a mean error needs to be reasonably large relative to its spread before it
can be distinguished from zero. Saying "revisions lean upward" is only defensible if the
number survives that check, so the page reports whether it does.

**Parse failures are disclosed, not dropped.** If a Daily article uses wording the parser
doesn't recognise, it's listed on the page and excluded from the statistics. Silently
discarding awkwardly-worded months would bias the results toward the tidy ones.

## Limitations

- **Rounding sets a precision floor.** Revisions smaller than 0.1 percentage points are
  invisible to this method.
- **The sample is short.** A few dozen months describes what happened; it doesn't settle
  whether a small average bias is real. Where a figure can't be distinguished from zero,
  the page says so.
- **Recent months are under-revised.** The newest months have had less time to be revised
  than older ones, which mechanically shrinks their measured revisions. Comparing a
  recent month's revision to a three-year-old month's is not quite like for like.
- **CPI and the Labour Force Survey are not scored.** Neither publishes an advance
  estimate, so the central comparison on this page simply doesn't exist for them. CPI is
  also essentially never revised, which makes the revision analysis degenerate. Including
  them would have meant a weaker, different chart rather than the same chart with more
  data — so they were dropped, deliberately.
- **The parser depends on StatCan's prose.** The advance estimate is recovered from a
  sentence in The Daily. If StatCan changes its house style, the parser will flag the
  releases it can't read rather than guess — but it will need updating.
- **One agency, one series.** This scores StatCan's monthly GDP estimate. It is not a
  general statement about Canadian economic forecasting.

## Automation

`.github/workflows/refresh.yml` runs at 14:00 UTC on the 3rd of each month — several days
after StatCan's usual release window, so a release that slips past a holiday is still
caught. It validates the colour scheme, re-fetches, re-analyses, re-renders, sanity-checks
the output, and commits.

It's built to **fail loudly rather than publish quietly**. The build stops if the cube is
no longer `CURRENT`, if the vector stops resolving to the expected series, if fewer than
12 releases are crawled, if no surprises or revisions can be computed, if the rendered
page is suspiciously small or missing expected content, or if the page ever stops being
self-contained. A stale page that still looks freshly generated is the failure mode worth
engineering against.

## Attribution

Contains information licensed under the
[Statistics Canada Open Licence](https://www.statcan.gc.ca/en/reference/licence).
Bank of Canada data used under the
[Bank of Canada Terms of Use](https://www.bankofcanada.ca/terms/).
Neither institution endorses this project or is affiliated with it.

Code is MIT licensed. See [LICENSE](LICENSE).
