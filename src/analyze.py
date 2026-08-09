"""
analyze.py - turn the raw files in data/raw into the scorecard in data/processed.

Reads nothing from the internet. Everything here is arithmetic on the cached
raw data, so the numbers on the page can be reproduced offline and checked by
hand.

WHAT IS BEING COMPARED
----------------------
For each reference month we can end up with three numbers for the same thing -
the month-over-month percent change in real GDP:

  advance estimate  StatCan's own published guess, released about a month
                    before the official figure, based on partial source data.
  first print       the first official estimate, the number markets and
                    journalists react to on release day.
  latest revised    what that same month says today, after every revision.

From those, two errors:

  surprise  = first print   - advance estimate
              How wrong was the agency's own early guess?
              Positive means the economy did better than the advance said.

  revision  = latest revised - first print
              How much did the number move after everyone had already acted
              on it? Positive means it was revised up.

And two naive benchmarks, which exist to answer "is the advance estimate
adding information, or would guessing have done just as well?":

  random walk  predict this month's change with last month's change
  3-month MA   predict it with the average of the last three

A METHODOLOGICAL POINT WORTH DEFENDING
--------------------------------------
The benchmarks are computed from FIRST PRINTS, not from revised data. When the
advance estimate for month M is published, the most recent figure a forecaster
actually had was the first print for M-1 - the revised value did not exist yet.
Feeding revised data into the benchmark would give it information from the
future and would flatter it unfairly. Using vintage data keeps the comparison
honest, and it is only possible because the vintages were recovered from The
Daily.

A SECOND ONE: ROUNDING
----------------------
StatCan publishes these percent changes to one decimal place. The "latest
revised" figure, by contrast, is computed here from the current level series
and so has full precision. Comparing a full-precision number against a rounded
one would manufacture revisions of up to 0.05 points that are purely an
artifact of rounding. Both sides are therefore rounded to one decimal before
differencing, and revisions smaller than 0.1 points are not detectable by this
method at all. That is a real limitation and it is stated on the page.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"


class AnalysisError(RuntimeError):
    """Raised when the inputs cannot support an honest scorecard."""


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def _load(name: str):
    path = RAW / name
    if not path.exists():
        raise AnalysisError(f"Missing {path}. Run `python src/fetch.py` first.")
    return json.loads(path.read_text(encoding="utf-8"))


def _month_key(iso_date: str) -> str:
    """'2026-05-01' -> '2026-05'."""
    return iso_date[:7]


def _prev_month(key: str) -> str:
    y, m = (int(x) for x in key.split("-"))
    return f"{y - 1:04d}-12" if m == 1 else f"{y:04d}-{m - 1:02d}"


def _month_label(key: str) -> str:
    y, m = (int(x) for x in key.split("-"))
    return f"{dt.date(y, m, 1):%b %Y}"


# --------------------------------------------------------------------------
# Building the series
# --------------------------------------------------------------------------


def revised_changes(levels: list[dict]) -> dict[str, float]:
    """Month-over-month percent change from the current (revised) level series.

    The level series is chained 2017 dollars, seasonally adjusted at annual
    rates. Seasonal adjustment matters here: the published headline change is
    a seasonally adjusted one, so comparing it against an unadjusted change
    would be comparing two different quantities. The vector chosen in fetch.py
    is the seasonally adjusted one for exactly this reason.
    """
    by_month = {_month_key(p["refPer"]): p["value"] for p in levels if p.get("value") is not None}
    out: dict[str, float] = {}
    for key, value in by_month.items():
        prev = by_month.get(_prev_month(key))
        if prev:
            out[key] = round((value / prev - 1) * 100, 1)
    return out


def build_records(articles: list[dict], revised: dict[str, float]) -> list[dict]:
    """Join advance estimates, first prints, and revised values by month."""
    first_print: dict[str, float] = {}
    advance: dict[str, float] = {}
    release_date: dict[str, str] = {}

    for a in articles:
        ref = a.get("reference_month")
        if ref and a.get("first_print_pct") is not None:
            first_print[ref] = a["first_print_pct"]
            release_date[ref] = a.get("release_date", "")
        adv_month = a.get("advance_month")
        if adv_month and a.get("advance_pct") is not None:
            advance[adv_month] = a["advance_pct"]

    records = []
    for month in sorted(first_print):
        fp = first_print[month]
        records.append(
            {
                "month": month,
                "label": _month_label(month),
                "release_date": release_date.get(month, ""),
                "advance": advance.get(month),
                "first_print": fp,
                "revised": revised.get(month),
                # Filled in below once the full ordered list exists.
                "naive_rw": None,
                "naive_ma3": None,
            }
        )

    # Benchmarks use only first prints strictly before the target month, which
    # is exactly the information set a forecaster had at the time.
    ordered = [r["month"] for r in records]
    for i, rec in enumerate(records):
        prior = [first_print[m] for m in ordered[max(0, i - 3) : i]]
        if i >= 1:
            rec["naive_rw"] = first_print[ordered[i - 1]]
        if len(prior) == 3:
            rec["naive_ma3"] = round(statistics.fmean(prior), 1)

    # Errors.
    for rec in records:
        rec["surprise"] = (
            round(rec["first_print"] - rec["advance"], 1)
            if rec["advance"] is not None
            else None
        )
        rec["revision"] = (
            round(rec["revised"] - rec["first_print"], 1)
            if rec["revised"] is not None
            else None
        )
        rec["err_rw"] = (
            round(rec["first_print"] - rec["naive_rw"], 1)
            if rec["naive_rw"] is not None
            else None
        )
        rec["err_ma3"] = (
            round(rec["first_print"] - rec["naive_ma3"], 1)
            if rec["naive_ma3"] is not None
            else None
        )

    return records


# --------------------------------------------------------------------------
# Summary statistics
# --------------------------------------------------------------------------


def error_stats(errors: list[float], name: str) -> dict:
    """Mean, MAE, RMSE and a t-statistic on the mean.

    The t-statistic is here to stop the write-up over-reading a small average.
    With roughly three dozen monthly observations, a mean error needs to be
    fairly large relative to its spread before it is distinguishable from
    zero, and saying "revisions lean upward" is only defensible if the number
    survives that check.
    """
    n = len(errors)
    if n == 0:
        return {"name": name, "n": 0}
    mean = statistics.fmean(errors)
    mae = statistics.fmean(abs(e) for e in errors)
    rmse = math.sqrt(statistics.fmean(e * e for e in errors))
    sd = statistics.stdev(errors) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 1 and sd > 0 else 0.0
    t = mean / se if se > 0 else 0.0
    return {
        "name": name,
        "n": n,
        "mean": round(mean, 3),
        "mae": round(mae, 3),
        "rmse": round(rmse, 3),
        "sd": round(sd, 3),
        "t_stat": round(t, 2),
        # |t| > 2 is the usual rough-and-ready cutoff for "distinguishable
        # from zero at about the 5% level" once n is above ~30.
        "mean_differs_from_zero": abs(t) > 2.0,
        "share_positive": round(
            sum(1 for e in errors if e > 0) / n, 3
        ),
        "share_zero": round(sum(1 for e in errors if e == 0) / n, 3),
        "largest_abs": round(max(abs(e) for e in errors), 3),
    }


def revision_robustness(records: list[dict], drop_first: int = 12) -> dict:
    """Does the revision bias survive dropping the start of the sample?

    This check exists because the first version of this analysis reported a
    clearly upward revision bias on the full sample (t = 2.55) - and the six
    largest upward revisions all turned out to sit in 2021, during the
    post-pandemic recovery, when GDP was moving fast and source data was
    unusually incomplete. A finding that rests on one unusual year is not the
    general claim it looks like.

    Dropping a fixed number of the EARLIEST months, rather than naming 2021,
    keeps this meaningful as the sample rolls forward: the check will always
    ask whether the result depends on the oldest and most-revised part of the
    window.
    """
    revisions = [r["revision"] for r in records if r["revision"] is not None]
    trimmed = revisions[drop_first:]
    if len(trimmed) < 12:
        return {"applicable": False}
    full = error_stats(revisions, "full sample")
    later = error_stats(trimmed, f"excluding first {drop_first} months")
    return {
        "applicable": True,
        "drop_first": drop_first,
        "full_mean": full["mean"],
        "full_t": full["t_stat"],
        "full_significant": full["mean_differs_from_zero"],
        "later_n": later["n"],
        "later_mean": later["mean"],
        "later_t": later["t_stat"],
        "later_significant": later["mean_differs_from_zero"],
        # The direction agreeing across both windows is weaker evidence than
        # significance, but it is still evidence, and it is worth reporting
        # separately rather than collapsing everything into one t-statistic.
        "sign_agrees": (full["mean"] > 0) == (later["mean"] > 0),
    }


def directional_accuracy(records: list[dict]) -> dict:
    """How often did the advance estimate get the DIRECTION right?

    Direction is what most readers actually take from a release - "the economy
    grew" or "the economy shrank" - so getting the sign right matters
    separately from getting the magnitude right. Months where either figure is
    exactly 0.0 are counted separately rather than scored, because "flat" is
    not a direction and forcing it into up-or-down would overstate accuracy.
    """
    hit = miss = flat = 0
    for r in records:
        if r["advance"] is None:
            continue
        a, f = r["advance"], r["first_print"]
        if a == 0.0 or f == 0.0:
            flat += 1
        elif (a > 0) == (f > 0):
            hit += 1
        else:
            miss += 1
    scored = hit + miss
    return {
        "hit": hit,
        "miss": miss,
        "flat_excluded": flat,
        "scored": scored,
        "accuracy": round(hit / scored, 3) if scored else None,
    }


def skill_score(model_mae: float, benchmark_mae: float) -> float | None:
    """1 - MAE(model)/MAE(benchmark). Positive means the model beat the naive.

    A skill score of 0 means the forecast was no better than the benchmark;
    1 would mean perfect. Negative means the naive rule won.
    """
    if not benchmark_mae:
        return None
    return round(1 - model_mae / benchmark_mae, 3)


# --------------------------------------------------------------------------
# Policy rate context
# --------------------------------------------------------------------------


def policy_rate_monthly(observations: list[dict]) -> list[dict]:
    """Reduce the business-daily policy rate to one value per month.

    The last observation in each month is used, because the page shows the
    rate as context alongside monthly GDP and a month-end value is the one
    that was in force when that month's data was being generated.
    """
    by_month: dict[str, float] = {}
    for obs in observations:
        month = obs["d"][:7]
        raw = obs.get("V39079", {}).get("v")
        if raw not in (None, ""):
            by_month[month] = float(raw)
    return [{"month": m, "rate": v} for m, v in sorted(by_month.items())]


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main() -> int:
    levels_payload = _load("wds_gdp_levels.json")
    articles = _load("daily_articles.json")
    boc = _load("boc_policy_rate.json")

    revised = revised_changes(levels_payload["vectorDataPoint"])
    records = build_records(articles, revised)

    if len(records) < 12:
        raise AnalysisError(
            f"Only {len(records)} usable monthly records. "
            "Refusing to publish a scorecard this thin."
        )

    # ---- coverage: what did we fail to parse, and why it is disclosed -----
    parse_failures = [
        {"url": a["url"], "release_date": a.get("release_date"), "problems": a["problems"]}
        for a in articles
        if a.get("problems")
    ]

    surprises = [r["surprise"] for r in records if r["surprise"] is not None]
    revisions = [r["revision"] for r in records if r["revision"] is not None]
    err_rw = [r["err_rw"] for r in records if r["err_rw"] is not None]
    err_ma3 = [r["err_ma3"] for r in records if r["err_ma3"] is not None]

    if not surprises or not revisions:
        raise AnalysisError(
            "No surprises or no revisions could be computed - the parse or the "
            "join has failed. Refusing to publish an empty scorecard."
        )

    stats = {
        "advance": error_stats(surprises, "Advance estimate"),
        "revision": error_stats(revisions, "Revision to first print"),
        "naive_rw": error_stats(err_rw, "Naive: last month repeated"),
        "naive_ma3": error_stats(err_ma3, "Naive: 3-month average"),
    }

    # Compare like with like: the skill score is computed only over the months
    # where BOTH the advance estimate and the benchmark exist, otherwise the
    # two MAEs would be measured on different samples.
    paired = [
        r
        for r in records
        if r["surprise"] is not None and r["err_rw"] is not None and r["err_ma3"] is not None
    ]
    paired_stats = {
        "n": len(paired),
        "advance_mae": round(statistics.fmean(abs(r["surprise"]) for r in paired), 3)
        if paired
        else None,
        "naive_rw_mae": round(statistics.fmean(abs(r["err_rw"]) for r in paired), 3)
        if paired
        else None,
        "naive_ma3_mae": round(statistics.fmean(abs(r["err_ma3"]) for r in paired), 3)
        if paired
        else None,
    }
    if paired:
        paired_stats["skill_vs_rw"] = skill_score(
            paired_stats["advance_mae"], paired_stats["naive_rw_mae"]
        )
        paired_stats["skill_vs_ma3"] = skill_score(
            paired_stats["advance_mae"], paired_stats["naive_ma3_mae"]
        )

    out = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source_release": levels_payload.get("fetched"),
        "series_title": levels_payload.get("seriesTitleEn"),
        "coverage": {
            "releases_crawled": len(articles),
            "months_with_first_print": len(records),
            "months_with_advance": len(surprises),
            "months_with_revision": len(revisions),
            "first_month": records[0]["month"],
            "last_month": records[-1]["month"],
            "parse_failures": parse_failures,
        },
        "records": records,
        "stats": stats,
        "paired": paired_stats,
        "robustness": revision_robustness(records),
        "directional": directional_accuracy(records),
        "policy_rate": policy_rate_monthly(boc["observations"]),
    }

    PROCESSED.mkdir(parents=True, exist_ok=True)
    path = PROCESSED / "scorecard.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")

    # ---- human-readable summary, so the pipeline is checkable at a glance --
    print(f"Months with a first print : {len(records)} "
          f"({records[0]['label']} to {records[-1]['label']})")
    print(f"Months with an advance    : {len(surprises)}")
    print(f"Months with a revision    : {len(revisions)}")
    if parse_failures:
        print(f"Articles with parse problems: {len(parse_failures)}")
        for f in parse_failures:
            print(f"  ! {f['release_date']}: {f['problems']}")
    print()
    for key in ("advance", "revision", "naive_rw", "naive_ma3"):
        s = stats[key]
        if s.get("n"):
            print(
                f"{s['name']:<28} n={s['n']:<3} mean={s['mean']:+.3f} "
                f"MAE={s['mae']:.3f} RMSE={s['rmse']:.3f} t={s['t_stat']:+.2f}"
                f"{'  <- mean differs from 0' if s['mean_differs_from_zero'] else ''}"
            )
    print()
    print(f"Paired comparison on n={paired_stats['n']} months:")
    print(f"  advance MAE {paired_stats['advance_mae']}  "
          f"vs last-month {paired_stats['naive_rw_mae']}  "
          f"vs 3-mo avg {paired_stats['naive_ma3_mae']}")
    print(f"  skill vs last-month repeated : {paired_stats.get('skill_vs_rw')}")
    print(f"  skill vs 3-month average     : {paired_stats.get('skill_vs_ma3')}")
    d = out["directional"]
    print(f"  direction correct {d['hit']}/{d['scored']} "
          f"({d['accuracy']}), {d['flat_excluded']} flat months excluded")

    rb = out["robustness"]
    if rb.get("applicable"):
        print(f"\nRobustness of the revision bias:")
        print(f"  full sample                  mean={rb['full_mean']:+.3f} "
              f"t={rb['full_t']:+.2f} "
              f"{'significant' if rb['full_significant'] else 'not significant'}")
        print(f"  excluding first {rb['drop_first']} months  "
              f"mean={rb['later_mean']:+.3f} t={rb['later_t']:+.2f} "
              f"{'significant' if rb['later_significant'] else 'not significant'} "
              f"(n={rb['later_n']})")
    print(f"\nWrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
