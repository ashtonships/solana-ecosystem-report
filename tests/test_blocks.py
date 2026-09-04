"""Offline tests for the block-sampling transforms.

No network. Every test feeds the shape `getBlock` actually returns at
`transactionDetails: "accounts"` — captured from live mainnet responses — to a
pure function.

Two properties carry most of the weight here:

  Vote transactions must never reach the fee statistics. They are the bulk of
  Solana's transaction count and all pay exactly 5000 lamports, so including
  them pins the median to 5000 forever and the metric silently stops meaning
  anything. `test_vote_transactions_do_not_reach_the_median` is the guard.

  Daily active addresses must stay null. Sums extrapolate from a sample;
  unique-address counts do not. The temptation to multiply the sampled count
  up to a day is exactly the mistake being tested against.
"""

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import blocks  # noqa: E402

JITO = sorted(blocks.JITO_TIP_ACCOUNTS)[0]


def account(pubkey, signer=False):
    return {"pubkey": pubkey, "signer": signer, "source": "transaction", "writable": True}


def transaction(fee, keys, signatures=1, err=None, pre=None, post=None):
    """A transaction in the shape the "accounts" detail level returns."""
    return {
        "meta": {
            "err": err,
            "fee": fee,
            "preBalances": pre if pre is not None else [0] * len(keys),
            "postBalances": post if post is not None else [0] * len(keys),
            "status": {"Ok": None} if err is None else {"Err": err},
        },
        "transaction": {
            "accountKeys": keys,
            "signatures": ["sig%d" % i for i in range(signatures)],
        },
        "version": "legacy",
    }


def vote_tx(payer="validator1"):
    """A real vote transaction: two signers, the vote program, 5000 lamports."""
    return transaction(5000, [
        account(payer, signer=True),
        account("voteaccount1"),
        account(blocks.VOTE_PROGRAM),
    ])


def block(transactions, block_time=1_785_928_251, fee_reward=None):
    body = {"blockHeight": 1, "blockTime": block_time, "blockhash": "h",
            "parentSlot": 100, "previousBlockhash": "p", "transactions": transactions}
    body["rewards"] = ([] if fee_reward is None else
                       [{"commission": None, "lamports": fee_reward, "postBalance": 1,
                         "pubkey": "leader1", "rewardType": "Fee"}])
    return body


class TestRpcBudget(unittest.TestCase):
    def test_retry_after_is_respected_inside_the_original_timeout(self):
        clock = [0.0]
        delays = []

        def sleep(delay):
            delays.append(delay)
            clock[0] += delay

        throttled = urllib.error.HTTPError("https://rpc.example", 429, "limited",
                                           {"Retry-After": "2"}, None)
        response = io.BytesIO(json.dumps({"result": 123}).encode())
        with mock.patch("blocks.time.monotonic", side_effect=lambda: clock[0]), \
             mock.patch("blocks.time.sleep", side_effect=sleep), \
             mock.patch("blocks.urllib.request.urlopen", side_effect=[throttled, response]) as fetch:
            result = blocks.call("getSlot", [], "https://rpc.example", timeout=10)
        self.assertEqual(result, 123)
        self.assertEqual(delays, [2])
        self.assertEqual([call.kwargs["timeout"] for call in fetch.call_args_list], [10, 8])

    def test_permanent_failure_and_retry_beyond_deadline_do_not_repeat(self):
        for status, headers in ((403, {}), (429, {"Retry-After": "15"})):
            with self.subTest(status=status), \
                 self.assertLogs("blocks", level="WARNING"), \
                 mock.patch("blocks.time.monotonic", return_value=0), \
                 mock.patch("blocks.time.sleep") as sleep, \
                 mock.patch("blocks.urllib.request.urlopen", side_effect=urllib.error.HTTPError(
                     "https://rpc.example", status, "failed", headers, None,
                 )) as fetch:
                self.assertIsNone(blocks.call("getSlot", [], "https://rpc.example", timeout=10))
                self.assertEqual(fetch.call_count, 1)
                sleep.assert_not_called()

    def test_failure_log_does_not_include_endpoint_or_provider_message(self):
        secret_endpoint = "https://rpc.example/private-token?api-key=secret"
        error = urllib.error.HTTPError(secret_endpoint, 403, "private provider message", {}, None)
        with mock.patch("blocks.urllib.request.urlopen", side_effect=error), \
             self.assertLogs("blocks", level="WARNING") as logs:
            self.assertIsNone(blocks.call("getSlot", [], secret_endpoint))
        self.assertEqual(logs.output, ["WARNING:blocks:RPC getSlot failed: HTTP 403"])

    def test_skipped_slot_attempts_share_one_timeout(self):
        clock = [0.0]

        def failed_call(method, params, endpoint, timeout):
            clock[0] += min(4, timeout)
            return None

        with mock.patch("blocks.time.monotonic", side_effect=lambda: clock[0]), \
             mock.patch("blocks.call", side_effect=failed_call) as fetch:
            self.assertIsNone(blocks.fetch_block(100, "https://rpc.example", timeout=5))
        self.assertEqual([call.args[3] for call in fetch.call_args_list], [5, 1])
        self.assertEqual(clock[0], 5)

    def test_truncated_collection_keeps_the_newest_finalized_evidence(self):
        clock = [0.0]
        head = 1_000_000

        def fetched(slot, endpoint, timeout):
            clock[0] += timeout
            return slot, block([transaction(9_000, [account("alice", signer=True)])])

        with mock.patch("blocks.time.monotonic", side_effect=lambda: clock[0]), \
             mock.patch("blocks.fetch_head", return_value=head), \
             mock.patch("blocks.fetch_production_rate", return_value=1.0), \
             mock.patch("blocks.fetch_block", side_effect=fetched) as fetch:
            result = blocks.collect_activity("https://rpc.example", samples=16, budget_seconds=5)
        fetch.assert_called_once_with(head - blocks.FINALITY_LAG_SLOTS, "https://rpc.example", 5)
        self.assertEqual(result["window"]["blocks_sampled"], 1)
        self.assertEqual(result["window"]["last_slot"], head - blocks.FINALITY_LAG_SLOTS)
        self.assertTrue(result["window"]["truncated"])
        self.assertIsNone(result["rev"]["sample_mean_estimate_sol"])


class TestCompletedEpochRange(unittest.TestCase):
    def test_derives_the_most_recent_fully_completed_normal_epoch(self):
        result = blocks.completed_epoch_range(
            {
                "epoch": 700,
                "absoluteSlot": 302_523_456,
                "slotIndex": 123_456,
                "slotsInEpoch": 432_000,
            },
            {
                "warmup": False,
                "firstNormalEpoch": 0,
                "firstNormalSlot": 0,
                "slotsPerEpoch": 432_000,
            },
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["epoch"], 699)
        self.assertEqual(result["first_slot"], 301_968_000)
        self.assertEqual(result["last_slot"], 302_399_999)
        self.assertEqual(result["leader_slots"], 432_000)

    def test_handles_the_final_warmup_epoch_from_the_schedule(self):
        result = blocks.completed_epoch_range(
            {"epoch": 2, "absoluteSlot": 100, "slotIndex": 4, "slotsInEpoch": 128},
            {
                "warmup": True,
                "firstNormalEpoch": 2,
                "firstNormalSlot": 96,
                "slotsPerEpoch": 128,
            },
        )
        self.assertEqual(result["epoch"], 1)
        self.assertEqual(result["first_slot"], 32)
        self.assertEqual(result["last_slot"], 95)
        self.assertEqual(result["leader_slots"], 64)

    def test_current_epoch_is_never_mislabeled_as_completed(self):
        result = blocks.completed_epoch_range(
            {"epoch": 0, "absoluteSlot": 12, "slotIndex": 12, "slotsInEpoch": 32},
            {
                "warmup": True,
                "firstNormalEpoch": 2,
                "firstNormalSlot": 96,
                "slotsPerEpoch": 128,
            },
        )
        self.assertFalse(result["available"])
        self.assertNotIn("epoch", result)


class TestBlockProductionRequest(unittest.TestCase):
    @staticmethod
    def response(first_slot, last_slot, *, context_slot=20_000,
                 api_version="4.2.0", by_identity=None):
        slot_count = last_slot - first_slot + 1
        return {
            "context": {"slot": context_slot, "apiVersion": api_version},
            "value": {
                "byIdentity": by_identity or {"node-A": [slot_count, slot_count]},
                "range": {"firstSlot": first_slot, "lastSlot": last_slot},
            },
        }

    @mock.patch("blocks.call")
    def test_requests_the_exact_completed_range_at_finalized_commitment(self, rpc_call):
        rpc_call.return_value = self.response(100, 103)
        epoch_range = {
            "available": True,
            "epoch": 699,
            "first_slot": 100,
            "last_slot": 103,
            "leader_slots": 4,
        }
        result = blocks.fetch_block_production(
            epoch_range, "https://rpc.example", timeout=17,
        )

        self.assertEqual(result["value"]["range"], {
            "firstSlot": 100, "lastSlot": 103,
        })
        self.assertEqual(result["collection"]["request_count"], 1)
        rpc_call.assert_called_once_with(
            "getBlockProduction",
            [{
                "commitment": "finalized",
                "range": {"firstSlot": 100, "lastSlot": 103},
            }],
            "https://rpc.example",
            17,
        )

    def test_transient_chunk_failure_recovers_with_exact_coverage(self):
        epoch_range = {"available": True, "epoch": 699, "first_slot": 100,
                       "last_slot": 103, "leader_slots": 4}
        responses = [
            urllib.error.HTTPError("https://rpc.example", 503, "unavailable", {}, None),
            io.BytesIO(json.dumps({"result": self.response(100, 103)}).encode()),
        ]
        with mock.patch("blocks.urllib.request.urlopen", side_effect=responses) as fetch, \
             mock.patch("blocks.time.sleep"):
            result = blocks.fetch_block_production(epoch_range, "https://rpc.example")
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(result["collection"]["coverage_numerator_slots"], 4)
        self.assertEqual(result["collection"]["coverage_denominator_slots"], 4)
        self.assertTrue(result["collection"]["coverage_complete"])
        self.assertEqual(result["value"]["byIdentity"], {"node-A": [4, 4]})

    @mock.patch("blocks.call")
    def test_aggregates_a_completed_epoch_from_exact_contiguous_chunks(self, rpc_call):
        def response(_method, params, _endpoint, _timeout):
            requested = params[0]["range"]
            first_slot = requested["firstSlot"]
            last_slot = requested["lastSlot"]
            slot_count = last_slot - first_slot + 1
            if first_slot == 100:
                identities = {"node-A": [slot_count, slot_count - 1]}
                context_slot = 20_003
            elif first_slot == 5_100:
                identities = {"node-B": [slot_count, slot_count - 2]}
                context_slot = 20_001
            else:
                identities = {"node-A": [slot_count, slot_count]}
                context_slot = 20_002
            return self.response(
                first_slot, last_slot, context_slot=context_slot,
                by_identity=identities,
            )

        rpc_call.side_effect = response
        epoch_range = {
            "available": True,
            "epoch": 699,
            "first_slot": 100,
            "last_slot": 10_100,
            "leader_slots": 10_001,
        }

        with (
            mock.patch.object(blocks, "BLOCK_PRODUCTION_REQUESTS_PER_WINDOW", 2),
            mock.patch("blocks.time.sleep") as pause,
        ):
            result = blocks.fetch_block_production(epoch_range, "rpc", timeout=17)

        self.assertEqual(rpc_call.call_count, 3)
        pause.assert_called_once_with(10.0)
        self.assertEqual(
            [call.args[1][0]["range"] for call in rpc_call.call_args_list],
            [
                {"firstSlot": 100, "lastSlot": 5_099},
                {"firstSlot": 5_100, "lastSlot": 10_099},
                {"firstSlot": 10_100, "lastSlot": 10_100},
            ],
        )
        self.assertEqual(result["value"]["byIdentity"], {
            "node-A": [5_001, 5_000],
            "node-B": [5_000, 4_998],
        })
        self.assertEqual(result["context"], {
            "slot": 20_001, "apiVersion": "4.2.0",
        })
        self.assertEqual(result["collection"], {
            "mode": "contiguous_chunks",
            "request_count": 3,
            "chunk_slot_limit": 5_000,
            "first_slot": 100,
            "last_slot": 10_100,
            "coverage_numerator_slots": 10_001,
            "coverage_denominator_slots": 10_001,
            "coverage_complete": True,
            "context_slot_min": 20_001,
            "context_slot_max": 20_003,
        })

    def test_missing_malformed_or_mismatched_chunk_refuses_the_whole_epoch(self):
        first = self.response(100, 5_099)
        failures = {
            "missing": None,
            "malformed context": {
                "context": {"slot": "late", "apiVersion": "4.2.0"},
                "value": self.response(5_100, 6_099)["value"],
            },
            "mismatched range": self.response(5_100, 6_098),
        }
        epoch_range = {
            "available": True,
            "epoch": 699,
            "first_slot": 100,
            "last_slot": 6_099,
            "leader_slots": 6_000,
        }

        for label, failed_chunk in failures.items():
            with self.subTest(label=label), mock.patch("blocks.call") as rpc_call:
                rpc_call.side_effect = [first, failed_chunk]
                result = blocks.fetch_block_production(epoch_range, "rpc")
                self.assertEqual(set(result), {"available", "reason"})
                self.assertFalse(result["available"])
                self.assertIn("chunk", result["reason"])
                self.assertEqual(rpc_call.call_count, 2)


class TestBlockProductionNormalization(unittest.TestCase):
    def epoch_range(self):
        return {
            "available": True,
            "epoch": 699,
            "first_slot": 100,
            "last_slot": 103,
            "leader_slots": 4,
        }

    def production(self):
        return {
            "context": {"slot": 120, "apiVersion": "2.2.0"},
            "value": {
                "byIdentity": {"node-A": [3, 2], "node-B": [1, 1]},
                "range": {"firstSlot": 100, "lastSlot": 103},
            },
        }

    def vote_accounts(self):
        return {
            "current": [
                {
                    "nodePubkey": "node-A",
                    "votePubkey": "vote-A-1",
                    "activatedStake": 10,
                },
                {
                    "nodePubkey": "node-A",
                    "votePubkey": "vote-A-2",
                    "activatedStake": 20,
                },
                {
                    # A vote key that equals node-B must not be joined as its identity.
                    "nodePubkey": "different-node",
                    "votePubkey": "node-B",
                    "activatedStake": 30,
                },
            ],
            "delinquent": [],
        }

    def test_preserves_exact_epoch_range_context_and_skip_denominators(self):
        production = self.production()
        production["collection"] = {
            "mode": "contiguous_chunks",
            "request_count": 2,
            "chunk_slot_limit": 2,
            "first_slot": 100,
            "last_slot": 103,
            "coverage_numerator_slots": 4,
            "coverage_denominator_slots": 4,
            "coverage_complete": True,
            "context_slot_min": 120,
            "context_slot_max": 121,
        }
        result = blocks.normalize_block_production(
            production, self.vote_accounts(), self.epoch_range(),
            "2026-08-25T15:00:00Z",
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["epoch"], 699)
        self.assertEqual(result["first_slot"], 100)
        self.assertEqual(result["last_slot"], 103)
        self.assertEqual(result["context_slot"], 120)
        self.assertEqual(result["api_version"], "2.2.0")
        self.assertEqual(result["leader_slots"], 4)
        self.assertEqual(result["blocks_produced"], 3)
        self.assertEqual(result["skipped_slots"], 1)
        self.assertEqual(result["skip_rate"], 0.25)
        self.assertEqual(result["vote_enrichment_observed_at"], "2026-08-25T15:00:00Z")
        self.assertEqual(result["collection"], production["collection"])

    def test_joins_by_node_identity_and_aggregates_multiple_vote_accounts(self):
        result = blocks.normalize_block_production(
            self.production(), self.vote_accounts(), self.epoch_range(),
            "2026-08-25T15:00:00Z",
        )
        rows = {row["identity"]: row for row in result["validators"]}
        self.assertEqual(rows["node-A"]["vote_account_count"], 2)
        self.assertEqual(rows["node-A"]["activated_stake_lamports"], 30)
        self.assertEqual(
            [entry["vote_pubkey"] for entry in rows["node-A"]["vote_accounts"]],
            ["vote-A-1", "vote-A-2"],
        )
        self.assertFalse(rows["node-B"]["vote_identity_matched"])
        self.assertEqual(rows["node-B"]["vote_account_count"], 0)
        self.assertIsNone(rows["node-B"]["activated_stake_lamports"])

    def test_malformed_or_partial_rpc_data_is_unavailable_not_zero(self):
        malformed = [
            {},
            {"context": {"slot": 120}, "value": self.production()["value"]},
            {
                "context": {"slot": 120, "apiVersion": "2.2.0"},
                "value": {
                    "byIdentity": {"node-A": [4, 3]},
                    "range": {"firstSlot": 100, "lastSlot": 102},
                },
            },
            {
                "context": {"slot": 120, "apiVersion": "2.2.0"},
                "value": {
                    "byIdentity": {"node-A": [3, 3]},
                    "range": {"firstSlot": 100, "lastSlot": 103},
                },
            },
            {
                "context": {"slot": 120, "apiVersion": "2.2.0"},
                "value": {
                    "byIdentity": {"node-A": [4, 5]},
                    "range": {"firstSlot": 100, "lastSlot": 103},
                },
            },
            {
                "context": {"slot": 120, "apiVersion": "2.2.0"},
                "value": {
                    "byIdentity": {"node-A": [-1, 0], "node-B": [5, 5]},
                    "range": {"firstSlot": 100, "lastSlot": 103},
                },
            },
        ]
        for production in malformed:
            with self.subTest(production=production):
                result = blocks.normalize_block_production(
                    production, self.vote_accounts(), self.epoch_range(),
                    "2026-08-25T15:00:00Z",
                )
                self.assertEqual(set(result), {"available", "reason"})
                self.assertFalse(result["available"])

    def test_duplicate_vote_account_identity_is_unavailable(self):
        votes = self.vote_accounts()
        votes["delinquent"].append({
            "nodePubkey": "node-B",
            "votePubkey": "vote-A-1",
            "activatedStake": 40,
        })
        result = blocks.normalize_block_production(
            self.production(), votes, self.epoch_range(), "2026-08-25T15:00:00Z",
        )
        self.assertFalse(result["available"])
        self.assertIn("duplicate", result["reason"])


class TestVoteSeparation(unittest.TestCase):
    def test_vote_transactions_do_not_reach_the_median(self):
        # 8 votes at 5000 and 3 real transactions. Including votes would make
        # the median 5000 and the metric permanently uninformative.
        body = block([vote_tx() for _ in range(8)] + [
            transaction(50_000, [account("alice", signer=True), account("prog")]),
            transaction(60_000, [account("bob", signer=True), account("prog")]),
            transaction(70_000, [account("carol", signer=True), account("prog")]),
        ])
        summary = blocks.summarize_block(body, slot=101)
        self.assertEqual(summary["tx_vote"], 8)
        self.assertEqual(summary["tx_nonvote"], 3)

        fees = blocks.summarize_fees([summary])
        self.assertEqual(fees["median_lamports"], 60_000)
        self.assertEqual(fees["nonvote_transactions_sampled"], 3)
        self.assertEqual(fees["vote_share_pct"], round(100 * 8 / 11, 2))

    def test_vote_fees_still_count_toward_rev(self):
        # Votes are excluded from the *user fee* statistics but they are real
        # lamports paid to the network, so REV must still include them.
        summary = blocks.summarize_block(block([vote_tx(), vote_tx()]), slot=101)
        self.assertEqual(summary["rev_lamports"], 10_000)
        self.assertEqual(summary["tx_nonvote"], 0)

    def test_vote_only_block_has_no_fee_statistics(self):
        summary = blocks.summarize_block(block([vote_tx()]), slot=101)
        self.assertFalse(blocks.summarize_fees([summary])["available"])


class TestFeeSplit(unittest.TestCase):
    def test_message_signature_base_fee_is_a_lower_bound(self):
        summary = blocks.summarize_block(block([
            transaction(35_000, [account("alice", signer=True), account("bob", signer=True)],
                        signatures=2),
        ]), slot=101)
        self.assertEqual(summary["message_signature_base_fee_lower_bound_lamports"], 10_000)
        self.assertEqual(summary["unclassified_fee_lamports"], 25_000)
        self.assertEqual(
            summary["message_signature_base_fee_lower_bound_lamports"]
            + summary["unclassified_fee_lamports"],
            summary["fee_lamports"],
        )
        self.assertNotIn("base_lamports", summary)
        self.assertNotIn("priority_lamports", summary)

    def test_a_fee_at_the_floor_has_no_unclassified_component(self):
        summary = blocks.summarize_block(block([
            transaction(5_000, [account("alice", signer=True)]),
        ]), slot=101)
        self.assertEqual(summary["message_signature_base_fee_lower_bound_lamports"], 5_000)
        self.assertEqual(summary["unclassified_fee_lamports"], 0)

    def test_unclassified_residual_is_never_negative_on_a_malformed_fee(self):
        # A fee below the signature floor should clamp, not produce a negative
        # residual that would quietly subtract from REV.
        summary = blocks.summarize_block(block([
            transaction(1_000, [account("a", signer=True), account("b", signer=True)],
                        signatures=2),
        ]), slot=101)
        self.assertEqual(summary["unclassified_fee_lamports"], 0)
        self.assertEqual(summary["message_signature_base_fee_lower_bound_lamports"], 1_000)
        self.assertGreaterEqual(summary["rev_lamports"], 0)

    def test_missing_message_signatures_leave_the_entire_fee_unclassified(self):
        entry = transaction(20_000, [account("alice", signer=True)])
        del entry["transaction"]["signatures"]
        summary = blocks.summarize_block(block([entry]), slot=101)
        self.assertEqual(summary["message_signature_base_fee_lower_bound_lamports"], 0)
        self.assertEqual(summary["unclassified_fee_lamports"], 20_000)


class TestJitoTips(unittest.TestCase):
    def test_tip_is_read_as_a_positive_balance_delta(self):
        summary = blocks.summarize_block(block([
            transaction(5_000, [account("alice", signer=True), account(JITO)],
                        pre=[1_000_000, 500], post=[900_000, 100_500]),
        ]), slot=101)
        self.assertEqual(summary["jito_lamports"], 100_000)
        self.assertEqual(summary["rev_lamports"], 105_000)

    def test_a_tip_account_that_only_pays_out_is_not_counted_as_a_tip(self):
        # Only inbound deltas are tips. A decrease must not become a negative
        # tip and quietly reduce REV.
        summary = blocks.summarize_block(block([
            transaction(5_000, [account("alice", signer=True), account(JITO)],
                        pre=[1_000, 900_000], post=[1_000, 800_000]),
        ]), slot=101)
        self.assertEqual(summary["jito_lamports"], 0)

    def test_transfers_to_other_accounts_are_not_tips(self):
        summary = blocks.summarize_block(block([
            transaction(5_000, [account("alice", signer=True), account("not-a-tip-account")],
                        pre=[1_000_000, 0], post=[900_000, 100_000]),
        ]), slot=101)
        self.assertEqual(summary["jito_lamports"], 0)

    def test_missing_balances_do_not_crash(self):
        entry = transaction(5_000, [account("alice", signer=True), account(JITO)])
        del entry["meta"]["preBalances"]
        self.assertEqual(blocks.summarize_block(block([entry]), slot=101)["jito_lamports"], 0)


class TestAddresses(unittest.TestCase):
    def two_blocks(self):
        first = blocks.summarize_block(block([
            transaction(5_000, [account("alice", signer=True), account("prog")]),
            transaction(5_000, [account("bob", signer=True), account("prog")]),
        ]), slot=101)
        second = blocks.summarize_block(block([
            transaction(5_000, [account("bob", signer=True), account("prog")]),
            transaction(5_000, [account("carol", signer=True), account("other")]),
        ]), slot=202)
        return [first, second]

    def test_fee_payers_are_a_union_not_a_sum(self):
        # bob appears in both blocks; counting him twice is the whole error.
        summary = blocks.summarize_addresses(self.two_blocks())
        self.assertEqual(summary["unique_fee_payers_sampled"], 3)
        self.assertEqual(summary["mean_fee_payers_per_block"], 2.0)

    def test_only_the_first_account_key_is_the_fee_payer(self):
        summary = blocks.summarize_addresses([
            blocks.summarize_block(block([
                transaction(10_000, [
                    account("payer", signer=True),
                    account("multisigner", signer=True),
                    account("program"),
                ], signatures=2),
            ]), slot=101),
        ])
        self.assertEqual(summary["unique_fee_payers_sampled"], 1)
        self.assertEqual(summary["mean_fee_payers_per_block"], 1.0)

    def test_daily_active_is_withheld_rather_than_extrapolated(self):
        # The central honesty property of this module.
        summary = blocks.summarize_addresses(self.two_blocks())
        self.assertIsNone(summary["daily_active_addresses"])
        self.assertFalse(summary["daily_active_available"])
        self.assertIn("not a daily total", summary["note"])

    def test_vote_validators_are_not_counted_as_active_addresses(self):
        # Vote signers are validators doing consensus work, not network users.
        summary = blocks.summarize_addresses([
            blocks.summarize_block(block([vote_tx("validator1"), vote_tx("validator2")]), slot=101),
        ])
        self.assertEqual(summary["unique_fee_payers_sampled"], 0)

    def test_non_signer_accounts_are_touched_but_are_not_payers(self):
        summary = blocks.summarize_addresses(self.two_blocks())
        self.assertEqual(summary["unique_accounts_sampled"], 5)  # 3 payers + 2 programs


class TestFeeDistribution(unittest.TestCase):
    def test_burn_is_measured_against_the_blocks_own_reward(self):
        # 100_000 in fees, leader received 70_000, so 30_000 burned. No burn
        # rate is assumed anywhere, which is what keeps this correct across a
        # protocol change to the fee split.
        summary = blocks.summarize_block(block([
            transaction(100_000, [account("alice", signer=True), account("prog")]),
        ], fee_reward=70_000), slot=101)
        result = blocks.summarize_fee_distribution([summary])
        self.assertEqual(result["burned_sol"], 3e-05)
        self.assertEqual(result["burned_pct"], 30.0)
        self.assertEqual(result["blocks_reconciled"], 1)

    def test_blocks_without_a_reward_entry_are_left_out_of_the_pairing(self):
        with_reward = blocks.summarize_block(block([
            transaction(100_000, [account("alice", signer=True)]),
        ], fee_reward=70_000), slot=101)
        without = blocks.summarize_block(block([
            transaction(900_000, [account("bob", signer=True)]),
        ]), slot=202)
        result = blocks.summarize_fee_distribution([with_reward, without])
        # Pairing the second block's fees with no reward would report them as
        # entirely burned and wreck the ratio.
        self.assertEqual(result["blocks_reconciled"], 1)
        self.assertEqual(result["burned_pct"], 30.0)

    def test_a_reward_larger_than_fees_is_unavailable_not_a_negative_burn(self):
        summary = blocks.summarize_block(block([
            transaction(10_000, [account("alice", signer=True)]),
        ], fee_reward=90_000), slot=101)
        self.assertFalse(blocks.summarize_fee_distribution([summary])["available"])

    def test_no_rewards_anywhere_is_unavailable(self):
        summary = blocks.summarize_block(block([
            transaction(10_000, [account("alice", signer=True)]),
        ]), slot=101)
        self.assertFalse(blocks.summarize_fee_distribution([summary])["available"])


class TestRev(unittest.TestCase):
    def summaries(self):
        return [
            blocks.summarize_block(block([
                transaction(15_000, [account("alice", signer=True), account(JITO)],
                            pre=[0, 0], post=[0, 5_000]),
            ]), slot=101),
            blocks.summarize_block(block([
                transaction(25_000, [account("bob", signer=True)]),
            ]), slot=202),
        ]

    def test_rev_is_transaction_fees_plus_detected_tips(self):
        result = blocks.summarize_rev(self.summaries(), blocks_in_window=1000)
        self.assertEqual(result["sampled_sol"]["transaction_fees"], 4e-05)
        self.assertEqual(result["sampled_sol"]["message_signature_base_fee_lower_bound"], 1e-05)
        self.assertEqual(result["sampled_sol"]["unclassified_fee_residual"], 3e-05)
        self.assertEqual(result["sampled_sol"]["jito_tips"], 5e-06)
        self.assertEqual(result["sampled_sol"]["total"], 4.5e-05)
        self.assertNotIn("base", result["sampled_sol"])
        self.assertNotIn("priority", result["sampled_sol"])
        self.assertIn("bounded between zero and the residual", result["fee_decomposition"])
        self.assertEqual(result["jito_tip_account_source"]["coverage"], "8/8")
        self.assertEqual(
            result["jito_tip_account_source"]["source_revision"],
            "93dec9d9e8ec0f2a20dea9f0a6f2d14bcd9494cd",
        )

    def test_sampled_components_keep_lamport_precision_when_reconciling(self):
        result = blocks.summarize_rev([{
            "fee_lamports": 501,
            "message_signature_base_fee_lower_bound_lamports": 1,
            "unclassified_fee_lamports": 500,
            "jito_lamports": 501,
            "rev_lamports": 1_002,
        }], blocks_in_window=1)
        sampled = result["sampled_sol"]
        self.assertEqual(sampled["transaction_fees"], 0.000000501)
        self.assertAlmostEqual(
            sampled["total"], sampled["transaction_fees"] + sampled["jito_tips"],
            places=12,
        )
        self.assertAlmostEqual(
            sampled["transaction_fees"],
            sampled["message_signature_base_fee_lower_bound"]
            + sampled["unclassified_fee_residual"],
            places=12,
        )

    def test_the_observed_window_estimate_is_scaled_by_measured_block_count(self):
        result = blocks.summarize_rev(self.summaries(), blocks_in_window=1000)
        # mean 22_500 lamports per block x 1000 blocks
        self.assertEqual(result["sample_mean_estimate_sol"], 0.02)
        self.assertTrue(result["estimated"])
        self.assertNotIn("estimated_24h_sol", result)

    def test_the_per_block_spread_is_published_with_the_estimate(self):
        # An estimate from a handful of bursty blocks must not read as precise.
        result = blocks.summarize_rev(self.summaries(), blocks_in_window=1000)
        self.assertEqual(result["per_block_sol"]["min"], 2e-05)
        self.assertEqual(result["per_block_sol"]["max"], 2.5e-05)

    def test_the_estimate_carries_a_descriptive_sample_mean_interval(self):
        # Repeat runs of the live sampler have moved this figure by a third, so
        # a bare point estimate would imply precision the sample cannot support.
        # Uses mainnet-scale fees: the daily figure is reported to two decimal
        # places, which a fraction of a lamport would round away.
        realistic = [
            blocks.summarize_block(block([
                transaction(fee, [account("payer%d" % fee, signer=True)]),
            ]), slot=slot)
            for slot, fee in enumerate([20_000_000, 45_000_000, 31_000_000, 60_000_000])
        ]
        result = blocks.summarize_rev(realistic, blocks_in_window=215_719)
        interval = result["sample_mean_interval"]
        self.assertLess(interval["low_sol"], result["sample_mean_estimate_sol"])
        self.assertGreater(interval["high_sol"], result["sample_mean_estimate_sol"])
        self.assertIn("95%", interval["method"])
        self.assertIn("temporal", interval["limitation"])
        self.assertIn("network", interval["limitation"])

    def test_the_interval_never_goes_negative(self):
        # A wide spread on a small mean would otherwise produce a negative
        # lower bound, which is not a possible quantity of fees.
        summaries = [
            blocks.summarize_block(block([transaction(5_000, [account("a", signer=True)])]), slot=1),
            blocks.summarize_block(block([transaction(9_000_000, [account("b", signer=True)])]), slot=2),
        ]
        result = blocks.summarize_rev(summaries, blocks_in_window=1000)
        self.assertEqual(result["sample_mean_interval"]["low_sol"], 0.0)

    def test_a_single_block_gets_no_interval_rather_than_a_zero_width_one(self):
        one = [blocks.summarize_block(block([
            transaction(15_000, [account("alice", signer=True)]),
        ]), slot=101)]
        result = blocks.summarize_rev(one, blocks_in_window=1000)
        self.assertIsNone(result["sample_mean_interval"])
        self.assertIsNotNone(result["sample_mean_estimate_sol"])

    def test_an_unmeasured_block_count_yields_no_window_estimate(self):
        result = blocks.summarize_rev(self.summaries(), blocks_in_window=None)
        self.assertIsNone(result["sample_mean_estimate_sol"])
        self.assertTrue(result["available"])  # sampled figures still stand


class TestSampleSlots(unittest.TestCase):
    def test_samples_span_the_whole_window_oldest_first(self):
        slots = blocks.sample_slots(1_000_000, samples=5, window_slots=200_000)
        self.assertEqual(len(slots), 5)
        self.assertEqual(slots, sorted(slots))
        self.assertEqual(slots[-1], 1_000_000 - blocks.FINALITY_LAG_SLOTS)
        self.assertEqual(slots[0], slots[-1] - 200_000)

    def test_spacing_is_even(self):
        slots = blocks.sample_slots(1_000_000, samples=5, window_slots=200_000)
        gaps = {slots[i + 1] - slots[i] for i in range(len(slots) - 1)}
        self.assertEqual(gaps, {50_000})

    def test_every_sample_stays_behind_the_head(self):
        for slot in blocks.sample_slots(1_000_000, samples=16):
            self.assertLessEqual(slot, 1_000_000 - blocks.FINALITY_LAG_SLOTS)

    def test_a_head_shallower_than_the_window_yields_nothing(self):
        self.assertEqual(blocks.sample_slots(1_000, samples=4, window_slots=216_000), [])

    def test_degenerate_requests_yield_nothing(self):
        self.assertEqual(blocks.sample_slots(1_000_000, samples=0), [])
        self.assertEqual(blocks.sample_slots(1_000_000, samples=4, window_slots=0), [])


class TestPercentile(unittest.TestCase):
    def test_nearest_rank(self):
        values = list(range(1, 101))
        self.assertEqual(blocks.percentile(values, 50), 50)
        self.assertEqual(blocks.percentile(values, 90), 90)
        self.assertEqual(blocks.percentile(values, 99), 99)
        self.assertEqual(blocks.percentile(values, 100), 100)

    def test_single_value_and_empty(self):
        self.assertEqual(blocks.percentile([7], 99), 7)
        self.assertIsNone(blocks.percentile([], 50))


class TestDegradation(unittest.TestCase):
    def test_unusable_bodies_return_none_rather_than_raising(self):
        for bad in (None, {}, "nope", [], {"transactions": "not a list"}):
            self.assertIsNone(blocks.summarize_block(bad, slot=1))

    def test_malformed_transactions_are_skipped_not_fatal(self):
        summary = blocks.summarize_block(block([
            None, "nope", {}, {"meta": {}}, {"transaction": {}},
            {"meta": {"fee": 1}, "transaction": {"accountKeys": "not a list"}},
            transaction(9_000, [account("alice", signer=True)]),
        ]), slot=101)
        self.assertEqual(summary["tx_nonvote"], 1)
        self.assertEqual(summary["fee_lamports"], 9_000)

    def test_no_sampled_blocks_is_unavailable_not_a_page_of_zeros(self):
        result = blocks.build_activity([], blocks.DEFAULT_ENDPOINT)
        self.assertFalse(result["available"])
        self.assertIn("reason", result)
        # Crucially: no zero-valued figure anywhere to be mistaken for data.
        self.assertNotIn("rev", result)
        self.assertNotIn("fees", result)

    def test_an_unmeasured_production_rate_does_not_block_the_section(self):
        summary = blocks.summarize_block(block([
            transaction(9_000, [account("alice", signer=True)]),
        ]), slot=101)
        result = blocks.build_activity([summary], blocks.DEFAULT_ENDPOINT, production_rate=None)
        self.assertTrue(result["available"])
        self.assertIsNone(result["window"]["production_rate"])
        self.assertIsNone(result["rev"]["sample_mean_estimate_sol"])

    def test_records_that_no_key_is_required(self):
        self.assertFalse(blocks.build_activity([], blocks.DEFAULT_ENDPOINT)["requires_api_key"])


class TestBuildActivity(unittest.TestCase):
    def activity(self):
        summaries = [
            blocks.summarize_block(block([
                transaction(20_000, [account("alice", signer=True), account("prog")]),
                vote_tx(),
            ], block_time=1_785_800_000, fee_reward=15_000), slot=101),
            blocks.summarize_block(block([
                transaction(40_000, [account("bob", signer=True), account("prog")]),
            ], block_time=1_785_879_012, fee_reward=30_000), slot=216_101),
        ]
        return blocks.build_activity(summaries, "https://rpc.example", production_rate=0.999)

    def test_window_reports_observed_elapsed_time_from_block_stamps(self):
        window = self.activity()["window"]
        self.assertEqual(window["first_block_time"], 1_785_800_000)
        self.assertEqual(window["last_block_time"], 1_785_879_012)
        self.assertEqual(window["observed_seconds"], 79_012)
        self.assertEqual(window["first_slot"], 101)
        self.assertEqual(window["last_slot"], 216_101)
        self.assertEqual(window["blocks_sampled"], 2)

    def test_rev_names_the_exact_window_and_sampling_bias(self):
        rev = self.activity()["rev"]
        self.assertEqual(rev["estimate_window_seconds"], 79_012)
        self.assertNotIn("24h", " ".join(str(value) for value in rev.values()))
        self.assertIn("temporal", rev["limitation"])
        self.assertIn("network", rev["limitation"])

    def test_every_subsection_is_present_and_available(self):
        activity = self.activity()
        for section in ("fees", "rev", "addresses", "fee_split"):
            self.assertTrue(activity[section]["available"], section)

    def test_a_full_run_is_not_marked_truncated(self):
        self.assertFalse(self.activity()["window"]["truncated"])

    def test_a_budget_stop_is_recorded_rather_than_hidden(self):
        # A short sample is still valid data, but presenting it as full
        # coverage would overstate it.
        summary = blocks.summarize_block(block([
            transaction(9_000, [account("alice", signer=True)]),
        ]), slot=101)
        window = blocks.build_activity([summary], "https://rpc.example",
                                       samples_requested=16, truncated=True)["window"]
        self.assertTrue(window["truncated"])
        self.assertEqual(window["blocks_sampled"], 1)
        self.assertEqual(window["blocks_requested"], 16)

    def test_the_endpoint_is_recorded_for_reproducibility(self):
        self.assertEqual(self.activity()["source"]["endpoint"], "https://rpc.example")

    def test_partial_sample_estimate_uses_the_observed_slot_span(self):
        summaries = [blocks.summarize_block(block([
            transaction(1_000_000, [account("alice", signer=True)]),
        ], block_time=1_785_800_000 + offset), slot=100 + offset)
            for offset in (0, 100)]
        result = blocks.build_activity(summaries, "https://rpc.example", production_rate=1.0,
                                       samples_requested=16, truncated=True)
        self.assertEqual(result["rev"]["estimated_blocks_in_window"], 100)
        self.assertEqual(result["rev"]["estimate_window_seconds"], 100)
        self.assertEqual(result["rev"]["sample_mean_estimate_sol"], 0.1)


if __name__ == "__main__":
    unittest.main()
