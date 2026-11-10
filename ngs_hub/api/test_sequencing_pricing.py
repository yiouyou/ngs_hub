"""Progressive sequencing contracts; in-memory documents and no business writes."""

import json
from copy import deepcopy
from decimal import Decimal
from itertools import permutations
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from ngs_hub.api import desk_quote, portal
from ngs_hub.api.pricing_snapshot import get_recorded_breakdown
from ngs_hub.api.sequencing_pricing import allocate_sequencing, apply_document_sequencing, progressive_bands
from ngs_hub.api.test_desk_quote import DeskQuoteTestCase
from ngs_hub.ngs_hub.doctype.ngs_order.ngs_order import NGSOrder

SEQUENCING = "Sequencing Only - 1M Reads"
DNA = "DNA Small Fragment Library Prep & Sequencing"
RATES = (Decimal("1.9"), Decimal("1.25"), Decimal("0.54"), Decimal("1"))
RATE_CODES = (
	"SEQ_ONLY_1M_READS",
	"SEQ_ONLY_1M_READS_201_600",
	"SEQ_ONLY_1M_READS_601_1200",
	"SEQ_ONLY_1M_READS_OVER_1200",
)
BOUNDARIES = (
	("1", "1.90"),
	("200", "380.00"),
	("201", "381.25"),
	("600", "880.00"),
	("601", "880.54"),
	("1200", "1204.00"),
	("1201", "1205.00"),
	("0.125", "0.24"),
	("200.125", "380.16"),
	("600.125", "880.07"),
	("1200.125", "1204.13"),
)
INVALID_NUMBERS = (None, "", "bad", True, -1, float("nan"), float("inf"), float("-inf"), "NaN", "Infinity")


def sequencing_row(name="SEQ-A", reads=150, quantity=1, **values):
	return {
		"name": name,
		"project_type": SEQUENCING,
		"quantity": quantity,
		"million_reads_per_sample": reads,
		**values,
	}


def money_sum(rows):
	return sum((Decimal(str(row["amount"])) for row in rows), Decimal(0))


class TestProgressiveBands(TestCase):
	def test_independent_boundary_and_fractional_totals(self):
		for reads, expected in BOUNDARIES:
			with self.subTest(reads=reads):
				bands = progressive_bands(reads, RATES)
				self.assertEqual(money_sum(bands), Decimal(expected))
				self.assertEqual(sum(band["reads"] for band in bands), Decimal(reads))
				for band in bands:
					for field in ("reads", "rate", "amount"):
						self.assertIsInstance(band[field], Decimal)

	def test_each_band_has_its_own_capacity_and_rate(self):
		bands = progressive_bands("1201", RATES)
		self.assertEqual(
			[(band["reads"], band["rate"], band["amount"]) for band in bands],
			[
				(Decimal(200), Decimal("1.9"), Decimal(380)),
				(Decimal(400), Decimal("1.25"), Decimal(500)),
				(Decimal(600), Decimal("0.54"), Decimal(324)),
				(Decimal(1), Decimal(1), Decimal(1)),
			],
		)
		self.assertEqual(progressive_bands(0, RATES), [])

	def test_cents_round_half_up_per_band(self):
		self.assertEqual(
			[band["amount"] for band in progressive_bands("200.004", RATES)], [Decimal(380), Decimal("0.01")]
		)
		self.assertEqual(
			[band["amount"] for band in progressive_bands("1200.005", RATES)],
			[Decimal(380), Decimal(500), Decimal(324), Decimal("0.01")],
		)

	def test_invalid_totals_and_missing_or_nonfinite_rates_are_rejected(self):
		for reads in INVALID_NUMBERS:
			with self.subTest(reads=reads), self.assertRaises(ValueError):
				progressive_bands(reads, RATES)
		for rates in ((), RATES[:3], (*RATES, 1)):
			with self.subTest(rates=rates), self.assertRaises(ValueError):
				progressive_bands(1, rates)
		for index in range(4):
			for invalid in INVALID_NUMBERS:
				with self.subTest(index=index, invalid=invalid), self.assertRaises(ValueError):
					rates = list(RATES)
					rates[index] = invalid
					progressive_bands(1, rates)


class TestSequencingAllocation(TestCase):
	def assert_components_balance(self, result):
		self.assertEqual(money_sum(result["components"]), Decimal(str(result["amount"])))
		for part in result["components"]:
			self.assertLessEqual(
				abs(
					Decimal(str(part["unit_price"])) * Decimal(str(part["quantity"]))
					- Decimal(str(part["amount"]))
				),
				Decimal("0.005"),
			)

	def test_quantity_times_reads_is_pooled_and_qc_is_per_library(self):
		result = allocate_sequencing([sequencing_row(quantity=2)], RATES, 18)[0]
		self.assertEqual((result["unit_price"], result["amount"]), (270.5, 541))
		self.assertEqual([part["amount"] for part in result["components"]], [380, 125, 36])
		self.assertEqual(result["components"][-1]["label"], "Library QC")
		self.assertEqual(result["rule_notes"].count("Library QC added."), 1)
		self.assert_components_balance(result)
		result = allocate_sequencing([sequencing_row(reads=100, quantity=3)], RATES, 18)[0]
		self.assertEqual(result["amount"], 559)
		self.assertEqual(result["components"][-1]["amount"], 54)
		self.assert_components_balance(result)

	def test_below_200m_example_remains_321(self):
		results = allocate_sequencing([sequencing_row("A", 100), sequencing_row("B", 50)], RATES, 18)
		self.assertEqual([row["amount"] for row in results], [208, 113])
		self.assertEqual(money_sum(results), Decimal(321))

	def test_no_sequencing_rows_preserve_existing_parameters_without_catalog_access(self):
		recorded = json.dumps({"version": 1, "rates": [1.9, 1.25, 0.54, 1], "qc_fee": 18}, indent=2)
		for rows in (
			[],
			[{"project_type": DNA, "quantity": 2, "million_reads_per_sample": 150}],
			[
				{"project_type": "Sequencing Only - 10B Lane", "quantity": 1},
				{"project_type": "Sequencing Only - 25B Lane", "quantity": 2},
			],
		):
			for parameters in (recorded, None):
				with self.subTest(rows=rows, parameters=parameters):
					before = deepcopy(rows)
					price_getter = Mock(
						side_effect=AssertionError("Excluded rows must not read catalog rates")
					)
					self.assertEqual(apply_document_sequencing(rows, price_getter, parameters), parameters)
					self.assertEqual(rows, before)
					price_getter.assert_not_called()

	def test_cent_ties_preserve_manual_total_and_align_preview_identity_with_saved_rows(self):
		for manual_name, plain_name in (("b", "a"), ("a", "b")):
			for hints in ({}, {"manual": "a", "plain": "b"}, {"manual": "b", "plain": "a"}):
				rows = [
					sequencing_row(manual_name, "1.05", use_price_override=1, override_unit_price=100),
					sequencing_row(plain_name, "1.05"),
				]
				if hints:
					rows[0]["pricing_allocation_key"] = hints["manual"]
					rows[1]["pricing_allocation_key"] = hints["plain"]
				for ordered in permutations(rows):
					with self.subTest(names=[row["name"] for row in ordered], hints=hints):
						results = allocate_sequencing(ordered, RATES, 18)
						self.assertEqual(money_sum(results), Decimal("120.00"))
						for row, result in zip(ordered, results, strict=True):
							manual = bool(row.get("use_price_override"))
							self.assertEqual(result["amount"], 100 if manual else 20)
							self.assertEqual(
								[part["amount"] for part in result["components"]],
								[1.99, 18, 80.01] if manual else [2, 18],
							)
							self.assert_components_balance(result)
		saved = [sequencing_row("a", "1.05"), sequencing_row("b", "1.05")]
		saved_results = allocate_sequencing(saved, RATES, 18)
		self.assertEqual([result["amount"] for result in saved_results], [20, 19.99])
		expected = {row["name"]: result for row, result in zip(saved, saved_results, strict=True)}
		preview = [
			sequencing_row("preview-b", "1.05", pricing_allocation_key="a"),
			sequencing_row("preview-a", "1.05", pricing_allocation_key="b"),
		]
		for ordered in permutations(preview):
			with self.subTest(preview_order=[row["name"] for row in ordered]):
				results = allocate_sequencing(ordered, RATES, 18)
				self.assertEqual(
					{
						row["pricing_allocation_key"]: result
						for row, result in zip(ordered, results, strict=True)
					},
					expected,
				)

	def test_split_merge_and_permutations_preserve_cents_and_band_totals(self):
		merged = allocate_sequencing([sequencing_row("MERGED", "200.01", 3)], RATES, 18)
		split = [sequencing_row(name, "200.01") for name in ("C", "A", "B")]
		for rows in permutations(split):
			with self.subTest(order=[row["name"] for row in rows]):
				results = allocate_sequencing(rows, RATES, 18)
				self.assertEqual(money_sum(results), Decimal("934.02"))
				self.assertEqual(money_sum(results), money_sum(merged))
				self.assertEqual(
					{row["name"]: result["amount"] for row, result in zip(rows, results, strict=True)},
					{"A": 311.35, "B": 311.35, "C": 311.32},
				)
				for index, expected in enumerate(("380", "500", "0.02", "54")):
					self.assertEqual(
						money_sum([row["components"][index] for row in results]), Decimal(expected)
					)
				for result in results:
					self.assert_components_balance(result)

	def test_read_proportional_allocation_is_stable_for_unequal_quantities(self):
		rows = [sequencing_row("A", "0.125", 3), sequencing_row("B", 300), sequencing_row("C", 200, 2)]
		baseline = allocate_sequencing(rows, RATES, 18)
		self.assertEqual(money_sum(baseline), Decimal("1042.20"))
		for ordered in permutations(rows):
			results = allocate_sequencing(ordered, RATES, 18)
			self.assertEqual(
				{row["name"]: result for row, result in zip(ordered, results, strict=True)},
				{row["name"]: result for row, result in zip(rows, baseline, strict=True)},
			)
			for index, expected in enumerate(("380", "500", "54.20", "108")):
				self.assertEqual(money_sum([row["components"][index] for row in results]), Decimal(expected))

	def test_manual_override_keeps_reads_in_pool_and_records_explicit_adjustment(self):
		rows = [sequencing_row("A", use_price_override=1, override_unit_price=190), sequencing_row("B")]
		results = allocate_sequencing(rows, RATES, 18)
		self.assertEqual([row["amount"] for row in results], [190, 270.5])
		self.assertEqual([part["amount"] for part in results[0]["components"]], [190, 62.5, 18, -80.5])
		self.assertIn("Manual", results[0]["components"][-1]["label"])
		for result in results:
			self.assert_components_balance(result)
		for override, expected, adjustment in ((190, 380, -161), (0, 0, -541), (300, 600, 59)):
			with self.subTest(override=override):
				result = allocate_sequencing(
					[sequencing_row(quantity=2, use_price_override=1, override_unit_price=override)],
					RATES,
					18,
				)[0]
				self.assertEqual((result["unit_price"], result["amount"]), (override, expected))
				self.assertEqual(result["components"][-1]["amount"], adjustment)
				self.assert_components_balance(result)

	def test_inputs_and_returned_components_are_not_shared_or_mutated(self):
		rows = [sequencing_row("A"), sequencing_row("B")]
		original = deepcopy(rows)
		results = allocate_sequencing(rows, RATES, 18)
		self.assertEqual(rows, original)
		results[0]["components"][0]["amount"] = -1
		self.assertEqual(results[1]["components"][0]["amount"], 190)
		self.assertEqual(allocate_sequencing(rows, RATES, 18)[0]["components"][0]["amount"], 190)
		self.assertEqual(allocate_sequencing([], RATES, 18), [])

	def test_invalid_quantities_reads_qc_overrides_and_missing_rates_are_rejected(self):
		for field in ("quantity", "million_reads_per_sample"):
			for invalid in (*INVALID_NUMBERS, 0):
				with self.subTest(field=field, invalid=invalid), self.assertRaises(ValueError):
					allocate_sequencing([sequencing_row(**{field: invalid})], RATES, 18)
		for invalid in INVALID_NUMBERS:
			with self.subTest(qc=invalid), self.assertRaises(ValueError):
				allocate_sequencing([sequencing_row()], RATES, invalid)
			with self.subTest(override=invalid), self.assertRaises(ValueError):
				allocate_sequencing(
					[sequencing_row(use_price_override=1, override_unit_price=invalid)], RATES, 18
				)
		for rates in (RATES[:3], (*RATES[:3], None)):
			with self.subTest(rates=rates), self.assertRaises(ValueError):
				allocate_sequencing([sequencing_row()], rates, 18)


class TestSequencingControllers(DeskQuoteTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		order = NGSOrder({"doctype": "NGS Order", "items": [{"quantity": 1}]})
		for doc in (order, order.items[0]):
			doc.meta.get_valid_fields()

	def assert_snapshot(self, row, expected_components=None):
		components = get_recorded_breakdown(row)
		self.assertIsNotNone(components)
		self.assertEqual(money_sum(components), Decimal(str(row.amount)))
		if expected_components is not None:
			self.assertEqual([part["amount"] for part in components], expected_components)
		with patch.object(
			portal, "get_service_price", side_effect=AssertionError("Snapshot must not reprice")
		):
			self.assertEqual(portal.price_breakdown(row), components)
		return components

	def test_controller_boundary_totals_and_saved_components(self):
		for reads, fee in BOUNDARIES:
			with self.subTest(reads=reads):
				quote = self.make_quote(sequencing_row(reads=reads))
				quote.validate()
				self.assertEqual(quote.sequencing_pricing_version, 1)
				self.assertEqual(Decimal(str(quote.total_amount)), Decimal(fee) + 18)
				parts = self.assert_snapshot(quote.items[0])
				self.assertEqual((parts[-1]["label"], parts[-1]["amount"]), ("Library QC", 18))

	def test_fixed_lanes_and_dna_do_not_contribute_to_progressive_pool(self):
		quote = self.make_quote(
			sequencing_row(quantity=2),
			{"project_type": "Sequencing Only - 10B Lane", "quantity": 2},
			{"project_type": "Sequencing Only - 25B Lane", "quantity": 1},
			{"project_type": DNA, "quantity": 2, "million_reads_per_sample": 300},
		)
		quote.validate()
		self.assertEqual([row.amount for row in quote.items], [541, 3236, 2618, 990])
		self.assertEqual(quote.total_amount, 7385)
		self.assert_snapshot(quote.items[0], [380, 125, 36])
		self.assertEqual((quote.items[3].library_qc, quote.items[3].add_on_sequencing), (0, 0))
		for row in quote.items:
			self.assert_snapshot(row)

	def test_dna_bills_all_reads_plus_prep_without_qc_or_included_reads(self):
		for reads, unit_price in ((1, 46.5), (0.125, 45.1875), (1.5, 47.25), (200, 345), (1201, 1846.5)):
			with self.subTest(reads=reads):
				_, row = self.price(DNA, million_reads_per_sample=reads, quantity=2)
				self.assertEqual(row.service, "DNA_SMALL_FRAGMENT_PREP")
				self.assertEqual((row.unit_price, row.amount), (unit_price, unit_price * 2))
				self.assertEqual((row.library_qc, row.add_on_sequencing), (0, 0))
				self.assert_snapshot(row, [90, (unit_price - 45) * 2])

	def test_manual_override_snapshot_keeps_progressive_components_and_adjustment(self):
		quote = self.make_quote(
			sequencing_row("A", use_price_override=1, override_unit_price=190), sequencing_row("B")
		)
		quote.validate()
		self.assertEqual(quote.total_amount, 460.5)
		self.assert_snapshot(quote.items[0], [190, 62.5, 18, -80.5])
		self.assert_snapshot(quote.items[1], [190, 62.5, 18])
		before = self.snapshot(quote)
		for _ in range(3):
			quote.validate()
			self.assertEqual(self.snapshot(quote), before)

	def test_saved_previous_version_wins_over_forged_field_or_ephemeral_flag(self):
		for version, unit_price, amount in ((0, 303, 606), (1, 270.5, 541)):
			with self.subTest(version=version):
				quote = self.make_quote(
					sequencing_row(quantity=2), name="QUOTE-STORED", sequencing_pricing_version=version
				)
				quote._doc_before_save = deepcopy(quote)
				quote.sequencing_pricing_version = 1 - version
				quote.flags.sequencing_pricing_version = 1 - version
				for _ in range(3):
					quote.validate()
					self.assertEqual(quote.sequencing_pricing_version, version)
					self.assertEqual(
						(quote.items[0].unit_price, quote.items[0].amount, quote.total_amount),
						(unit_price, amount, amount),
					)
					self.assert_snapshot(quote.items[0])

	def test_new_quote_defaults_progressive_but_legacy_preview_flag_is_honored(self):
		for flag, version, amount in ((None, 1, 541), (0, 0, 606), (1, 1, 541)):
			with self.subTest(flag=flag):
				quote = self.make_quote(sequencing_row(quantity=2), sequencing_pricing_version=0)
				if flag is not None:
					quote.flags.sequencing_pricing_version = flag
				quote.validate()
				self.assertEqual((quote.sequencing_pricing_version, quote.total_amount), (version, amount))

	def test_missing_nonfinite_and_negative_tier_rates_fail_closed(self):
		for code in (*RATE_CODES, "LIBRARY_QC"):
			for invalid in (None, -1, "NaN", "Infinity"):
				with self.subTest(code=code, invalid=invalid):
					original = self.catalog[code]
					try:
						if invalid is None:
							del self.catalog[code]
						else:
							self.catalog[code] = frappe._dict({**original, "unit_price": invalid})
						with self.assertRaises(frappe.ValidationError):
							self.price(SEQUENCING, million_reads_per_sample=1201)
					finally:
						self.catalog[code] = original

	def test_nonfinite_and_malformed_reads_are_rejected_for_dna_and_sequencing(self):
		for project in (DNA, SEQUENCING):
			for reads in (-1, float("nan"), float("inf"), float("-inf"), "NaN", "Infinity", "bad"):
				with self.subTest(project=project, reads=reads), self.assertRaises(frappe.ValidationError):
					self.price(project, million_reads_per_sample=reads)

	def test_quoted_order_retains_version_rates_and_snapshot_after_catalog_changes(self):
		quote = self.make_quote(sequencing_row(quantity=2))
		quote.validate()
		quote.name = "QUOTED"
		parameters = json.loads(quote.sequencing_pricing_rates)
		self.assertEqual(parameters, {"version": 1, "rates": [1.9, 1.25, 0.54, 1], "qc_fee": 18})
		original = self.assert_snapshot(quote.items[0], [380, 125, 36])
		for code in (*RATE_CODES, "LIBRARY_QC"):
			self.catalog[code].unit_price = 999
		order = self.make_order(quote)
		with (
			patch.object(frappe, "get_cached_doc", return_value=quote),
			patch.object(frappe.db, "get_value", return_value=270.5),
		):
			order.validate()
		self.assertEqual(order.sequencing_pricing_version, 1)
		self.assertEqual(json.loads(order.sequencing_pricing_rates), parameters)
		self.assertEqual((order.items[0].unit_price, order.items[0].amount), (270.5, 541))
		self.assertEqual(self.assert_snapshot(order.items[0]), original)
		order.name = "ORDER-STORED"
		order.set("__islocal", 0)
		order._doc_before_save = deepcopy(order)
		order.sequencing_pricing_version = 0
		order.sequencing_pricing_rates = "forged"
		order.items[0].quantity = 4
		with (
			patch.object(frappe, "get_cached_doc", return_value=quote),
			patch.object(frappe.db, "get_value", return_value=999),
		):
			order.validate()
		self.assertEqual(order.sequencing_pricing_version, 1)
		self.assertEqual(json.loads(order.sequencing_pricing_rates), parameters)
		self.assertEqual((order.items[0].unit_price, order.items[0].amount), (238, 952))
		self.assert_snapshot(order.items[0], [380, 500, 72])

	def test_saved_quote_uses_previous_rates_after_catalog_changes_or_forged_parameters(self):
		quote = self.make_quote(sequencing_row(quantity=2))
		quote.validate()
		parameters = quote.sequencing_pricing_rates
		quote.name = "QUOTE-STORED"
		quote.set("__islocal", 0)
		quote._doc_before_save = deepcopy(quote)
		for code in (*RATE_CODES, "LIBRARY_QC"):
			self.catalog[code].unit_price = 999
		forged = json.dumps({"version": 1, "rates": [999] * 4, "qc_fee": 999})
		quote.sequencing_pricing_rates = forged
		quote.flags.sequencing_pricing_rates = forged
		for _ in range(3):
			quote.validate()
			self.assertEqual(json.loads(quote.sequencing_pricing_rates), json.loads(parameters))
			self.assertEqual((quote.items[0].unit_price, quote.total_amount), (270.5, 541))
			self.assert_snapshot(quote.items[0], [380, 125, 36])

	def test_new_quote_captures_current_rates_but_private_preview_can_supply_saved_rates(self):
		parameters = json.dumps({"version": 1, "rates": [1.9, 1.25, 0.54, 1], "qc_fee": 18})
		for code in RATE_CODES:
			self.catalog[code].unit_price = 2
		self.catalog["LIBRARY_QC"].unit_price = 19
		quote = self.make_quote(sequencing_row(quantity=2), sequencing_pricing_rates=parameters)
		quote.validate()
		self.assertEqual(quote.total_amount, 638)
		self.assertEqual(
			json.loads(quote.sequencing_pricing_rates), {"version": 1, "rates": [2] * 4, "qc_fee": 19}
		)
		preview = self.make_quote(sequencing_row(quantity=2))
		preview.flags.sequencing_pricing_version = 1
		preview.flags.sequencing_pricing_rates = parameters
		preview.validate()
		self.assertEqual(preview.total_amount, 541)
		self.assert_snapshot(preview.items[0], [380, 125, 36])

	def test_legacy_quote_to_order_does_not_upgrade_to_progressive(self):
		quote = self.make_quote(sequencing_row(quantity=2))
		quote.flags.sequencing_pricing_version = 0
		quote.validate()
		quote.name = "LEGACY-QUOTED"
		order = self.make_order(quote)
		with (
			patch.object(frappe, "get_cached_doc", return_value=quote),
			patch.object(frappe.db, "get_value", return_value=303),
		):
			order.validate()
		self.assertEqual(order.sequencing_pricing_version, 0)
		self.assertEqual((order.items[0].unit_price, order.items[0].amount), (303, 606))
		self.assert_snapshot(order.items[0], [570, 36])

	@staticmethod
	def make_order(quote):
		return NGSOrder(
			{
				"doctype": "NGS Order",
				"__islocal": 1,
				"quote": quote.name,
				"customer": quote.customer,
				"no_po_available": 1,
				"sample_registration_form": "/private/files/in-memory-only.xlsx",
				"items": [],
			}
		)


class TestBatchSequencingPreview(DeskQuoteTestCase):
	def test_new_batch_prices_whole_pool_without_mutating_payload_or_saved_quote(self):
		rows = [sequencing_row("A"), sequencing_row("B")]
		original = deepcopy(rows)
		before = self.snapshot(self.existing_quote)
		result = desk_quote.preview_quote_items(json.dumps(rows), customer="CUSTOMER-TEST")
		self.assertEqual((result["sequencing_pricing_version"], result["total_amount"]), (1, 541))
		self.assertEqual([row["amount"] for row in result["items"]], [270.5, 270.5])
		for row in result["items"]:
			self.assertEqual([part["amount"] for part in get_recorded_breakdown(row)], [190, 62.5, 18])
		self.assertEqual(rows, original)
		self.assertEqual(self.snapshot(self.existing_quote), before)

	def test_existing_quote_batch_uses_saved_version_not_client_flags(self):
		for version, expected in ((0, 606), (1, 541)):
			with self.subTest(version=version):
				self.existing_quote.sequencing_pricing_version = version
				before = deepcopy(self.existing_quote.as_dict())
				result = desk_quote.preview_quote_items(
					[
						sequencing_row(
							quantity=2, sequencing_pricing_version=1 - version, ignore_permissions=1
						)
					],
					quote_name="QUOTE-TEST",
					customer="CUSTOMER-TEST",
				)
				self.assertEqual(
					(result["sequencing_pricing_version"], result["total_amount"]), (version, expected)
				)
				self.assertEqual(self.existing_quote.as_dict(), before)

	def test_batch_rejects_invalid_shapes_and_oversized_payloads_before_document_creation(self):
		for values in (
			None,
			{},
			[],
			1,
			True,
			"null",
			"{}",
			[None],
			[sequencing_row(), "bad"],
			[sequencing_row()] * 201,
		):
			with self.subTest(type=type(values).__name__), self.assertRaises(frappe.ValidationError):
				desk_quote.preview_quote_items(values)
		self.new_doc.assert_not_called()

	def test_existing_batch_uses_saved_rates_after_catalog_edit_and_ignores_client_rates(self):
		parameters = json.dumps({"version": 1, "rates": [1.9, 1.25, 0.54, 1], "qc_fee": 18})
		self.existing_quote.sequencing_pricing_version = 1
		self.existing_quote.sequencing_pricing_rates = parameters
		for code in (*RATE_CODES, "LIBRARY_QC"):
			self.catalog[code].unit_price = 999
		before = deepcopy(self.existing_quote.as_dict())
		result = desk_quote.preview_quote_items(
			[sequencing_row(quantity=2, sequencing_pricing_rates="forged")],
			quote_name="QUOTE-TEST",
		)
		self.assertEqual((result["sequencing_pricing_version"], result["total_amount"]), (1, 541))
		self.assertEqual(
			[part["amount"] for part in get_recorded_breakdown(result["items"][0])], [380, 125, 36]
		)
		self.assertEqual(self.existing_quote.as_dict(), before)

	def test_batch_requires_internal_user_and_quote_customer_permissions(self):
		for user, user_type, enabled, roles in (
			("Guest", "System User", 1, ["NGS Internal Staff"]),
			("web@example.test", "Website User", 1, ["System Manager"]),
			("staff@example.test", "System User", 0, ["NGS Internal Staff"]),
			("unrelated@example.test", "System User", 1, ["Sales User"]),
		):
			with self.subTest(user=user), self.assertRaises(frappe.PermissionError):
				self.session.user, self.user_type, self.enabled, self.roles = user, user_type, enabled, roles
				desk_quote.preview_quote_items([sequencing_row()])
		self.session.user, self.user_type, self.enabled, self.roles = (
			"staff@example.test",
			"System User",
			1,
			["NGS Internal Staff"],
		)
		for permission in ("allow_create", "allow_write", "allow_customer_read"):
			with self.subTest(permission=permission):
				setattr(self, permission, False)
				try:
					with self.assertRaises(frappe.PermissionError):
						desk_quote.preview_quote_items(
							[sequencing_row()],
							quote_name="QUOTE-TEST" if permission == "allow_write" else None,
							customer="CUSTOMER-TEST",
						)
				finally:
					setattr(self, permission, True)
		self.new_doc.assert_not_called()
