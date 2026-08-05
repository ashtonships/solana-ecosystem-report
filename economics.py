#!/usr/bin/env python3
"""Economic indicators from keyless public APIs.

Standard library only. None of these endpoints requires an API key, an
account, or a header beyond the default — verified by direct unauthenticated
request against each one.

    DeFiLlama   TVL, 7-day trend        api.llama.fi/v2/historicalChainTvl/Solana
    DeFiLlama   stablecoin supply       stablecoins.llama.fi/stablecoinchains
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
import urllib.error
import urllib.request
from typing import Any

SOURCES = {
    "tvl": "https://api.llama.fi/v2/historicalChainTvl/Solana",
    "price": (
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=solana&vs_currencies=usd"
        "&include_market_cap=true&include_24hr_vol=true&include_24hr_change=true"
    ),
    "stablecoins": "https://stablecoins.llama.fi/stablecoinchains",
    "dex": (
        "https://api.llama.fi/overview/dexs/solana"
        "?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true"
    ),
}

SECONDS_PER_DAY = 86_400


# ── network boundary ─────────────────────────────────────────────────────────

def fetch(url: str, timeout: int = 20) -> Any | None:
    """GET and decode JSON. Returns None on any failure — never raises."""
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "solana-ecosystem-report/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            json.JSONDecodeError, ValueError):
        return None


def fetch_all(timeout: int = 20) -> dict[str, Any | None]:
    """Fetch every source independently so one failure cannot take out the rest."""
    return {name: fetch(url, timeout) for name, url in SOURCES.items()}


# ── pure transforms ──────────────────────────────────────────────────────────

def summarize_tvl(raw: Any) -> dict[str, Any]:
    """Latest TVL plus a 7-day trend from the historical series."""
    if not isinstance(raw, list) or not raw:
        return {"available": False}

    points = [
        p for p in raw
        if isinstance(p, dict)
        and isinstance(p.get("tvl"), (int, float))
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


def summarize_price(raw: Any) -> dict[str, Any]:
    solana = raw.get("solana") if isinstance(raw, dict) else None
    if not isinstance(solana, dict) or not isinstance(solana.get("usd"), (int, float)):
        return {"available": False}
    return {
        "available": True,
        "price_usd": round(float(solana["usd"]), 4),
        "market_cap_usd": round(float(solana["usd_market_cap"]), 2)
        if isinstance(solana.get("usd_market_cap"), (int, float)) else None,
        "volume_24h_usd": round(float(solana["usd_24h_vol"]), 2)
        if isinstance(solana.get("usd_24h_vol"), (int, float)) else None,
        "change_24h_pct": round(float(solana["usd_24h_change"]), 2)
        if isinstance(solana.get("usd_24h_change"), (int, float)) else None,
    }


def summarize_stablecoins(raw: Any) -> dict[str, Any]:
    """Solana's stablecoin float. The API returns every chain; find Solana."""
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
    if not isinstance(pegged_usd, (int, float)):
        return {"available": False}

    return {
        "available": True,
        "stablecoin_usd": round(float(pegged_usd), 2),
        # Non-USD pegs are real but tiny here; summing them separately keeps
        # the headline figure honest about what it actually measures.
        "non_usd_pegged_usd": round(
            sum(v for k, v in circulating.items()
                if k != "peggedUSD" and isinstance(v, (int, float))), 2,
        ),
    }


def summarize_dex(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("total24h"), (int, float)):
        return {"available": False}
    return {
        "available": True,
        "volume_24h_usd": round(float(raw["total24h"]), 2),
        "volume_7d_usd": round(float(raw["total7d"]), 2)
        if isinstance(raw.get("total7d"), (int, float)) else None,
        "change_1d_pct": round(float(raw["change_1d"]), 2)
        if isinstance(raw.get("change_1d"), (int, float)) else None,
    }


def build_economics(raw: dict[str, Any | None]) -> dict[str, Any]:
    """Assemble the economics section. Pure — no network, no clock."""
    tvl = summarize_tvl(raw.get("tvl"))
    price = summarize_price(raw.get("price"))
    stablecoins = summarize_stablecoins(raw.get("stablecoins"))
    dex = summarize_dex(raw.get("dex"))

    parts = {"tvl": tvl, "price": price, "stablecoins": stablecoins, "dex": dex}
    return {
        # True if anything came back — the section renders whatever succeeded.
        "available": any(p["available"] for p in parts.values()),
        "sources": {
            name: {"available": part["available"], "url": SOURCES[name]}
            for name, part in parts.items()
        },
        "requires_api_key": False,
        **parts,
    }


def collect_economics(timeout: int = 20) -> dict[str, Any]:
    return build_economics(fetch_all(timeout))


if __name__ == "__main__":
    print(json.dumps(collect_economics(), indent=2))
