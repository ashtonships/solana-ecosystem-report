#!/usr/bin/env python3
"""Render a snapshot into a Markdown report and a dark interactive HTML dashboard.

Standard library only. Pure functions over a snapshot dict — no network, no
clock, no template engine. The HTML is one self-contained file with inline CSS
so it can be opened straight from disk with no build step and no CDN.

    python3 render.py                          # render snapshots/latest.json
    python3 render.py --snapshot <PATH>
    python3 render.py --out-dir <DIR>
"""

from __future__ import annotations

import argparse
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import charts as charts_module
import delta as delta_module
import detect
import upgrades as upgrades_data

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
OUT_DIR = Path(__file__).parent / "dist"


def fmt(value: Any, suffix: str = "", dash: str = "—") -> str:
    """Format a number for display, or a dash when it is genuinely absent."""
    if value is None:
        return dash
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return f"{value:,.2f}{suffix}".replace(".00", "") if isinstance(value, float) else f"{value:,}{suffix}"
    return str(value)


def fmt_pct(value: Any, suffix: str = "", dash: str = "—") -> str:
    """Signed percentage — a change rendered without a sign is ambiguous."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return dash
    return f"{value:+.2f}%{(' ' + suffix) if suffix else ''}"


def fmt_sol(value: Any, dash: str = "—") -> str:
    """Compact SOL for headline cards — a 9-digit figure is unreadable at a glance."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return dash
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:,.2f}B SOL"
    if value >= 1_000_000:
        return f"{value / 1_000_000:,.1f}M SOL"
    if value >= 1_000:
        return f"{value / 1_000:,.1f}K SOL"
    return f"{value:,.2f} SOL"


def fmt_id(value: Any, dash: str = "—") -> str:
    """Identifiers (epoch, slot index) print bare — `1,012` reads as a quantity."""
    return dash if value is None else str(value)


def sol_price_usd(snapshot: dict[str, Any]) -> float | None:
    """SOL price from the economics section, or None if that source failed.

    The activity section is priced in SOL because that is what the chain
    reports. USD is layered on here so a CoinGecko outage costs the dollar
    figures only and never the on-chain ones.
    """
    price = snapshot.get("economics", {}).get("price", {})
    value = price.get("price_usd") if price.get("available") else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def fmt_usd(sol: Any, price: float | None, dash: str = "—") -> str:
    """SOL converted to USD, with the precision the magnitude needs.

    A median fee is a fraction of a cent and a daily REV total is six figures;
    one format cannot serve both without rendering one of them as "$0.00".
    """
    if not isinstance(sol, (int, float)) or isinstance(sol, bool) or price is None:
        return dash
    value = sol * price
    if value == 0:
        return "$0"
    if abs(value) < 0.01:
        return f"${value:.6f}".rstrip("0")
    if abs(value) < 1000:
        return f"${value:,.2f}"
    return f"${value:,.0f}"


def fmt_lamports(value: Any, dash: str = "—") -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return dash
    return f"{value:,.0f} lamports"


def hours(seconds: Any) -> str:
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        return "—"
    return f"{seconds / 3600:.1f}h"


def analysis_for(snapshot: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    """Anomalies for THIS snapshot: drop history collected after it.

    Without the cutoff, re-rendering an older snapshot would wear the newest
    snapshot's anomaly verdict — a page describing one moment with a panel
    describing another.
    """
    cutoff = snapshot.get("collected_at")
    if isinstance(cutoff, str) and cutoff:
        history = [s for s in history if s.get("collected_at", "") <= cutoff]
    return detect.analyse(history)


def block_time_iso(snapshot: dict[str, Any]) -> str:
    unix = snapshot.get("network", {}).get("block_time_unix")
    if not isinstance(unix, int):
        return "—"
    return datetime.fromtimestamp(unix, timezone.utc).isoformat(timespec="seconds")


# ── Markdown ─────────────────────────────────────────────────────────────────

SEVERITY_MARKS = {"critical": "🔴", "warning": "🟡", "info": "🔵"}


def render_anomalies_markdown(analysis: dict[str, Any] | None) -> list[str]:
    """Anomaly section. An absent baseline is stated, never shown as all-clear."""
    if not analysis:
        return []

    lines = ["## Anomalies", ""]
    status = analysis.get("status")

    if status in ("no_data", "insufficient_history"):
        lines += [f"⚪ **Not yet assessable** — {analysis.get('message', '')}", ""]
        return lines

    findings = analysis.get("findings", [])
    if not findings:
        lines += [
            f"🟢 **None detected** across {analysis.get('snapshots_analysed')} snapshots "
            f"(baseline of {analysis.get('baseline_size')}).",
            "",
        ]
        return lines

    lines += ["| | Finding | Observed | Baseline |", "| --- | --- | --- | --- |"]
    for item in findings:
        lines.append(
            f"| {SEVERITY_MARKS.get(item['severity'], '')} {item['severity']} "
            f"| **{item['title']}** — {item['detail']} "
            f"| {item['observed']} | {item['baseline'] if item['baseline'] is not None else '—'} |"
        )
    lines.append("")
    return lines


def fmt_delta_value(value: Any, unit: str = "", dash: str = "—") -> str:
    """A delta figure printed with its unit, or a dash when it is genuinely absent."""
    if value is None:
        return dash
    if isinstance(value, int):
        return f"{value:,}{unit}"
    if isinstance(value, float):
        text = f"{value:,}"
        return f"{text}{unit}"
    return f"{value}{unit}"


def fmt_delta_change(item: dict[str, Any]) -> str:
    """Signed change with its percentage, or `n/a` when the percentage is undefined.

    A move away from zero has no meaningful percentage. Printing one anyway —
    or quietly dropping to `+0.00%` — is the classic way a delta table lies.
    """
    change = item.get("change")
    unit = item.get("unit", "")
    sign = "+" if isinstance(change, (int, float)) and change > 0 else ""
    body = f"{sign}{fmt_delta_value(change, unit)}"
    pct = item.get("change_pct")
    if pct is None:
        return f"{body} (% n/a from zero)"
    return f"{body} ({pct:+.2f}%)"


def render_delta_markdown(comparison: dict[str, Any] | None) -> list[str]:
    """What changed since the previous snapshot. Deterministic, never narrated."""
    if not comparison:
        return []

    lines = ["## What changed since the last snapshot", ""]

    if comparison.get("status") != "ok":
        lines += [f"⚪ **Not yet comparable** — {comparison.get('message', '')}", ""]
        return lines

    lines += [
        f"> `{comparison.get('previous_collected_at')}` → "
        f"`{comparison.get('current_collected_at')}` "
        f"({delta_module.format_elapsed(comparison.get('elapsed_seconds'))} apart). "
        f"{comparison['counts']['changed']} metric(s) moved past threshold, "
        f"{comparison['counts']['steady']} steady, "
        f"{comparison['counts']['not_comparable']} not comparable.",
        "",
    ]

    changes = comparison.get("changes", [])
    if changes:
        lines += [
            "| Metric | Previous | Current | Change | Basis |",
            "| --- | --- | --- | --- | --- |",
        ]
        for item in changes:
            lines.append(
                f"| {item['label']} | {fmt_delta_value(item['previous'], item['unit'])} "
                f"| {fmt_delta_value(item['current'], item['unit'])} "
                f"| {fmt_delta_change(item)} "
                f"| {'sampled/extrapolated' if item['basis'] == 'sampled' else 'measured'} |"
            )
        lines.append("")
        for item in changes:
            lines += [
                f"**{item['label']}** — {item['why_it_matters']}  ",
                f"_Verify:_ {item['what_to_verify']}",
                "",
            ]
    else:
        lines += [
            "🟢 **No metric moved past its threshold** across "
            f"{comparison['counts']['steady']} compared metric(s).",
            "",
        ]

    not_comparable = comparison.get("not_comparable", [])
    if not_comparable:
        lines += [
            "Not comparable — reported as such rather than as a change of zero:",
            "",
        ]
        lines += [f"- **{item['label']}** — {item['reason']}" for item in not_comparable]
        lines.append("")

    return lines


def render_delta_html(comparison: dict[str, Any] | None) -> str:
    """The same delta, as a panel. Same data, same determinism."""
    if not comparison:
        return ""

    heading = ("<h2>What changed since the last snapshot "
               "<span class='keyless'>deterministic</span></h2>")

    if comparison.get("status") != "ok":
        return (
            heading
            + "<div class='anomaly-note pending'><strong>Not yet comparable.</strong> "
            + html.escape(str(comparison.get("message", ""))) + "</div>"
        )

    counts = comparison["counts"]
    parts = [
        heading,
        f"<p class='unavailable'>{html.escape(str(comparison.get('previous_collected_at')))} → "
        f"{html.escape(str(comparison.get('current_collected_at')))} · "
        f"{delta_module.format_elapsed(comparison.get('elapsed_seconds'))} apart · "
        f"{counts['changed']} moved · {counts['steady']} steady · "
        f"{counts['not_comparable']} not comparable</p>",
    ]

    changes = comparison.get("changes", [])
    if changes:
        parts.append("<table><thead><tr><th>Metric</th><th>Previous</th><th>Current</th>"
                     "<th>Change</th><th>Why it matters · what to verify</th>"
                     "</tr></thead><tbody>")
        for item in changes:
            direction = item.get("direction", "flat")
            badge = ("<span class='basis-badge sampled'>sampled</span>"
                     if item["basis"] == "sampled" else "")
            parts.append(
                f"<tr><td>{html.escape(item['label'])}{badge}</td>"
                f"<td class='mono'>{html.escape(fmt_delta_value(item['previous'], item['unit']))}</td>"
                f"<td class='mono'>{html.escape(fmt_delta_value(item['current'], item['unit']))}</td>"
                f"<td class='mono delta-{html.escape(direction)}'>"
                f"{html.escape(fmt_delta_change(item))}</td>"
                f"<td>{html.escape(item['why_it_matters'])}"
                f"<small>Verify: {html.escape(item['what_to_verify'])}</small></td></tr>"
            )
        parts.append("</tbody></table>")
    else:
        parts.append(
            "<div class='anomaly-note clear'><strong>No metric moved past its "
            f"threshold</strong> across {counts['steady']} compared metric(s).</div>"
        )

    not_comparable = comparison.get("not_comparable", [])
    if not_comparable:
        items = "".join(
            f"<li>{html.escape(item['label'])} — {html.escape(item['reason'])}</li>"
            for item in not_comparable
        )
        parts.append(
            "<div class='anomaly-note pending' style='margin-top:12px'>"
            "<strong>Not comparable, so not reported as a change:</strong>"
            f"<ul class='plain-list'>{items}</ul></div>"
        )

    return "".join(parts)


def interval_suffix(rev: dict[str, Any], price: float | None) -> str:
    """The confidence interval printed beside the daily REV estimate.

    Omitted rather than faked when there was only one block to work from — an
    interval of zero width would be the most misleading thing on the page.
    """
    low, high = rev.get("estimated_24h_sol_low"), rev.get("estimated_24h_sol_high")
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        return ""
    return (f", {rev.get('confidence', 'interval')} "
            f"{fmt(low)}–{fmt(high)} SOL ({fmt_usd(low, price)}–{fmt_usd(high, price)})")


def truncation_note(window: dict[str, Any]) -> str:
    if not window.get("truncated"):
        return ""
    return (f" ⚠️ Sampling stopped early at {fmt(window.get('blocks_sampled'))} of "
            f"{fmt(window.get('blocks_requested'))} blocks — the endpoint was throttling. "
            "The figures below are from the blocks that were read.")


def render_activity_markdown(snapshot: dict[str, Any]) -> list[str]:
    """Fees, REV and address activity, sampled from block bodies.

    Every number here states its basis in the table itself. A median fee with
    no "non-vote" label is not a smaller claim than a wrong one — it is the
    same claim, unfalsifiable.
    """
    activity = snapshot.get("activity", {})
    lines = ["## Fees, REV and activity", ""]

    if not activity.get("available"):
        lines += ["_Block sampling unavailable in this snapshot._", ""]
        return lines

    price = sol_price_usd(snapshot)
    window = activity.get("window", {})
    fees = activity.get("fees", {})
    rev = activity.get("rev", {})
    addresses = activity.get("addresses", {})
    split = activity.get("fee_split", {})

    lines += [
        f"> Sampled from **{fmt(window.get('blocks_sampled'))} blocks** evenly spaced across "
        f"{hours(window.get('observed_seconds'))} of chain history "
        f"(~{fmt(rev.get('blocks_in_window'))} blocks produced in that window). "
        f"Public JSON-RPC `getBlock`, no API key.{truncation_note(window)}",
        "",
    ]

    if fees.get("available"):
        lines += [
            "| Transaction fee | Value | USD |",
            "| --- | --- | --- |",
            f"| Median | {fmt_lamports(fees.get('median_lamports'))} "
            f"| {fmt_usd(fees.get('median_sol'), price)} |",
            f"| Mean | {fmt_lamports(fees.get('mean_lamports'))} "
            f"| {fmt_usd((fees.get('mean_lamports') or 0) / 1e9, price)} |",
            f"| 90th percentile | {fmt_lamports(fees.get('p90_lamports'))} "
            f"| {fmt_usd((fees.get('p90_lamports') or 0) / 1e9, price)} |",
            f"| 99th percentile | {fmt_lamports(fees.get('p99_lamports'))} "
            f"| {fmt_usd((fees.get('p99_lamports') or 0) / 1e9, price)} |",
            "",
            f"Measured over **{fmt(fees.get('nonvote_transactions_sampled'))} non-vote transactions**. "
            f"Vote transactions were {fmt(fees.get('vote_share_pct'), '%')} of all sampled traffic and are "
            "excluded — every vote pays exactly 5,000 lamports, so including them pins the median there "
            "and the figure stops describing what it costs anyone to use the network. "
            f"{fmt(fees.get('failure_rate_pct'), '%')} of non-vote transactions failed on-chain.",
            "",
        ]
    else:
        lines += ["_Fee distribution unavailable in this snapshot._", ""]

    if rev.get("available"):
        sampled = rev.get("sampled_sol", {})
        per_block = rev.get("per_block_sol", {})
        lines += [
            "### Real economic value",
            "",
            f"REV is **{rev.get('definition', '—')}**.",
            "",
            "| Component | Sampled (SOL) | Share |",
            "| --- | --- | --- |",
        ]
        total = sampled.get("total") or 0
        for label, key in (("Base fees", "base"), ("Priority fees", "priority"), ("Jito tips", "jito_tips")):
            value = sampled.get(key)
            share = f"{100 * value / total:.1f}%" if total and isinstance(value, (int, float)) else "—"
            lines.append(f"| {label} | {fmt(value)} | {share} |")
        lines += [
            f"| **Total sampled** | **{fmt(total)}** | 100% |",
            "",
            f"**Estimated 24h REV: {fmt(rev.get('estimated_24h_sol'), ' SOL')}** "
            f"({fmt_usd(rev.get('estimated_24h_sol'), price)}){interval_suffix(rev, price)}.",
            "",
            f"Extrapolated, not measured: {rev.get('method', '')}. "
            f"Per-block REV across the sample ranged {fmt(per_block.get('min'))} to "
            f"{fmt(per_block.get('max'))} SOL (mean {fmt(per_block.get('mean'))}), so treat the daily "
            "figure as an order-of-magnitude estimate from a small sample, not a settled total.",
            "",
        ]
        if split.get("available"):
            lines += [
                f"Of the fees in the {fmt(split.get('blocks_reconciled'))} reconciled blocks, "
                f"**{fmt(split.get('burned_pct'), '%')} was burned** "
                f"({fmt(split.get('burned_sol'), ' SOL')}) and "
                f"{fmt(split.get('validator_reward_sol'), ' SOL')} went to block leaders. "
                "Measured from each block's own fee reward entry — no burn rate is assumed, "
                "so this stays correct across a change to the fee split.",
                "",
            ]

    if addresses.get("available"):
        lines += [
            "### Address activity",
            "",
            "| Metric | Value |",
            "| --- | --- |",
            f"| Unique fee payers (sampled) | {fmt(addresses.get('unique_fee_payers_sampled'))} |",
            f"| Unique accounts touched (sampled) | {fmt(addresses.get('unique_accounts_sampled'))} |",
            f"| Mean fee payers per block | {fmt(addresses.get('mean_fee_payers_per_block'))} |",
            "| **Daily active addresses** | **not derivable — see below** |",
            "",
            f"⚠️ {addresses.get('note', '')}",
            "",
        ]

    return lines


def render_news_markdown(snapshot: dict[str, Any]) -> list[str]:
    """Releases and announcements, replayed from what the snapshot recorded.

    Three states per feed, and they are not interchangeable: unavailable (the
    fetch or the parse failed), published nothing, and a list of entries.
    Collapsing the first two into an empty section would turn a failed request
    into a claim that the ecosystem has been quiet.
    """
    news = snapshot.get("news")
    lines = ["## Releases and announcements", ""]

    if news is None:
        # A snapshot older than the feature. Not the same as a failed fetch.
        lines += ["_This snapshot predates the releases section and recorded no feeds._", ""]
        return lines

    if not news.get("available"):
        lines += [
            "_No feed was reachable when this snapshot was collected. This is a "
            "statement about the fetch, not about the ecosystem._",
            "",
        ]
        return lines

    lines += [f"> {news.get('note', '')}", ""]

    for source in news.get("sources", {}).values():
        lines += [f"### {source.get('label', '—')}", ""]
        lines += [f"_{source.get('why', '')}_", ""]

        if not source.get("available"):
            lines += [
                f"⚠️ **Unavailable** — {source.get('reason', 'source failed')}. "
                f"Other feeds are unaffected. Source: {source.get('url', '—')}",
                "",
            ]
            continue

        items = source.get("items", [])
        if not items:
            lines += [
                f"_The feed was read and published no entries — "
                f"{source.get('reason', '')}._",
                "",
            ]
            continue

        lines += ["| Published (UTC) | Entry |", "| --- | --- |"]
        for item in items:
            title = item.get("title", "—")
            link = item.get("link")
            text = f"[{title}]({link})" if link else title
            lines.append(f"| {item.get('published') or '—'} | {text} |")
        lines += [
            "",
            f"From `{source.get('url', '—')}` — {source.get('publisher', '')}, "
            "public and keyless, recorded at collection time.",
            "",
        ]

    return lines


def render_news_html(snapshot: dict[str, Any]) -> str:
    """The same feeds as a panel, with the same three states kept distinct."""
    news = snapshot.get("news")
    heading = ("<h2>Releases and announcements "
               "<span class='keyless'>keyless official feeds</span></h2>")

    if news is None:
        return (heading + "<p class='unavailable'>This snapshot predates the releases "
                "section and recorded no feeds.</p>")

    if not news.get("available"):
        return (
            heading
            + "<p class='unavailable'>No feed was reachable when this snapshot was "
            "collected. That is a statement about the fetch, not about the ecosystem.</p>"
        )

    parts = [heading, f"<p class='unavailable'>{html.escape(str(news.get('note', '')))}</p>",
             "<div class='news-grid'>"]

    for source in news.get("sources", {}).values():
        body: str
        if not source.get("available"):
            body = ("<p class='unavailable'>Unavailable — "
                    f"{html.escape(str(source.get('reason', 'source failed')))}. "
                    "Other feeds are unaffected.</p>")
        elif not source.get("items"):
            body = ("<p class='unavailable'>Read successfully; the feed published "
                    "no entries.</p>")
        else:
            rows = []
            for item in source["items"]:
                title = html.escape(str(item.get("title", "—")))
                link = item.get("link")
                anchor = (f"<a href='{html.escape(link)}' target='_blank' "
                          f"rel='noreferrer'>{title}</a>"
                          if isinstance(link, str) else title)
                author = item.get("author")
                byline = (f" <span class='news-author'>{html.escape(str(author))}</span>"
                          if author else "")
                rows.append(
                    f"<li><span class='news-date mono'>"
                    f"{html.escape(str(item.get('published') or '—'))}</span>"
                    f"<span class='news-title'>{anchor}{byline}</span></li>"
                )
            body = f"<ul class='news-list'>{''.join(rows)}</ul>"

        parts.append(
            "<section class='news-source'>"
            f"<h3 class='news-heading'>{html.escape(str(source.get('label', '—')))}</h3>"
            f"<p class='news-why'>{html.escape(str(source.get('why', '')))}</p>"
            f"{body}"
            f"<p class='chart-note'>{html.escape(str(source.get('publisher', '')))} · "
            "no API key · recorded into the snapshot at collection time</p>"
            "</section>"
        )

    parts.append("</div>")
    return "".join(parts)


def render_markdown(
    snapshot: dict[str, Any],
    analysis: dict[str, Any] | None = None,
    comparison: dict[str, Any] | None = None,
    history: list[dict[str, Any]] | None = None,
) -> str:
    network = snapshot.get("network", {})
    epoch = snapshot.get("epoch", {})
    perf = snapshot.get("performance", {})
    supply = snapshot.get("supply", {})
    validators = snapshot.get("validators", {})

    lines: list[str] = [
        "# Solana Ecosystem Report",
        "",
        f"**Collected:** {snapshot.get('collected_at', '—')}  ",
        f"**Source:** `{snapshot.get('source', {}).get('endpoint', '—')}` (public JSON-RPC, no API key)  ",
        f"**Network health:** {'🟢 healthy' if network.get('healthy') else '🔴 unhealthy'}",
        "",
        *render_anomalies_markdown(analysis),
        *render_delta_markdown(comparison),
        "## Network",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Current slot | {fmt(network.get('slot'))} |",
        f"| Block time | {block_time_iso(snapshot)} |",
        f"| Block height | {fmt(epoch.get('block_height'))} |",
        f"| Total transactions | {fmt(epoch.get('transaction_count'))} |",
        "",
        "## Epoch",
        "",
    ]

    if epoch.get("available"):
        progress = epoch.get("progress_pct")
        lines += [
            "| Metric | Value |",
            "| --- | --- |",
            f"| Epoch | {fmt_id(epoch.get('epoch'))} |",
            f"| Progress | {fmt(progress, '%')} |",
            f"| Slot in epoch | {fmt(epoch.get('slot_index'))} of {fmt(epoch.get('slots_in_epoch'))} |",
            "",
        ]
    else:
        lines += ["_Epoch data unavailable in this snapshot._", ""]

    lines += ["## Performance", ""]
    if perf.get("available"):
        lines += [
            "| Metric | Value |",
            "| --- | --- |",
            f"| Latest TPS | {fmt(perf.get('latest_tps'))} |",
            f"| Mean TPS ({perf.get('samples_used')} samples) | {fmt(perf.get('mean_tps'))} |",
            f"| Peak TPS | {fmt(perf.get('peak_tps'))} |",
            f"| Mean slot time | {fmt(perf.get('mean_slot_time_secs'), 's')} |",
            "",
        ]
    else:
        lines += ["_Performance samples unavailable in this snapshot._", ""]

    if history:
        lines += charts_module.render_charts_markdown(history)

    lines += render_activity_markdown(snapshot)

    economics = snapshot.get("economics", {})
    lines += ["## Economic indicators", ""]
    if economics.get("available"):
        price = economics.get("price", {})
        tvl = economics.get("tvl", {})
        stables = economics.get("stablecoins", {})
        dex = economics.get("dex", {})
        lines += [
            "| Metric | Value | Source |",
            "| --- | --- | --- |",
            f"| SOL price | {'$' + format(price['price_usd'], ',.2f') if price.get('available') else '—'} "
            f"({fmt_pct(price.get('change_24h_pct'), '24h')}) | CoinGecko |",
            f"| Market cap | {'$' + fmt(price.get('market_cap_usd')) if price.get('available') else '—'} | CoinGecko |",
            f"| 24h trading volume | {'$' + fmt(price.get('volume_24h_usd')) if price.get('available') else '—'} | CoinGecko |",
            f"| Total value locked | {'$' + fmt(tvl.get('tvl_usd')) if tvl.get('available') else '—'} "
            f"({fmt_pct(tvl.get('change_7d_pct'), '7d')}) | DeFiLlama |",
            f"| Stablecoin supply | {'$' + fmt(stables.get('stablecoin_usd')) if stables.get('available') else '—'} | DeFiLlama |",
            f"| DEX volume 24h | {'$' + fmt(dex.get('volume_24h_usd')) if dex.get('available') else '—'} "
            f"({fmt_pct(dex.get('change_1d_pct'), '1d')}) | DeFiLlama |",
            "",
            "_All economic sources are public and keyless — no API key or account required._",
            "",
        ]
        unavailable = [n for n, s in economics.get("sources", {}).items() if not s.get("available")]
        if unavailable:
            lines += [f"⚠️ Unavailable this run: {', '.join(sorted(unavailable))}.", ""]
    else:
        lines += ["_Economic sources unavailable in this snapshot._", ""]

    lines += ["## Supply", ""]
    if supply.get("available"):
        lines += [
            "| Metric | Value |",
            "| --- | --- |",
            f"| Total supply | {fmt(supply.get('total_sol'), ' SOL')} |",
            f"| Circulating | {fmt(supply.get('circulating_sol'), ' SOL')} |",
            f"| Circulating share | {fmt(supply.get('circulating_pct'), '%')} |",
            "",
        ]
    else:
        lines += ["_Supply data unavailable in this snapshot._", ""]

    lines += ["## Validators", ""]
    if validators.get("available"):
        lines += [
            "| Metric | Value |",
            "| --- | --- |",
            f"| Active validators | {fmt(validators.get('active_count'))} |",
            f"| Delinquent validators | {fmt(validators.get('delinquent_count'))} ({fmt(validators.get('delinquent_pct'), '%')}) |",
            f"| Active stake | {fmt(validators.get('active_stake_sol'), ' SOL')} |",
            f"| Nakamoto coefficient | {fmt(validators.get('nakamoto_coefficient'))} |",
            f"| Median commission | {fmt(validators.get('commission', {}).get('median_pct'))}% "
            f"(mean {fmt(validators.get('commission', {}).get('mean_pct'))}%, "
            f"{fmt(validators.get('commission', {}).get('zero_commission_count'))} at 0%) |",
            "",
            "### Top validators by stake",
            "",
            "| # | Identity | Stake (SOL) | Share | Commission |",
            "| --- | --- | --- | --- | --- |",
        ]
        for rank, validator in enumerate(validators.get("top_validators", []), start=1):
            lines.append(
                f"| {rank} | `{validator.get('identity', '—')}` "
                f"| {fmt(validator.get('stake_sol'))} "
                f"| {fmt(validator.get('share_pct'), '%')} "
                f"| {fmt(validator.get('commission'), '%')} |"
            )
        lines.append("")
    else:
        lines += ["_Validator data unavailable in this snapshot._", ""]

    lines += render_news_markdown(snapshot)

    roadmap = upgrades_data.upgrade_section()
    if roadmap["available"]:
        lines += [
            f"## Upcoming upgrades",
            "",
            f"> **Static reference data**, last checked {roadmap['last_verified']}. "
            "Not a live feed — verify against the linked SIMD repository.",
            "",
        ]
        for item in roadmap["upgrades"]:
            lines += [
                f"### {item['name']} — {item['status']}",
                "",
                item["summary"],
                "",
                f"_{item['why_it_matters']}_",
                "",
                f"Source: {item['source']}",
                "",
            ]

    lines += [
        "---",
        "",
        "Generated from a single snapshot by `render.py`. On-chain data comes from public",
        "Solana JSON-RPC; economic data from public keyless endpoints. No third-party",
        "Python packages, no API keys, and no account is required for any source.",
        "",
    ]
    return "\n".join(lines)


# ── HTML ─────────────────────────────────────────────────────────────────────

CSS = """
:root {
  --bg: #0b0d10; --panel: #14171c; --panel-2: #1a1e24;
  --line: #242a33; --text: #e6e9ef; --dim: #8b95a5;
  --accent: #14f195; --accent-2: #9945ff; --warn: #ffb020; --bad: #ff4d4f;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
}
.wrap { max-width: 1100px; margin: 0 auto; padding: 40px 24px 72px; }
header { border-bottom: 1px solid var(--line); padding-bottom: 20px; margin-bottom: 28px; }
h1 { margin: 0 0 6px; font-size: 26px; letter-spacing: -0.4px; }
h1 .dot { color: var(--accent); }
.meta { color: var(--dim); font-size: 13px; }
.meta code { color: var(--accent-2); }
.pill {
  display: inline-block; padding: 2px 10px; border-radius: 999px;
  font-size: 12px; font-weight: 600; margin-left: 8px;
}
.pill.ok { background: rgba(20,241,149,0.14); color: var(--accent); }
.pill.bad { background: rgba(255,77,79,0.14); color: var(--bad); }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 1px;
     color: var(--dim); margin: 34px 0 12px; font-weight: 600; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }
.card { background: var(--panel); border: 1px solid var(--line);
        border-radius: 12px; padding: 16px 18px; }
.card .label { color: var(--dim); font-size: 11px; text-transform: uppercase;
               letter-spacing: 0.6px; margin-bottom: 6px; }
.card .value { font-size: 24px; font-weight: 600; letter-spacing: -0.5px; }
.card .sub { color: var(--dim); font-size: 12px; margin-top: 4px; }
.bar { height: 6px; background: var(--panel-2); border-radius: 3px;
       overflow: hidden; margin-top: 10px; }
.bar > i { display: block; height: 100%;
           background: linear-gradient(90deg, var(--accent-2), var(--accent)); }
table { width: 100%; border-collapse: collapse; font-size: 13px;
        background: var(--panel); border: 1px solid var(--line); border-radius: 12px; }
th { text-align: left; color: var(--dim); font-size: 11px; text-transform: uppercase;
     letter-spacing: 0.6px; font-weight: 600; padding: 11px 14px;
     border-bottom: 1px solid var(--line); }
td { padding: 11px 14px; border-bottom: 1px solid var(--line); }
tr:last-child td { border-bottom: none; }
tr:hover td { background: var(--panel-2); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.sparkline { display: flex; align-items: flex-end; gap: 3px; height: 40px; margin-top: 10px; }
.sparkline > i { flex: 1; background: var(--accent-2); border-radius: 2px 2px 0 0; min-height: 2px; }
.sparkline > i:last-child { background: var(--accent); }
.unavailable { color: var(--dim); font-style: italic; padding: 14px 0; }
.keyless { margin-left: 8px; padding: 2px 8px; border-radius: 999px;
           background: rgba(20,241,149,0.12); color: var(--accent);
           font-size: 10px; letter-spacing: 0.4px; text-transform: none; }
.anomaly-note { border: 1px solid var(--line); border-radius: 10px;
                padding: 13px 16px; font-size: 13px; background: var(--panel); }
.anomaly-note.clear   { border-left: 3px solid var(--accent); }
/* Deliberately not green — "no baseline yet" must not read as "all clear". */
.anomaly-note.pending { border-left: 3px solid var(--dim); color: var(--dim); }
.anomaly-list { list-style: none; margin: 0; padding: 0; }
.anomaly { display: flex; gap: 14px; align-items: baseline;
           background: var(--panel); border: 1px solid var(--line);
           border-radius: 10px; padding: 12px 16px; margin-bottom: 8px; }
.anomaly small { display: block; color: var(--dim); font-size: 12px; margin-top: 2px; }
.anomaly-sev { min-width: 68px; font-size: 11px; font-weight: 700;
               text-transform: uppercase; letter-spacing: 0.6px; }
.anomaly.critical { border-left: 3px solid var(--bad); }
.anomaly.critical .anomaly-sev { color: var(--bad); }
.anomaly.warning  { border-left: 3px solid var(--warn); }
.anomaly.warning .anomaly-sev { color: var(--warn); }
.anomaly.info     { border-left: 3px solid var(--accent-2); }
.anomaly.info .anomaly-sev { color: var(--accent-2); }
.anomaly-nums { margin-left: auto; color: var(--dim); white-space: nowrap; }
/* Charts. Small multiples: one series per plot, one y-axis per plot — two
   scales on one pair of axes would invent a relationship the data lacks. */
.chart-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
              gap: 12px; }
.chart { margin: 0; background: var(--panel); border: 1px solid var(--line);
         border-radius: 12px; padding: 14px 16px 12px; }
.chart-title { color: var(--text); font-size: 12px; font-weight: 600;
               text-transform: uppercase; letter-spacing: 0.6px; margin-bottom: 6px; }
.chart-unit { color: var(--dim); font-weight: 400; text-transform: none;
              letter-spacing: 0; }
.chart-svg { width: 100%; height: auto; display: block; overflow: visible; }
.chart-tick { fill: var(--dim); font-size: 9px;
              font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
              font-variant-numeric: tabular-nums; }
.chart-endlabel { fill: var(--text); font-size: 10px; font-weight: 600;
                  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.chart-hit:hover { fill: rgba(255,255,255,0.04); }
.chart-caption { color: var(--dim); font-size: 11px; margin: 8px 0 0; }
.chart-note { color: var(--dim); font-size: 11px; margin: 4px 0 0; opacity: 0.8; }
.basis-key { display: inline-block; width: 18px; height: 0; vertical-align: middle;
             margin: 0 2px 3px 6px; }
.basis-key.measured { border-top: 2px solid #0fbf76; }
/* Dashed, and a different hue, and badged — three channels, so the sampled/
   measured distinction survives colourblindness and greyscale printing. */
.basis-key.sampled { border-top: 2px dashed #c98500; }
.basis-badge { margin-left: 8px; padding: 1px 7px; border-radius: 999px;
               font-size: 10px; letter-spacing: 0.3px; vertical-align: middle; }
/* Sampled and measured must never look alike — same rule as the charts. */
.basis-badge.sampled { background: rgba(255,176,32,0.12); color: var(--warn); }
.basis-badge.measured { background: rgba(20,241,149,0.12); color: var(--accent); }
.delta-up { color: var(--accent); }
.delta-down { color: var(--warn); }
.delta-flat { color: var(--dim); }
td small { display: block; color: var(--dim); font-size: 11px; margin-top: 3px; }
.plain-list { list-style: none; margin: 8px 0 0; padding: 0; font-size: 12px; }
.plain-list li { margin-top: 3px; }
.news-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
             gap: 12px; }
.news-source { background: var(--panel); border: 1px solid var(--line);
               border-radius: 12px; padding: 14px 16px 12px; }
.news-heading { margin: 0 0 4px; font-size: 12px; text-transform: uppercase;
                letter-spacing: 0.6px; color: var(--text); }
.news-why { margin: 0 0 10px; color: var(--dim); font-size: 11px; }
.news-list { list-style: none; margin: 0; padding: 0; }
.news-list li { display: flex; gap: 10px; align-items: baseline; font-size: 12px;
                padding: 6px 0; border-bottom: 1px solid var(--line); }
.news-list li:last-child { border-bottom: none; }
.news-date { color: var(--dim); font-size: 10px; white-space: nowrap; }
.news-title a { color: var(--text); text-decoration: none; }
.news-title a:hover { color: var(--accent); }
.news-author { color: var(--dim); font-size: 10px; }
.static-badge { margin-left: 8px; padding: 2px 8px; border-radius: 999px;
                background: rgba(255,176,32,0.12); color: var(--warn);
                font-size: 10px; letter-spacing: 0.4px; text-transform: none; }
.upgrade-list { list-style: none; margin: 0; padding: 0; }
.upgrade-list li { background: var(--panel); border: 1px solid var(--line);
                   border-radius: 10px; padding: 14px 16px; margin-bottom: 8px; }
.upgrade-list small { display: block; color: var(--dim); font-size: 12px; margin-top: 4px; }
.upgrade-list .upgrade-why { color: var(--text); opacity: 0.75; }
.upgrade-list a { color: var(--accent-2); font-size: 11px; text-decoration: none;
                  display: inline-block; margin-top: 8px; }
.upgrade-status { margin-left: 8px; font-size: 11px; color: var(--warn);
                  text-transform: uppercase; letter-spacing: 0.5px; }
footer { margin-top: 44px; padding-top: 18px; border-top: 1px solid var(--line);
         color: var(--dim); font-size: 12px; }
"""


def card(label: str, value: str, sub: str = "", extra: str = "") -> str:
    sub_html = f'<div class="sub">{html.escape(sub)}</div>' if sub else ""
    return (
        f'<div class="card"><div class="label">{html.escape(label)}</div>'
        f'<div class="value">{html.escape(value)}</div>{sub_html}{extra}</div>'
    )


def render_anomalies_html(analysis: dict[str, Any] | None) -> str:
    """Anomaly panel. "No baseline" and "all clear" are visually distinct states."""
    if not analysis:
        return ""

    status = analysis.get("status")
    if status in ("no_data", "insufficient_history"):
        return (
            "<h2>Anomalies</h2>"
            f"<div class='anomaly-note pending'><strong>Not yet assessable.</strong> "
            f"{html.escape(str(analysis.get('message', '')))}</div>"
        )

    findings = analysis.get("findings", [])
    if not findings:
        return (
            "<h2>Anomalies</h2>"
            f"<div class='anomaly-note clear'><strong>None detected</strong> across "
            f"{analysis.get('snapshots_analysed')} snapshots "
            f"(baseline of {analysis.get('baseline_size')}).</div>"
        )

    rows = "".join(
        f"<li class='anomaly {html.escape(item['severity'])}'>"
        f"<span class='anomaly-sev'>{html.escape(item['severity'])}</span>"
        f"<span><strong>{html.escape(item['title'])}</strong>"
        f"<small>{html.escape(item['detail'])}</small></span>"
        f"<span class='anomaly-nums mono'>{html.escape(str(item['observed']))}"
        + (f" vs {html.escape(str(item['baseline']))}" if item["baseline"] is not None else "")
        + "</span></li>"
        for item in findings
    )
    return f"<h2>Anomalies</h2><ul class='anomaly-list'>{rows}</ul>"


def render_activity_html(snapshot: dict[str, Any]) -> str:
    """Fees, REV and address activity panel.

    The withheld daily-active figure gets its own visibly distinct card rather
    than being dropped: an omitted metric reads as an oversight, while a card
    that says "not derivable" and why reads as a decision.
    """
    activity = snapshot.get("activity", {})
    if not activity.get("available"):
        return ("<h2>Fees, REV and activity</h2>"
                "<p class='unavailable'>Block sampling unavailable in this snapshot.</p>")

    price = sol_price_usd(snapshot)
    window = activity.get("window", {})
    fees = activity.get("fees", {})
    rev = activity.get("rev", {})
    addresses = activity.get("addresses", {})
    split = activity.get("fee_split", {})

    parts = [
        "<h2>Fees, REV and activity <span class='keyless'>sampled from block bodies</span></h2>",
        f"<p class='unavailable'>{fmt(window.get('blocks_sampled'))} blocks evenly spaced across "
        f"{hours(window.get('observed_seconds'))} of chain history · "
        f"{fmt(fees.get('nonvote_transactions_sampled'))} non-vote transactions measured · "
        f"public JSON-RPC, no API key.{html.escape(truncation_note(window))}</p>",
    ]

    if rev.get("available"):
        sampled = rev.get("sampled_sol", {})
        per_block = rev.get("per_block_sol", {})
        parts.append("<div class='grid'>")
        low, high = rev.get("estimated_24h_sol_low"), rev.get("estimated_24h_sol_high")
        spread = (f" · 95% CI {fmt(low)}–{fmt(high)}"
                  if isinstance(low, (int, float)) and isinstance(high, (int, float)) else "")
        parts.append(card(
            "REV 24h (estimated)",
            fmt_usd(rev.get("estimated_24h_sol"), price),
            f"{fmt(rev.get('estimated_24h_sol'))} SOL{spread}",
        ))
        parts.append(card("Base fees (sampled)", f"{fmt(sampled.get('base'))} SOL", "protocol floor"))
        parts.append(card("Priority fees (sampled)", f"{fmt(sampled.get('priority'))} SOL", "congestion bids"))
        parts.append(card("Jito tips (sampled)", f"{fmt(sampled.get('jito_tips'))} SOL", "MEV, from balance deltas"))
        if split.get("available"):
            parts.append(card(
                "Fees burned", fmt(split.get("burned_pct"), "%"),
                f"{fmt(split.get('burned_sol'))} SOL of {fmt(split.get('blocks_reconciled'))} "
                "blocks — measured, not assumed",
            ))
        parts.append("</div>")
        parts.append(
            f"<p class='unavailable'>Daily REV is extrapolated from {fmt(window.get('blocks_sampled'))} "
            f"blocks, which individually ranged {fmt(per_block.get('min'))}–{fmt(per_block.get('max'))} SOL. "
            "Order of magnitude, not a settled total.</p>"
        )

    if fees.get("available"):
        parts.append("<h2>Transaction fee distribution</h2>")
        parts.append("<div class='grid'>")
        parts.append(card(
            "Median fee", fmt_usd(fees.get("median_sol"), price),
            f"{fmt_lamports(fees.get('median_lamports'))} · non-vote only",
        ))
        parts.append(card(
            "Mean fee", fmt_usd((fees.get("mean_lamports") or 0) / 1e9, price),
            f"{fmt_lamports(fees.get('mean_lamports'))} · skewed by the tail",
        ))
        parts.append(card(
            "90th percentile", fmt_usd((fees.get("p90_lamports") or 0) / 1e9, price),
            fmt_lamports(fees.get("p90_lamports")),
        ))
        parts.append(card(
            "99th percentile", fmt_usd((fees.get("p99_lamports") or 0) / 1e9, price),
            fmt_lamports(fees.get("p99_lamports")),
        ))
        parts.append(card(
            "Vote share of traffic", fmt(fees.get("vote_share_pct"), "%"),
            "excluded from the fees above",
        ))
        parts.append(card(
            "Non-vote failure rate", fmt(fees.get("failure_rate_pct"), "%"),
            "failed on-chain, fee still paid",
        ))
        parts.append("</div>")

    if addresses.get("available"):
        parts.append("<h2>Address activity</h2>")
        parts.append("<div class='grid'>")
        parts.append(card(
            "Unique fee payers", fmt(addresses.get("unique_fee_payers_sampled")),
            f"across {fmt(addresses.get('blocks_sampled'))} sampled blocks",
        ))
        parts.append(card(
            "Unique accounts touched", fmt(addresses.get("unique_accounts_sampled")),
            "non-vote transactions only",
        ))
        parts.append(card(
            "Fee payers per block", fmt(addresses.get("mean_fee_payers_per_block")), "mean",
        ))
        parts.append("</div>")
        parts.append(
            "<div class='anomaly-note pending' style='margin-top:12px'>"
            "<strong>Daily active addresses: not derivable here.</strong> "
            f"{html.escape(str(addresses.get('note', '')))}</div>"
        )

    return "".join(parts)


def render_html(
    snapshot: dict[str, Any],
    analysis: dict[str, Any] | None = None,
    comparison: dict[str, Any] | None = None,
    history: list[dict[str, Any]] | None = None,
) -> str:
    network = snapshot.get("network", {})
    epoch = snapshot.get("epoch", {})
    perf = snapshot.get("performance", {})
    supply = snapshot.get("supply", {})
    validators = snapshot.get("validators", {})

    healthy = network.get("healthy")
    parts: list[str] = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Solana Ecosystem Report</title>",
        f"<style>{CSS}</style></head><body><div class='wrap'>",
        "<header>",
        '<h1>Solana Ecosystem Report<span class="dot">.</span>',
        f'<span class="pill {"ok" if healthy else "bad"}">{"healthy" if healthy else "unhealthy"}</span></h1>',
        f'<div class="meta">Collected {html.escape(str(snapshot.get("collected_at", "—")))} · '
        f'source <code>{html.escape(str(snapshot.get("source", {}).get("endpoint", "—")))}</code> · '
        "no API key required</div>",
        "</header>",
        render_anomalies_html(analysis),
        render_delta_html(comparison),
    ]

    # Network + epoch
    parts.append("<h2>Network</h2><div class='grid'>")
    parts.append(card("Current slot", fmt(network.get("slot"))))
    parts.append(card("Block height", fmt(epoch.get("block_height"))))
    parts.append(card("Total transactions", fmt(epoch.get("transaction_count"))))
    if epoch.get("available") and epoch.get("progress_pct") is not None:
        progress = epoch["progress_pct"]
        parts.append(card(
            f"Epoch {fmt_id(epoch.get('epoch'))}", f"{fmt(progress, '%')}",
            f"slot {fmt(epoch.get('slot_index'))} of {fmt(epoch.get('slots_in_epoch'))}",
            f'<div class="bar"><i style="width:{max(0, min(100, progress))}%"></i></div>',
        ))
    parts.append("</div>")

    # Performance
    parts.append("<h2>Performance</h2>")
    if perf.get("available"):
        samples = perf.get("samples", [])
        peak = max((s.get("tps", 0) for s in samples), default=0) or 1
        bars = "".join(
            f'<i style="height:{max(4, round(100 * s.get("tps", 0) / peak))}%"></i>'
            for s in reversed(samples)
        )
        parts.append("<div class='grid'>")
        parts.append(card(
            "Latest TPS", fmt(perf.get("latest_tps")),
            f"peak {fmt(perf.get('peak_tps'))} across {perf.get('samples_used')} samples",
            f'<div class="sparkline">{bars}</div>',
        ))
        parts.append(card("Mean TPS", fmt(perf.get("mean_tps")), "recent samples"))
        parts.append(card("Mean slot time", fmt(perf.get("mean_slot_time_secs"), "s"), "target is 0.4s"))
        parts.append("</div>")
    else:
        parts.append("<p class='unavailable'>Performance samples unavailable in this snapshot.</p>")

    # Trends across the committed snapshot history. Inline SVG, no script and
    # no external asset, so the page still draws itself offline from file://.
    if history:
        parts.append(charts_module.render_charts_html(history))

    parts.append(render_activity_html(snapshot))

    # Economic indicators — third-party sources, each degrading independently.
    economics = snapshot.get("economics", {})
    parts.append("<h2>Economic indicators <span class='keyless'>keyless sources</span></h2>")
    if economics.get("available"):
        price = economics.get("price", {})
        tvl = economics.get("tvl", {})
        stables = economics.get("stablecoins", {})
        dex = economics.get("dex", {})

        def money(section: dict[str, Any], key: str) -> str:
            # An unavailable source must read "—", never "$0".
            if not section.get("available") or not isinstance(section.get(key), (int, float)):
                return "—"
            return "$" + fmt_sol(section[key]).replace(" SOL", "")

        def delta(section: dict[str, Any], key: str, label: str) -> str:
            value = section.get(key)
            if not section.get("available") or not isinstance(value, (int, float)):
                return "source unavailable"
            return f"{value:+.2f}% {label}"

        parts.append("<div class='grid'>")
        parts.append(card(
            "SOL price",
            f"${price['price_usd']:,.2f}" if price.get("available") else "—",
            delta(price, "change_24h_pct", "24h"),
        ))
        parts.append(card("Market cap", money(price, "market_cap_usd"), "CoinGecko"))
        parts.append(card("Total value locked", money(tvl, "tvl_usd"),
                          delta(tvl, "change_7d_pct", "7d")))
        parts.append(card("Stablecoin supply", money(stables, "stablecoin_usd"), "DeFiLlama"))
        parts.append(card("DEX volume 24h", money(dex, "volume_24h_usd"),
                          delta(dex, "change_1d_pct", "1d")))
        parts.append(card("Spot volume 24h", money(price, "volume_24h_usd"), "CoinGecko"))
        parts.append("</div>")

        unavailable = sorted(
            n for n, s in economics.get("sources", {}).items() if not s.get("available")
        )
        if unavailable:
            parts.append(
                f"<p class='unavailable'>Unavailable this run: "
                f"{html.escape(', '.join(unavailable))}. Other sources are unaffected.</p>"
            )
    else:
        parts.append("<p class='unavailable'>Economic sources unavailable in this snapshot.</p>")

    # Supply
    parts.append("<h2>Supply</h2>")
    if supply.get("available"):
        parts.append("<div class='grid'>")
        parts.append(card(
            "Total supply", fmt_sol(supply.get("total_sol")),
            f"{fmt(supply.get('total_sol'))} exactly",
        ))
        parts.append(card(
            "Circulating", fmt_sol(supply.get("circulating_sol")),
            f"{fmt(supply.get('circulating_pct'), '%')} of total",
        ))
        parts.append(card("Non-circulating", fmt_sol(supply.get("non_circulating_sol"))))
        parts.append("</div>")
    else:
        parts.append("<p class='unavailable'>Supply data unavailable in this snapshot.</p>")

    # Validators
    parts.append("<h2>Validators</h2>")
    if validators.get("available"):
        delinquent_pct = validators.get("delinquent_pct") or 0
        parts.append("<div class='grid'>")
        parts.append(card("Active", fmt(validators.get("active_count"))))
        parts.append(card(
            "Delinquent", fmt(validators.get("delinquent_count")),
            f"{fmt(delinquent_pct, '%')} of all validators",
        ))
        parts.append(card(
            "Active stake", fmt_sol(validators.get("active_stake_sol")),
            f"{fmt(validators.get('active_stake_sol'))} exactly",
        ))
        parts.append(card(
            "Nakamoto coefficient", fmt(validators.get("nakamoto_coefficient")),
            "validators to reach 33% of stake",
        ))
        commission = validators.get("commission", {})
        if commission.get("available"):
            parts.append(card(
                "Median commission", f"{fmt(commission.get('median_pct'))}%",
                f"mean {fmt(commission.get('mean_pct'))}% · "
                f"{fmt(commission.get('zero_commission_count'))} at 0%",
            ))
        parts.append("</div>")

        parts.append("<h2>Top validators by stake</h2><table><thead><tr>")
        parts.append("<th>#</th><th>Identity</th><th>Stake (SOL)</th><th>Share</th><th>Commission</th>")
        parts.append("</tr></thead><tbody>")
        for rank, validator in enumerate(validators.get("top_validators", []), start=1):
            parts.append(
                f"<tr><td>{rank}</td>"
                f"<td class='mono'>{html.escape(str(validator.get('identity', '—')))}</td>"
                f"<td>{fmt(validator.get('stake_sol'))}</td>"
                f"<td>{fmt(validator.get('share_pct'), '%')}</td>"
                f"<td>{fmt(validator.get('commission'), '%')}</td></tr>"
            )
        parts.append("</tbody></table>")
    else:
        parts.append("<p class='unavailable'>Validator data unavailable in this snapshot.</p>")

    parts.append(render_news_html(snapshot))

    roadmap = upgrades_data.upgrade_section()
    if roadmap["available"]:
        parts.append(
            "<h2>Upcoming upgrades <span class='static-badge'>static reference · "
            f"checked {html.escape(roadmap['last_verified'])}</span></h2>"
        )
        parts.append("<ul class='upgrade-list'>")
        for item in roadmap["upgrades"]:
            parts.append(
                f"<li><div><strong>{html.escape(item['name'])}</strong>"
                f"<span class='upgrade-status'>{html.escape(item['status'])}</span></div>"
                f"<small>{html.escape(item['summary'])}</small>"
                f"<small class='upgrade-why'>{html.escape(item['why_it_matters'])}</small>"
                f"<a href='{html.escape(item['source'])}' target='_blank' rel='noreferrer'>"
                f"{html.escape(item['identifier'])} source</a></li>"
            )
        parts.append("</ul>")
        parts.append(f"<p class='unavailable'>{html.escape(roadmap['note'])}</p>")

    parts.append(
        "<footer>Generated by <span class='mono'>render.py</span> from a single JSON snapshot. "
        "Python standard library only — no third-party packages, no API keys, no build step. "
        "On-chain data from public Solana JSON-RPC; economic data from public keyless endpoints."
        "</footer></div></body></html>"
    )
    return "\n".join(parts)


# ── entry point ──────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Render a Solana snapshot to Markdown and HTML.")
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT_DIR / "latest.json")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    if not args.snapshot.exists():
        print(f"snapshot not found: {args.snapshot}\nRun collect.py first.")
        return 1

    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    args.out_dir.mkdir(parents=True, exist_ok=True)

    # Anomaly detection reads the accumulated history — no network, no new
    # source — restricted to what existed when this snapshot was collected.
    history = detect.load_history(args.snapshot.parent)
    analysis = analysis_for(snapshot, history)
    # Deterministic comparison against the previous snapshot, from the same
    # committed history. Also no network, and also cut off at this snapshot.
    comparison = delta_module.delta_for(snapshot, history)

    markdown_path = args.out_dir / "report.md"
    html_path = args.out_dir / "index.html"
    json_path = args.out_dir / "report.json"

    # Charts read the same committed history, cut off at this snapshot: a page
    # about one moment must not plot points collected after it.
    cutoff = snapshot.get("collected_at")
    charted = ([s for s in history if s.get("collected_at", "") <= cutoff]
               if isinstance(cutoff, str) and cutoff else history)

    markdown_path.write_text(
        render_markdown(snapshot, analysis, comparison, charted), encoding="utf-8")
    html_path.write_text(
        render_html(snapshot, analysis, comparison, charted), encoding="utf-8")
    json_path.write_text(
        json.dumps({**snapshot, "anomalies": analysis, "delta": comparison,
                    "history": charts_module.history_json(charted)}, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"wrote {markdown_path}\nwrote {html_path}\nwrote {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
