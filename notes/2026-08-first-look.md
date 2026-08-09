# What five years of Canadian GDP releases actually say

*First analysis note. June 2021 to May 2026 — 59 monthly releases, 49 with a matching
advance estimate. Written 9 August 2026 from `data/processed/scorecard.json`.*

---

## The forecast is the boring part

I expected StatCan's advance estimate to be a rough guess. It isn't. Across 49 months it
missed the first official figure by an average of 0.11 percentage points, and 32 of those
months landed within 0.1 points — essentially exact, given these numbers are published to
one decimal place. It called the direction correctly in 27 of the 30 months where a
direction existed.

More importantly, it beats the benchmarks. Repeating last month's growth misses by 0.23
points; a three-month average misses by 0.17. The advance estimate's 0.11 roughly halves
the naive error. That gap is what "adding information" looks like.

That comparison only means anything because the benchmarks use vintage data. When the
advance estimate for a month was published, the newest figure available was the *first
print* for the previous month, not today's revised value. Feeding revised numbers into a
naive rule hands it information from the future. Avoiding that required recovering first
prints from The Daily, which is most of the engineering here.

## The revisions are the interesting part

**Revisions are larger than the forecast error.** The published figure moves by 0.15
percentage points on average after the fact, against the advance estimate's 0.11. The
number that gets traded on is, in a real sense, less settled than the preliminary guess
before it.

They also lean upward: 29 months revised up, 15 down, 15 unchanged. The average revision
is +0.07 points, which on the full sample is unlikely to be chance (t = +2.55).

## Where I argued myself down

That was nearly the headline of this project, and it would have been an overclaim.

The six largest revisions — +0.8 in July 2021, +0.4 in each of August, September and
December 2021, −0.4 in January 2022, +0.5 in January 2023 — cluster in the post-pandemic
recovery, when GDP moved fast and source data was unusually incomplete. A result driven
by one abnormal year isn't the general claim it looks like.

So I checked. Dropping the earliest 12 months, the average revision is still upward at
+0.04 points, but the t-statistic falls to +1.75 and stops being distinguishable from
zero. Other cuts give the same picture: the mean stays positive everywhere (+0.04 to
+0.07), but significance comes and goes with the window. Older months have also had more
time to be revised, which compresses recent revisions toward zero and makes any trend
look front-loaded.

The honest reading is that the direction is consistent and the evidence isn't decisive.
The page says exactly that, beneath the chart. I'd rather ship the qualification than
have someone find it for me in an interview.

## What it means if you use the data

The takeaway isn't "StatCan is unreliable." It's that **the uncertainty sits somewhere
other than where most people assume.** The advance estimate is treated as soft and the
first print as hard; on this evidence that ordering is roughly backwards. A typical
monthly change is 0.1 to 0.3 points and a typical revision is 0.15 — so anyone acting on
a single month's print is using a number that may move by more than the change it
reports.

## Limits I'd flag before anyone asks

- Everything is rounded to one decimal at source, so revisions under 0.1 points are
  invisible to this method.
- 59 months is a description, not a law, and it covers one unusual macroeconomic period.
- Only GDP publishes an advance estimate. CPI publishes none and is essentially never
  revised; the Labour Force Survey publishes none either. Neither supports the central
  comparison — which is why this covers one series properly rather than three badly.
- Vintages come from parsing StatCan's prose. Nine of 60 crawled releases couldn't be read
  cleanly and are listed on the page rather than dropped quietly.
