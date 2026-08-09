"""
validate_colours.py - compute, rather than eyeball, the accessibility of the
colour scheme used on the page.

The brief asks for verified contrast ratios rather than assumed ones, and for a
palette that never encodes meaning in colour alone. This file is how those
claims are checked. It prints a table that is pasted into the README, and it
exits non-zero if any hard gate fails - so the monthly GitHub Action cannot
publish a page whose colours have silently regressed.

Two things are measured:

1. WCAG 2.1 contrast ratios (the sRGB relative-luminance formula from the spec)
   for every text and chart colour against the surface it actually sits on, in
   both light and dark mode. Body text must clear 4.5:1; chart marks and UI
   must clear 3:1.

2. Colour-vision-deficiency separation. Red/green is the worst possible choice
   for a chart about direction, so this page uses a blue/orange diverging pair.
   To show that is genuinely safe rather than merely conventional, each pair of
   chart colours is simulated under protanopia and deuteranopia using the
   Machado, Oliveira & Fernandes (2009) transforms at full severity, and the
   perceptual distance between them is measured as Euclidean distance in OKLab
   (x100). The threshold used here is dE >= 8, with 6-8 treated as a floor that
   is only acceptable because every chart on this page also carries position,
   direct labels, and a full data table.

No dependencies - this is pure standard library, so it runs anywhere the rest
of the pipeline runs.
"""

from __future__ import annotations

import math
import sys

# --------------------------------------------------------------------------
# Thresholds (from WCAG 2.1 and the data-visualisation guidance followed here)
# --------------------------------------------------------------------------

WCAG_BODY_TEXT = 4.5  # normal-size body text
WCAG_LARGE_TEXT = 3.0  # >=24px, or >=18.66px bold
WCAG_NON_TEXT = 3.0  # chart marks, UI boundaries

CVD_TARGET = 8.0  # OKLab dE x100, simulated protan/deutan
CVD_FLOOR = 6.0  # acceptable only with secondary encoding

# Machado, Oliveira & Fernandes (2009), severity 1.0, applied in linear RGB.
MACHADO = {
    "protan": (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
    "deutan": (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.011820, 0.042940, 0.968881),
    ),
}


# --------------------------------------------------------------------------
# Colour conversion
# --------------------------------------------------------------------------


def hex_to_srgb(h: str) -> tuple[float, float, float]:
    h = h.strip().lstrip("#")
    if len(h) != 6:
        raise ValueError(f"expected a 6-digit hex colour, got {h!r}")
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear(h: str) -> tuple[float, float, float]:
    r, g, b = hex_to_srgb(h)
    return (_srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b))


def relative_luminance(h: str) -> float:
    """WCAG 2.1 relative luminance."""
    r, g, b = linear(h)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG 2.1 contrast ratio between two colours. Always >= 1."""
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _oklab_from_linear(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = rgb
    l = math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m = math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s = math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return (
        0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
        1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
        0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
    )


def _simulate(h: str, kind: str) -> tuple[float, float, float]:
    r, g, b = linear(h)
    M = MACHADO[kind]
    clamp = lambda c: max(0.0, min(1.0, c))  # noqa: E731
    return (
        clamp(M[0][0] * r + M[0][1] * g + M[0][2] * b),
        clamp(M[1][0] * r + M[1][1] * g + M[1][2] * b),
        clamp(M[2][0] * r + M[2][1] * g + M[2][2] * b),
    )


def delta_e(h1: str, h2: str, kind: str | None = None) -> float:
    """Euclidean distance in OKLab x100. `kind=None` is unsimulated vision."""
    a = _oklab_from_linear(_simulate(h1, kind) if kind else linear(h1))
    b = _oklab_from_linear(_simulate(h2, kind) if kind else linear(h2))
    return 100 * math.dist(a, b)


# --------------------------------------------------------------------------
# The palette actually used by render.py
# --------------------------------------------------------------------------
#
# Kept in one place so the page and this checker can never drift apart.
# render.py imports PALETTE from here.

PALETTE = {
    "light": {
        "surface": "#fcfcfb",
        "page": "#f4f4f1",
        "text_primary": "#141413",
        "text_secondary": "#4a4945",
        "text_muted": "#6b6a64",
        "grid": "#dcdbd4",
        # The zero baseline is what makes "above" and "below" readable, so it
        # is an information carrier and has to clear 3:1 - the first colour I
        # picked here (#a8a79f) measured 2.35:1 and was caught by this file.
        "axis": "#8a8a82",
        # The diverging pair. Blue = the outcome came in ABOVE what was
        # expected; orange = BELOW. Chosen over red/green, which is
        # unreadable for roughly 8% of men.
        "above": "#1f6fd0",
        "below": "#c1500f",
        # Neutral reference used for benchmark/context marks.
        "neutral": "#57564f",
    },
    "dark": {
        "surface": "#1a1a19",
        "page": "#0f0f0e",
        "text_primary": "#f7f7f5",
        "text_secondary": "#c9c8be",
        "text_muted": "#9d9c93",
        "grid": "#2f2f2c",
        "axis": "#6f6f67",
        "above": "#5fa3ec",
        "below": "#f0894a",
        "neutral": "#a3a29a",
    },
}


def report() -> int:
    """Print the verification table. Returns a process exit code."""
    failures: list[str] = []
    lines: list[str] = []

    for mode in ("light", "dark"):
        p = PALETTE[mode]
        surface = p["surface"]
        lines.append(f"\n{mode.upper()} MODE  (chart surface {surface})")
        lines.append(f"  {'role':<18} {'hex':<9} {'vs surface':>10}   {'gate':<8} result")

        checks = [
            ("text_primary", WCAG_BODY_TEXT, "body text"),
            ("text_secondary", WCAG_BODY_TEXT, "body text"),
            ("text_muted", WCAG_BODY_TEXT, "body text"),
            ("above", WCAG_NON_TEXT, "chart mark"),
            ("below", WCAG_NON_TEXT, "chart mark"),
            ("neutral", WCAG_NON_TEXT, "chart mark"),
            ("axis", WCAG_NON_TEXT, "UI line"),
        ]
        for role, gate, kind in checks:
            ratio = contrast(p[role], surface)
            ok = ratio >= gate
            if not ok:
                failures.append(f"{mode}/{role}: {ratio:.2f}:1 < {gate}:1 ({kind})")
            lines.append(
                f"  {role:<18} {p[role]:<9} {ratio:>9.2f}:1   {gate:<8.1f} "
                f"{'PASS' if ok else 'FAIL'}  {kind}"
            )

        # Gridlines are decorative hairlines, not information carriers - they
        # are deliberately low-contrast and are reported, not gated.
        g = contrast(p["grid"], surface)
        lines.append(
            f"  {'grid (decorative)':<18} {p['grid']:<9} {g:>9.2f}:1   {'-':<8} "
            f"n/a   not an information carrier"
        )

        # The two diverging colours must be distinguishable from each other,
        # including for readers with colour-vision deficiency.
        pair = (p["above"], p["below"])
        normal = delta_e(*pair)
        prot = delta_e(*pair, "protan")
        deut = delta_e(*pair, "deutan")
        worst = min(prot, deut)
        state = "PASS" if worst >= CVD_TARGET else ("FLOOR" if worst >= CVD_FLOOR else "FAIL")
        if worst < CVD_FLOOR:
            failures.append(f"{mode}: above/below dE {worst:.1f} < {CVD_FLOOR}")
        lines.append(
            f"  above vs below     dE normal {normal:5.1f} | protan {prot:5.1f} | "
            f"deutan {deut:5.1f} -> {state} (target >= {CVD_TARGET:.0f})"
        )

    print("\n".join(lines))
    print(
        "\nEvery chart also encodes direction by position (above or below a zero "
        "\nbaseline), by a text label, and by a full data table, so colour is never "
        "\nthe only channel carrying meaning."
    )

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nAll gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(report())
