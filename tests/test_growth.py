"""Offline tests for tokenized-equity registry and supply transforms."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import growth  # noqa: E402


def rpc_supply(amount="100000000", decimals=8, ui_amount=1.0, ui_amount_string="1"):
    return {
        "context": {"slot": 321, "apiVersion": "2.3.7"},
        "value": {
            "amount": amount,
            "decimals": decimals,
            "uiAmount": ui_amount,
            "uiAmountString": ui_amount_string,
        },
    }


def rpc_mint_account(
    owner="TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    account_type="mint",
    extensions=None,
    program="spl-token-2022",
):
    if extensions is None:
        extensions = [{
            "extension": "scaledUiAmountConfig",
            "state": {
                "authority": "authority-a",
                "multiplier": "1.25",
                "newMultiplier": "1.5",
                "newMultiplierEffectiveTimestamp": 1_800_000_000,
            },
        }]
    return {
        "context": {"slot": 320, "apiVersion": "2.3.7"},
        "value": {
            "owner": owner,
            "data": {
                "program": program,
                "parsed": {
                    "type": account_type,
                    "info": {"extensions": extensions},
                },
            },
        },
    }


def token_registry_source(count=107):
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    groups = []
    for index in range(count):
        mint = "X" * 41 + alphabet[index // len(alphabet)] + alphabet[index % len(alphabet)]
        label = '"McDonald\'s variants"' if index == 0 else f"'Asset {index} variants'"
        groups.append(
            "{\n"
            f"  id: 'xstock-asset-{index}',\n"
            f"  label: {label},\n"
            "  addresses: [\n"
            f"    {{ address: '{mint}', label: 'xStock' }},\n"
            f"    {{ address: '{'Y' * 41 + alphabet[index // len(alphabet)] + alphabet[index % len(alphabet)]}', label: 'Ondo' }},\n"
            "  ],\n"
            "},"
        )
    return "export const XSTOCK_VARIANT_GROUPS = [\n" + "\n".join(groups) + "\n];"


def cached_supply(collected_at="2026-08-25T12:00:00+00:00", **overrides):
    observation = {
        "raw_amount": "100000000",
        "decimals": 8,
        "ui_amount": 1.0,
        "ui_amount_string": "1",
        "rpc_context_slot": 321,
        "rpc_api_version": "2.3.7",
        "collected_at": collected_at,
        "multiplier_provenance": {
            "source_method": "getAccountInfo(finalized,jsonParsed)",
            "program_id": growth.TOKEN_2022_PROGRAM_ID,
            "program": "spl-token-2022",
            "rpc_context_slot": 320,
            "extension": "scaledUiAmountConfig",
            "state": {
                "authority": "authority-a",
                "multiplier": "1.25",
                "newMultiplier": "1.5",
                "newMultiplierEffectiveTimestamp": 1_800_000_000,
            },
        },
    }
    observation.update(overrides)
    return observation


def validated_account_provenance():
    return {
        "source_method": "getAccountInfo(finalized,jsonParsed)",
        "program_id": growth.TOKEN_PROGRAM_ID,
        "program": "spl-token",
        "rpc_context_slot": 320,
    }


def empty_selected_stablecoins():
    return growth.summarize_selected_usd_stablecoin_supplies({})


class TestRegistry(unittest.TestCase):
    def test_pinned_token_registry_selects_exact_xstock_labels_and_expected_identity_counts(self):
        products = growth.parse_token_registry_xstocks(token_registry_source())
        self.assertEqual(len(products), 107)
        self.assertEqual(len({row["variant_group_id"] for row in products}), 107)
        self.assertEqual(len({row["solana_mint"] for row in products}), 107)
        self.assertTrue(all(len(row["solana_mint"]) == 43 for row in products))
        self.assertTrue(all(row["variant_label"] == "xStock" for row in products))
        self.assertNotIn("Y" * 41 + "11", {row["solana_mint"] for row in products})
        self.assertEqual(products[0]["name"], "McDonald's")

    def test_pinned_token_registry_fails_closed_on_count_case_or_identity_drift(self):
        self.assertEqual(growth.parse_token_registry_xstocks(token_registry_source(106)), [])
        wrong_case = token_registry_source().replace("label: 'xStock'", "label: 'xstock'", 1)
        self.assertEqual(growth.parse_token_registry_xstocks(wrong_case), [])
        duplicate = token_registry_source().replace(
            "id: 'xstock-asset-1'", "id: 'xstock-asset-0'", 1,
        )
        self.assertEqual(growth.parse_token_registry_xstocks(duplicate), [])

    def test_registry_fetch_has_pinned_mit_provenance_and_no_issuer_fallback(self):
        with patch("growth.fetch_text", return_value=token_registry_source()) as fetch:
            result = growth.fetch_xstocks_registry()
        fetch.assert_called_once_with(growth.TOKEN_REGISTRY_SOURCE_URL, 20)
        self.assertEqual(len(result["products"]), 107)
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(result["source_key"], growth.TOKEN_REGISTRY_SOURCE_KEY)
        self.assertEqual(result["source_revision"], growth.TOKEN_REGISTRY_SOURCE_REVISION)
        self.assertEqual(result["source_license"], "MIT")
        self.assertEqual(result["provenance"]["selection"], "address label exactly 'xStock'")

    def test_normalizes_official_api_assets_with_solana_deployments(self):
        payload = {"nodes": [
            {
                "id": "asset-1", "name": "Tesla xStock", "symbol": "TSLAx",
                "isTradingHalted": False,
                "deployments": [
                    {"network": "Ethereum", "address": "0x1"},
                    {"network": "Solana", "address": "mint-tsla", "supportsAtomicSwaps": True},
                ],
            },
            {"id": "asset-2", "name": "No Solana", "symbol": "NOSOLx",
             "deployments": [{"network": "Ethereum", "address": "0x2"}]},
        ]}
        self.assertEqual(growth.parse_xstocks_api_assets(payload), [{
            "slug": "asset-1", "name": "Tesla xStock", "symbol": "TSLAx",
            "solana_mint": "mint-tsla", "trading_halted": False,
            "supports_atomic_swaps": True,
        }])

    def test_extracts_only_products_with_a_solana_mint(self):
        html = """<html><script id="__NEXT_DATA__" type="application/json">{
          "props":{"pageProps":{"products":[
            {"slug":"nvda","name":"NVIDIA xStock","symbol":"NVDAx","addresses":{"solana":"mint-nvda"}},
            {"slug":"eth-only","name":"Other","symbol":"OTHER","addresses":{"ethereum":"0x1"}}
          ]}}
        }</script></html>"""
        products = growth.parse_xstocks_registry(html)
        self.assertEqual(products, [{
            "slug": "nvda", "name": "NVIDIA xStock", "symbol": "NVDAx", "solana_mint": "mint-nvda",
        }])

    def test_registry_schema_drift_is_unavailable_not_an_empty_market(self):
        for bad in (None, "", "<html></html>", "<script id='__NEXT_DATA__'>{bad}</script>"):
            self.assertEqual(growth.parse_xstocks_registry(bad), [])

    def test_paginated_fetch_preserves_partial_rows_but_marks_incomplete(self):
        first = {"nodes": [{"id": "one"}], "page": {"hasNextPage": True}}
        with patch("growth.fetch_json", side_effect=[first, None]):
            result = growth.fetch_paginated_nodes("https://example.test/assets")
        self.assertEqual(result["rows"], [{"id": "one"}])
        self.assertEqual(result["pages_requested"], 2)
        self.assertEqual(result["pages_succeeded"], 1)
        self.assertFalse(result["coverage_complete"])

    def test_registry_fetch_never_uses_issuer_api_or_page_fallback(self):
        html = """<script id="__NEXT_DATA__" type="application/json">{
          "props":{"pageProps":{"products":[
            {"slug":"nvda","name":"NVIDIA xStock","symbol":"NVDAx","addresses":{"solana":"mint-nvda"}}
          ]}}
        }</script>"""
        with patch("growth.fetch_paginated_nodes", return_value={
            "rows": [], "pages_requested": 1, "pages_succeeded": 0,
            "coverage_complete": False,
        }) as issuer_api, patch("growth.fetch_text", return_value=html) as fetch:
            result = growth.fetch_xstocks_registry()
        issuer_api.assert_not_called()
        fetch.assert_called_once_with(growth.TOKEN_REGISTRY_SOURCE_URL, 20)
        self.assertEqual(result["products"], [])
        self.assertEqual(result["source_kind"], "pinned official token registry")
        self.assertFalse(result["coverage_complete"])


class TestSupplyEvidence(unittest.TestCase):
    def test_token_supply_preflight_requires_finalized_scaled_token_2022_mint(self):
        with patch("growth._rpc_result", side_effect=[rpc_mint_account(), rpc_supply()]) as rpc:
            result = growth.fetch_token_supply("https://rpc.example", "mint-a")
        self.assertEqual([call.args[1] for call in rpc.call_args_list], [
            "getAccountInfo", "getTokenSupply",
        ])
        self.assertEqual(rpc.call_args_list[0].args[2], [
            "mint-a", {"commitment": "finalized", "encoding": "jsonParsed"},
        ])
        self.assertEqual(rpc.call_args_list[1].args[2], [
            "mint-a", {"commitment": "finalized"},
        ])
        self.assertEqual(
            result["multiplier_provenance"]["extension"], "scaledUiAmountConfig",
        )
        self.assertEqual(result["multiplier_provenance"]["rpc_context_slot"], 320)
        observation = growth.normalize_supply_observation(
            result, "2026-08-25T12:00:00+00:00",
        )
        self.assertIn("multiplier_provenance", observation)
        self.assertNotIn("account_provenance", observation)

    def test_token_supply_accepts_proven_legacy_spl_token_mint(self):
        account = rpc_mint_account(
            owner=growth.TOKEN_PROGRAM_ID, program="spl-token", extensions=[],
        )
        with patch("growth._rpc_result", side_effect=[account, rpc_supply()]) as rpc:
            result = growth.fetch_token_supply("https://rpc.example", "mint-a")
        self.assertEqual([call.args[1] for call in rpc.call_args_list], [
            "getAccountInfo", "getTokenSupply",
        ])
        self.assertNotIn("multiplier_provenance", result)
        self.assertEqual(result["account_provenance"], {
            **validated_account_provenance(), "rpc_api_version": "2.3.7",
        })
        observation = growth.normalize_supply_observation(
            result, "2026-08-25T12:00:00+00:00",
        )
        self.assertEqual(
            observation["account_provenance"], result["account_provenance"],
        )
        self.assertTrue(growth._valid_supply_observation(
            observation, require_scaled=True,
        ))
        equities = growth.build_tokenized_equities(
            [{"slug": "xstock-sui", "name": "SUI", "symbol": None,
              "solana_mint": "mint-a"}],
            {"mint-a": observation},
            observed_at_unix=growth._timestamp("2026-08-25T12:01:00+00:00"),
        )
        self.assertEqual(
            equities["all_assets"][0]["supply_account_provenance"],
            result["account_provenance"],
        )
        self.assertNotIn(
            "supply_multiplier_provenance", equities["all_assets"][0],
        )

    def test_token_supply_preflight_rejects_wrong_owner_account_type_or_extension(self):
        invalid = (
            rpc_mint_account(owner="11111111111111111111111111111111"),
            rpc_mint_account(account_type="account"),
            rpc_mint_account(extensions=[]),
        )
        for account in invalid:
            with self.subTest(account=account), \
                 patch("growth._rpc_result", return_value=account) as rpc:
                self.assertIsNone(growth.fetch_token_supply(
                    "https://rpc.example", "mint-a",
                ))
            self.assertEqual(rpc.call_count, 1)
            self.assertEqual(rpc.call_args.args[1], "getAccountInfo")

    def test_legacy_token_supply_rejects_malformed_or_wrong_program_accounts(self):
        invalid = (
            rpc_mint_account(
                owner=growth.TOKEN_PROGRAM_ID, program="spl-token-2022", extensions=[],
            ),
            rpc_mint_account(
                owner=growth.TOKEN_PROGRAM_ID, program="spl-token",
                account_type="account", extensions=[],
            ),
        )
        for account in invalid:
            with self.subTest(account=account), \
                 patch("growth._rpc_result", return_value=account) as rpc:
                self.assertIsNone(growth.fetch_token_supply(
                    "https://rpc.example", "mint-a",
                ))
            self.assertEqual(rpc.call_count, 1)
            self.assertEqual(rpc.call_args.args[1], "getAccountInfo")

    def test_scaled_multiplier_authority_is_present_optional_nonzero_pubkey(self):
        provenance = cached_supply()["multiplier_provenance"]
        for authority in (None, "authority-a"):
            with self.subTest(valid=authority):
                candidate = json.loads(json.dumps(provenance))
                candidate["state"]["authority"] = authority
                self.assertTrue(growth._valid_xstock_multiplier_provenance(candidate))
        for authority in ("missing", "", "   ", 7):
            with self.subTest(invalid=authority):
                candidate = json.loads(json.dumps(provenance))
                if authority == "missing":
                    del candidate["state"]["authority"]
                else:
                    candidate["state"]["authority"] = authority
                self.assertFalse(growth._valid_xstock_multiplier_provenance(candidate))

    def test_normalizes_exact_rpc_supply_provenance_without_inventing_multiplier(self):
        result = growth.normalize_supply_observation(
            rpc_supply(
                amount="32157773860220", decimals=8,
                ui_amount=321577.7386022, ui_amount_string="321577.7386022",
            ),
            "2026-08-25T12:00:00+00:00",
        )
        self.assertEqual(result["raw_amount"], "32157773860220")
        self.assertEqual(result["decimals"], 8)
        self.assertEqual(result["ui_amount"], 321577.7386022)
        self.assertEqual(result["ui_amount_string"], "321577.7386022")
        self.assertEqual(result["rpc_context_slot"], 321)
        self.assertEqual(result["rpc_api_version"], "2.3.7")
        self.assertEqual(result["collected_at"], "2026-08-25T12:00:00+00:00")
        self.assertNotIn("multiplier_provenance", result)

    def test_retains_multiplier_provenance_only_when_the_source_supplies_it(self):
        raw = rpc_supply()
        raw["multiplier_provenance"] = {
            "extension": "scaled-ui-amount", "activation_slot": 300,
        }
        result = growth.normalize_supply_observation(
            raw, "2026-08-25T12:00:00+00:00",
        )
        self.assertEqual(result["multiplier_provenance"], raw["multiplier_provenance"])

    def test_rejects_supply_without_exact_amount_or_rpc_context(self):
        for raw in (
            None,
            {"context": {"slot": 1, "apiVersion": "2"}, "value": {}},
            {"context": {"slot": 1}, "value": rpc_supply()["value"]},
            {"context": rpc_supply()["context"],
             "value": {**rpc_supply()["value"], "amount": "not-an-integer"}},
        ):
            self.assertIsNone(growth.normalize_supply_observation(
                raw, "2026-08-25T12:00:00+00:00",
            ))
        self.assertIsNone(growth.normalize_supply_observation(
            rpc_supply(), "2026-08-25T12:00:00",
        ))
        self.assertIsNone(growth.normalize_supply_observation(
            rpc_supply(decimals=256), "2026-08-25T12:00:00+00:00",
        ))

    def test_oversized_rpc_ui_amount_degrades_instead_of_raising(self):
        self.assertIsNone(growth.normalize_supply_observation(
            rpc_supply(ui_amount=10**10_000),
            "2026-08-25T12:00:00+00:00",
        ))


class TestSelectedStablecoinSupply(unittest.TestCase):
    def observations(self, count=4):
        result = {}
        for index, asset in enumerate(growth.SELECTED_USD_STABLECOINS[:count], 1):
            result[asset["mint"]] = cached_supply(
                raw_amount=str(index * 100), decimals=2,
                ui_amount=float(index), ui_amount_string=str(index),
                rpc_context_slot=320 + index,
                collected_at=f"2026-08-25T12:00:0{index}+00:00",
                account_provenance=validated_account_provenance(),
            )
        return result

    def test_full_four_mint_summary_uses_decimal_total_and_shares(self):
        summary = growth.summarize_selected_usd_stablecoin_supplies(
            self.observations(),
        )
        self.assertTrue(summary["available"])
        self.assertEqual(summary["coverage_numerator"], 4)
        self.assertEqual(summary["coverage_denominator"], 4)
        self.assertEqual(summary["selected_total_supply_decimal"], "10.00")
        self.assertEqual(
            [row["share_of_selected_total"] for row in summary["assets"]],
            ["0.1", "0.2", "0.3", "0.4"],
        )
        self.assertEqual(summary["assets"][0]["rpc_context_slot"], 321)
        self.assertEqual(summary["assets"][0]["rpc_ui_amount_string"], "1")
        self.assertEqual(summary["slot_range"], {"first": 321, "last": 324})
        self.assertEqual(summary["oldest_observation_at"], "2026-08-25T12:00:01+00:00")
        self.assertEqual(summary["newest_observation_at"], "2026-08-25T12:00:04+00:00")
        self.assertEqual(
            summary["assets"][0]["collected_at"], "2026-08-25T12:00:01+00:00",
        )
        self.assertIsNone(summary["assets"][0]["event_time"])
        self.assertEqual(summary["registry_source"]["source_revision"],
                         growth.SELECTED_STABLECOIN_SOURCE_REVISION)
        self.assertEqual(summary["registry_source"]["source_license"], "GPL-3.0")

    def test_partial_summary_reports_exact_n_of_four_without_total_or_shares(self):
        summary = growth.summarize_selected_usd_stablecoin_supplies(
            self.observations(3),
        )
        self.assertFalse(summary["available"])
        self.assertEqual(summary["state"], "partial")
        self.assertEqual(summary["coverage_numerator"], 3)
        self.assertEqual(summary["coverage_denominator"], 4)
        self.assertNotIn("selected_total_supply_decimal", summary)
        self.assertTrue(all(
            "share_of_selected_total" not in row for row in summary["assets"]
        ))
        limitations = summary["limitations"].lower()
        self.assertIn("exactly four selected", limitations)
        self.assertIn("not circulating supply", limitations)
        self.assertIn("does not represent all stablecoins", limitations)
        self.assertIn("event time is unavailable", limitations)
        self.assertEqual(summary["universe_coverage"], "unknown")
        self.assertEqual(summary["slot_range"], {"first": 321, "last": 323})

    def test_fetch_uses_validated_rpc_path_and_keeps_failures_as_gaps(self):
        endpoint = "https://user:password@rpc.example/v2/secret?api-key=SUPERSECRET"
        with patch("growth._fetch_validated_token_supply", side_effect=[
            {**rpc_supply(amount=str(index * 100), decimals=2,
                          ui_amount=float(index), ui_amount_string=str(index)),
             "account_provenance": validated_account_provenance()}
            for index in range(1, 4)
        ] + [None]) as fetch, patch("growth.time.time", return_value=1000):
            summary = growth.fetch_selected_usd_stablecoin_supplies(
                endpoint,
            )
        self.assertEqual(fetch.call_count, 4)
        self.assertTrue(all(call.kwargs["require_scaled"] is False
                            for call in fetch.call_args_list))
        self.assertEqual(summary["coverage_numerator"], 3)
        self.assertEqual(summary["coverage_denominator"], 4)
        self.assertNotIn("selected_total_supply_decimal", summary)
        self.assertEqual(summary["rpc"], {
            "endpoint": growth.CUSTOM_RPC_ENDPOINT_LABEL,
            "endpoint_identity": growth.rpc_endpoint_identity(endpoint),
            "methods": ["getAccountInfo", "getTokenSupply"],
            "commitment": "finalized",
        })
        self.assertNotIn("SUPERSECRET", json.dumps(summary))
        self.assertNotIn("password", json.dumps(summary))

    def test_malformed_rpc_decimals_degrade_to_an_unavailable_mint(self):
        mint = growth.SELECTED_USD_STABLECOINS[0]["mint"]
        summary = growth.summarize_selected_usd_stablecoin_supplies({
            mint: cached_supply(
                decimals=10**10,
                account_provenance=validated_account_provenance(),
            ),
        })
        self.assertEqual(summary["coverage_numerator"], 0)
        self.assertEqual(summary["state"], "unavailable")

    def test_builds_supply_evidence_but_keeps_price_valuation_unavailable(self):
        products = [
            {"slug": "nvda", "name": "NVIDIA xStock", "symbol": "NVDAx", "solana_mint": "mint-nvda"},
            {"slug": "missing", "name": "Missing Supply", "symbol": "MISSx", "solana_mint": "mint-missing"},
        ]
        observations = {
            "mint-nvda": cached_supply(
                raw_amount="32157773860220", ui_amount=321577.7386022,
                ui_amount_string="321577.7386022",
            ),
        }
        summary = growth.build_tokenized_equities(
            products, observations, observed_at_unix=1_787_659_300,
        )
        self.assertTrue(summary["available"])
        self.assertEqual(summary["registry_asset_count"], 2)
        self.assertEqual(summary["supply_observed_asset_count"], 1)
        self.assertEqual(summary["fresh_supply_asset_count"], 1)
        self.assertEqual(summary["valued_asset_count"], 0)
        self.assertEqual(summary["assets"][0]["mint"], "mint-nvda")
        self.assertEqual(summary["assets"][0]["supply"], 321577.7386022)
        self.assertEqual(summary["assets"][0]["supply_raw_amount"], "32157773860220")
        self.assertEqual(summary["assets"][0]["supply_rpc_ui_amount"], 321577.7386022)
        self.assertEqual(
            summary["assets"][0]["supply_rpc_ui_amount_string"], "321577.7386022",
        )
        self.assertEqual(summary["assets"][0]["supply_context_slot"], 321)
        self.assertEqual(summary["assets"][0]["supply_rpc_api_version"], "2.3.7")
        self.assertEqual(summary["assets"][0]["supply_freshness"], "fresh")
        self.assertFalse(summary["valuation"]["available"])
        self.assertEqual(summary["valuation"]["scope"], "unavailable")
        self.assertNotIn("price_usd", summary["assets"][0])
        self.assertNotIn("issuance_value_usd", summary["assets"][0])
        self.assertEqual(len(summary["all_assets"]), 2)
        self.assertEqual(summary["all_assets"][0]["mint"], "mint-nvda")

    def test_supply_freshness_boundary_is_six_hours(self):
        products = [
            {"slug": "fresh", "name": "Fresh", "symbol": "FRESHx", "solana_mint": "mint-fresh"},
            {"slug": "stale", "name": "Stale", "symbol": "STALEx", "solana_mint": "mint-stale"},
        ]
        observations = {
            "mint-fresh": cached_supply(collected_at="2026-08-25T06:00:00+00:00"),
            "mint-stale": cached_supply(collected_at="2026-08-25T05:59:59+00:00"),
        }
        summary = growth.build_tokenized_equities(
            products, observations, observed_at_unix=1_787_659_200,
        )
        by_symbol = {asset["symbol"]: asset for asset in summary["all_assets"]}
        self.assertEqual(by_symbol["FRESHx"]["supply_freshness"], "fresh")
        self.assertEqual(by_symbol["STALEx"]["supply_freshness"], "stale")
        self.assertEqual(summary["fresh_supply_asset_count"], 1)

    def test_untrusted_or_future_xstock_supply_is_never_published(self):
        products = [{
            "slug": "a", "name": "Asset", "symbol": "Ax", "solana_mint": "mint-a",
        }]
        for observation in (
            cached_supply(multiplier_provenance={}),
            cached_supply(collected_at="2026-08-25T12:00:01+00:00"),
        ):
            with self.subTest(observation=observation):
                summary = growth.build_tokenized_equities(
                    products, {"mint-a": observation},
                    observed_at_unix=1_787_659_200,
                )
                self.assertFalse(summary["available"])
                self.assertEqual(summary["supply_observed_asset_count"], 0)
                self.assertIsNone(summary["assets"][0]["supply"])
                self.assertEqual(summary["assets"][0]["supply_freshness"], "unavailable")
                coverage = growth.summarize_supply_coverage(
                    1, ["mint-a"], {
                        "version": growth.SUPPLY_STATE_VERSION,
                        "cursor_mint": "mint-a",
                        "observations": {"mint-a": observation},
                    }, 1_787_659_200, 0, 0, True,
                )
                self.assertEqual(coverage["observed_asset_count"], 0)
                self.assertEqual(coverage["coverage_numerator"], 0)


class TestSupplyState(unittest.TestCase):
    def test_public_endpoint_reference_preserves_only_known_keyless_urls(self):
        self.assertEqual(growth.rpc_endpoint_reference(
            "https://api.mainnet.solana.com",
        ), {
            "endpoint": "https://api.mainnet.solana.com",
            "endpoint_identity": growth.rpc_endpoint_identity(
                "https://api.mainnet.solana.com",
            ),
        })
        secret = "https://tenant:password@rpc.example/v2/APISECRET?api-key=QUERYSECRET"
        reference = growth.rpc_endpoint_reference(secret)
        self.assertEqual(reference["endpoint"], "custom RPC endpoint")
        self.assertEqual(reference["endpoint_identity"], growth.rpc_endpoint_identity(secret))
        self.assertNotIn("password", json.dumps(reference))
        self.assertNotIn("APISECRET", json.dumps(reference))
        self.assertNotIn("QUERYSECRET", json.dumps(reference))

    def test_changed_rpc_endpoint_discards_cached_observations_before_failed_queries(self):
        products = [{
            "slug": "xstock-a", "name": "Asset", "symbol": None,
            "solana_mint": "mint-a",
        }]
        mainnet_state = {
            "version": growth.SUPPLY_STATE_VERSION,
            "rpc_endpoint_identity": growth.rpc_endpoint_identity(
                "https://api.mainnet.solana.com",
            ),
            "cursor_mint": "mint-a",
            "updated_at": "2026-08-25T12:00:00+00:00",
            "observations": {"mint-a": cached_supply()},
        }
        devnet = "https://devnet.example/rpc?api-key=do-not-persist"
        with patch("growth.time.time", return_value=1_787_659_200), \
             patch("growth.fetch_xstocks_registry", return_value={
                 "products": products, "coverage_complete": True,
             }), patch("growth.fetch_token_supply", return_value=None) as fetch, \
             patch("growth.fetch_dex_pairs", return_value={
                 "rows": [], "batches_expected": 1, "batches_requested": 1,
                 "batches_succeeded": 1,
             }), patch("growth.fetch_selected_usd_stablecoin_supplies",
                       return_value=empty_selected_stablecoins()), \
             patch("growth.fetch_json", return_value=None):
            result, next_state = growth.collect_growth(
                devnet, supply_state=mainnet_state,
            )

        self.assertFalse(result["tokenized_equities"]["available"])
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[:2], (devnet, "mint-a"))
        self.assertEqual(result["sources"]["supply"]["observed_asset_count"], 0)
        self.assertEqual(
            result["sources"]["supply"]["rpc_endpoint_identity"],
            growth.rpc_endpoint_identity(devnet),
        )
        self.assertNotIn("endpoint_identity", result["sources"]["supply"])
        self.assertEqual(next_state["observations"], {})
        self.assertEqual(
            next_state["rpc_endpoint_identity"], growth.rpc_endpoint_identity(devnet),
        )
        self.assertNotIn(devnet, json.dumps(next_state))

    def test_cursor_is_stable_across_registry_reorder_and_cursor_deletion(self):
        state = {"version": 1, "cursor_mint": "mint-b", "observations": {}}
        expected = ["mint-c", "mint-a", "mint-b"]
        self.assertEqual(growth.supply_query_order(
            ["mint-c", "mint-b", "mint-a"], state,
        ), expected)
        self.assertEqual(growth.supply_query_order(
            ["mint-a", "mint-c", "mint-b"], state,
        ), expected)
        self.assertEqual(growth.supply_query_order(
            ["mint-a", "mint-c"], state,
        ), ["mint-c", "mint-a"])

    def test_coverage_reports_every_requested_numerator_and_window(self):
        state = {
            "version": 1,
            "cursor_mint": "mint-b",
            "observations": {
                "mint-a": cached_supply("2026-08-25T11:59:00+00:00"),
                "mint-b": cached_supply("2026-08-25T05:00:00+00:00"),
                "deleted-mint": cached_supply("2026-08-25T11:00:00+00:00"),
            },
        }
        coverage = growth.summarize_supply_coverage(
            registry_asset_count=2,
            eligible_mints=["mint-a", "mint-b"],
            state=state,
            observed_at_unix=1_787_659_200,
            queried_this_run_asset_count=1,
            successful_this_run_asset_count=1,
            registry_complete=True,
        )
        self.assertEqual(coverage["registry_asset_count"], 2)
        self.assertEqual(coverage["eligible_asset_count"], 2)
        self.assertEqual(coverage["queried_this_run_asset_count"], 1)
        self.assertEqual(coverage["successful_this_run_asset_count"], 1)
        self.assertEqual(coverage["attempt_scope"], "current collection run")
        self.assertNotIn("queried_asset_count", coverage)
        self.assertEqual(coverage["fresh_asset_count"], 1)
        self.assertEqual(coverage["valued_asset_count"], 0)
        self.assertEqual(coverage["coverage_numerator"], 2)
        self.assertEqual(coverage["coverage_denominator"], 2)
        self.assertEqual(coverage["oldest_observation_at"], "2026-08-25T05:00:00+00:00")
        self.assertEqual(coverage["newest_observation_at"], "2026-08-25T11:59:00+00:00")
        self.assertEqual(coverage["observation_span_seconds"], 25_140)
        self.assertTrue(coverage["sweep_complete"])
        self.assertEqual(coverage["scope"], "registry-wide")

    def test_incomplete_or_older_than_72h_never_claims_registry_wide(self):
        state = {
            "version": 1,
            "cursor_mint": "mint-a",
            "observations": {
                "mint-a": cached_supply("2026-08-22T11:59:59+00:00"),
            },
        }
        coverage = growth.summarize_supply_coverage(
            2, ["mint-a", "mint-b"], state, 1_787_659_200, 0, 0, True,
        )
        self.assertFalse(coverage["sweep_complete"])
        self.assertEqual(coverage["coverage_numerator"], 0)
        self.assertEqual(coverage["scope"], "observed subset")

    def test_state_round_trip_is_versioned_and_atomic_contract_is_json(self):
        state = {
            "version": growth.SUPPLY_STATE_VERSION,
            "rpc_endpoint_identity": growth.rpc_endpoint_identity(
                "https://api.mainnet.solana.com",
            ),
            "cursor_mint": "mint-a",
            "updated_at": "2026-08-25T12:00:00+00:00",
            "observations": {"mint-a": cached_supply()},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "xstocks-supply.json"
            growth.save_supply_state(state, path)
            self.assertEqual(growth.load_supply_state(path), state)
            json.loads(path.read_text(encoding="utf-8"))

    def test_malformed_existing_state_fails_closed_instead_of_being_overwritten(self):
        for payload in (
            {"version": 99, "observations": {}},
            {"version": growth.SUPPLY_STATE_VERSION,
             "rpc_endpoint_identity": 7, "observations": {}},
        ):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "xstocks-supply.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                state = growth.load_supply_state(path)
                self.assertIn("load_error", state)
                self.assertEqual(state["observations"], {})

    def test_legacy_state_discards_unbound_observations_instead_of_guessing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "xstocks-supply.json"
            path.write_text(json.dumps({
                "version": 1,
                "cursor_mint": "mint-a",
                "observations": {"mint-a": cached_supply()},
            }), encoding="utf-8")
            state = growth.load_supply_state(path)
        self.assertEqual(state["version"], growth.SUPPLY_STATE_VERSION)
        self.assertIsNone(state["rpc_endpoint_identity"])
        self.assertEqual(state["observations"], {})
        self.assertIn("no RPC endpoint identity", state["reset_reason"])


class TestMarketCoverage(unittest.TestCase):
    def test_dex_fetch_preserves_malformed_rows_for_truthful_summary_counts(self):
        valid = {
            "pairAddress": "pair-1", "dexId": "orca",
            "baseToken": {"address": "mint-a"},
            "quoteToken": {"address": "usdc"},
            "volume": {"h24": 10}, "liquidity": {"usd": 20},
        }
        with patch("growth.fetch_json", return_value=[valid, None, "broken"]):
            fetched = growth.fetch_dex_pairs(["mint-a"])
        summary = growth.summarize_dex_volume(fetched, {"mint-a"})

        self.assertTrue(summary["transport_complete"])
        self.assertTrue(summary["available"])
        self.assertTrue(summary["partial"])
        self.assertEqual(summary["source_row_count"], 3)
        self.assertEqual(summary["invalid_row_count"], 2)

    def test_reversed_account_and_supply_slots_are_rejected_before_publication(self):
        account = rpc_mint_account()
        account["context"]["slot"] = 400
        supply = rpc_supply()
        supply["context"]["slot"] = 399
        with patch("growth._rpc_result", side_effect=[account, supply]):
            result = growth._fetch_validated_token_supply(
                "https://api.mainnet.solana.com", "mint-a", require_scaled=True,
            )
        self.assertIsNone(result)

    def test_dex_volume_deduplicates_pairs_and_declares_partial_market_scope(self):
        pairs = {
            "rows": [
                {"pairAddress": "pair-1", "dexId": "orca",
                 "baseToken": {"address": "mint-a", "symbol": "Ax"},
                 "quoteToken": {"address": "usdc", "symbol": "USDC"},
                 "volume": {"h24": 125.5}, "liquidity": {"usd": 1000},
                 "url": "https://dexscreener.com/solana/pair-1"},
                {"pairAddress": "pair-1", "dexId": "orca",
                 "baseToken": {"address": "mint-a", "symbol": "Ax"},
                 "quoteToken": {"address": "usdc", "symbol": "USDC"},
                 "volume": {"h24": 125.5}, "liquidity": {"usd": 1000}},
                {"pairAddress": "pair-2", "dexId": "raydium",
                 "baseToken": {"address": "mint-b", "symbol": "Bx"},
                 "quoteToken": {"address": "mint-a", "symbol": "Ax"},
                 "volume": {"h24": 74.5}, "liquidity": {"usd": 500}},
            ],
            "batches_expected": 2, "batches_requested": 2, "batches_succeeded": 2,
        }
        summary = growth.summarize_dex_volume(pairs, {"mint-a", "mint-b"})
        self.assertTrue(summary["available"])
        self.assertEqual(summary["volume_24h_usd"], 200.0)
        self.assertEqual(summary["pair_count"], 2)
        self.assertEqual(summary["source_row_count"], 3)
        self.assertEqual(summary["exact_duplicate_row_count"], 1)
        self.assertEqual(summary["conflicting_pair_count"], 0)
        self.assertEqual(summary["invalid_row_count"], 0)
        self.assertEqual(summary["unrelated_row_count"], 0)
        self.assertEqual(summary["volume_covered_pair_count"], 2)
        self.assertEqual(summary["volume_invalid_pair_count"], 0)
        self.assertEqual(summary["liquidity_covered_pair_count"], 2)
        self.assertEqual(summary["liquidity_invalid_pair_count"], 0)
        self.assertEqual(summary["assets_with_pairs"], 2)
        self.assertEqual(summary["liquidity_usd"], 1500.0)
        self.assertTrue(summary["transport_complete"])
        self.assertTrue(summary["partial"])
        self.assertNotIn("coverage_complete", summary)
        self.assertNotIn("top_pairs", summary)
        self.assertEqual(summary["market_coverage"], "partial")
        self.assertIn("RFQ fills", summary["exclusions"])
        self.assertIn("centralized venues", summary["exclusions"])

    def test_dex_missing_invalid_or_negative_volume_never_becomes_zero(self):
        for value in (None, "12", -1, float("inf")):
            with self.subTest(value=value):
                pair = {
                    "pairAddress": "pair-1", "dexId": "orca",
                    "baseToken": {"address": "mint-a", "symbol": "Ax"},
                    "quoteToken": {"address": "usdc", "symbol": "USDC"},
                    "liquidity": {"usd": 100},
                }
                if value is not None:
                    pair["volume"] = {"h24": value}
                summary = growth.summarize_dex_volume({
                    "rows": [pair], "batches_expected": 1,
                    "batches_requested": 1, "batches_succeeded": 1,
                }, {"mint-a"})
                self.assertFalse(summary["available"])
                self.assertTrue(summary["partial"])
                self.assertNotIn("volume_24h_usd", summary)
                self.assertEqual(summary["pair_count"], 1)
                self.assertEqual(summary["volume_covered_pair_count"], 0)
                self.assertEqual(summary["volume_invalid_pair_count"], 1)
                self.assertEqual(summary["liquidity_covered_pair_count"], 1)
                self.assertEqual(summary["liquidity_usd"], 100.0)
                self.assertIn("0 of 1", summary["reason"])

    def test_dex_conflicting_duplicate_pair_fails_aggregate_closed(self):
        summary = growth.summarize_dex_volume({
            "rows": [
                {"pairAddress": "pair-1", "dexId": "orca",
                 "baseToken": {"address": "mint-a"},
                 "quoteToken": {"address": "usdc"},
                 "volume": {"h24": 1}, "liquidity": {"usd": 2}},
                {"pairAddress": "pair-1", "dexId": "orca",
                 "baseToken": {"address": "mint-a"},
                 "quoteToken": {"address": "usdc"},
                 "volume": {"h24": 999}, "liquidity": {"usd": 888}},
            ],
            "batches_expected": 1, "batches_requested": 1,
            "batches_succeeded": 1,
        }, {"mint-a"})
        self.assertFalse(summary["available"])
        self.assertTrue(summary["partial"])
        self.assertEqual(summary["conflicting_pair_count"], 1)
        self.assertEqual(summary["pair_count"], 0)
        self.assertNotIn("volume_24h_usd", summary)
        self.assertNotIn("liquidity_usd", summary)
        self.assertIn("conflicting", summary["reason"])

    def test_dex_unrelated_and_unidentified_rows_are_invalid_partial_evidence(self):
        summary = growth.summarize_dex_volume({
            "rows": [
                {"pairAddress": "pair-1", "dexId": "orca",
                 "baseToken": {"address": "mint-a"},
                 "quoteToken": {"address": "usdc"},
                 "volume": {"h24": 10}, "liquidity": {"usd": 20}},
                {"pairAddress": "pair-unrelated", "dexId": "orca",
                 "baseToken": {"address": "other-a"},
                 "quoteToken": {"address": "other-b"},
                 "volume": {"h24": 1}, "liquidity": {"usd": 2}},
                {"baseToken": {"address": "mint-a"},
                 "quoteToken": {"address": "usdc"},
                 "volume": {"h24": 3}, "liquidity": {"usd": 4}},
                {"pairAddress": "pair-malformed",
                 "baseToken": {"address": []}, "quoteToken": "bad",
                 "volume": {"h24": 5}, "liquidity": {"usd": 6}},
            ],
            "batches_expected": 1, "batches_requested": 1,
            "batches_succeeded": 1,
        }, {"mint-a"})
        self.assertTrue(summary["available"])
        self.assertTrue(summary["partial"])
        self.assertEqual(summary["volume_24h_usd"], 10.0)
        self.assertEqual(summary["liquidity_usd"], 20.0)
        self.assertEqual(summary["source_row_count"], 4)
        self.assertEqual(summary["invalid_row_count"], 3)
        self.assertEqual(summary["unrelated_row_count"], 1)
        self.assertEqual(summary["pair_count"], 1)

    def test_dex_liquidity_is_independent_and_never_synthesizes_zero(self):
        summary = growth.summarize_dex_volume({
            "rows": [{
                "pairAddress": "pair-1", "dexId": "orca",
                "baseToken": {"address": "mint-a"},
                "quoteToken": {"address": "usdc"},
                "volume": {"h24": 10},
            }],
            "batches_expected": 1, "batches_requested": 1,
            "batches_succeeded": 1,
        }, {"mint-a"})
        self.assertTrue(summary["available"])
        self.assertTrue(summary["partial"])
        self.assertEqual(summary["volume_24h_usd"], 10.0)
        self.assertNotIn("liquidity_usd", summary)
        self.assertEqual(summary["liquidity_covered_pair_count"], 0)
        self.assertEqual(summary["liquidity_invalid_pair_count"], 1)

    def test_dex_transport_fails_closed_without_expected_batch_evidence(self):
        pair = {
            "pairAddress": "pair-1", "dexId": "orca",
            "baseToken": {"address": "mint-a", "symbol": "Ax"},
            "quoteToken": {"address": "usdc", "symbol": "USDC"},
            "volume": {"h24": 125.5}, "liquidity": {"usd": 1000},
        }
        missing_expected = growth.summarize_dex_volume({
            "rows": [pair], "batches_requested": 1, "batches_succeeded": 1,
        }, {"mint-a"})
        self.assertFalse(missing_expected["transport_complete"])
        self.assertEqual(missing_expected["market_coverage"], "not established")
        self.assertFalse(missing_expected["available"])

        failed = growth.summarize_dex_volume({
            "rows": [], "batches_expected": 1,
            "batches_requested": 1, "batches_succeeded": 0,
        }, {"mint-a"})
        self.assertFalse(failed["transport_complete"])
        self.assertEqual(failed["market_coverage"], "not established")
        self.assertIn("transport incomplete", failed["reason"])

    def test_activity_benchmark_uses_latest_day_with_broad_provider_coverage(self):
        raw = {
            "generatedAt": "2026-08-23T11:50:17Z",
            "rows": [
                {"date": "2026-08-21", "metricName": "Active Addresses", "unit": "Count", "providerName": "A", "value": 100},
                {"date": "2026-08-21", "metricName": "Active Addresses", "unit": "Count", "providerName": "B", "value": 200},
                {"date": "2026-08-21", "metricName": "Active Addresses", "unit": "Count", "providerName": "C", "value": 300},
                {"date": "2026-08-21", "metricName": "Active Addresses", "unit": "Count", "providerName": "D", "value": 400},
                {"date": "2026-08-22", "metricName": "Active Addresses", "unit": "Count", "providerName": "A", "value": 150},
                {"date": "2026-08-22", "metricName": "Active Addresses", "unit": "Count", "providerName": "B", "value": 250},
            ],
        }
        summary = growth.summarize_provider_benchmark(raw, "Active Addresses")
        self.assertTrue(summary["available"])
        self.assertFalse(summary["canonical"])
        self.assertEqual(summary["date"], "2026-08-21")
        self.assertEqual(summary["provider_count"], 4)
        self.assertEqual(summary["minimum"], 100)
        self.assertEqual(summary["maximum"], 400)
        self.assertNotIn("median", summary)
        self.assertEqual(summary["semantic_metric_id"], "stablecoin_active_address_provider_range")
        self.assertEqual(summary["display_name"], "Stablecoin active-address provider range")
        self.assertEqual(summary["source_label"], "Active Addresses")
        self.assertEqual(
            summary["scope"],
            "provider observations for Solana stablecoin activity, not network-wide DAA or unique humans",
        )

    def test_activity_benchmark_retains_all_dated_provider_observations(self):
        raw = {
            "generatedAt": "2026-08-23T11:50:17Z",
            "rows": [
                {"date": "2026-08-22", "metricName": "Active Addresses", "providerName": "B", "value": 220},
                {"date": "2026-08-21", "metricName": "Active Addresses", "providerName": "A", "value": 100},
                {"date": "2026-08-22", "metricName": "Active Addresses", "providerName": "A", "value": 120},
                {"date": "2026-08-21", "metricName": "Active Addresses", "providerName": "B", "value": 200},
                {"date": "2026-08-21", "metricName": "Active Addresses", "providerName": "C", "value": 300},
                {"date": "2026-08-21", "metricName": "Active Addresses", "providerName": "C", "value": "300"},
                {"date": "2026-08-22", "metricName": "Active Addresses", "providerName": "C", "value": 320},
                {"date": "2026-08-22", "metricName": "Fee Payers", "providerName": "A", "value": 999},
            ],
        }
        summary = growth.summarize_provider_benchmark(raw, "Active Addresses")
        self.assertTrue(summary["available"])
        self.assertFalse(summary["partial"])
        self.assertEqual(summary["observed_row_count"], 6)
        self.assertEqual(summary["observed_date_count"], 2)
        self.assertEqual(summary["observed_provider_count"], 3)
        self.assertEqual(summary["oldest_date"], "2026-08-21")
        self.assertEqual(summary["newest_date"], "2026-08-22")
        self.assertEqual(summary["exact_duplicate_row_count"], 1)
        self.assertEqual(summary["conflicting_identity_count"], 0)
        self.assertEqual(summary["invalid_row_count"], 0)
        self.assertEqual(
            [(row["date"], row["provider"], row["value"])
             for row in summary["provider_observations"]],
            [
                ("2026-08-21", "A", 100),
                ("2026-08-21", "B", 200),
                ("2026-08-21", "C", 300),
                ("2026-08-22", "A", 120),
                ("2026-08-22", "B", 220),
                ("2026-08-22", "C", 320),
            ],
        )

    def test_activity_benchmark_omits_conflicts_and_marks_invalid_rows_partial(self):
        raw = {"rows": [
            {"date": "2026-08-21", "metricName": "Fee Payers", "providerName": "A", "value": 100},
            {"date": "2026-08-21", "metricName": "Fee Payers", "providerName": "A", "value": 101},
            {"date": "2026-08-21", "metricName": "Fee Payers", "providerName": "B", "value": 200},
            {"date": "2026-08-21", "metricName": "Fee Payers", "providerName": "C", "value": 300},
            {"date": "2026-08-21", "metricName": "Fee Payers", "providerName": "D", "value": 400},
            {"date": "2026-02-30", "metricName": "Fee Payers", "providerName": "E", "value": 500},
            {"date": "2026-08-21", "metricName": "Fee Payers", "providerName": " ", "value": 600},
            {"date": "2026-08-21", "metricName": "Fee Payers", "providerName": "F", "value": -1},
        ]}
        summary = growth.summarize_provider_benchmark(raw, "Fee Payers")
        self.assertTrue(summary["available"])
        self.assertTrue(summary["partial"])
        self.assertEqual(summary["invalid_row_count"], 3)
        self.assertEqual(summary["conflicting_identity_count"], 1)
        self.assertEqual(summary["conflicts"], [{"date": "2026-08-21", "provider": "A"}])
        self.assertEqual(summary["observed_row_count"], 3)
        self.assertEqual([row["provider"] for row in summary["provider_observations"]], ["B", "C", "D"])

    def test_fee_payer_benchmark_names_transaction_initiator_scope(self):
        raw = {"rows": [
            {"date": "2026-08-21", "metricName": "Fee Payers", "providerName": name, "value": value}
            for name, value in (("A", 100), ("B", 200), ("C", 300))
        ]}
        summary = growth.summarize_provider_benchmark(raw, "Fee Payers")
        self.assertEqual(summary["semantic_metric_id"], "transaction_initiator_provider_range")
        self.assertEqual(summary["display_name"], "Transaction-initiator provider range")
        self.assertEqual(summary["source_label"], "Fee Payers")
        self.assertEqual(
            summary["scope"],
            "provider observations of transaction initiators, not unique humans",
        )

    def test_reserve_summary_preserves_missing_rows_and_positive_coverage(self):
        summary = growth.summarize_proof_of_reserves({
            "rows": [
                {"symbol": "Ax", "timestamp": "2026-08-23T00:00:00Z", "sharesHeld": "10", "circulatingSupply": "9"},
                {"symbol": "Bx", "timestamp": None, "sharesHeld": "0", "circulatingSupply": "0"},
            ],
            "pages_requested": 1, "pages_succeeded": 1,
        })
        self.assertTrue(summary["available"])
        self.assertEqual(summary["asset_count"], 2)
        self.assertEqual(summary["timestamped_asset_count"], 1)
        self.assertEqual(summary["positive_reserve_asset_count"], 1)
        self.assertTrue(summary["coverage_complete"])

    def test_collection_records_supply_attempt_failures_and_keeps_other_sources(self):
        products = [{"slug": "a", "name": "Asset", "symbol": "Ax", "solana_mint": "mint-a"}]
        dex = {"rows": [{
            "pairAddress": "pair-a", "dexId": "orca",
            "baseToken": {"address": "mint-a", "symbol": "Ax"},
            "quoteToken": {"address": "usdc", "symbol": "USDC"},
            "volume": {"h24": 10}, "liquidity": {"usd": 100},
        }], "batches_expected": 1, "batches_requested": 1, "batches_succeeded": 1}
        with patch("growth.time.time", return_value=1000), \
             patch("growth.fetch_xstocks_registry", return_value={
                 "products": products, "source_url": growth.REGISTRY_API_URL,
                 "source_kind": "documented public API", "coverage_complete": True,
             }), patch("growth.fetch_token_supply", return_value=None), \
             patch("growth.fetch_dex_pairs", return_value=dex) as dex_fetch, \
             patch("growth.fetch_selected_usd_stablecoin_supplies",
                   return_value=empty_selected_stablecoins()), \
             patch("growth.fetch_paginated_nodes") as issuer_api, \
             patch("growth.fetch_json") as provider_api:
            result, next_state = growth.collect_growth("https://rpc.example")
        issuer_api.assert_not_called()
        provider_api.assert_called_once_with(growth.SOLANA_DATA_URL, 12)
        dex_fetch.assert_not_called()
        self.assertTrue(result["available"])
        self.assertFalse(result["tokenized_equities"]["available"])
        self.assertFalse(result["tokenized_equities"]["volume"]["available"])
        self.assertTrue(result["sources"]["dex_volume"]["held"])
        self.assertIn(
            "automated public redistribution",
            result["tokenized_equities"]["volume"]["reason"],
        )
        self.assertEqual(
            result["sources"]["dex_volume"]["partial"],
            result["tokenized_equities"]["volume"]["partial"],
        )
        self.assertFalse(result["tokenized_equities"]["proof_of_reserves"]["available"])
        self.assertTrue(result["sources"]["proof_of_reserves"]["held"])
        self.assertFalse(result["daily_fee_payers"]["available"])
        self.assertIn("source-rights", result["daily_fee_payers"]["reason"])
        self.assertEqual(result["sources"]["supply"]["queried_this_run_asset_count"], 1)
        self.assertEqual(result["sources"]["supply"]["successful_this_run_asset_count"], 0)
        self.assertEqual(result["sources"]["supply"]["failed_this_run_asset_count"], 1)
        self.assertEqual(result["sources"]["supply"]["fresh_asset_count"], 0)
        self.assertEqual(result["sources"]["supply"]["valued_asset_count"], 0)
        self.assertEqual(result["sources"]["supply"]["coverage_denominator"], 1)
        self.assertFalse(result["sources"]["supply"]["sweep_complete"])
        self.assertNotIn("price", result["sources"])
        self.assertEqual(next_state["cursor_mint"], "mint-a")
        self.assertFalse(result["sources"]["activity_benchmark"]["available"])
        self.assertTrue(result["sources"]["activity_benchmark"]["held"])
        self.assertFalse(result["sources"]["activity_benchmark"]["active_addresses_available"])
        self.assertFalse(result["sources"]["activity_benchmark"]["fee_payers_available"])
        self.assertFalse(result["sources"]["activity_benchmark"]["active_addresses_history_available"])
        self.assertFalse(result["sources"]["activity_benchmark"]["fee_payers_history_available"])
        self.assertEqual(result["sources"]["activity_benchmark"]["fee_payers_observed_row_count"], 0)
        self.assertIn("selected_usd_stablecoins", result)
        self.assertFalse(result["sources"]["selected_usd_stablecoins"]["available"])
        self.assertFalse(result["sources"]["selected_usd_stablecoins"]["coverage_complete"])

    def test_accepted_solana_data_rows_populate_active_address_range(self):
        raw = {
            "generatedAt": "2026-09-01T00:00:00Z",
            "rows": [
                {"date": "2026-08-30", "metricName": "Active Addresses",
                 "unit": "Count", "providerName": name, "value": value}
                for name, value in (
                    ("Allium", 3_100_000), ("Artemis", 3_300_000),
                    ("Blockworks", 3_200_000), ("Dune", 3_000_000),
                )
            ],
        }
        with patch("growth.fetch_xstocks_registry", return_value={
                 "products": [], "coverage_complete": True,
             }), patch("growth.fetch_dex_pairs", return_value={
                 "rows": [], "batches_expected": 0, "batches_requested": 0,
                 "batches_succeeded": 0,
             }), patch("growth.fetch_selected_usd_stablecoin_supplies",
                       return_value=empty_selected_stablecoins()), \
             patch("growth.fetch_paginated_nodes"), \
             patch("growth.fetch_json", return_value=raw):
            result, next_state = growth.collect_growth("https://rpc.example")
        addresses = result["daily_active_addresses"]
        self.assertTrue(addresses["available"])
        self.assertEqual(addresses["date"], "2026-08-30")
        self.assertEqual(addresses["minimum"], 3_000_000)
        self.assertEqual(addresses["maximum"], 3_300_000)
        self.assertEqual(addresses["provider_count"], 4)
        self.assertNotIn("reason", addresses)
        fee_payers = result["daily_fee_payers"]
        self.assertFalse(fee_payers["available"])
        self.assertIn("source-rights", fee_payers["reason"])
        source = result["sources"]["activity_benchmark"]
        self.assertTrue(source["available"])
        self.assertTrue(source["active_addresses_available"])
        self.assertFalse(source["fee_payers_available"])
        self.assertTrue(source["held"])
        self.assertIn("Fee Payers", source["reason"])
        self.assertEqual(source["active_addresses_observed_row_count"], 4)

    def test_successful_supply_updates_cache_and_can_be_registry_wide(self):
        products = [{"slug": "a", "name": "Asset", "symbol": "Ax", "solana_mint": "mint-a"}]
        with patch("growth.time.time", return_value=1_787_659_200), \
             patch("growth.fetch_xstocks_registry", return_value={
                 "products": products, "source_url": growth.REGISTRY_API_URL,
                 "source_kind": "documented public API", "coverage_complete": True,
             }), patch("growth.fetch_token_supply", return_value={
                 **rpc_supply(),
                 "multiplier_provenance": cached_supply()["multiplier_provenance"],
             }), \
             patch("growth.time.sleep"), \
             patch("growth.fetch_dex_pairs", return_value={
                 "rows": [], "batches_expected": 1, "batches_requested": 1,
                 "batches_succeeded": 1,
             }), patch("growth.fetch_selected_usd_stablecoin_supplies",
                       return_value=empty_selected_stablecoins()), \
             patch("growth.fetch_paginated_nodes") as issuer_api, \
             patch("growth.fetch_json", return_value=None):
            result, next_state = growth.collect_growth("https://rpc.example")
        issuer_api.assert_not_called()
        supply = result["sources"]["supply"]
        self.assertTrue(result["tokenized_equities"]["available"])
        self.assertEqual(supply["registry_asset_count"], 1)
        self.assertEqual(supply["eligible_asset_count"], 1)
        self.assertEqual(supply["queried_this_run_asset_count"], 1)
        self.assertEqual(supply["successful_this_run_asset_count"], 1)
        self.assertEqual(supply["fresh_asset_count"], 1)
        self.assertEqual(supply["valued_asset_count"], 0)
        self.assertTrue(supply["sweep_complete"])
        self.assertEqual(supply["scope"], "registry-wide")
        self.assertEqual(next_state["observations"]["mint-a"]["raw_amount"], "100000000")
        self.assertEqual(
            next_state["observations"]["mint-a"]["multiplier_provenance"]["extension"],
            "scaledUiAmountConfig",
        )
        self.assertFalse(result["tokenized_equities"]["valuation"]["available"])

    def test_invalid_loaded_state_blocks_supply_mutation_but_not_other_growth_sources(self):
        products = [{"slug": "a", "name": "Asset", "symbol": "Ax", "solana_mint": "mint-a"}]
        invalid_state = growth.empty_supply_state()
        invalid_state["load_error"] = "unsupported supply state version"
        with patch("growth.fetch_xstocks_registry", return_value={
                 "products": products, "source_url": growth.REGISTRY_API_URL,
                 "source_kind": "documented public API", "coverage_complete": True,
             }), patch("growth.fetch_token_supply") as fetch, \
             patch("growth.fetch_dex_pairs", return_value={
                 "rows": [], "batches_expected": 1, "batches_requested": 1,
                 "batches_succeeded": 1,
             }), patch("growth.fetch_selected_usd_stablecoin_supplies",
                       return_value=empty_selected_stablecoins()), \
             patch("growth.fetch_paginated_nodes") as issuer_api, \
             patch("growth.fetch_json", return_value=None):
            result, next_state = growth.collect_growth(
                "https://rpc.example", supply_state=invalid_state,
            )
        issuer_api.assert_not_called()
        fetch.assert_not_called()
        self.assertIsNone(next_state)
        self.assertIn("unsupported", result["sources"]["supply"]["state_error"])
        self.assertTrue(result["sources"]["registry"]["available"])


if __name__ == "__main__":
    unittest.main()
