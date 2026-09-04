#!/usr/bin/env python3
"""Tokenized-equity registry, resumable on-chain supply, and market context."""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import blocks
import transport

REGISTRY_URL = "https://xstocks.fi/products"
REGISTRY_API_URL = "https://api.xstocks.fi/api/v2/public/assets"
PROOF_OF_RESERVES_URL = "https://api.xstocks.fi/api/v2/public/proof-of-reserves"
TOKEN_REGISTRY_SOURCE_REVISION = "661a6f0ca466ccf74ea967dae7e3abbcdc088bc0"
TOKEN_REGISTRY_SOURCE_PATH = "packages/asset-registry/src/data/xstock-variant-groups.ts"
TOKEN_REGISTRY_SOURCE_URL = (
    "https://raw.githubusercontent.com/solana-foundation/tokens/"
    f"{TOKEN_REGISTRY_SOURCE_REVISION}/{TOKEN_REGISTRY_SOURCE_PATH}"
)
TOKEN_REGISTRY_SOURCE_KEY = "solana-foundation/tokens:xstock-variant-groups"
TOKEN_REGISTRY_SOURCE_LICENSE = "MIT"
TOKEN_REGISTRY_EXPECTED_XSTOCKS = 107
TOKEN_2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
SELECTED_STABLECOIN_SOURCE_REVISION = "46091c373d7681a469e4130155187503def93387"
SELECTED_STABLECOIN_SOURCE_PATH = (
    "apps/docs/content/docs/en/payments/production-readiness.mdx"
)
SELECTED_STABLECOIN_SOURCE_URL = (
    "https://github.com/solana-foundation/solana-com/blob/"
    f"{SELECTED_STABLECOIN_SOURCE_REVISION}/{SELECTED_STABLECOIN_SOURCE_PATH}#L477-L480"
)
SELECTED_STABLECOIN_SOURCE_KEY = "solana-foundation/solana-com:selected-usd-stablecoins"
SELECTED_STABLECOIN_SOURCE_LICENSE = "GPL-3.0"
SELECTED_USD_STABLECOINS = (
    {"symbol": "USDC", "issuer": "Circle",
     "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"},
    {"symbol": "USDT", "issuer": "Tether",
     "mint": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"},
    {"symbol": "PYUSD", "issuer": "PayPal USD issued by Paxos",
     "mint": "2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo"},
    {"symbol": "USDG", "issuer": "Paxos Digital Singapore",
     "mint": "2u1tszSeqZ3qBWF3uNGPFc8TzMk2tdiwknnRMWGWjGWH"},
)
SOLANA_DATA_URL = "https://solana.com/api/databricks/data?days=365"
DEXSCREENER_BASE_URL = "https://api.dexscreener.com/tokens/v1/solana/"
DEXSCREENER_PUBLICATION_HOLD_REASON = (
    "DEX Screener collection is disabled because the current API terms do not "
    "establish permission for automated public redistribution of derived aggregates."
)
USER_AGENT = "solana-ecosystem-report/0.1"
REGISTRY_PAGE_SIZE = 100
DEXSCREENER_BATCH_SIZE = 30
DISPLAY_ASSET_LIMIT = 12
SUPPLY_STATE_VERSION = 2
SUPPLY_FRESH_SECONDS = 6 * 60 * 60
SUPPLY_SWEEP_MAX_SECONDS = 72 * 60 * 60
SUPPLY_STATE_PATH = Path(__file__).parent / "state" / "xstocks-supply.json"
PUBLIC_RPC_ENDPOINTS = frozenset({
    "https://api.mainnet.solana.com",
    "https://api.mainnet-beta.solana.com",
})
CUSTOM_RPC_ENDPOINT_LABEL = "custom RPC endpoint"
PROVIDER_BENCHMARK_SEMANTICS = {
    "Active Addresses": {
        "semantic_metric_id": "stablecoin_active_address_provider_range",
        "display_name": "Stablecoin active-address provider range",
        "source_label": "Active Addresses",
        "scope": "provider observations for Solana stablecoin activity, not network-wide DAA or unique humans",
    },
    "Fee Payers": {
        "semantic_metric_id": "transaction_initiator_provider_range",
        "display_name": "Transaction-initiator provider range",
        "source_label": "Fee Payers",
        "scope": "provider observations of transaction initiators, not unique humans",
    },
}


class _NextDataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.capture = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "script" and attributes.get("id") == "__NEXT_DATA__":
            self.capture = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.capture:
            self.capture = False

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.parts.append(data)


def parse_xstocks_registry(page: Any) -> list[dict[str, str]]:
    """Extract the issuer's Solana product registry from its stable page URL."""
    if not isinstance(page, str) or not page:
        return []
    parser = _NextDataParser()
    try:
        parser.feed(page)
        payload = json.loads("".join(parser.parts))
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    products = payload.get("props", {}).get("pageProps", {}).get("products")
    if not isinstance(products, list):
        return []
    normalized = []
    for product in products:
        if not isinstance(product, dict):
            continue
        addresses = product.get("addresses")
        mint = addresses.get("solana") if isinstance(addresses, dict) else None
        if not isinstance(mint, str) or not mint.strip():
            continue
        name = product.get("name")
        symbol = product.get("symbol")
        slug = product.get("slug")
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(symbol, str) or not symbol.strip():
            continue
        if not isinstance(slug, str) or not slug.strip():
            continue
        normalized.append({
            "slug": slug.strip(), "name": name.strip(), "symbol": symbol.strip(),
            "solana_mint": mint.strip(),
        })
    normalized.sort(key=lambda item: (item["symbol"], item["solana_mint"]))
    return normalized


def parse_xstocks_api_assets(payload: Any) -> list[dict[str, Any]]:
    """Normalize the issuer's documented public asset API to Solana deployments."""
    nodes = payload.get("nodes") if isinstance(payload, dict) else None
    if not isinstance(nodes, list):
        return []
    normalized = []
    for asset in nodes:
        if not isinstance(asset, dict):
            continue
        deployments = asset.get("deployments")
        solana = next((
            item for item in deployments
            if isinstance(item, dict) and item.get("network") == "Solana"
            and isinstance(item.get("address"), str) and item["address"].strip()
        ), None) if isinstance(deployments, list) else None
        name = asset.get("name")
        symbol = asset.get("symbol")
        identifier = asset.get("id")
        if solana is None or not all(isinstance(value, str) and value.strip()
                                     for value in (name, symbol, identifier)):
            continue
        normalized.append({
            "slug": identifier.strip(),
            "name": name.strip(),
            "symbol": symbol.strip(),
            "solana_mint": solana["address"].strip(),
            "trading_halted": asset.get("isTradingHalted") is True,
            "supports_atomic_swaps": solana.get("supportsAtomicSwaps") is True,
        })
    normalized.sort(key=lambda item: (item["symbol"], item["solana_mint"]))
    return normalized


def parse_token_registry_xstocks(source: Any) -> list[dict[str, Any]]:
    """Select the exact xStock variants from the pinned official registry."""
    if not isinstance(source, str):
        return []
    groups = re.findall(
        r"\{\s*id:\s*'([^']+)',\s*label:\s*(?:'([^']*)'|\"([^\"]*)\"),"
        r"\s*addresses:\s*\[(.*?)\],\s*\}",
        source,
        re.DOTALL,
    )
    products = []
    for group_id, single_label, double_label, addresses in groups:
        selected = [
            address for address, label in re.findall(
                r"\{\s*address:\s*'([^']+)',\s*label:\s*'([^']+)'\s*\}",
                addresses,
            )
            if label == "xStock"
        ]
        if not selected:
            continue
        label = single_label or double_label
        if (len(selected) != 1 or not group_id.startswith("xstock-")
                or not label.endswith(" variants")
                or re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{43}", selected[0]) is None):
            return []
        products.append({
            "slug": group_id,
            "name": label.removesuffix(" variants"),
            "symbol": None,
            "solana_mint": selected[0],
            "variant_group_id": group_id,
            "variant_label": "xStock",
        })
    expected = TOKEN_REGISTRY_EXPECTED_XSTOCKS
    if (len(re.findall(r"label:\s*'xStock'", source)) != expected
            or len(products) != expected
            or len({item["variant_group_id"] for item in products}) != expected
            or len({item["solana_mint"] for item in products}) != expected):
        return []
    products.sort(key=lambda item: item["variant_group_id"])
    return products


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _explicit_nonnegative_number(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = _number(value)
    return number if number is not None and number >= 0 else None


def summarize_dex_volume(raw: Any, registry_mints: set[str]) -> dict[str, Any]:
    """Aggregate DEX Screener pools once each; never call this total market volume."""
    rows = raw.get("rows") if isinstance(raw, dict) else None
    rows = rows if isinstance(rows, list) else []
    pairs: dict[str, dict[str, Any]] = {}
    seen_evidence: dict[str, tuple[Any, ...]] = {}
    conflicts: set[str] = set()
    exact_duplicates = invalid_rows = unrelated_rows = 0
    for row in rows:
        if not isinstance(row, dict):
            invalid_rows += 1
            continue
        address = row.get("pairAddress")
        if not isinstance(address, str) or not address:
            invalid_rows += 1
            continue
        base = row.get("baseToken")
        quote = row.get("quoteToken")
        base_address = base.get("address") if isinstance(base, dict) else None
        quote_address = quote.get("address") if isinstance(quote, dict) else None
        base_mint = base_address if isinstance(base_address, str) and base_address else None
        quote_mint = quote_address if isinstance(quote_address, str) and quote_address else None
        related = base_mint in registry_mints or quote_mint in registry_mints
        if not related:
            invalid_rows += 1
            if isinstance(base_mint, str) or isinstance(quote_mint, str):
                unrelated_rows += 1
        volume = row.get("volume")
        liquidity = row.get("liquidity")
        evidence = (
            row.get("dexId"), base_mint, quote_mint,
            volume.get("h24") if isinstance(volume, dict) else None,
            liquidity.get("usd") if isinstance(liquidity, dict) else None,
        )
        if address in seen_evidence:
            if seen_evidence[address] == evidence:
                exact_duplicates += 1
            else:
                conflicts.add(address)
            continue
        seen_evidence[address] = evidence
        if related:
            pairs[address] = row

    retained = {
        address: row for address, row in pairs.items() if address not in conflicts
    }
    covered = {
        mint
        for row in retained.values()
        for side in ("baseToken", "quoteToken")
        if isinstance((token := row.get(side)), dict)
        and isinstance((mint := token.get("address")), str)
        and mint in registry_mints
    }
    volume_values = []
    liquidity_values = []
    for row in retained.values():
        volume = row.get("volume")
        liquidity = row.get("liquidity")
        volume_value = _explicit_nonnegative_number(
            volume.get("h24") if isinstance(volume, dict) else None,
        )
        liquidity_value = _explicit_nonnegative_number(
            liquidity.get("usd") if isinstance(liquidity, dict) else None,
        )
        if volume_value is not None:
            volume_values.append(volume_value)
        if liquidity_value is not None:
            liquidity_values.append(liquidity_value)

    batches_requested = raw.get("batches_requested") if isinstance(raw, dict) else None
    batches_succeeded = raw.get("batches_succeeded") if isinstance(raw, dict) else None
    batches_expected = raw.get("batches_expected") if isinstance(raw, dict) else None
    expected_known = (
        isinstance(batches_expected, int) and not isinstance(batches_expected, bool)
        and batches_expected > 0
    )
    transport_complete = (
        expected_known and batches_requested == batches_expected
        and batches_succeeded == batches_expected
    )
    market_coverage = "partial" if transport_complete else "not established"
    exclusions = ["RFQ fills", "centralized venues", "unindexed or unsupported pools"]
    pair_count = len(retained)
    volume_count = len(volume_values)
    liquidity_count = len(liquidity_values)
    result = {
        "available": False,
        "partial": bool(
            not transport_complete or conflicts or invalid_rows
            or volume_count != pair_count or liquidity_count != pair_count
            or market_coverage != "complete"
        ),
        "scope": "Solana DEX pools indexed by DEX Screener",
        "source_row_count": len(rows),
        "pair_count": pair_count,
        "exact_duplicate_row_count": exact_duplicates,
        "conflicting_pair_count": len(conflicts),
        "invalid_row_count": invalid_rows,
        "unrelated_row_count": unrelated_rows,
        "volume_covered_pair_count": volume_count,
        "volume_invalid_pair_count": pair_count - volume_count,
        "liquidity_covered_pair_count": liquidity_count,
        "liquidity_invalid_pair_count": pair_count - liquidity_count,
        "pairs_with_volume": volume_count,
        "assets_with_pairs": len(covered),
        "registry_asset_count": len(registry_mints),
        "batches_expected": batches_expected,
        "batches_requested": batches_requested,
        "batches_succeeded": batches_succeeded,
        "transport_complete": transport_complete,
        "market_coverage": market_coverage,
        "exclusions": exclusions,
        "source_url": DEXSCREENER_BASE_URL,
        "limitations": (
            "Tracked DEX pools only; excludes " + ", ".join(exclusions) + "."
            + (" Transport was incomplete." if not transport_complete else "")
        ),
    }
    if conflicts:
        return {
            **result,
            "reason": (
                f"{len(conflicts)} conflicting pair identities; derived aggregate withheld."
            ),
        }
    if not expected_known:
        return {**result,
            "reason": "Expected DEX Screener batch evidence is unavailable.",
        }
    if not retained:
        return {**result,
            "reason": (
                "No indexed Solana DEX pools were returned for the issuer registry."
                if transport_complete and not invalid_rows
                else "DEX Screener transport incomplete; market observations are unavailable."
                if not transport_complete and not invalid_rows
                else "No usable queried-registry pair evidence was retained."
            ),
        }
    if liquidity_count == pair_count:
        result["liquidity_usd"] = round(sum(liquidity_values), 2)
    if volume_count != pair_count:
        return {**result,
            "reason": (
                f"Explicit finite non-negative 24h volume covers {volume_count} of "
                f"{pair_count} retained unique pairs; derived aggregate withheld."
            ),
        }
    return {
        **result,
        "available": True,
        "volume_24h_usd": round(sum(volume_values), 2),
    }


def summarize_provider_benchmark(raw: Any, metric_name: str) -> dict[str, Any]:
    """Retain dated provider rows and return a broad-overlap range, never one count."""
    semantics = PROVIDER_BENCHMARK_SEMANTICS.get(metric_name)
    source_generated_at = raw.get("generatedAt") if isinstance(raw, dict) else None
    summary: dict[str, Any] = {
        "available": False,
        "history_available": False,
        "partial": False,
        "canonical": False,
        "metric": metric_name,
        **(semantics or {}),
        "unit": "Count",
        "provider_observations": [],
        "source_row_count": 0,
        "observed_row_count": 0,
        "observed_date_count": 0,
        "observed_provider_count": 0,
        "oldest_date": None,
        "newest_date": None,
        "invalid_row_count": 0,
        "exact_duplicate_row_count": 0,
        "conflicting_identity_count": 0,
        "conflicts": [],
        "source_generated_at": source_generated_at if isinstance(source_generated_at, str) else None,
        "source_url": SOLANA_DATA_URL,
        "note": "Providers use different methodologies; publish ranges and source-native rows, not one exact total.",
    }
    if semantics is None:
        summary["reason"] = "Unsupported provider benchmark."
        return summary
    rows = raw.get("rows") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        summary["reason"] = "Provider rows unavailable."
        return summary
    identities: dict[tuple[str, str], dict[float, int]] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("metricName") != metric_name:
            continue
        summary["source_row_count"] += 1
        date = row.get("date")
        provider = row.get("providerName")
        value = _number(row.get("value"))
        unit = row.get("unit")
        try:
            valid_date = (
                isinstance(date, str)
                and datetime.strptime(date, "%Y-%m-%d").date().isoformat() == date
            )
        except ValueError:
            valid_date = False
        if (not valid_date or not isinstance(provider, str) or not provider.strip()
                or value is None or value < 0 or not value.is_integer()
                or (unit is not None and unit != "Count")):
            summary["invalid_row_count"] += 1
            continue
        identity = (date, provider.strip())
        values = identities.setdefault(identity, {})
        if value in values:
            summary["exact_duplicate_row_count"] += 1
        values[value] = values.get(value, 0) + 1

    conflicts = [identity for identity, values in identities.items() if len(values) > 1]
    summary["conflicting_identity_count"] = len(conflicts)
    summary["conflicts"] = [
        {"date": date, "provider": provider} for date, provider in sorted(conflicts)
    ]
    observations = [
        {"date": date, "provider": provider, "value": int(next(iter(values)))}
        for (date, provider), values in sorted(identities.items()) if len(values) == 1
    ]
    dates = sorted({row["date"] for row in observations})
    providers_seen = {row["provider"] for row in observations}
    summary.update({
        "history_available": bool(observations),
        "partial": bool(summary["invalid_row_count"] or conflicts),
        "provider_observations": observations,
        "observed_row_count": len(observations),
        "observed_date_count": len(dates),
        "observed_provider_count": len(providers_seen),
        "oldest_date": dates[0] if dates else None,
        "newest_date": dates[-1] if dates else None,
    })
    if not observations:
        summary["reason"] = "No unambiguous valid provider observations."
        return summary

    by_date: dict[str, dict[str, float]] = {}
    for row in observations:
        by_date.setdefault(row["date"], {})[row["provider"]] = float(row["value"])
    widest = max(len(values) for values in by_date.values())
    required = max(3, widest - 1)
    complete_dates = [date for date, values in by_date.items() if len(values) >= required]
    if not complete_dates:
        summary["partial"] = True
        summary["reason"] = "Fewer than three providers overlap on one day."
        return summary
    date = max(complete_dates)
    provider_values = by_date[date]
    values = sorted(provider_values.values())
    summary.update({
        "available": True,
        "date": date,
        "provider_count": len(provider_values),
        "minimum": int(min(values)),
        "maximum": int(max(values)),
    })
    return summary


def summarize_proof_of_reserves(raw: Any) -> dict[str, Any]:
    rows = raw.get("rows") if isinstance(raw, dict) else None
    rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    pages_requested = raw.get("pages_requested") if isinstance(raw, dict) else None
    pages_succeeded = raw.get("pages_succeeded") if isinstance(raw, dict) else None
    return {
        "available": bool(rows),
        "asset_count": len(rows),
        "timestamped_asset_count": sum(isinstance(row.get("timestamp"), str) for row in rows),
        "positive_reserve_asset_count": sum((_number(row.get("sharesHeld")) or 0) > 0 for row in rows),
        "coverage_complete": (
            isinstance(pages_requested, int) and pages_requested > 0
            and pages_succeeded == pages_requested
        ),
        "scope": "issuer-reported proof of reserves across all chain deployments",
        "source_url": PROOF_OF_RESERVES_URL,
    }


def _timestamp(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (OverflowError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    try:
        return int(parsed.timestamp())
    except (OverflowError, ValueError):
        return None


def _iso_timestamp(unix: int) -> str:
    return datetime.fromtimestamp(unix, timezone.utc).isoformat(timespec="seconds")


def _supply_value(raw: Any) -> tuple[Decimal, int] | None:
    text = raw.get("ui_amount_string") if isinstance(raw, dict) else None
    decimals = raw.get("decimals") if isinstance(raw, dict) else None
    if not isinstance(text, str) or not isinstance(decimals, int):
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return amount, decimals


def normalize_supply_observation(raw: Any, collected_at: str) -> dict[str, Any] | None:
    """Validate and retain the exact finalized getTokenSupply response fields."""
    context = raw.get("context") if isinstance(raw, dict) else None
    value = raw.get("value") if isinstance(raw, dict) else None
    if not isinstance(context, dict) or not isinstance(value, dict):
        return None
    amount = value.get("amount")
    decimals = value.get("decimals")
    ui_amount = value.get("uiAmount")
    ui_text = value.get("uiAmountString")
    slot = context.get("slot")
    version = context.get("apiVersion")
    if not isinstance(amount, str) or not amount.isdigit():
        return None
    if (not isinstance(decimals, int) or isinstance(decimals, bool)
            or not 0 <= decimals <= 255):
        return None
    if not isinstance(ui_text, str) or not isinstance(slot, int) or isinstance(slot, bool) or slot < 0:
        return None
    if not isinstance(version, str) or not version:
        return None
    if _timestamp(collected_at) is None:
        return None
    try:
        ui_decimal = Decimal(ui_text)
    except InvalidOperation:
        return None
    if not ui_decimal.is_finite() or ui_decimal < 0:
        return None
    numeric_ui_amount = _number(ui_amount) if ui_amount is not None else None
    if ui_amount is not None and (numeric_ui_amount is None or numeric_ui_amount < 0):
        return None
    observation = {
        "raw_amount": amount,
        "decimals": decimals,
        "ui_amount": ui_amount,
        "ui_amount_string": ui_text,
        "rpc_context_slot": slot,
        "rpc_api_version": version,
        "collected_at": collected_at,
    }
    provenance = raw.get("multiplier_provenance")
    if isinstance(provenance, dict) and provenance:
        observation["multiplier_provenance"] = provenance
    else:
        account_provenance = raw.get("account_provenance")
        if isinstance(account_provenance, dict) and account_provenance:
            observation["account_provenance"] = account_provenance
    return observation


def _valid_xstock_multiplier_provenance(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    state = value.get("state")
    if not isinstance(state, dict):
        return False
    try:
        multipliers = (Decimal(state["multiplier"]), Decimal(state["newMultiplier"]))
    except (InvalidOperation, KeyError, TypeError):
        return False
    effective = state.get("newMultiplierEffectiveTimestamp")
    authority = state.get("authority")
    slot = value.get("rpc_context_slot")
    return (
        value.get("source_method") == "getAccountInfo(finalized,jsonParsed)"
        and value.get("program_id") == TOKEN_2022_PROGRAM_ID
        and value.get("program") == "spl-token-2022"
        and value.get("extension") == "scaledUiAmountConfig"
        and all(multiplier.is_finite() and multiplier > 0 for multiplier in multipliers)
        and isinstance(effective, int) and not isinstance(effective, bool) and effective >= 0
        and "authority" in state
        and (authority is None or isinstance(authority, str) and bool(authority.strip()))
        and isinstance(slot, int) and not isinstance(slot, bool) and slot >= 0
    )


def _valid_legacy_xstock_account_provenance(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    slot = value.get("rpc_context_slot")
    version = value.get("rpc_api_version")
    return (
        value.get("source_method") == "getAccountInfo(finalized,jsonParsed)"
        and value.get("program_id") == TOKEN_PROGRAM_ID
        and value.get("program") == "spl-token"
        and isinstance(slot, int) and not isinstance(slot, bool) and slot >= 0
        and (version is None or isinstance(version, str) and bool(version))
    )


def _valid_supply_observation(value: Any, *, require_scaled: bool = False) -> bool:
    if not isinstance(value, dict):
        return False
    synthetic = {
        "context": {
            "slot": value.get("rpc_context_slot"),
            "apiVersion": value.get("rpc_api_version"),
        },
        "value": {
            "amount": value.get("raw_amount"),
            "decimals": value.get("decimals"),
            "uiAmount": value.get("ui_amount"),
            "uiAmountString": value.get("ui_amount_string"),
        },
    }
    normalized = normalize_supply_observation(synthetic, value.get("collected_at"))
    if normalized is None:
        return False
    provenance = value.get("multiplier_provenance")
    if require_scaled:
        account_provenance = value.get("account_provenance")
        return (
            _valid_xstock_multiplier_provenance(provenance)
            and account_provenance is None
        ) or (
            provenance is None
            and _valid_legacy_xstock_account_provenance(account_provenance)
        )
    return provenance is None or isinstance(provenance, dict)


def summarize_selected_usd_stablecoin_supplies(observations: Any) -> dict[str, Any]:
    """Summarize exact total supplies for the four selected USD stablecoin mints."""
    observed = observations if isinstance(observations, dict) else {}
    assets = []
    amounts: list[Decimal] = []
    slots: list[int] = []
    collected_times: list[tuple[int, str]] = []
    for source in SELECTED_USD_STABLECOINS:
        observation = observed.get(source["mint"])
        account_provenance = (
            observation.get("account_provenance")
            if isinstance(observation, dict) else None
        )
        amount = None
        if (_valid_supply_observation(observation)
                and isinstance(account_provenance, dict)
                and account_provenance.get("program_id") in {
                    TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID,
                }
                and account_provenance.get("source_method")
                == "getAccountInfo(finalized,jsonParsed)"):
            try:
                amount = Decimal(observation["raw_amount"]).scaleb(
                    -observation["decimals"],
                )
            except InvalidOperation:
                amount = None
        asset = {**source, "available": amount is not None}
        if amount is not None:
            asset.update({
                "total_supply_decimal": format(amount, "f"),
                "raw_amount": observation["raw_amount"],
                "decimals": observation["decimals"],
                "rpc_ui_amount_string": observation["ui_amount_string"],
                "rpc_context_slot": observation["rpc_context_slot"],
                "rpc_api_version": observation["rpc_api_version"],
                "event_time": None,
                "collected_at": observation["collected_at"],
                "basis": "finalized on-chain total token supply",
                "account_provenance": account_provenance,
            })
            amounts.append(amount)
            slots.append(observation["rpc_context_slot"])
            collected_times.append((
                _timestamp(observation["collected_at"]), observation["collected_at"],
            ))
        else:
            asset["reason"] = "Finalized validated mint supply is unavailable."
        assets.append(asset)
    numerator = len(amounts)
    summary = {
        "metric_id": "selected_usd_stablecoin_total_supply",
        "available": numerator == len(SELECTED_USD_STABLECOINS),
        "state": "current" if numerator == 4 else ("partial" if numerator else "unavailable"),
        "coverage_numerator": numerator,
        "coverage_denominator": len(SELECTED_USD_STABLECOINS),
        "coverage_label": f"{numerator}/{len(SELECTED_USD_STABLECOINS)}",
        "universe_coverage": "unknown",
        "unit": "selected stablecoin token units",
        "basis": "finalized on-chain total token supply",
        "assets": assets,
        "slot_range": {
            "first": min(slots) if slots else None,
            "last": max(slots) if slots else None,
        },
        "oldest_observation_at": min(collected_times)[1] if collected_times else None,
        "newest_observation_at": max(collected_times)[1] if collected_times else None,
        "registry_source": {
            "source_key": SELECTED_STABLECOIN_SOURCE_KEY,
            "url": SELECTED_STABLECOIN_SOURCE_URL,
            "path": SELECTED_STABLECOIN_SOURCE_PATH,
            "source_revision": SELECTED_STABLECOIN_SOURCE_REVISION,
            "source_license": SELECTED_STABLECOIN_SOURCE_LICENSE,
            "usage": "factual mint and issuer identifiers only",
        },
        "limitations": (
            "Exactly four selected USD stablecoin mints; broader universe coverage "
            "is unknown, so this does not represent all stablecoins. Total token "
            "supply is not circulating supply, USD value, liquidity, reserves, or "
            "executable depth. RPC context slots are retained; event time is unavailable."
        ),
    }
    if numerator == len(SELECTED_USD_STABLECOINS):
        total = sum(amounts, Decimal(0))
        summary["selected_total_supply_decimal"] = format(total, "f")
        if total:
            for asset, amount in zip(assets, amounts):
                asset["share_of_selected_total"] = format(amount / total, "f")
    return summary


def rpc_endpoint_identity(endpoint: Any) -> str | None:
    """Return a stable opaque identity without persisting endpoint credentials."""
    if not isinstance(endpoint, str) or not endpoint.strip():
        return None
    return "sha256:" + hashlib.sha256(endpoint.strip().encode()).hexdigest()


def rpc_endpoint_reference(endpoint: Any) -> dict[str, str | None]:
    """Return a safe public label plus the opaque identity of the exact URL."""
    exact = endpoint.strip() if isinstance(endpoint, str) else None
    label = exact if exact in PUBLIC_RPC_ENDPOINTS else CUSTOM_RPC_ENDPOINT_LABEL
    return {
        "endpoint": label,
        "endpoint_identity": rpc_endpoint_identity(endpoint),
    }


def empty_supply_state(endpoint: str | None = None) -> dict[str, Any]:
    return {
        "version": SUPPLY_STATE_VERSION,
        "rpc_endpoint_identity": rpc_endpoint_identity(endpoint),
        "cursor_mint": None,
        "updated_at": None,
        "observations": {},
    }


def _validated_supply_state(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or raw.get("version") != SUPPLY_STATE_VERSION:
        return None
    cursor = raw.get("cursor_mint")
    updated = raw.get("updated_at")
    observations = raw.get("observations")
    endpoint_identity = raw.get("rpc_endpoint_identity")
    if (endpoint_identity is not None
            and (not isinstance(endpoint_identity, str)
                 or re.fullmatch(r"sha256:[0-9a-f]{64}", endpoint_identity) is None)):
        return None
    if cursor is not None and (not isinstance(cursor, str) or not cursor):
        return None
    if updated is not None and _timestamp(updated) is None:
        return None
    if not isinstance(observations, dict):
        return None
    if endpoint_identity is None and (cursor is not None or updated is not None or observations):
        return None
    if any(not isinstance(mint, str) or not mint
           or not _valid_supply_observation(observation, require_scaled=True)
           for mint, observation in observations.items()):
        return None
    return {
        "version": SUPPLY_STATE_VERSION,
        "rpc_endpoint_identity": endpoint_identity,
        "cursor_mint": cursor,
        "updated_at": updated,
        "observations": {
            mint: dict(observation) for mint, observation in observations.items()
        },
    }


def load_supply_state(path: Path = SUPPLY_STATE_PATH) -> dict[str, Any]:
    if not path.exists():
        return empty_supply_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as error:
        state = empty_supply_state()
        state["load_error"] = f"could not read supply state: {error}"
        return state
    if isinstance(raw, dict) and raw.get("version") == 1:
        state = empty_supply_state()
        state["reset_reason"] = "legacy supply state has no RPC endpoint identity"
        return state
    state = _validated_supply_state(raw)
    if state is not None:
        return state
    state = empty_supply_state()
    state["load_error"] = "unsupported or malformed supply state"
    return state


def save_supply_state(state: dict[str, Any], path: Path = SUPPLY_STATE_PATH) -> None:
    validated = _validated_supply_state(state)
    if (validated is None or validated["rpc_endpoint_identity"] is None
            or state.get("load_error") is not None):
        raise ValueError("refusing to overwrite an invalid supply state")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            json.dump(validated, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def supply_query_order(mints: list[str], state: dict[str, Any]) -> list[str]:
    """Stable mint-keyed rotation; input order and deleted cursors cannot reset it."""
    ordered = sorted(set(mint for mint in mints if isinstance(mint, str) and mint))
    if not ordered:
        return []
    cursor = state.get("cursor_mint") if isinstance(state, dict) else None
    if not isinstance(cursor, str):
        return ordered
    start = bisect.bisect_right(ordered, cursor)
    return ordered[start:] + ordered[:start]


def _observation_age(observation: Any, observed_at_unix: int) -> int | None:
    timestamp = _timestamp(observation.get("collected_at")) if isinstance(observation, dict) else None
    if timestamp is None or timestamp > observed_at_unix:
        return None
    return max(0, observed_at_unix - timestamp)


def summarize_supply_coverage(
    registry_asset_count: int,
    eligible_mints: list[str],
    state: dict[str, Any],
    observed_at_unix: int,
    queried_this_run_asset_count: int,
    successful_this_run_asset_count: int,
    registry_complete: bool,
) -> dict[str, Any]:
    eligible = set(eligible_mints)
    observations = state.get("observations") if isinstance(state, dict) else None
    observations = observations if isinstance(observations, dict) else {}
    matching = {
        mint: observation for mint, observation in observations.items()
        if (mint in eligible
            and _valid_supply_observation(observation, require_scaled=True)
            and _observation_age(observation, observed_at_unix) is not None)
    }
    ages = {
        mint: _observation_age(observation, observed_at_unix)
        for mint, observation in matching.items()
    }
    fresh = sum(age is not None and age <= SUPPLY_FRESH_SECONDS for age in ages.values())
    sweep_covered = sum(
        age is not None and age <= SUPPLY_SWEEP_MAX_SECONDS for age in ages.values()
    )
    times = sorted(
        (timestamp, observation["collected_at"])
        for observation in matching.values()
        if (timestamp := _timestamp(observation.get("collected_at"))) is not None
    )
    observation_span_seconds = times[-1][0] - times[0][0] if times else None
    denominator = len(eligible)
    sweep_complete = bool(
        registry_complete and denominator and sweep_covered == denominator
        and observation_span_seconds is not None
        and observation_span_seconds <= SUPPLY_SWEEP_MAX_SECONDS
    )
    return {
        "registry_asset_count": registry_asset_count,
        "registry_complete": registry_complete,
        "eligible_asset_count": denominator,
        "queried_this_run_asset_count": queried_this_run_asset_count,
        "successful_this_run_asset_count": successful_this_run_asset_count,
        "failed_this_run_asset_count": (
            queried_this_run_asset_count - successful_this_run_asset_count
        ),
        "attempt_scope": "current collection run",
        "observed_asset_count": len(matching),
        "fresh_asset_count": fresh,
        "valued_asset_count": 0,
        "coverage_numerator": sweep_covered,
        "coverage_denominator": denominator,
        "fresh_max_age_seconds": SUPPLY_FRESH_SECONDS,
        "sweep_max_age_seconds": SUPPLY_SWEEP_MAX_SECONDS,
        "oldest_observation_at": times[0][1] if times else None,
        "newest_observation_at": times[-1][1] if times else None,
        "observation_span_seconds": observation_span_seconds,
        "sweep_complete": sweep_complete,
        "scope": "registry-wide" if sweep_complete else "observed subset",
        "coverage_basis": "eligible assets with a valid supply observation no older than 72 hours",
    }


def build_tokenized_equities(
    products: Any, supplies: Any, limit: int = 12,
    observed_at_unix: int | None = None,
) -> dict[str, Any]:
    """Join registry and cached supply while price valuation remains blocked."""
    registry = [item for item in products if isinstance(item, dict)] if isinstance(products, list) else []
    supply_map = supplies if isinstance(supplies, dict) else {}
    assets = []
    for product in registry:
        mint = product.get("solana_mint")
        if not isinstance(mint, str):
            continue
        observation = supply_map.get(mint)
        supply_age_seconds = (
            _observation_age(observation, observed_at_unix)
            if (observed_at_unix is not None
                and _valid_supply_observation(observation, require_scaled=True))
            else None
        )
        if supply_age_seconds is None:
            observation = None
        supply = _supply_value(observation)
        supply_value, supply_decimals = supply if supply is not None else (None, None)
        if supply_value is None or supply_age_seconds is None:
            freshness = "unavailable" if supply_value is None else "unknown"
        else:
            freshness = "fresh" if supply_age_seconds <= SUPPLY_FRESH_SECONDS else "stale"
        asset = {
            "symbol": product.get("symbol"),
            "name": product.get("name"),
            "slug": product.get("slug"),
            "mint": mint,
            "supply": float(supply_value) if supply_value is not None else None,
            "supply_raw_amount": observation.get("raw_amount") if isinstance(observation, dict) else None,
            "supply_decimals": supply_decimals,
            "supply_rpc_ui_amount": (
                observation.get("ui_amount") if isinstance(observation, dict) else None
            ),
            "supply_rpc_ui_amount_string": (
                observation.get("ui_amount_string") if isinstance(observation, dict) else None
            ),
            "supply_context_slot": (
                observation.get("rpc_context_slot") if isinstance(observation, dict) else None
            ),
            "supply_rpc_api_version": (
                observation.get("rpc_api_version") if isinstance(observation, dict) else None
            ),
            "supply_collected_at": (
                observation.get("collected_at") if isinstance(observation, dict) else None
            ),
            "supply_age_seconds": supply_age_seconds,
            "supply_freshness": freshness,
            "supply_fresh_max_age_seconds": SUPPLY_FRESH_SECONDS,
            "supply_unit": "token",
            "supply_source_id": "solana_getTokenSupply",
            "supply_source_method": "getTokenSupply(finalized)",
            "basis": "finalized on-chain token supply",
        }
        if isinstance(observation, dict) and isinstance(
            observation.get("multiplier_provenance"), dict,
        ):
            asset["supply_multiplier_provenance"] = observation["multiplier_provenance"]
        elif isinstance(observation, dict) and isinstance(
            observation.get("account_provenance"), dict,
        ):
            asset["supply_account_provenance"] = observation["account_provenance"]
        assets.append(asset)
    assets.sort(key=lambda item: (
        item.get("supply") is None,
        str(item.get("symbol") or ""),
        str(item.get("mint") or ""),
    ))
    observed = [item for item in assets if item.get("supply") is not None]
    base = {
        "available": bool(observed),
        "registry_asset_count": len(registry),
        "supply_observed_asset_count": len(observed),
        "fresh_supply_asset_count": sum(
            item.get("supply_freshness") == "fresh" for item in assets
        ),
        "stale_supply_asset_count": sum(
            item.get("supply_freshness") == "stale" for item in assets
        ),
        "valued_asset_count": 0,
        "observed_at_unix": observed_at_unix,
        "supply_fresh_max_age_seconds": SUPPLY_FRESH_SECONDS,
        "assets": assets[:limit],
        "all_assets": assets,
        "valuation": {
            "available": False,
            "scope": "unavailable",
            "reason": (
                "No price valuation is published while source redistribution rights "
                "and source-native price timestamps remain unresolved."
            ),
        },
        "volume": {
            "available": False,
            "reason": "No attributable keyless source for tokenized-equity trading volume was proven.",
        },
        "note": (
            "Finalized on-chain supply observations only. Supply is not trading "
            "volume, issuer AUM, liquidity, reserves, or USD valuation."
        ),
    }
    return base


def fetch_text(url: str, timeout: int = 20) -> str | None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return transport.read_bounded(response).decode("utf-8")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            UnicodeDecodeError, ValueError):
        return None


def fetch_json(url: str, timeout: int = 20) -> Any | None:
    text = fetch_text(url, timeout)
    if text is None:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def fetch_paginated_nodes(
    base_url: str, timeout: int = 20, page_size: int = REGISTRY_PAGE_SIZE,
    query: str = "", max_pages: int = 20, total_deadline_seconds: int = 30,
) -> dict[str, Any]:
    """Fetch an entire documented xStocks public collection or fail closed."""
    rows: list[dict[str, Any]] = []
    requested = succeeded = 0
    deadline = time.monotonic() + total_deadline_seconds
    for page in range(max_pages):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"rows": rows, "pages_requested": requested,
                    "pages_succeeded": succeeded, "coverage_complete": False}
        requested += 1
        separator = "&" if query else "?"
        url = f"{base_url}{query}{separator}page={page}&pageSize={page_size}"
        payload = fetch_json(url, max(1, min(timeout, int(remaining))))
        nodes = payload.get("nodes") if isinstance(payload, dict) else None
        page_info = payload.get("page") if isinstance(payload, dict) else None
        if not isinstance(nodes, list) or not isinstance(page_info, dict):
            return {"rows": rows, "pages_requested": requested, "pages_succeeded": succeeded,
                    "coverage_complete": False}
        rows.extend(item for item in nodes if isinstance(item, dict))
        succeeded += 1
        if page_info.get("hasNextPage") is not True:
            return {"rows": rows, "pages_requested": requested, "pages_succeeded": succeeded,
                    "coverage_complete": True}
    return {"rows": rows, "pages_requested": requested, "pages_succeeded": succeeded,
            "coverage_complete": False}


def fetch_xstocks_registry(timeout: int = 20) -> dict[str, Any]:
    products = parse_token_registry_xstocks(fetch_text(TOKEN_REGISTRY_SOURCE_URL, timeout))
    complete = len(products) == TOKEN_REGISTRY_EXPECTED_XSTOCKS
    return {
        "products": products,
        "coverage_complete": complete,
        "source_url": TOKEN_REGISTRY_SOURCE_URL,
        "source_kind": "pinned official token registry",
        "source_key": TOKEN_REGISTRY_SOURCE_KEY,
        "source_revision": TOKEN_REGISTRY_SOURCE_REVISION,
        "source_license": TOKEN_REGISTRY_SOURCE_LICENSE,
        "provenance": {
            "repository": "https://github.com/solana-foundation/tokens",
            "path": TOKEN_REGISTRY_SOURCE_PATH,
            "revision": TOKEN_REGISTRY_SOURCE_REVISION,
            "license": TOKEN_REGISTRY_SOURCE_LICENSE,
            "selection": "address label exactly 'xStock'",
            "expected_unique_group_count": TOKEN_REGISTRY_EXPECTED_XSTOCKS,
            "expected_unique_mint_count": TOKEN_REGISTRY_EXPECTED_XSTOCKS,
        },
        "reason": None if complete else (
            "Pinned token registry transport or exact xStock identity contract failed."
        ),
    }


def fetch_dex_pairs(
    mints: list[str], timeout: int = 20, total_deadline_seconds: int = 30,
) -> dict[str, Any]:
    rows: list[Any] = []
    requested = succeeded = 0
    expected = math.ceil(len(mints) / DEXSCREENER_BATCH_SIZE) if mints else 0
    deadline = time.monotonic() + total_deadline_seconds
    for start in range(0, len(mints), DEXSCREENER_BATCH_SIZE):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        requested += 1
        addresses = ",".join(mints[start:start + DEXSCREENER_BATCH_SIZE])
        payload = fetch_json(
            DEXSCREENER_BASE_URL + addresses,
            max(1, min(timeout, int(remaining))),
        )
        if isinstance(payload, list):
            rows.extend(payload)
            succeeded += 1
    return {"rows": rows, "batches_expected": expected,
            "batches_requested": requested, "batches_succeeded": succeeded}


def _rpc_result(endpoint: str, method: str, params: list[Any], timeout: float) -> Any | None:
    return blocks.call(method, params, endpoint, timeout)


def _mint_account_provenance(
    raw: Any, require_scaled: bool,
) -> dict[str, Any] | None:
    context = raw.get("context") if isinstance(raw, dict) else None
    account = raw.get("value") if isinstance(raw, dict) else None
    data = account.get("data") if isinstance(account, dict) else None
    parsed = data.get("parsed") if isinstance(data, dict) else None
    info = parsed.get("info") if isinstance(parsed, dict) else None
    extensions = info.get("extensions") if isinstance(info, dict) else None
    extension = next((
        item for item in extensions
        if isinstance(item, dict) and item.get("extension") == "scaledUiAmountConfig"
        and isinstance(item.get("state"), dict)
    ), None) if isinstance(extensions, list) else None
    slot = context.get("slot") if isinstance(context, dict) else None
    owner = account.get("owner") if isinstance(account, dict) else None
    program = data.get("program") if isinstance(data, dict) else None
    if (owner not in {TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID}
            or program != ({TOKEN_PROGRAM_ID: "spl-token",
                            TOKEN_2022_PROGRAM_ID: "spl-token-2022"}.get(owner))
            or not isinstance(parsed, dict) or parsed.get("type") != "mint"
            or (require_scaled and owner == TOKEN_2022_PROGRAM_ID and extension is None)
            or not isinstance(slot, int) or isinstance(slot, bool)
            or slot < 0):
        return None
    provenance = {
        "source_method": "getAccountInfo(finalized,jsonParsed)",
        "program_id": owner,
        "program": program,
        "rpc_context_slot": slot,
    }
    if require_scaled and owner == TOKEN_2022_PROGRAM_ID:
        provenance.update({
            "extension": "scaledUiAmountConfig",
            "state": dict(extension["state"]),
        })
    version = context.get("apiVersion")
    if isinstance(version, str) and version:
        provenance["rpc_api_version"] = version
    return provenance


def _fetch_validated_token_supply(
    endpoint: str, mint: str, timeout: float = 20, *, require_scaled: bool,
) -> Any | None:
    deadline = time.monotonic() + timeout
    account = _rpc_result(endpoint, "getAccountInfo", [
        mint, {"commitment": "finalized", "encoding": "jsonParsed"},
    ], timeout)
    provenance = _mint_account_provenance(account, require_scaled)
    remaining = deadline - time.monotonic()
    if provenance is None or remaining <= 0:
        return None
    supply = _rpc_result(endpoint, "getTokenSupply", [
        mint, {"commitment": "finalized"},
    ], remaining)
    if not isinstance(supply, dict):
        return None
    supply_context = supply.get("context")
    supply_slot = supply_context.get("slot") if isinstance(supply_context, dict) else None
    account_slot = provenance.get("rpc_context_slot")
    if (not isinstance(supply_slot, int) or isinstance(supply_slot, bool)
            or supply_slot < 0 or account_slot > supply_slot):
        return None
    result = {**supply, "account_provenance": provenance}
    if require_scaled and provenance.get("program_id") == TOKEN_2022_PROGRAM_ID:
        result["multiplier_provenance"] = provenance
    return result


def fetch_token_supply(endpoint: str, mint: str, timeout: float = 20) -> Any | None:
    return _fetch_validated_token_supply(
        endpoint, mint, timeout, require_scaled=True,
    )


def fetch_selected_usd_stablecoin_supplies(
    endpoint: str, timeout: int = 20, total_deadline_seconds: int = 30,
) -> dict[str, Any]:
    observations = {}
    attempts = 0
    deadline = time.monotonic() + total_deadline_seconds
    for index, asset in enumerate(SELECTED_USD_STABLECOINS):
        attempt_started = time.monotonic()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        attempts += 1
        raw = _fetch_validated_token_supply(
            endpoint, asset["mint"], min(timeout, remaining),
            require_scaled=False,
        )
        collected_at = _iso_timestamp(int(time.time()))
        observation = normalize_supply_observation(raw, collected_at)
        if observation is not None:
            observations[asset["mint"]] = observation
        if index + 1 < len(SELECTED_USD_STABLECOINS):
            delay = 0.6 - (time.monotonic() - attempt_started)
            remaining = deadline - time.monotonic()
            if delay > 0 and remaining > 0:
                time.sleep(min(delay, remaining))
    summary = summarize_selected_usd_stablecoin_supplies(observations)
    summary.update({
        "queried_asset_count": attempts,
        "failed_asset_count": attempts - summary["coverage_numerator"],
        "deadline_exhausted": attempts < len(SELECTED_USD_STABLECOINS),
        "rpc": {
            **rpc_endpoint_reference(endpoint),
            "methods": ["getAccountInfo", "getTokenSupply"],
            "commitment": "finalized",
        },
    })
    return summary


def collect_growth(
    endpoint: str, timeout: int = 12, total_deadline_seconds: int = 75,
    supply_state: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Collect growth evidence and return the next supply cursor separately.

    This function never writes the cursor. The caller persists it only after
    the validated snapshot has been written successfully.
    """
    registry_result = fetch_xstocks_registry(timeout)
    products = registry_result.get("products")
    products = products if isinstance(products, list) else []
    mints = [
        mint for product in products
        if isinstance(product, dict)
        and isinstance((mint := product.get("solana_mint")), str)
        and mint
    ]
    eligible_mints = sorted(set(mints))

    endpoint_identity = rpc_endpoint_identity(endpoint)
    endpoint_reference = rpc_endpoint_reference(endpoint)
    supplied_state = empty_supply_state(endpoint) if supply_state is None else supply_state
    state_error = supplied_state.get("load_error") if isinstance(supplied_state, dict) else None
    state_reset_reason = (
        supplied_state.get("reset_reason") if isinstance(supplied_state, dict) else None
    )
    if isinstance(supplied_state, dict) and supplied_state.get("version") == 1:
        working_state = empty_supply_state(endpoint)
        state_reset_reason = "legacy supply state has no RPC endpoint identity"
    else:
        working_state = _validated_supply_state(supplied_state) if state_error is None else None
    if working_state is None and state_error is None:
        state_error = "unsupported or malformed supply state"
    if working_state is None:
        working_state = empty_supply_state(endpoint)
    elif endpoint_identity is None:
        state_error = "RPC endpoint identity is unavailable"
    elif working_state["rpc_endpoint_identity"] != endpoint_identity:
        if (working_state["rpc_endpoint_identity"] is not None
                or working_state["observations"] or working_state["cursor_mint"] is not None):
            state_reset_reason = "RPC endpoint changed; cached supply observations discarded"
        working_state = empty_supply_state(endpoint)

    # The four required, inexpensive observations precede the larger sweep.
    stablecoins = fetch_selected_usd_stablecoin_supplies(
        endpoint, min(timeout, 20), total_deadline_seconds=30,
    )
    deadline = time.monotonic() + total_deadline_seconds
    supply_attempts = 0
    supply_successes = 0
    query_order = supply_query_order(eligible_mints, working_state)
    if state_error is None:
        for index, mint in enumerate(query_order):
            attempt_started = time.monotonic()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            supply_attempts += 1
            result = fetch_token_supply(endpoint, mint, min(6, remaining))
            collected_at = _iso_timestamp(int(time.time()))
            observation = normalize_supply_observation(result, collected_at)
            if observation is not None:
                working_state["observations"][mint] = observation
                supply_successes += 1
            # Advance on every bounded attempt so one bad mint cannot starve the sweep.
            working_state["cursor_mint"] = mint
            working_state["updated_at"] = collected_at
            if index + 1 < len(query_order):
                # Each mint opens two HTTP connections; the public limit is
                # 40 new connections / 10s across methods, not 40 mint pairs.
                delay = 0.6 - (time.monotonic() - attempt_started)
                remaining = deadline - time.monotonic()
                if delay > 0 and remaining > 0:
                    time.sleep(min(delay, remaining))
    next_supply_state = working_state if state_error is None else None

    dex_raw = {
        "rows": [],
        "batches_expected": math.ceil(len(eligible_mints) / DEXSCREENER_BATCH_SIZE),
        "batches_requested": 0,
        "batches_succeeded": 0,
    }
    dex_volume = summarize_dex_volume(dex_raw, set(eligible_mints))
    dex_volume["reason"] = DEXSCREENER_PUBLICATION_HOLD_REASON
    reserves = {
        "available": False,
        "scope": "unavailable",
        "reason": (
            "Issuer API collection is disabled while automated retrieval and "
            "redistribution rights remain unresolved."
        ),
    }
    # Active Addresses rows are provider-scoped estimates of network-wide daily
    # activity, never a canonical complete-network count. Owner accepted public
    # redistribution of this feed on 2026-09-01. Fee Payers stays held pending a
    # separate acceptance decision.
    solana_data_raw = fetch_json(SOLANA_DATA_URL, timeout)
    daily_addresses = summarize_provider_benchmark(solana_data_raw, "Active Addresses")
    fee_payers = summarize_provider_benchmark(None, "Fee Payers")
    fee_payers_hold_reason = (
        "Fee Payers provider-row republication is held pending explicit "
        "source-rights acceptance; transaction-initiator ranges remain unavailable."
    )
    fee_payers["reason"] = fee_payers_hold_reason
    if daily_addresses.get("available") is not True:
        daily_addresses["reason"] = (
            "Solana Data provider activity rows were not collected this run "
            "(fetch failed or no overlapping provider day)."
        )

    observed_at_unix = int(time.time())
    coverage = summarize_supply_coverage(
        registry_asset_count=len(products),
        eligible_mints=eligible_mints,
        state=working_state,
        observed_at_unix=observed_at_unix,
        queried_this_run_asset_count=supply_attempts,
        successful_this_run_asset_count=supply_successes,
        registry_complete=registry_result.get("coverage_complete") is True,
    )
    equities = build_tokenized_equities(
        products, working_state["observations"], limit=DISPLAY_ASSET_LIMIT,
        observed_at_unix=observed_at_unix,
    )
    equities["eligible_asset_count"] = coverage["eligible_asset_count"]
    equities["supply_queried_this_run_asset_count"] = supply_attempts
    equities["supply_successful_this_run_asset_count"] = supply_successes
    equities["supply_failed_this_run_asset_count"] = supply_attempts - supply_successes
    equities["supply_deadline_exhausted"] = (
        state_error is None and supply_attempts < len(query_order)
    )
    equities["supply_coverage"] = coverage
    equities["displayed_asset_count"] = len(equities.get("assets", []))
    equities["display_asset_limit"] = DISPLAY_ASSET_LIMIT
    equities["volume"] = dex_volume
    equities["proof_of_reserves"] = reserves
    equities["note"] = (
        f"Finalized supply has usable observations for {coverage['observed_asset_count']} of "
        f"{coverage['eligible_asset_count']} eligible registry assets; the top "
        f"{DISPLAY_ASSET_LIMIT} registry rows are shown. Price valuation remains unavailable. "
        "Supply is not trading volume, issuer AUM, liquidity, reserves, or USD valuation."
    )

    report = {
        "available": any((
            bool(products), equities.get("available") is True,
            dex_volume.get("available") is True, reserves.get("available") is True,
            stablecoins.get("coverage_numerator", 0) > 0,
            daily_addresses.get("history_available") is True,
            fee_payers.get("history_available") is True,
        )),
        "requires_api_key": (
            False if endpoint_reference["endpoint"] in PUBLIC_RPC_ENDPOINTS else None
        ),
        "tokenized_equities": equities,
        "selected_usd_stablecoins": stablecoins,
        "daily_active_addresses": daily_addresses,
        "daily_fee_payers": fee_payers,
        "sources": {
            "registry": {
                "url": registry_result.get("source_url"),
                "kind": registry_result.get("source_kind"),
                "available": bool(products),
                "coverage_complete": registry_result.get("coverage_complete") is True,
                "asset_count": len(products),
                "source_key": registry_result.get("source_key"),
                "source_revision": registry_result.get("source_revision"),
                "source_license": registry_result.get("source_license"),
                "provenance": registry_result.get("provenance"),
                "reason": registry_result.get("reason"),
            },
            "supply": {
                "method": "getTokenSupply",
                "endpoint": endpoint_reference["endpoint"],
                "rpc_endpoint_identity": endpoint_reference["endpoint_identity"],
                "available": coverage["observed_asset_count"] > 0,
                "commitment": "finalized",
                "state_version": SUPPLY_STATE_VERSION,
                "deadline_exhausted": equities["supply_deadline_exhausted"],
                **coverage,
            },
            "dex_volume": {
                "url": DEXSCREENER_BASE_URL, "available": dex_volume.get("available") is True,
                "partial": dex_volume.get("partial"),
                "scope": dex_volume.get("scope"),
                "transport_complete": dex_volume.get("transport_complete") is True,
                "market_coverage": dex_volume.get("market_coverage"),
                "exclusions": dex_volume.get("exclusions"),
                "held": True,
                "reason": DEXSCREENER_PUBLICATION_HOLD_REASON,
            },
            "proof_of_reserves": {
                "url": PROOF_OF_RESERVES_URL, "available": False,
                "coverage_complete": False,
                "scope": reserves.get("scope"),
                "held": True,
                "reason": reserves.get("reason"),
            },
            "selected_usd_stablecoins": {
                "available": stablecoins.get("available") is True,
                "partial": stablecoins.get("state") == "partial",
                "coverage_complete": stablecoins.get("available") is True,
                "state": stablecoins.get("state"),
                "coverage_numerator": stablecoins.get("coverage_numerator", 0),
                "coverage_denominator": stablecoins.get("coverage_denominator", 4),
                "registry_source": stablecoins.get("registry_source"),
                "rpc": stablecoins.get("rpc"),
            },
            "activity_benchmark": {
                "url": SOLANA_DATA_URL,
                "available": any((
                    daily_addresses.get("history_available") is True,
                    fee_payers.get("history_available") is True,
                )),
                "held": fee_payers.get("available") is not True,
                "reason": fee_payers_hold_reason,
                "active_addresses_available": daily_addresses.get("available") is True,
                "fee_payers_available": fee_payers.get("available") is True,
                "active_addresses_history_available": (
                    daily_addresses.get("history_available") is True
                ),
                "fee_payers_history_available": fee_payers.get("history_available") is True,
                "active_addresses_observed_row_count": daily_addresses.get("observed_row_count", 0),
                "fee_payers_observed_row_count": fee_payers.get("observed_row_count", 0),
                "partial": (
                    daily_addresses.get("partial") is True
                    or fee_payers.get("partial") is True
                ),
                "canonical": False,
            },
        },
    }
    if state_error is not None:
        report["sources"]["supply"]["state_error"] = state_error
    if state_reset_reason is not None:
        report["sources"]["supply"]["state_reset_reason"] = state_reset_reason
    return report, next_supply_state


if __name__ == "__main__":
    # Diagnostic only. collect.py owns persistence after snapshot validation.
    report, _ = collect_growth(
        "https://api.mainnet.solana.com", supply_state=load_supply_state(),
    )
    print(json.dumps(report, indent=2))
