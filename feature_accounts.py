"""Finalized on-chain state for a small pinned set of Agave feature gates.

Feature identifiers and factual SIMD mappings come from the attributed Agave
revision below. This is an independent parser, not copied validator code.
Editorial roadmap content and predicted activation dates are not collected.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
from typing import Any

import blocks
import growth

SOURCE_REVISION = "062cf37d3d726605a8d484c4788bb2197ff0fb4e"
SOURCE_REPOSITORY = "https://github.com/anza-xyz/agave"
SOURCE_PATH = "feature-set/src/lib.rs"
FEATURE_PROGRAM_ID = "Feature111111111111111111111111111111111111"
METADATA = {
    "repository": SOURCE_REPOSITORY,
    "source_revision": SOURCE_REVISION,
    "source_path": SOURCE_PATH,
    "source_url": f"{SOURCE_REPOSITORY}/blob/{SOURCE_REVISION}/{SOURCE_PATH}",
    "license": "Apache-2.0",
    "license_url": f"{SOURCE_REPOSITORY}/blob/{SOURCE_REVISION}/LICENSE",
    "license_sha256": "048838ecab23173fe6db9b3a4f5d9e2f5480b8692dfca556fed4aa039ca06bc8",
    "feature_program_id": FEATURE_PROGRAM_ID,
}
FEATURES = (
    ("alpenglow", "Alpenglow consensus", "SIMD-0326",
     "A1pengvuM6JEcyNuTnMqepBKhwHE3N6PmUrdATGawhJS"),
    ("reduce_slot_time_to_350ms", "350 ms slot target", "SIMD-0525",
     "iBRL5RuWhw4yqaAZu96RUULHckHTZAoe2b77qaV38JZ"),
    ("reduce_slot_time_to_300ms", "300 ms slot target", "SIMD-0525",
     "iBRLL3k18HST852F1Mf3Lv83waTNQmmqvKDxvYGwQFL"),
    ("reduce_slot_time_to_250ms", "250 ms slot target", "SIMD-0525",
     "iBRLMc81UjRa8fn8A6eE8bJTnRbgQoPTynM51akENCV"),
    ("reduce_slot_time_to_200ms", "200 ms slot target", "SIMD-0525",
     "iBRLjhJnkmDZgNoZRDMW11d8ZV7HvsL3vAyRjZB5npW"),
    ("set_lamports_per_byte_to_6333", "Rent step 1: 6,333 lamports per byte", "SIMD-0437",
     "4a6f7o7iTcA8hRDCrPLkSatnt5Ykxiu36wo5p1Tt12wC"),
    ("set_lamports_per_byte_to_5080", "Rent step 2: 5,080 lamports per byte", "SIMD-0437",
     "61BtM7BkDEE8Yq5fskEVAQT9mYA8qCejJWoLe5apqg81"),
    ("set_lamports_per_byte_to_2575", "Rent step 3: 2,575 lamports per byte", "SIMD-0437",
     "Ftxb3ZKq7aNqgxDBbP7EonvR2RszZk9ctjdsTX38kQaz"),
    ("set_lamports_per_byte_to_1322", "Rent step 4: 1,322 lamports per byte", "SIMD-0437",
     "GsUBNYNDPdMLHPD37TToHzrzcNcjpC9w5n1EcJk5iTaM"),
    ("set_lamports_per_byte_to_696", "Rent step 5: 696 lamports per byte", "SIMD-0437",
     "mZdnRh9T2EbDNvqKjkCR3bvo5c816tJaojtE9Xs7iuY"),
)
NOTE = (
    "Finalized account observations for ten pinned feature gates, not a complete "
    "upgrade inventory or a performance measurement. Account absence is reported "
    "as absence, not proof that a proposal is rejected or an upgrade is inactive. "
    "Earlier activated steps can coexist with later steps."
)


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _observed_at(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.utcoffset() is None:
            return None
        return stamp.astimezone(timezone.utc).isoformat(timespec="seconds")
    except ValueError:
        return None


def _account_state(account: Any, context_slot: int) -> tuple[str, int | None, str | None]:
    if account is None:
        return "account_absent", None, "No account at this address in the finalized response."
    if (not isinstance(account, dict) or account.get("owner") != FEATURE_PROGRAM_ID
            or account.get("executable") is not False):
        return "unavailable", None, "Feature account owner or executable flag is invalid."
    data = account.get("data")
    if (not isinstance(data, list) or len(data) != 2
            or not isinstance(data[0], str) or len(data[0]) != 12 or data[1] != "base64"):
        return "unavailable", None, "Feature account encoding is invalid."
    try:
        decoded = base64.b64decode(data[0], validate=True)
    except (binascii.Error, ValueError):
        return "unavailable", None, "Feature account encoding is invalid."
    if len(decoded) != 9 or ("space" in account and account["space"] != 9):
        return "unavailable", None, "Feature account size is invalid."
    # Agave SDK Feature is Option<u64>: one tag byte plus an eight-byte slot.
    # Pending accounts reserve all nine bytes; the padding has no slot meaning.
    if decoded[0] == 0:
        return "pending", None, None
    if decoded[0] != 1:
        return "unavailable", None, "Feature activation tag is invalid."
    activated_at_slot = int.from_bytes(decoded[1:], "little")
    if activated_at_slot > context_slot:
        return "unavailable", None, "Feature activation slot exceeds the finalized context."
    return "activated", activated_at_slot, None


def parse_feature_accounts(raw: Any, endpoint: str, observed_at: str) -> dict[str, Any]:
    """Normalize a complete ordered getMultipleAccounts response, without I/O."""
    stamp = _observed_at(observed_at)
    context = raw.get("context") if isinstance(raw, dict) else None
    values = raw.get("value") if isinstance(raw, dict) else None
    slot = context.get("slot") if isinstance(context, dict) else None
    version = context.get("apiVersion") if isinstance(context, dict) else None
    identities = [feature[3] for feature in FEATURES]
    valid = (
        stamp is not None and len(identities) == len(set(identities))
        and _nonnegative_int(slot) and isinstance(values, list) and len(values) == len(FEATURES)
    )
    features = []
    for index, (key, title, simd, address) in enumerate(FEATURES):
        state, activation, reason = (
            _account_state(values[index], slot) if valid else
            ("unavailable", None, "Finalized feature response or observation time is invalid.")
        )
        features.append({
            "key": key, "title": title, "simd": simd, "address": address,
            "state": state, "activated_at_slot": activation, "reason": reason,
        })
    observed = sum(row["state"] != "unavailable" for row in features)
    return {
        "available": observed > 0,
        "observed_at": stamp,
        "coverage_complete": observed == len(FEATURES),
        "coverage_numerator": observed,
        "coverage_denominator": len(FEATURES),
        "activated_feature_count": sum(row["state"] == "activated" for row in features),
        "source": {
            "method": "getMultipleAccounts", "commitment": "finalized",
            **growth.rpc_endpoint_reference(endpoint),
            "rpc_context_slot": slot if valid else None,
            "rpc_api_version": version if valid and isinstance(version, str) and version else None,
        },
        "metadata": dict(METADATA),
        "features": features,
        "note": NOTE,
    }


def collect_feature_accounts(endpoint: str, timeout: float = 8) -> dict[str, Any]:
    """One small finalized RPC request; no paid source or broad account scan."""
    raw = blocks.call("getMultipleAccounts", [
        [feature[3] for feature in FEATURES],
        {"commitment": "finalized", "encoding": "base64"},
    ], endpoint, timeout)
    return parse_feature_accounts(
        raw, endpoint, datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
