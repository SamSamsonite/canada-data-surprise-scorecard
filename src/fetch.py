"""
fetch.py - pull raw data from Statistics Canada and the Bank of Canada.

This module does one job: get bytes from the internet and put them on disk,
unchanged, in data/raw/. It does not compute anything. Keeping fetching and
analysis in separate files means I can re-run the analysis a hundred times
without hammering StatCan's servers, and it means the cached raw files in the
repo are enough for anyone to reproduce my numbers offline.

THE CENTRAL DATA PROBLEM
------------------------
This project compares what was FIRST published against what is published NOW.
That requires "vintage" data - the value as it appeared on release day.

The StatCan Web Data Service does not serve vintages. It serves only current,
fully-revised values. I verified this directly: the last 40 datapoints of the
headline GDP vector carry only two distinct `releaseTime` stamps, which tells
you which months were touched at the last revision but not what they used to
say. There is no endpoint that returns "the value as of March 2024".

So the API cannot answer the question. The Daily can.

Every monthly GDP release is announced in The Daily, StatCan's official release
bulletin, and each article states two numbers on the record:

  1. the first print   - "Real gross domestic product (GDP) grew 0.1% in May"
  2. the advance estimate for the FOLLOWING month
     - "Advance information indicates that real GDP increased 0.2% in June."

Consecutive articles therefore pair up perfectly: the advance estimate for month
X appears in one release, and the first print for month X appears in the next.
The Daily is archived permanently and carried under the Statistics Canada Open
Licence, so parsing it is both reproducible and redistributable.

Sources (both free, no API key, openly licensed):
  StatCan Web Data Service - https://www150.statcan.gc.ca/t1/wds/rest/
  Bank of Canada Valet     - https://www.bankofcanada.ca/valet/docs
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

import requests

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"

WDS = "https://www150.statcan.gc.ca/t1/wds/rest"
VALET = "https://www.bankofcanada.ca/valet"
DAILY = "https://www150.statcan.gc.ca/n1/daily-quotidien"

# Identity string. StatCan asks that automated clients be identifiable.
UA = {
    "User-Agent": (
        "canada-data-surprise-scorecard/1.0 "
        "(open-data portfolio project; +https://github.com/)"
    )
}

# Product 36-10-0434-01, "Gross domestic product (GDP) at basic prices, by
# industry, monthly". VERIFIED LIVE against getCubeMetadata rather than
# recalled from memory - StatCan renumbers tables, so the pipeline re-checks
# this on every run and fails if the cube stops being CURRENT.
GDP_PRODUCT_ID = 36100434

# Vector 65201210 = coordinate 1.1.1.1 of that cube:
#   Canada; Seasonally adjusted at annual rates; Chained (2017) dollars;
#   All industries
# Confirmed via getSeriesInfoFromVector (the series title is asserted at
# fetch time below, so a silent renumbering breaks the build loudly).
GDP_VECTOR = 65201210
GDP_VECTOR_TITLE = (
    "Canada;Seasonally adjusted at annual rates;Chained (2017) dollars;All industries"
)

# Bank of Canada policy rate. V39079 = "Target for the overnight rate",
# business-daily. Used only as timeline context, never as a scored forecast.
BOC_POLICY_RATE = "V39079"

# How far back to walk The Daily's "previous release" chain. 60 releases is
# five years, which comfortably covers the post-pandemic period without
# reaching back into 2020, when the series behaved so abnormally that any
# forecast-error statistic computed across it would be meaningless.
MAX_RELEASES = 60

# Politeness delay between requests to StatCan.
SLEEP = 0.4


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


class FetchError(RuntimeError):
    """Raised when a source cannot be fetched or looks structurally wrong.

    The GitHub Action must fail loudly rather than publish stale or empty
    data, so every failure path in this file raises instead of returning None.
    """


# StatCan's servers intermittently stall rather than returning a clean 404,
# so every request is retried with a short backoff. Without this the monthly
# job fails perhaps one run in three for reasons that have nothing to do with
# the data.
RETRIES = 3
TIMEOUT = 45


def _request(method: str, url: str, **kw) -> requests.Response:
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            return requests.request(method, url, headers=UA, timeout=TIMEOUT, **kw)
        except requests.RequestException as exc:
            last = exc
            if attempt < RETRIES - 1:
                time.sleep(1.5 * (attempt + 1))
    raise FetchError(f"{method} {url} failed after {RETRIES} attempts: {last}")


def _get(url: str, *, expect_html: bool = False) -> str:
    resp = _request("GET", url)
    if resp.status_code != 200:
        raise FetchError(f"GET {url} returned HTTP {resp.status_code}")
    if expect_html and "<html" not in resp.text.lower():
        raise FetchError(f"GET {url} did not return HTML")
    return resp.text


def _post_json(url: str, payload: list) -> list:
    resp = _request("POST", url, json=payload)
    if resp.status_code != 200:
        raise FetchError(f"POST {url} returned HTTP {resp.status_code}")
    data = resp.json()
    if not isinstance(data, list) or not data:
        raise FetchError(f"POST {url} returned an unexpected shape")
    if data[0].get("status") != "SUCCESS":
        raise FetchError(f"POST {url} returned status {data[0].get('status')!r}")
    return data


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Statistics Canada - Web Data Service
# --------------------------------------------------------------------------


def fetch_cube_metadata() -> dict:
    """Fetch metadata for the monthly GDP-by-industry cube.

    Two jobs. First, it confirms the table still exists under the product ID
    hard-coded above - the brief's warning that StatCan renumbers tables is
    real, and a wrong ID would otherwise produce a plausible-looking but wrong
    page. Second, its `releaseTime` gives the exact date of the most recent
    release, which is how the Daily crawler finds its starting article without
    guessing at dates.
    """
    data = _post_json(f"{WDS}/getCubeMetadata", [{"productId": GDP_PRODUCT_ID}])
    obj = data[0]["object"]

    # archiveStatusCode "2" == CURRENT. Anything else means the table has been
    # retired or replaced, and the whole pipeline is pointed at the wrong data.
    if str(obj.get("archiveStatusCode")) != "2":
        raise FetchError(
            f"Cube {GDP_PRODUCT_ID} is no longer CURRENT "
            f"(archiveStatusEn={obj.get('archiveStatusEn')!r}). "
            "The table has probably been renumbered - re-verify the product ID."
        )

    _write(RAW / "wds_cube_metadata.json", json.dumps(obj, indent=1, ensure_ascii=False))
    return obj


def fetch_gdp_levels(latest_n: int = 120) -> list[dict]:
    """Fetch the CURRENT (fully revised) monthly GDP level series.

    These are the "latest revised" numbers. Every value here has been through
    however many rounds of revision StatCan has applied since first publication,
    which is exactly what makes them the right comparison for a first print.

    Note this is the LEVEL series (chained 2017 dollars, seasonally adjusted at
    annual rates). The month-over-month percent changes are computed in
    analyze.py, not here, because that is an analytical choice and this file
    only fetches.
    """
    # Confirm the vector still points at the series we think it does.
    info = _post_json(f"{WDS}/getSeriesInfoFromVector", [{"vectorId": GDP_VECTOR}])
    title = info[0]["object"].get("SeriesTitleEn", "")
    if title != GDP_VECTOR_TITLE:
        raise FetchError(
            f"Vector {GDP_VECTOR} now resolves to {title!r}, "
            f"expected {GDP_VECTOR_TITLE!r}. Re-verify the vector ID."
        )

    data = _post_json(
        f"{WDS}/getDataFromVectorsAndLatestNPeriods",
        [{"vectorId": GDP_VECTOR, "latestN": latest_n}],
    )
    points = data[0]["object"]["vectorDataPoint"]
    if len(points) < 24:
        raise FetchError(f"Only {len(points)} GDP datapoints returned; expected >= 24")

    _write(
        RAW / "wds_gdp_levels.json",
        json.dumps(
            {
                "vectorId": GDP_VECTOR,
                "seriesTitleEn": title,
                "fetched": dt.date.today().isoformat(),
                "vectorDataPoint": points,
            },
            indent=1,
        ),
    )
    return points


# --------------------------------------------------------------------------
# Bank of Canada - Valet
# --------------------------------------------------------------------------


def fetch_policy_rate(start: str = "2021-01-01") -> list[dict]:
    """Fetch the Bank of Canada's target for the overnight rate.

    Context only. The policy rate is not a forecast and is never scored on this
    page - it is here so a reader can see what the Bank was doing while the
    forecast errors were happening.
    """
    url = f"{VALET}/observations/{BOC_POLICY_RATE}/json?start_date={start}"
    text = _get(url)
    payload = json.loads(text)
    obs = payload.get("observations", [])
    if not obs:
        raise FetchError("Valet returned no observations for the policy rate")

    _write(RAW / "boc_policy_rate.json", json.dumps(payload, indent=1))
    return obs


# --------------------------------------------------------------------------
# The Daily - the vintage archive
# --------------------------------------------------------------------------

MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ],
        start=1,
    )
}

# Direction words StatCan actually uses in these bulletins. The published
# number is always unsigned ("decreased 0.2%"), so the verb carries the sign.
# Anything not in this lexicon is treated as a parse failure rather than being
# guessed at - see parse_daily_article.
UP_WORDS = (
    "increased", "rose", "grew", "expanded", "edged up", "was up",
    "gained", "climbed", "advanced", "ticked up",
)
DOWN_WORDS = (
    "decreased", "fell", "declined", "contracted", "edged down", "was down",
    "shrank", "dropped", "slipped", "ticked down", "retreated",
)
FLAT_WORDS = ("essentially unchanged", "unchanged", "flat", "little changed")


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&#160;", " ")
        .replace("&amp;", "&")
        .replace("&#8212;", "-")
        .replace("&mdash;", "-")
    )
    # Collapse whitespace including the non-breaking spaces StatCan uses
    # between a number and its percent sign.
    return re.sub(r"[\s ]+", " ", text).strip()


def _direction(phrase: str) -> int | None:
    """Map a StatCan verb phrase to +1, -1, or 0. None if unrecognised."""
    low = phrase.lower()
    # Check flat first: "essentially unchanged" contains no direction verb but
    # must not fall through to a failure.
    for w in FLAT_WORDS:
        if w in low:
            return 0
    # Check DOWN before UP: "edged down" would otherwise never be reached if a
    # stray "up" appeared earlier in the window.
    for w in DOWN_WORDS:
        if w in low:
            return -1
    for w in UP_WORDS:
        if w in low:
            return 1
    return None


def _signed_pct(window: str) -> float | None:
    """Extract a signed percent change from a phrase like 'grew 0.1%'."""
    direction = _direction(window)
    if direction is None:
        return None
    if direction == 0:
        # "essentially unchanged" is StatCan's phrasing for a change that
        # rounds to 0.0%. Treating it as exactly 0.0 is the only defensible
        # reading of a published figure with no digits attached.
        return 0.0
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", window)
    if not m:
        return None
    return direction * float(m.group(1))


def parse_daily_article(html: str, url: str) -> dict:
    """Extract the reference month, first print, and advance estimate.

    Returns a dict with explicit None values and a `problems` list rather than
    raising, so that one oddly-worded article does not abort a 60-release
    crawl. The caller reports coverage and the page shows it, because silently
    dropping releases we could not parse would bias the error statistics
    towards the months that happened to use standard phrasing.
    """
    text = _strip_html(html)
    out: dict = {
        "url": url,
        "reference_month": None,
        "first_print_pct": None,
        "advance_month": None,
        "advance_pct": None,
        "problems": [],
    }

    # --- reference month, from the article title -------------------------
    # e.g. "The Daily - Gross domestic product by industry, May 2026"
    m = re.search(
        r"Gross domestic product by industry,\s*([A-Z][a-z]+)\s+(\d{4})", text
    )
    if m and m.group(1) in MONTHS:
        out["reference_month"] = f"{int(m.group(2)):04d}-{MONTHS[m.group(1)]:02d}"
    else:
        out["problems"].append("could not read reference month from title")

    # --- first print -----------------------------------------------------
    # The headline sentence opens the article, e.g.
    #   "Real gross domestic product (GDP) grew 0.1% in May"
    #   "Real gross domestic product (GDP) edged down 0.1% in April"
    # We take a bounded window after the phrase so a later sentence about some
    # industry sub-aggregate cannot be mistaken for the headline.
    # Case-insensitive because the sentence sometimes begins mid-clause with a
    # lowercase "real gross domestic product (GDP) edged down 0.1% in June".
    # The "(GDP)" is required, which is what stops this from matching the
    # section heading "real gross domestic product by industry for July 2025".
    fp = re.search(
        r"real gross domestic product\s*\(\s*GDP\s*\)(.{0,80}?%|.{0,80}?unchanged)",
        text,
        re.I,
    )
    if fp:
        val = _signed_pct(fp.group(1))
        if val is None:
            out["problems"].append(f"unrecognised first-print phrasing: {fp.group(1)!r}")
        else:
            out["first_print_pct"] = val
    else:
        out["problems"].append("first-print sentence not found")

    # --- advance estimate for the following month ------------------------
    #   "Advance information indicates that real GDP increased 0.2% in June."
    # The sentence terminator is a period NOT followed by a digit. Matching a
    # bare "\." instead stops at the decimal point in "0.2%", which silently
    # truncated every advance estimate to "increased 0" - a bug worth keeping
    # this comment for, because the failure looked like odd StatCan phrasing
    # rather than a regex mistake.
    adv = re.search(
        r"Advance information indicates that real GDP\s*(.{0,160}?)\.(?!\d)", text
    )
    if adv:
        window = adv.group(1)
        val = _signed_pct(window)
        if val is None:
            out["problems"].append(f"unrecognised advance phrasing: {window!r}")
        else:
            out["advance_pct"] = val
        mm = re.search(r"in\s+([A-Z][a-z]+)", window)
        if mm and mm.group(1) in MONTHS:
            # The advance month is the month AFTER the reference month, so it
            # rolls into the next calendar year when the reference month is
            # December. Derive the year from the reference month rather than
            # assuming it matches.
            if out["reference_month"]:
                ry, rm = (int(x) for x in out["reference_month"].split("-"))
                am = MONTHS[mm.group(1)]
                ay = ry + 1 if am < rm else ry
                out["advance_month"] = f"{ay:04d}-{am:02d}"
        else:
            out["problems"].append("advance month not found")
    else:
        out["problems"].append("advance-estimate sentence not found")

    return out


def _find_latest_article(release_date: str) -> str:
    """Find the GDP Daily article published on a known release date.

    `release_date` comes from the cube metadata, so we know the day exactly and
    only have to identify which of that day's articles is the GDP one. The
    Daily numbers same-day articles with letter suffixes a, b, c...
    """
    d = dt.date.fromisoformat(release_date[:10])
    for suffix in "abcdef":
        url = f"{DAILY}/{d:%y%m%d}/dq{d:%y%m%d}{suffix}-eng.htm"
        try:
            html = _get(url, expect_html=True)
        except FetchError:
            # A missing suffix is expected - The Daily publishes a variable
            # number of articles per day - so keep probing the rest.
            continue
        if "Gross domestic product by industry" in _strip_html(html):
            return url
        time.sleep(SLEEP)
    raise FetchError(
        f"No GDP-by-industry article found in The Daily for {d:%Y-%m-%d}. "
        "The release may have been retitled."
    )


def crawl_daily(release_date: str, max_releases: int = MAX_RELEASES) -> list[dict]:
    """Walk The Daily backwards, one fetch per release.

    Each article carries a "Previous release" button linking to the previous
    GDP-by-industry bulletin. Following that chain is far cheaper and far more
    robust than guessing release dates - StatCan shifts the release day around
    holidays, and brute-forcing candidate URLs meant thousands of 404s.
    """
    cache = RAW / "daily"
    cache.mkdir(parents=True, exist_ok=True)

    url = _find_latest_article(release_date)
    articles: list[dict] = []
    seen: set[str] = set()

    for _ in range(max_releases):
        if url in seen:
            break  # defensive: a self-referential link would otherwise loop
        seen.add(url)

        name = url.rsplit("/", 1)[-1]
        cached = cache / name
        if cached.exists():
            html = cached.read_text(encoding="utf-8")
        else:
            try:
                html = _get(url, expect_html=True)
            except FetchError as exc:
                # A broken link partway down the chain should truncate the
                # history, not throw away the releases already collected. The
                # length check below still fails the build if we ended up with
                # too little data to say anything.
                print(f"  ! stopping crawl at {url}: {exc}")
                break
            _write(cached, html)
            time.sleep(SLEEP)

        record = parse_daily_article(html, url)
        record["release_date"] = _release_date_from_url(url)
        articles.append(record)

        m = re.search(
            r'href="([^"]*daily-quotidien/\d{6}/dq\d{6}[a-z]-eng\.htm)"[^>]*>\s*Previous release',
            html,
        )
        if not m:
            break
        # The "Previous release" href is site-relative and omits the /n1
        # segment that the articles are actually served under, so normalise
        # every link back to the canonical form rather than trusting it.
        tail = re.search(r"daily-quotidien/\d{6}/dq\d{6}[a-z]-eng\.htm", m.group(1))
        if not tail:
            break
        url = f"https://www150.statcan.gc.ca/n1/{tail.group(0)}"

    if len(articles) < 12:
        raise FetchError(
            f"Only {len(articles)} Daily articles crawled; expected at least 12. "
            "The 'Previous release' link structure may have changed."
        )

    _write(RAW / "daily_articles.json", json.dumps(articles, indent=1, ensure_ascii=False))
    return articles


def _release_date_from_url(url: str) -> str:
    m = re.search(r"/(\d{2})(\d{2})(\d{2})/dq", url)
    if not m:
        return ""
    yy, mm, dd = (int(g) for g in m.groups())
    return f"20{yy:02d}-{mm:02d}-{dd:02d}"


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch raw data for the scorecard.")
    ap.add_argument(
        "--offline",
        action="store_true",
        help="Use only what is already cached in data/raw (no network calls).",
    )
    ap.add_argument("--max-releases", type=int, default=MAX_RELEASES)
    args = ap.parse_args()

    if args.offline:
        needed = ["wds_gdp_levels.json", "boc_policy_rate.json", "daily_articles.json"]
        missing = [n for n in needed if not (RAW / n).exists()]
        if missing:
            raise FetchError(f"--offline but these cached files are missing: {missing}")
        print(f"offline: using cached raw data in {RAW}")
        return 0

    print("Fetching StatCan cube metadata ...")
    meta = fetch_cube_metadata()
    release_time = meta["releaseTime"]
    print(f"  cube {GDP_PRODUCT_ID} is CURRENT; last released {release_time}")

    print("Fetching current (revised) GDP levels ...")
    points = fetch_gdp_levels()
    print(f"  {len(points)} monthly datapoints, latest {points[-1]['refPer']}")

    print("Fetching Bank of Canada policy rate ...")
    obs = fetch_policy_rate()
    print(f"  {len(obs)} daily observations, latest {obs[-1]['d']}")

    print("Crawling The Daily for advance estimates and first prints ...")
    articles = crawl_daily(release_time, args.max_releases)
    clean = [a for a in articles if not a["problems"]]
    print(f"  {len(articles)} releases crawled, {len(clean)} parsed cleanly")
    for a in articles:
        if a["problems"]:
            print(f"  ! {a['release_date']} {a['url']}: {a['problems']}")

    print(f"\nRaw data written to {RAW}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
