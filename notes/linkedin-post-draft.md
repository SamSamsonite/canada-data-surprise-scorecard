# LinkedIn post draft

*~150 words. Plain, specific about the finding, no technology list, no hashtag spam.*

---

I've been working through the Bloomberg Market Concepts Economic Indicators module, and
wanted to build the thing rather than watch a video about it. So I rebuilt a GDP monitor
from free Canadian open data.

It scores Statistics Canada against itself: their advance estimate versus the official
figure that follows a month later, and that first figure versus what it says today after
revision.

Two things surprised me.

The advance estimate is good — it roughly halves the error of just repeating last month's
number.

The revisions are bigger. The published figure moves 0.15 percentage points on average
after the fact; the advance estimate only missed by 0.11. The number everyone reacts to
turns out to be less settled than the preliminary one before it.

I also found the upward lean in revisions mostly disappears outside the pandemic recovery
— so the page says that, rather than claiming more than the data supports.

Page and code in comments.

---

## Notes on this draft

- The finding, not the stack. No mention of Python, GitHub Actions, or SVG — anyone who
  cares can click through, and leading with tooling makes it read like a bootcamp project.
- The robustness caveat is *in the post*. It's a differentiator, not a weakness: it shows
  the analysis was stress-tested. Cutting it would be the easiest way to look naive to
  the one person in the feed who does this for a living.
- "Page and code in comments" — LinkedIn suppresses posts with external links in the body.
- No hashtags. If you feel you must, two at most (#economics #opendata), never five.
