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

import sys
import unittest
from pathlib import Path

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
    def test_base_is_per_signature_and_the_remainder_is_priority(self):
        summary = blocks.summarize_block(block([
            transaction(35_000, [account("alice", signer=True), account("bob", signer=True)],
                        signatures=2),
        ]), slot=101)
        self.assertEqual(summary["base_lamports"], 10_000)    # 2 x 5000
        self.assertEqual(summary["priority_lamports"], 25_000)

    def test_a_fee_at_the_floor_has_no_priority_component(self):
        summary = blocks.summarize_block(block([
            transaction(5_000, [account("alice", signer=True)]),
        ]), slot=101)
        self.assertEqual(summary["base_lamports"], 5_000)
        self.assertEqual(summary["priority_lamports"], 0)

    def test_priority_is_never_negative_on_a_malformed_fee(self):
        # A fee below the signature floor should clamp, not produce a negative
        # priority fee that would quietly subtract from REV.
        summary = blocks.summarize_block(block([
            transaction(1_000, [account("a", signer=True), account("b", signer=True)],
                        signatures=2),
        ]), slot=101)
        self.assertEqual(summary["priority_lamports"], 0)
        self.assertEqual(summary["base_lamports"], 1_000)
        self.assertGreaterEqual(summary["rev_lamports"], 0)


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

    def test_rev_is_base_plus_priority_plus_tips(self):
        result = blocks.summarize_rev(self.summaries(), blocks_in_window=1000)
        self.assertEqual(result["sampled_sol"]["base"], 1e-05)        # 2 x 5000
        self.assertEqual(result["sampled_sol"]["priority"], 3e-05)    # 10k + 20k
        self.assertEqual(result["sampled_sol"]["jito_tips"], 5e-06)
        self.assertEqual(result["sampled_sol"]["total"], 4.5e-05)

    def test_the_daily_figure_is_scaled_by_measured_block_count(self):
        result = blocks.summarize_rev(self.summaries(), blocks_in_window=1000)
        # mean 22_500 lamports per block x 1000 blocks
        self.assertEqual(result["estimated_24h_sol"], 0.02)
        self.assertTrue(result["estimated"])

    def test_the_per_block_spread_is_published_with_the_estimate(self):
        # An estimate from a handful of bursty blocks must not read as precise.
        result = blocks.summarize_rev(self.summaries(), blocks_in_window=1000)
        self.assertEqual(result["per_block_sol"]["min"], 2e-05)
        self.assertEqual(result["per_block_sol"]["max"], 2.5e-05)

    def test_the_daily_estimate_carries_a_confidence_interval(self):
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
        self.assertLess(result["estimated_24h_sol_low"], result["estimated_24h_sol"])
        self.assertGreater(result["estimated_24h_sol_high"], result["estimated_24h_sol"])
        self.assertIn("95%", result["confidence"])

    def test_the_interval_never_goes_negative(self):
        # A wide spread on a small mean would otherwise produce a negative
        # lower bound, which is not a possible quantity of fees.
        summaries = [
            blocks.summarize_block(block([transaction(5_000, [account("a", signer=True)])]), slot=1),
            blocks.summarize_block(block([transaction(9_000_000, [account("b", signer=True)])]), slot=2),
        ]
        result = blocks.summarize_rev(summaries, blocks_in_window=1000)
        self.assertEqual(result["estimated_24h_sol_low"], 0.0)

    def test_a_single_block_gets_no_interval_rather_than_a_zero_width_one(self):
        one = [blocks.summarize_block(block([
            transaction(15_000, [account("alice", signer=True)]),
        ]), slot=101)]
        result = blocks.summarize_rev(one, blocks_in_window=1000)
        self.assertIsNone(result["estimated_24h_sol_low"])
        self.assertIsNone(result["confidence"])
        self.assertIsNotNone(result["estimated_24h_sol"])

    def test_an_unmeasured_block_count_yields_no_daily_estimate(self):
        result = blocks.summarize_rev(self.summaries(), blocks_in_window=None)
        self.assertIsNone(result["estimated_24h_sol"])
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
        self.assertIsNone(result["rev"]["estimated_24h_sol"])

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
            ], block_time=1_785_886_400, fee_reward=30_000), slot=216_101),
        ]
        return blocks.build_activity(summaries, "https://rpc.example", production_rate=0.999)

    def test_window_reports_observed_elapsed_time_from_block_stamps(self):
        window = self.activity()["window"]
        self.assertEqual(window["observed_seconds"], 86_400)
        self.assertEqual(window["first_slot"], 101)
        self.assertEqual(window["last_slot"], 216_101)
        self.assertEqual(window["blocks_sampled"], 2)

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


if __name__ == "__main__":
    unittest.main()
