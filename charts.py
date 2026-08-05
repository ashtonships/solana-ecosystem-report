#!/usr/bin/env python3
"""Historical charts, drawn as inline SVG from committed snapshots only.

Standard library only. Pure functions over a list of snapshot dicts — no
network, no clock, no JavaScript, no chart library, no external asset. The
output is SVG markup embedded directly in the dashboard, so the page still
renders from `file://` with the machine offline.

    python3 charts.py           # summarise what the snapshot history supports

Everything here exists to stop a chart from claiming more than the data does:

**Only real snapshots are plotted.** There is no resampling, no smoothing and
no back-fill. A series with fewer than two real points is not charted at all;
it is listed as "not enough history yet", which is a different statement from
a flat line at zero.

**Gaps are drawn as gaps.** Two kinds. A snapshot where the metric is missing
breaks the line. So does a stretch of wall-clock time much longer than the
collection cadence — if CI was down for a day, the line stops and restarts
rather than sloping smoothly across the outage, which would be a picture of
data that was never collected.

**Sampled series never look measured.** Median fee and 24h REV come out of a
small block sample; TPS and slot time are read straight off the wire. The
sampled ones are drawn in a different hue *and* dashed *and* badged, so the
distinction survives a colourblind reader, a greyscale print, and a reader
who never looks at the legend.
"""

from __future__ import annotations

import html
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import detect

# ── palette ──────────────────────────────────────────────────────────────────
# Validated for the dashboard's dark surface (#0b0d10) with the dataviz
# validator: CVD separation ΔE 9.0 (deutan, ≥8 target), normal-vision ΔE 21.1
# (≥15 floor), chroma and contrast both pass. The measured hue sits 0.04 above
# the reference lightness ceiling, which is a deliberate trade: this surface is
# considerably darker than the reference surface the band was calibrated on,
# and the in-band alternatives dropped CVD separation into the 6–8 warn band.
# The dash pattern and the per-chart basis badge are secondary encodings, so
# identity never rests on hue alone regardless.
COLOR_MEASURED = "#0fbf76"
COLOR_SAMPLED = "#c98500"
COLOR_GRID = "#242a33"
COLOR_SURFACE = "#14171c"

# Chart geometry, in viewBox units. The SVG scales with its container.
WIDTH = 380
HEIGHT = 150
PAD_LEFT = 52
PAD_RIGHT = 14
PAD_TOP = 12
PAD_BOTTOM = 26

# A step between consecutive snapshots longer than this multiple of the median
# step is treated as a collection gap and drawn as a break in the line.
GAP_FACTOR = 2.5

# Above this many points, per-point markers become noise and are dropped; the
# endpoint marker stays. Below it, every real observation is marked so a short
# history reads as a handful of observations rather than an implied continuum.
MARKER_LIMIT = 12

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# The series the dashboard charts, in render order. `basis` decides both the
# colour and the badge; nothing else in this module distinguishes them.
SERIES: list[dict[str, Any]] = [
    {
        "key": "latest_tps",
        "label": "Transactions per second",
        "path": ("performance", "latest_tps"),
        "unit": "TPS",
        "decimals": 0,
        "basis": "measured",
        "note": "getRecentPerformanceSamples, read directly from the RPC endpoint.",
    },
    {
        "key": "mean_slot_time_secs",
        "label": "Mean slot time",
        "path": ("performance", "mean_slot_time_secs"),
        "unit": "s",
        "decimals": 3,
        "basis": "measured",
        "note": "Derived from the same performance samples. The network targets 0.400s.",
    },
    {
        "key": "delinquent_pct",
        "label": "Validator delinquency",
        "path": ("validators", "delinquent_pct"),
        "unit": "%",
        "decimals": 2,
        "basis": "measured",
        "note": "Delinquent share of the full validator set, from getVoteAccounts.",
    },
    {
        "key": "price_usd",
        "label": "SOL price",
        "path": ("economics", "price", "price_usd"),
        "unit": "USD",
        "decimals": 2,
        "basis": "measured",
        "note": "CoinGecko simple/price — keyless, and absent from snapshots taken "
                "before the economics sources were added.",
    },
    {
        "key": "tvl_usd",
        "label": "Total value locked",
        "path": ("economics", "tvl", "tvl_usd"),
        "unit": "USD",
        "decimals": 0,
        "basis": "measured",
        "note": "DeFiLlama chain TVL. Denominated in USD, so it moves with the SOL price.",
    },
    {
        "key": "median_fee_lamports",
        "label": "Median fee (non-vote)",
        "path": ("activity", "fees", "median_lamports"),
        "unit": "lamports",
        "decimals": 0,
        "basis": "sampled",
        "note": "Median over non-vote transactions in the sampled blocks — a sample, "
                "not a network-wide figure.",
    },
    {
        "key": "estimated_24h_rev_sol",
        "label": "REV 24h (estimated)",
        "path": ("activity", "rev", "estimated_24h_sol"),
        "unit": "SOL",
        "decimals": 0,
        "basis": "sampled",
        "note": "Extrapolated from a small block sample; consecutive estimates carry "
                "wide sampling noise. Read the published interval beside it.",
    },
]


# ── extraction ───────────────────────────────────────────────────────────────

def _value(snapshot: dict[str, Any], path: tuple[str, ...]) -> float | None:
    node: Any = snapshot
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    if isinstance(node, bool) or not isinstance(node, (int, float)):
        return None
    return float(node)


def _unix(snapshot: dict[str, Any]) -> float | None:
    try:
        return datetime.fromisoformat(str(snapshot.get("collected_at"))).timestamp()
    except (TypeError, ValueError):
        return None


def extract(snapshots: list[dict[str, Any]], spec: dict[str, Any]) -> list[dict[str, Any]]:
    """One point per snapshot with a usable timestamp. `value` is None when absent.

    Snapshots whose timestamp cannot be parsed are dropped entirely — a point
    with no position on the x-axis cannot be honestly placed on one.
    """
    points = []
    for snapshot in snapshots:
        when = _unix(snapshot)
        if when is None:
            continue
        points.append({
            "t": when,
            "at": str(snapshot.get("collected_at")),
            "value": _value(snapshot, spec["path"]),
        })
    points.sort(key=lambda p: p["t"])
    return points


def segments(points: list[dict[str, Any]], gap_factor: float = GAP_FACTOR) -> list[list[dict[str, Any]]]:
    """Split points into contiguous runs. Every break here becomes a gap on the page.

    A run breaks on a missing value, and on a time step longer than
    `gap_factor` times the median step. The second rule is the one that keeps a
    collection outage from being drawn as a smooth trend across it.
    """
    present = [p for p in points if p["value"] is not None]
    if len(present) < 2:
        return [[p] for p in present]

    steps = [b["t"] - a["t"] for a, b in zip(present, present[1:]) if b["t"] > a["t"]]
    typical = statistics.median(steps) if steps else 0.0
    limit = typical * gap_factor if typical > 0 else None

    runs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None

    for point in points:
        if point["value"] is None:
            # A snapshot exists but the metric does not: the line must stop.
            if current:
                runs.append(current)
                current = []
            previous = None
            continue
        if (previous is not None and limit is not None
                and point["t"] - previous["t"] > limit):
            runs.append(current)
            current = []
        current.append(point)
        previous = point

    if current:
        runs.append(current)
    return runs


def series_stats(points: list[dict[str, Any]], gap_factor: float = GAP_FACTOR) -> dict[str, Any]:
    """Everything the caption needs: coverage, range, and how many gaps there are."""
    present = [p for p in points if p["value"] is not None]
    values = [p["value"] for p in present]
    runs = segments(points, gap_factor)
    return {
        "points": len(present),
        "snapshots": len(points),
        "missing": len(points) - len(present),
        "gaps": max(0, len(runs) - 1),
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "first": values[0] if values else None,
        "last": values[-1] if values else None,
        "first_at": present[0]["at"] if present else None,
        "last_at": present[-1]["at"] if present else None,
        "chartable": len(present) >= 2,
    }


# ── scales ───────────────────────────────────────────────────────────────────

def nice_axis(low: float, high: float,
              ticks: int = 3) -> tuple[float, float, list[float], float]:
    """A y-axis with clean tick values covering [low, high].

    A constant series is given a band around its value rather than a
    zero-height axis: a flat line is the truth, and it needs somewhere to sit.
    """
    if high < low:
        low, high = high, low
    if high == low:
        pad = abs(high) * 0.1 if high else 1.0
        low, high = low - pad, high + pad

    span = high - low
    rough = span / max(1, ticks - 1)
    magnitude = 10 ** _floor_log10(rough)
    for multiple in (1, 2, 2.5, 5, 10):
        step = multiple * magnitude
        if step >= rough:
            break
    axis_low = _floor_to(low, step)
    axis_high = _ceil_to(high, step)

    values = []
    value = axis_low
    # Guard against a runaway loop on pathological inputs.
    while value <= axis_high + step / 1000 and len(values) < 12:
        values.append(round(value, 10))
        value += step
    return axis_low, axis_high, values, step


def _floor_log10(value: float) -> int:
    if value <= 0:
        return 0
    exponent = 0
    while value < 1:
        value *= 10
        exponent -= 1
    while value >= 10:
        value /= 10
        exponent += 1
    return exponent


def _floor_to(value: float, step: float) -> float:
    return step * (value // step)


def _ceil_to(value: float, step: float) -> float:
    return step * -((-value) // step)


def _scale_for(magnitude: float) -> tuple[float, str]:
    """Pick one compact divisor for a whole axis, never per tick."""
    if magnitude >= 1_000_000_000:
        return 1_000_000_000, "B"
    if magnitude >= 1_000_000:
        return 1_000_000, "M"
    if magnitude >= 10_000:
        return 1_000, "K"
    return 1, ""


def tick_labels(ticks: list[float], step: float) -> list[str]:
    """Axis tick text, with enough precision that adjacent ticks differ.

    Compacting to `4.8B` is only readable while the ticks are far apart. On a
    narrow band — TVL moving 0.2% across a day — every tick rounds to the same
    string and the axis silently stops carrying information. Precision is taken
    from the tick step, so the labels are always distinguishable.
    """
    if not ticks:
        return []
    divisor, suffix = _scale_for(max(abs(ticks[0]), abs(ticks[-1])))
    scaled_step = step / divisor if divisor else step
    decimals = 0 if scaled_step >= 1 else min(4, -_floor_log10(scaled_step))
    return [f"{value / divisor:,.{decimals}f}{suffix}" for value in ticks]


def fmt_time(unix: float) -> str:
    moment = datetime.fromtimestamp(unix, timezone.utc)
    return f"{MONTHS[moment.month - 1]} {moment.day:02d} {moment.hour:02d}:{moment.minute:02d}"


def fmt_value(value: float, decimals: int) -> str:
    return f"{value:,.{decimals}f}" if decimals else f"{value:,.0f}"


def fmt_endlabel(value: float, decimals: int) -> str:
    """The endpoint label. Compact above six digits so it cannot cover the plot.

    The exact figure is never lost — it is in the point's hover title, in the
    JSON series, and in the Markdown report.
    """
    if abs(value) >= 100_000:
        divisor, suffix = _scale_for(abs(value))
        return f"{value / divisor:,.2f}{suffix}"
    return fmt_value(value, decimals)


# ── SVG ──────────────────────────────────────────────────────────────────────

def _round(value: float) -> float:
    """Coordinates are rounded so the same snapshots always emit the same bytes."""
    return round(value, 2)


def svg_chart(spec: dict[str, Any], points: list[dict[str, Any]],
              gap_factor: float = GAP_FACTOR) -> str:
    """One inline SVG line chart. Returns "" if there is nothing honest to draw."""
    stats = series_stats(points, gap_factor)
    if not stats["chartable"]:
        return ""

    colour = COLOR_SAMPLED if spec["basis"] == "sampled" else COLOR_MEASURED
    dash = " stroke-dasharray='5 3'" if spec["basis"] == "sampled" else ""

    axis_low, axis_high, ticks, step = nice_axis(stats["min"], stats["max"])
    labels = tick_labels(ticks, step)
    span = axis_high - axis_low or 1.0

    present = [p for p in points if p["value"] is not None]
    t_low, t_high = points[0]["t"], points[-1]["t"]
    t_span = (t_high - t_low) or 1.0

    plot_w = WIDTH - PAD_LEFT - PAD_RIGHT
    plot_h = HEIGHT - PAD_TOP - PAD_BOTTOM

    def x_of(t: float) -> float:
        return _round(PAD_LEFT + plot_w * (t - t_low) / t_span)

    def y_of(value: float) -> float:
        return _round(PAD_TOP + plot_h * (1 - (value - axis_low) / span))

    parts: list[str] = []
    label = (f"{spec['label']} across {stats['points']} snapshots, "
             f"{fmt_time(points[0]['t'])} to {fmt_time(points[-1]['t'])} UTC")
    parts.append(
        f"<svg class='chart-svg' viewBox='0 0 {WIDTH} {HEIGHT}' "
        f"role='img' aria-label='{html.escape(label)}'>"
    )
    parts.append(f"<title>{html.escape(label)}</title>")

    # Gridlines and y ticks — hairline, solid, one step off the surface.
    for tick, text in zip(ticks, labels):
        y = y_of(tick)
        parts.append(
            f"<line x1='{PAD_LEFT}' y1='{y}' x2='{WIDTH - PAD_RIGHT}' y2='{y}' "
            f"stroke='{COLOR_GRID}' stroke-width='1'/>"
        )
        parts.append(
            f"<text x='{PAD_LEFT - 6}' y='{_round(y + 3.5)}' text-anchor='end' "
            f"class='chart-tick'>{html.escape(text)}</text>"
        )

    # The line itself, one path per contiguous run. The space between runs is
    # the gap — nothing is drawn across it, deliberately.
    for run in segments(points, gap_factor):
        if len(run) == 1:
            single = run[0]
            parts.append(
                f"<circle cx='{x_of(single['t'])}' cy='{y_of(single['value'])}' r='3' "
                f"fill='{colour}' stroke='{COLOR_SURFACE}' stroke-width='2'/>"
            )
            continue
        d = " ".join(
            f"{'M' if index == 0 else 'L'}{x_of(p['t'])} {y_of(p['value'])}"
            for index, p in enumerate(run)
        )
        parts.append(
            f"<path d='{d}' fill='none' stroke='{colour}' stroke-width='2' "
            f"stroke-linejoin='round' stroke-linecap='round'{dash}/>"
        )

    # Per-point markers only while there are few enough for each to be readable.
    if len(present) <= MARKER_LIMIT:
        for point in present:
            parts.append(
                f"<circle cx='{x_of(point['t'])}' cy='{y_of(point['value'])}' r='2.5' "
                f"fill='{colour}' stroke='{COLOR_SURFACE}' stroke-width='2'/>"
            )

    # Endpoint marker and its direct label — the one value worth labelling.
    last = present[-1]
    last_x, last_y = x_of(last["t"]), y_of(last["value"])
    parts.append(
        f"<circle cx='{last_x}' cy='{last_y}' r='4' fill='{colour}' "
        f"stroke='{COLOR_SURFACE}' stroke-width='2'/>"
    )
    label_anchor = "end" if last_x > WIDTH - PAD_RIGHT - 40 else "start"
    label_x = last_x - 7 if label_anchor == "end" else last_x + 7
    parts.append(
        f"<text x='{_round(label_x)}' y='{_round(max(PAD_TOP + 4, last_y - 7))}' "
        f"text-anchor='{label_anchor}' class='chart-endlabel'>"
        f"{html.escape(fmt_endlabel(last['value'], spec['decimals']))}</text>"
    )

    # x-axis: first and last real timestamps only. Every point's exact time is
    # in its hover band below, so the axis stays uncluttered.
    parts.append(
        f"<text x='{PAD_LEFT}' y='{HEIGHT - 8}' text-anchor='start' class='chart-tick'>"
        f"{html.escape(fmt_time(points[0]['t']))}</text>"
    )
    parts.append(
        f"<text x='{WIDTH - PAD_RIGHT}' y='{HEIGHT - 8}' text-anchor='end' "
        f"class='chart-tick'>{html.escape(fmt_time(points[-1]['t']))} UTC</text>"
    )

    # Hover: a nearest-point band per observation carrying a native SVG
    # <title>. No script, and the band is far wider than the marker, so it is
    # not a pinpoint target. Every value it shows is also in the JSON output.
    band = plot_w / max(1, len(points))
    for point in points:
        x = x_of(point["t"])
        reading = (fmt_value(point["value"], spec["decimals"]) + " " + spec["unit"]
                   if point["value"] is not None else "no value in this snapshot")
        parts.append(
            f"<rect x='{_round(max(PAD_LEFT, x - band / 2))}' y='{PAD_TOP}' "
            f"width='{_round(band)}' height='{plot_h}' fill='transparent' "
            f"class='chart-hit'><title>{html.escape(point['at'])} · "
            f"{html.escape(reading)}</title></rect>"
        )

    parts.append("</svg>")
    return "".join(parts)


def caption(spec: dict[str, Any], stats: dict[str, Any]) -> str:
    """The line under the chart. States coverage and gaps in words, not just pixels."""
    pieces = [f"{stats['points']} of {stats['snapshots']} snapshots"]
    if stats["missing"]:
        pieces.append(f"{stats['missing']} without this metric")
    if stats["gaps"]:
        pieces.append(f"{stats['gaps']} gap(s) left undrawn, never interpolated")
    pieces.append(
        f"range {fmt_value(stats['min'], spec['decimals'])}–"
        f"{fmt_value(stats['max'], spec['decimals'])} {spec['unit']}"
    )
    return " · ".join(pieces)


def render_charts_html(snapshots: list[dict[str, Any]],
                       gap_factor: float = GAP_FACTOR) -> str:
    """The history section: one small multiple per chartable series.

    Series without two real points are named and explained rather than drawn
    empty or dropped in silence.
    """
    heading = ("<h2>History <span class='keyless'>from committed snapshots only</span></h2>")

    if not snapshots:
        return (heading + "<p class='unavailable'>No snapshots on disk yet — "
                "history appears once collection has run more than once.</p>")

    figures: list[str] = []
    withheld: list[str] = []

    for spec in SERIES:
        points = extract(snapshots, spec)
        stats = series_stats(points, gap_factor)
        if not stats["chartable"]:
            withheld.append(
                f"{html.escape(spec['label'])} — "
                f"{stats['points']} usable point(s) across {stats['snapshots']} snapshot(s); "
                "two are needed before a trend exists"
            )
            continue
        chart = svg_chart(spec, points, gap_factor)
        badge = ("<span class='basis-badge sampled'>sampled</span>"
                 if spec["basis"] == "sampled"
                 else "<span class='basis-badge measured'>measured</span>")
        figures.append(
            "<figure class='chart'>"
            f"<figcaption class='chart-title'>{html.escape(spec['label'])} "
            f"<span class='chart-unit'>{html.escape(spec['unit'])}</span>{badge}</figcaption>"
            f"{chart}"
            f"<p class='chart-caption'>{html.escape(caption(spec, stats))}</p>"
            f"<p class='chart-note'>{html.escape(spec['note'])}</p>"
            "</figure>"
        )

    parts = [heading]
    parts.append(
        "<p class='unavailable'>Every point below is one committed snapshot. "
        "Nothing is smoothed, resampled or back-filled; a break in a line is a "
        "period with no collected data, drawn as a break rather than interpolated. "
        "<span class='basis-key measured'></span> measured directly · "
        "<span class='basis-key sampled'></span> sampled or extrapolated.</p>"
    )
    if figures:
        parts.append(f"<div class='chart-grid'>{''.join(figures)}</div>")
    else:
        parts.append("<p class='unavailable'>No series has two usable points yet.</p>")
    if withheld:
        items = "".join(f"<li>{item}</li>" for item in withheld)
        parts.append(
            "<div class='anomaly-note pending' style='margin-top:12px'>"
            "<strong>Not charted yet, and not drawn as zero:</strong>"
            f"<ul class='plain-list'>{items}</ul></div>"
        )
    return "".join(parts)


def render_charts_markdown(snapshots: list[dict[str, Any]],
                           gap_factor: float = GAP_FACTOR) -> list[str]:
    """The same history as a table. Markdown gets the numbers, not the pixels."""
    lines = ["## History", ""]
    if not snapshots:
        lines += ["_No snapshots on disk yet._", ""]
        return lines

    lines += [
        f"Across {len(snapshots)} committed snapshot(s). Ranges are over real "
        "observations only — snapshots missing a metric are counted as missing, "
        "never as zero.",
        "",
        "| Series | Points | Missing | Gaps | Min | Max | Latest | Basis |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for spec in SERIES:
        stats = series_stats(extract(snapshots, spec), gap_factor)
        decimals = spec["decimals"]
        if stats["points"]:
            low = fmt_value(stats["min"], decimals)
            high = fmt_value(stats["max"], decimals)
            last = fmt_value(stats["last"], decimals)
        else:
            low = high = last = "—"
        lines.append(
            f"| {spec['label']} ({spec['unit']}) | {stats['points']} | {stats['missing']} "
            f"| {stats['gaps']} | {low} | {high} | {last} "
            f"| {'sampled' if spec['basis'] == 'sampled' else 'measured'} |"
        )
    lines += [
        "",
        "A series with fewer than two points is charted nowhere and reported here as "
        "the count it actually has.",
        "",
    ]
    return lines


def history_json(snapshots: list[dict[str, Any]],
                 gap_factor: float = GAP_FACTOR) -> dict[str, Any]:
    """The plotted series, machine-readable. Missing values stay null."""
    out: dict[str, Any] = {"snapshots": len(snapshots), "series": {}}
    for spec in SERIES:
        points = extract(snapshots, spec)
        stats = series_stats(points, gap_factor)
        out["series"][spec["key"]] = {
            "label": spec["label"],
            "unit": spec["unit"],
            "basis": spec["basis"],
            "charted": stats["chartable"],
            "stats": stats,
            "points": [{"collected_at": p["at"], "value": p["value"]} for p in points],
        }
    return out


def main() -> int:
    snapshots = detect.load_history()
    if not snapshots:
        print("no snapshots found")
        return 0
    print(f"{len(snapshots)} snapshot(s)\n")
    for spec in SERIES:
        stats = series_stats(extract(snapshots, spec))
        state = "chart" if stats["chartable"] else "withheld"
        print(f"  [{state:8}] {spec['label']:28} points={stats['points']:3} "
              f"missing={stats['missing']:3} gaps={stats['gaps']}")
    if "--json" in sys.argv:
        print(json.dumps(history_json(snapshots), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
