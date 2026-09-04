"""Focused collection tests for tiered source reuse."""

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cadence  # noqa: E402
import collect  # noqa: E402
import growth  # noqa: E402


NOW = datetime(2026, 9, 4, 20, 0, tzinfo=timezone.utc)
ENDPOINT = "https://api.mainnet.solana.com"


def schedule(age_by_key: dict[str, timedelta] | None = None) -> dict:
    result = cadence.initial_schedule()
    for key in result:
        age = (age_by_key or {}).get(key, timedelta(minutes=10))
        stamp = (NOW - age).isoformat()
        result[key].update({
            "last_attempt_at": stamp,
            "last_success_at": stamp,
            "state": "fresh",
        })
    return result


def previous(age_by_key: dict[str, timedelta] | None = None) -> dict:
    provider_source = {"available": True, "marker": "provider-source"}
    return {
        "source": {
            "endpoint": ENDPOINT,
            "endpoint_identity": growth.rpc_endpoint_identity(ENDPOINT),
        },
        "collection_schedule": schedule(age_by_key),
        "activity": {"available": True, "marker": "activity"},
        "validators": {
            "block_production": {"available": True, "marker": "production"},
        },
        "feature_activation": {"available": True, "marker": "features"},
        "news": {"available": True, "marker": "news", "items": []},
        "growth": {
            "available": True,
            "tokenized_equities": {"available": True, "marker": "tokens"},
            "selected_usd_stablecoins": {"coverage_numerator": 4},
            "daily_active_addresses": {"history_available": True, "marker": "providers"},
            "daily_fee_payers": {"history_available": False},
            "sources": {
                "registry": {"available": True},
                "activity_benchmark": provider_source,
            },
        },
        "dune": {"available": True, "marker": "dune"},
    }


def complete_production(epoch=10, first_slot=100, last_slot=199,
                        observed_at="2026-09-04T19:00:00+00:00") -> dict:
    leader_slots = last_slot - first_slot + 1
    return {
        "available": True,
        "basis": "most recent fully completed epoch",
        "epoch": epoch,
        "first_slot": first_slot,
        "last_slot": last_slot,
        "context_slot": last_slot + 10,
        "api_version": "4.2.2",
        "leader_slots": leader_slots,
        "blocks_produced": leader_slots - 1,
        "skipped_slots": 1,
        "skip_rate": round(1 / leader_slots, 8),
        "skip_rate_definition": "skipped_slots / leader_slots",
        "vote_enrichment_observed_at": observed_at,
        "source": {"method": "getBlockProduction", "commitment": "finalized"},
        "validators": [{
            "identity": "node-1", "leader_slots": leader_slots,
            "blocks_produced": leader_slots - 1, "skipped_slots": 1,
        }],
        "collection": {
            "mode": "contiguous_chunks",
            "request_count": 1,
            "chunk_slot_limit": 5_000,
            "first_slot": first_slot,
            "last_slot": last_slot,
            "coverage_numerator_slots": leader_slots,
            "coverage_denominator_slots": leader_slots,
            "coverage_complete": True,
            "context_slot_min": last_slot + 10,
            "context_slot_max": last_slot + 10,
        },
    }


def production_snapshot(production: dict, collected_at="2026-09-04T19:01:00+00:00",
                        endpoint=ENDPOINT) -> dict:
    return {
        "source": {
            "endpoint": endpoint,
            "endpoint_identity": growth.rpc_endpoint_identity(endpoint),
        },
        "collected_at": collected_at,
        "epoch": {"available": True, "epoch": 11},
        "validators": {"block_production": deepcopy(production)},
    }


class CollectCadenceTests(unittest.TestCase):
    def _base_patches(self):
        return (
            patch.object(collect, "fetch_rpc", return_value=[]),
            patch.object(collect, "index_results", return_value={}),
            patch.object(collect, "fetch_block_time", return_value=None),
            patch.object(collect.economics, "collect_price_economics",
                         return_value={"available": True, "marker": "price"}),
            patch.object(collect, "source_code_state", return_value={}),
        )

    def test_recent_slow_sources_are_reused_exactly_while_fast_sources_run(self):
        prior = previous()
        patches = self._base_patches()
        with patches[0] as core, patches[1], patches[2], patches[3] as price, patches[4], \
             patch.object(collect.blocks, "collect_activity") as activity, \
             patch.object(collect.blocks, "fetch_block_production") as production, \
             patch.object(collect.feature_accounts, "collect_feature_accounts") as features, \
             patch.object(collect.news_module, "collect_news") as news, \
             patch.object(collect.growth_module, "collect_growth") as tokens, \
             patch.object(collect.growth_module, "refresh_growth_providers") as providers, \
             patch.object(collect.dune_module, "collect_dune") as dune:
            raw = collect.sources(
                ENDPOINT, with_price=True, with_dune=True,
                previous_snapshot=prior, now=NOW,
            )

        core.assert_called_once_with(ENDPOINT)
        price.assert_called_once_with()
        for collector in (activity, production, features, news, tokens, providers, dune):
            collector.assert_not_called()
        self.assertEqual(raw["activity"], prior["activity"])
        self.assertEqual(raw["block_production"], prior["validators"]["block_production"])
        self.assertEqual(raw["growth"], prior["growth"])
        self.assertIsNone(raw["_growth_supply_state"])
        for key, entry in raw["collection_schedule"].items():
            self.assertEqual(entry["state"], "reused", key)
            self.assertEqual(
                entry["last_success_at"], prior["collection_schedule"][key]["last_success_at"],
            )

    def test_six_hour_provider_refresh_does_not_run_or_advance_token_cursor(self):
        prior = previous({"growth_providers": timedelta(hours=6)})
        updated = deepcopy(prior["growth"])
        updated["daily_active_addresses"]["marker"] = "new-provider"
        patches = self._base_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch.object(collect.growth_module, "refresh_growth_providers",
                          return_value=updated) as providers, \
             patch.object(collect.growth_module, "collect_growth") as tokens:
            raw = collect.sources(ENDPOINT, previous_snapshot=prior, now=NOW)

        providers.assert_called_once_with(prior["growth"])
        tokens.assert_not_called()
        self.assertIsNone(raw["_growth_supply_state"])
        self.assertEqual(raw["growth"]["tokenized_equities"],
                         prior["growth"]["tokenized_equities"])
        self.assertEqual(raw["growth"]["daily_active_addresses"]["marker"],
                         "new-provider")
        self.assertEqual(raw["collection_schedule"]["growth_providers"]["state"],
                         "fresh")
        self.assertEqual(raw["collection_schedule"]["growth_tokens"]["state"],
                         "reused")

    def test_endpoint_change_forces_onchain_tiers_but_not_provider_or_news(self):
        prior = previous()
        prior["source"]["endpoint_identity"] = growth.rpc_endpoint_identity(
            "https://old.example",
        )
        new_growth = deepcopy(prior["growth"])
        new_state = {"version": 2, "marker": "cursor"}
        patches = self._base_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch.object(collect.blocks, "completed_epoch_range", return_value={}), \
             patch.object(collect.blocks, "fetch_block_production", return_value={}) as production, \
             patch.object(collect.blocks, "normalize_block_production",
                          return_value={"available": True}), \
             patch.object(collect.blocks, "collect_activity",
                          return_value={"available": True}) as activity, \
             patch.object(collect.feature_accounts, "collect_feature_accounts",
                          return_value={"available": True}) as features, \
             patch.object(collect.growth_module, "collect_growth",
                          return_value=(new_growth, new_state)) as tokens, \
             patch.object(collect.growth_module, "refresh_growth_providers") as providers, \
             patch.object(collect.news_module, "collect_news") as news:
            raw = collect.sources(ENDPOINT, previous_snapshot=prior, now=NOW)

        activity.assert_called_once()
        production.assert_called_once()
        features.assert_called_once()
        tokens.assert_called_once_with(
            ENDPOINT, supply_state=None,
            with_providers=False, previous_growth=prior["growth"],
        )
        providers.assert_not_called()
        news.assert_not_called()
        self.assertEqual(raw["_growth_supply_state"], new_state)
        for key in ("activity", "block_production", "feature_activation", "growth_tokens"):
            self.assertEqual(raw["collection_schedule"][key]["state"], "fresh")
        self.assertEqual(raw["collection_schedule"]["growth_providers"]["state"], "reused")

    def test_malformed_persisted_schedule_fails_before_any_transport(self):
        prior = previous()
        prior["collection_schedule"]["news"]["state"] = []
        with patch.object(collect, "fetch_rpc") as core:
            with self.assertRaisesRegex(
                collect.CollectionError, "prior collection schedule is invalid",
            ):
                collect.sources(ENDPOINT, previous_snapshot=prior, now=NOW)
        core.assert_not_called()

    def test_new_completed_epoch_refreshes_inside_the_hourly_window(self):
        prior = previous()
        prior["validators"]["block_production"].update({
            "epoch": 10, "first_slot": 100, "last_slot": 199,
        })
        indexed = {
            "getEpochInfo": {}, "getEpochSchedule": {},
            "getVoteAccounts": {"current": [], "delinquent": []},
        }
        new_range = {
            "available": True, "epoch": 11, "first_slot": 200,
            "last_slot": 299, "leader_slots": 100,
        }
        patches = self._base_patches()
        with patches[0], patch.object(collect, "index_results", return_value=indexed), \
             patches[2], patches[3], patches[4], \
             patch.object(collect.blocks, "completed_epoch_range", return_value=new_range), \
             patch.object(collect.blocks, "fetch_block_production", return_value={}) as fetch, \
             patch.object(collect.blocks, "normalize_block_production",
                          return_value={"available": True, **new_range}) as normalize:
            raw = collect.sources(ENDPOINT, previous_snapshot=prior, now=NOW)

        fetch.assert_called_once_with(new_range, ENDPOINT)
        normalize.assert_called_once()
        self.assertEqual(raw["block_production"]["epoch"], 11)
        self.assertEqual(raw["collection_schedule"]["block_production"]["state"],
                         "fresh")

    def test_two_failed_snapshots_recover_newest_complete_same_epoch_without_retry(self):
        retained = complete_production()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = [
                production_snapshot(retained),
                production_snapshot({"available": False, "reason": "chunk 75 failed"},
                                    "2026-09-04T19:16:00+00:00"),
                production_snapshot({"available": False, "reason": "chunk 75 failed"},
                                    "2026-09-04T19:31:00+00:00"),
            ]
            for index, snapshot in enumerate(snapshots):
                (root / f"snapshot-20260904T19{index:02d}00+0000.json").write_text(
                    json.dumps(snapshot), encoding="utf-8",
                )
            fallbacks = collect.load_block_production_fallbacks(root, limit=3)

        prior = previous()
        prior["epoch"] = {"available": True, "epoch": 11}
        prior["validators"]["block_production"] = {
            "available": False, "reason": "chunk 75 failed",
        }
        prior["collection_schedule"]["block_production"].update({
            "last_attempt_at": "2026-09-04T19:45:00+00:00",
            "last_success_at": None,
            "state": "failed",
        })
        current_range = {
            "available": True, "epoch": 10, "first_slot": 100,
            "last_slot": 199, "leader_slots": 100,
        }
        indexed = {
            "getEpochInfo": {"epoch": 11}, "getEpochSchedule": {},
            "getVoteAccounts": {"current": [], "delinquent": []},
        }
        patches = self._base_patches()
        with patches[0], patch.object(collect, "index_results", return_value=indexed), \
             patches[2], patches[3], patches[4], \
             patch.object(collect.blocks, "completed_epoch_range",
                          return_value=current_range), \
             patch.object(collect.blocks, "fetch_block_production") as fetch:
            raw = collect.sources(
                ENDPOINT, with_activity=False, with_news=False, with_growth=False,
                previous_snapshot=prior, block_production_fallbacks=fallbacks,
                now=NOW,
            )

        fetch.assert_not_called()
        self.assertEqual(raw["block_production"], retained)
        clock = raw["collection_schedule"]["block_production"]
        self.assertEqual(clock["state"], "failed")
        self.assertEqual(clock["last_success_at"], retained["vote_enrichment_observed_at"])

    def test_failed_due_refresh_retains_complete_prior_without_marking_success(self):
        retained = complete_production()
        fallback = production_snapshot(retained)
        prior = previous()
        prior["epoch"] = {"available": True, "epoch": 11}
        prior["validators"]["block_production"] = {
            "available": False, "reason": "prior failure",
        }
        prior["collection_schedule"]["block_production"].update({
            "last_attempt_at": "2026-09-04T19:00:00+00:00",
            "last_success_at": None,
            "state": "failed",
        })
        current_range = {
            "available": True, "epoch": 10, "first_slot": 100,
            "last_slot": 199, "leader_slots": 100,
        }
        indexed = {
            "getEpochInfo": {"epoch": 11}, "getEpochSchedule": {},
            "getVoteAccounts": {"current": [], "delinquent": []},
        }
        patches = self._base_patches()
        with patches[0], patch.object(collect, "index_results", return_value=indexed), \
             patches[2], patches[3], patches[4], \
             patch.object(collect.blocks, "completed_epoch_range",
                          return_value=current_range), \
             patch.object(collect.blocks, "fetch_block_production",
                          return_value={"available": False}) as fetch, \
             patch.object(collect.blocks, "normalize_block_production",
                          return_value={"available": False, "reason": "chunk 75 failed"}):
            raw = collect.sources(
                ENDPOINT, with_activity=False, with_news=False, with_growth=False,
                previous_snapshot=prior, block_production_fallbacks=[fallback],
                now=NOW,
            )

        fetch.assert_called_once_with(current_range, ENDPOINT)
        self.assertEqual(raw["block_production"], retained)
        clock = raw["collection_schedule"]["block_production"]
        self.assertEqual(clock["state"], "failed")
        self.assertEqual(clock["last_success_at"], retained["vote_enrichment_observed_at"])

    def test_production_fallback_rejects_new_epoch_endpoint_and_overage(self):
        cases = (
            ("epoch", production_snapshot(complete_production(epoch=9)), ENDPOINT),
            ("endpoint", production_snapshot(complete_production(), endpoint="https://old.example"), ENDPOINT),
            ("overage", production_snapshot(complete_production(
                observed_at="2026-09-04T12:59:59+00:00")), ENDPOINT),
        )
        for label, fallback, endpoint in cases:
            with self.subTest(label=label):
                prior = previous({"block_production": timedelta(hours=1)})
                prior["epoch"] = {"available": True, "epoch": 11}
                prior["validators"]["block_production"] = {
                    "available": False, "reason": "prior failure",
                }
                current_range = {
                    "available": True, "epoch": 10, "first_slot": 100,
                    "last_slot": 199, "leader_slots": 100,
                }
                indexed = {
                    "getEpochInfo": {"epoch": 11}, "getEpochSchedule": {},
                    "getVoteAccounts": {"current": [], "delinquent": []},
                }
                patches = self._base_patches()
                failed = {"available": False, "reason": "chunk 75 failed"}
                with patches[0], patch.object(collect, "index_results", return_value=indexed), \
                     patches[2], patches[3], patches[4], \
                     patch.object(collect.blocks, "completed_epoch_range",
                                  return_value=current_range), \
                     patch.object(collect.blocks, "fetch_block_production",
                                  return_value={"available": False}), \
                     patch.object(collect.blocks, "normalize_block_production",
                                  return_value=failed):
                    raw = collect.sources(
                        endpoint, with_activity=False, with_news=False, with_growth=False,
                        previous_snapshot=prior, block_production_fallbacks=[fallback],
                        now=NOW,
                    )

                self.assertEqual(raw["block_production"], failed)
                self.assertEqual(
                    raw["collection_schedule"]["block_production"]["state"], "failed",
                )

    def test_failed_missing_range_uses_hourly_backoff_but_epoch_change_forces(self):
        current_range = {
            "available": True, "epoch": 10, "first_slot": 100,
            "last_slot": 199, "leader_slots": 100,
        }
        for current_epoch, expected_calls in ((11, 0), (12, 1)):
            with self.subTest(current_epoch=current_epoch):
                prior = previous()
                prior["epoch"] = {"available": True, "epoch": 11}
                prior["validators"]["block_production"] = {
                    "available": False, "reason": "chunk 75 failed",
                }
                prior["collection_schedule"]["block_production"].update({
                    "last_attempt_at": "2026-09-04T19:45:00+00:00",
                    "last_success_at": None,
                    "state": "failed",
                })
                indexed = {
                    "getEpochInfo": {"epoch": current_epoch}, "getEpochSchedule": {},
                    "getVoteAccounts": {"current": [], "delinquent": []},
                }
                patches = self._base_patches()
                with patches[0], patch.object(collect, "index_results", return_value=indexed), \
                     patches[2], patches[3], patches[4], \
                     patch.object(collect.blocks, "completed_epoch_range",
                                  return_value=current_range), \
                     patch.object(collect.blocks, "fetch_block_production",
                                  return_value={"available": False}) as fetch, \
                     patch.object(collect.blocks, "normalize_block_production",
                                  return_value={"available": False}):
                    collect.sources(
                        ENDPOINT, with_activity=False, with_news=False,
                        with_growth=False, previous_snapshot=prior, now=NOW,
                    )

                self.assertEqual(fetch.call_count, expected_calls)

    def test_activity_reuse_does_not_reset_its_original_success_clock(self):
        activity = {
            "available": True,
            "source_state": "fresh",
            "last_success_at": "2026-09-04T19:00:00+00:00",
            "age_seconds": 120,
            "stale": False,
            "window": {"last_block_time": 1_788_548_280},
        }
        snapshot = {
            "collected_at": "2026-09-04T20:00:00+00:00",
            "activity": deepcopy(activity),
            "collection_schedule": cadence.initial_schedule(),
        }
        snapshot["collection_schedule"]["activity"].update({
            "last_attempt_at": activity["last_success_at"],
            "last_success_at": activity["last_success_at"],
            "state": "reused",
        })

        result = collect.apply_activity_last_known_good(snapshot, None)

        self.assertEqual(result["activity"]["last_success_at"],
                         activity["last_success_at"])
        self.assertEqual(result["activity"]["source_state"], "fresh")
        self.assertEqual(result["activity"]["age_seconds"], 3_720)

    def test_failed_activity_stays_last_known_good_on_the_next_not_due_run(self):
        prior = previous()
        prior["activity"] = {
            "available": True,
            "source_state": "last_known_good",
            "last_success_at": "2026-09-04T18:00:00+00:00",
            "carried_forward_at": "2026-09-04T19:50:00+00:00",
            "age_seconds": 6_000,
            "stale": True,
            "window": {"last_block_time": 1_788_547_200},
        }
        prior["collection_schedule"]["activity"].update({
            "last_attempt_at": "2026-09-04T19:50:00+00:00",
            "last_success_at": "2026-09-04T18:00:00+00:00",
            "state": "failed",
        })
        patches = self._base_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch.object(collect.blocks, "collect_activity") as activity:
            raw = collect.sources(ENDPOINT, previous_snapshot=prior, now=NOW)

        activity.assert_not_called()
        self.assertEqual(raw["collection_schedule"]["activity"]["state"], "failed")
        candidate = {
            "collected_at": "2026-09-04T20:00:00+00:00",
            "activity": raw["activity"],
            "collection_schedule": raw["collection_schedule"],
        }
        result = collect.apply_activity_last_known_good(candidate, prior)

        self.assertEqual(result["activity"]["source_state"], "last_known_good")
        self.assertEqual(
            result["activity"]["last_success_at"],
            prior["activity"]["last_success_at"],
        )
        self.assertEqual(result["activity"]["age_seconds"], 4_800)
        self.assertTrue(result["activity"]["stale"])

    def test_failed_activity_last_known_good_expires_while_not_due(self):
        prior = previous()
        prior["activity"] = {
            "available": True,
            "source_state": "last_known_good",
            "last_success_at": "2026-09-04T12:00:00+00:00",
            "age_seconds": 25_000,
            "stale": True,
            "window": {"last_block_time": 1_788_519_599},
        }
        prior["collection_schedule"]["activity"].update({
            "last_attempt_at": "2026-09-04T19:50:00+00:00",
            "last_success_at": "2026-09-04T12:00:00+00:00",
            "state": "failed",
        })
        candidate = {
            "collected_at": "2026-09-04T20:00:00+00:00",
            "activity": deepcopy(prior["activity"]),
            "collection_schedule": deepcopy(prior["collection_schedule"]),
        }

        result = collect.apply_activity_last_known_good(candidate, prior)

        self.assertEqual(result["activity"], {"available": False})

    def test_activity_failure_does_not_cross_rpc_endpoint_identity(self):
        current = {
            "collected_at": "2026-09-04T20:00:00+00:00",
            "source": {"endpoint_identity": "sha256:new"},
            "activity": {"available": False},
        }
        prior = {
            "collected_at": "2026-09-04T19:00:00+00:00",
            "source": {"endpoint_identity": "sha256:old"},
            "activity": {
                "available": True,
                "window": {"last_block_time": 1_788_548_280},
            },
        }

        result = collect.apply_activity_last_known_good(current, prior)

        self.assertEqual(result["activity"], {"available": False})


if __name__ == "__main__":
    unittest.main()
