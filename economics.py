#!/usr/bin/env python3
"""Optional economic indicators from provider APIs with source-specific rights.

Standard library only. None of these endpoints requires an API key, an
account, or a header beyond the default — verified by direct unauthenticated
request against each one.

    DeFiLlama   TVL, 7-day trend        api.llama.fi/v2/historicalChainTvl/Solana
    DeFiLlama   USD-pegged circulating supply
                                            stablecoins.llama.fi/stablecoinchains
    DeFiLlama   DEX volume              api.llama.fi/overview/dexs/solana
    CoinGecko   price, mcap, 24h vol    api.coingecko.com/api/v3/simple/price

Same architecture as the RPC collector: network access is confined to `fetch`,
every transform is a pure function, and each source degrades independently.
These are third-party services that can rate-limit or fail, so one outage must
never blank the others — and a failed source reports `available: false`, never
a zero. A dashboard printing "$0 TVL" during a DeFiLlama outage is worse than
one saying "unavailable".
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import facts
import transport

SOURCES = {
    "tvl": "https://api.llama.fi/v2/historicalChainTvl/Solana",
    "price": (
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=solana&vs_currencies=usd"
        "&include_market_cap=true&include_24hr_vol=true&include_24hr_change=true"
        "&include_last_updated_at=true"
    ),
    "stablecoins": "https://stablecoins.llama.fi/stablecoinchains",
    "dex": (
        "https://api.llama.fi/overview/dexs/solana"
        "?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true"
    ),
    "protocols": "https://api.llama.fi/protocols",
}

SECONDS_PER_DAY = 86_400


def is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


# ── network boundary ─────────────────────────────────────────────────────────

def fetch(url: str, timeout: int = 20) -> Any | None:
    """GET and decode JSON. Returns None on any failure — never raises."""
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "solana-ecosystem-report/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(transport.read_bounded(response).decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            json.JSONDecodeError, ValueError):
        return None


def fetch_all(timeout: int = 12) -> dict[str, Any | None]:
    """Fetch every source independently so one failure cannot take out the rest."""
    with ThreadPoolExecutor(max_workers=len(SOURCES)) as executor:
        futures = {name: executor.submit(fetch, url, timeout) for name, url in SOURCES.items()}
        return {name: future.result() for name, future in futures.items()}


# ── pure transforms ──────────────────────────────────────────────────────────

def summarize_tvl(raw: Any) -> dict[str, Any]:
    """Latest TVL plus a 7-day trend from the historical series."""
    if not isinstance(raw, list) or not raw:
        return {"available": False}

    points = [
        p for p in raw
        if isinstance(p, dict)
        and is_finite_number(p.get("tvl"))
        and isinstance(p.get("date"), int)
    ]
    if not points:
        return {"available": False}

    points.sort(key=lambda p: p["date"])
    latest = points[-1]

    # Nearest point at least 7 days older, if the series reaches back that far.
    target = latest["date"] - 7 * SECONDS_PER_DAY
    prior = [p for p in points if p["date"] <= target]
    change_7d = None
    if prior and prior[-1]["tvl"]:
        change_7d = round(100 * (latest["tvl"] - prior[-1]["tvl"]) / prior[-1]["tvl"], 2)

    return {
        "available": True,
        "tvl_usd": round(float(latest["tvl"]), 2),
        "as_of_unix": latest["date"],
        "change_7d_pct": change_7d,
        "history_points": len(points),
    }


def tvl_history_facts(raw: Any, collected_at: str) -> list[dict[str, Any]]:
    """Retain every valid source-native TVL point without inventing dates."""
    if not isinstance(raw, list):
        return []
    observations = []
    for point in raw:
        if (not isinstance(point, dict) or not isinstance(point.get("date"), int)
                or isinstance(point.get("date"), bool)
                or not is_finite_number(point.get("tvl")) or point["tvl"] < 0):
            continue
        observations.append({
            "metric_id": "tvl_usd",
            "subject_id": "solana",
            "event_time": datetime.fromtimestamp(
                point["date"], timezone.utc,
            ).isoformat(timespec="seconds"),
            "event_slot": None,
            "collected_at": collected_at,
            "value": float(point["tvl"]),
            "unit": "USD",
            "basis": "recorded",
            "state": "current",
            "source": "defillama-historical-chain-tvl-solana",
            "source_revision": None,
            "source_schema": "historicalChainTvl-v1",
            "coverage": None,
            "quality": "provider-reported Solana TVL; redistribution approval pending",
        })
    return facts.dedupe_facts(observations)


def summarize_price(raw: Any, observed_at_unix: int | None = None,
                    stale_after_seconds: int = 900) -> dict[str, Any]:
    solana = raw.get("solana") if isinstance(raw, dict) else None
    if not isinstance(solana, dict) or not is_finite_number(solana.get("usd")):
        return {"available": False, "freshness": "unavailable"}
    last_updated = solana.get("last_updated_at")
    last_updated = last_updated if isinstance(last_updated, int) and last_updated > 0 else None
    if last_updated is None:
        freshness = "missing"
    elif observed_at_unix is None:
        freshness = "recorded"
    else:
        freshness = "fresh" if observed_at_unix - last_updated <= stale_after_seconds else "stale"
    return {
        "available": True,
        "price_usd": round(float(solana["usd"]), 4),
        "market_cap_usd": round(float(solana["usd_market_cap"]), 2)
        if is_finite_number(solana.get("usd_market_cap")) else None,
        "volume_24h_usd": round(float(solana["usd_24h_vol"]), 2)
        if is_finite_number(solana.get("usd_24h_vol")) else None,
        "change_24h_pct": round(float(solana["usd_24h_change"]), 2)
        if is_finite_number(solana.get("usd_24h_change")) else None,
        "last_updated_at_unix": last_updated,
        "freshness": freshness,
    }


def summarize_stablecoins(raw: Any) -> dict[str, Any]:
    """Solana USD-pegged circulating supply. The API returns every chain."""
    if not isinstance(raw, list):
        return {"available": False}

    row = next(
        (r for r in raw
         if isinstance(r, dict) and (r.get("gecko_id") == "solana" or r.get("name") == "Solana")),
        None,
    )
    circulating = row.get("totalCirculatingUSD") if isinstance(row, dict) else None
    if not isinstance(circulating, dict):
        return {"available": False}

    pegged_usd = circulating.get("peggedUSD")
    if not is_finite_number(pegged_usd):
        return {"available": False}

    return {
        "available": True,
        "metric": "USD-pegged circulating supply",
        "provider_field": "totalCirculatingUSD.peggedUSD",
        "scope": (
            "Provider-reported circulating supply of USD-pegged assets on Solana; "
            "not all stablecoins, liquidity, or executable depth."
        ),
        "usd_pegged_circulating_usd": round(float(pegged_usd), 2),
        # Non-USD pegs are real but tiny here; summing them separately keeps
        # the headline figure honest about what it actually measures.
        "non_usd_pegged_usd": round(
            sum(v for k, v in circulating.items()
                if k != "peggedUSD" and is_finite_number(v)), 2,
        ),
    }


def summarize_dex(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not is_finite_number(raw.get("total24h")):
        return {"available": False}
    return {
        "available": True,
        # A successful response proves only that this provider request
        # completed. It does not prove complete economic market coverage.
        "transport_complete": True,
        "provider_scope": "DeFiLlama-indexed Solana DEX aggregate",
        "market_coverage": {
            "complete": False,
            "exclusions": ["CEX", "RFQ", "unindexed venues"],
            "note": "Provider coverage is not total Solana trading volume.",
        },
        "volume_24h_usd": round(float(raw["total24h"]), 2),
        "volume_7d_usd": round(float(raw["total7d"]), 2)
        if is_finite_number(raw.get("total7d")) else None,
        "change_1d_pct": round(float(raw["change_1d"]), 2)
        if is_finite_number(raw.get("change_1d")) else None,
    }


def summarize_protocols(raw: Any, limit: int = 10) -> dict[str, Any]:
    """Rank non-custodial protocols by their Solana-specific TVL field."""
    if not isinstance(raw, list) or not raw:
        return {"available": False}
    candidates = []
    for item in raw:
        if not isinstance(item, dict) or item.get("category") == "CEX":
            continue
        chain_tvls = item.get("chainTvls")
        solana_tvl = chain_tvls.get("Solana") if isinstance(chain_tvls, dict) else None
        if not is_finite_number(solana_tvl) or solana_tvl <= 0:
            continue
        candidates.append((item, solana_tvl))
    parent_ids = {
        item["parentProtocol"] for item, _ in candidates
        if isinstance(item.get("parentProtocol"), str) and item["parentProtocol"]
    }
    present_parent_ids = {
        item["id"] for item, _ in candidates
        if isinstance(item.get("id"), str) and item["id"] in parent_ids
    }
    rows = []
    excluded_children = 0
    for item, solana_tvl in candidates:
        parent_id = item.get("parentProtocol")
        if parent_id in present_parent_ids:
            excluded_children += 1
            continue
        protocol_id = item.get("id")
        is_parent_aggregate = protocol_id in present_parent_ids
        rows.append({
            "id": protocol_id,
            "name": item.get("name") if isinstance(item.get("name"), str) else "Unnamed protocol",
            "slug": item.get("slug") if isinstance(item.get("slug"), str) else None,
            "category": item.get("category") if isinstance(item.get("category"), str) else "Uncategorized",
            "solana_tvl_usd": round(float(solana_tvl), 2),
            "provider_protocol_id": protocol_id,
            "provider_family_id": protocol_id if is_parent_aggregate else parent_id or protocol_id,
            "ranking_basis": (
                "provider_parent_aggregate" if is_parent_aggregate
                else "provider_child" if parent_id
                else "provider_standalone"
            ),
        })
    if not rows:
        return {"available": False}
    rows.sort(key=lambda row: (-row["solana_tvl_usd"], str(row["name"]), str(row["id"] or "")))
    return {
        "available": True,
        "scope": "non-CEX protocols with reported Solana chain TVL",
        "excluded_categories": ["CEX"],
        "protocols": rows[:limit],
        "eligible_protocol_count": len(rows),
        "excluded_child_protocol_count": excluded_children,
    }


def build_economics(raw: dict[str, Any | None], observed_at_unix: int | None = None) -> dict[str, Any]:
    """Assemble the economics section. Pure — no network, no clock."""
    tvl = summarize_tvl(raw.get("tvl"))
    price = summarize_price(raw.get("price"), observed_at_unix)
    stablecoins = summarize_stablecoins(raw.get("stablecoins"))
    dex = summarize_dex(raw.get("dex"))
    protocols = summarize_protocols(raw.get("protocols"))

    parts = {
        "tvl": tvl, "price": price, "stablecoins": stablecoins,
        "dex": dex, "protocols": protocols,
    }
    sources = {
        name: {"available": part["available"], "url": SOURCES[name]}
        for name, part in parts.items()
    }
    sources["price"].update({
        "freshness": price.get("freshness"),
        "last_updated_at_unix": price.get("last_updated_at_unix"),
    })
    return {
        # True if anything came back — the section renders whatever succeeded.
        "available": any(p["available"] for p in parts.values()),
        "sources": sources,
        "requires_api_key": False,
        **parts,
    }


def collect_economics(timeout: int = 12) -> dict[str, Any]:
    return build_economics(fetch_all(timeout), int(time.time()))


if __name__ == "__main__":
    print(json.dumps(collect_economics(), indent=2))
