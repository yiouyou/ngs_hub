"""Standalone saved-fact tests: python -m unittest ngs_hub.api.test_pricing_snapshot."""

import json
import math
import unittest
from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch

from ngs_hub.api.pricing_snapshot import create_pricing_snapshot, get_recorded_breakdown


def component(label, unit_price, quantity=1):
	return {
		"label": label,
		"unit_price": unit_price,
		"quantity": quantity,
		"amount": None if unit_price is None else unit_price * quantity,
		**({"display": "TBD"} if unit_price is None else {}),
	}


def item(**values):
	return {
		"project_type": "Sequencing Only - 1M Reads",
		"service": "SEQ_ONLY_1M_READS",
		"sample_type": "Library",
		"species": "Human",
		"million_reads_per_sample": 100,
		"quantity": 1,
		"unit_price": 208,
		"amount": 208,
		**values,
	}


class TestPricingSnapshot(unittest.TestCase):
	def setUp(self):
		self.item = item()
		self.rows = [component("Sequencing (100M reads at $1.9/M)", 190), component("Library QC", 18)]
		self.raw = create_pricing_snapshot(self.item, self.rows)
		self.saved = json.loads(self.raw)

	def recorded(self, **values):
		return get_recorded_breakdown({**self.item, "pricing_details": self.raw, **values})

	def tampered(self, mutate):
		data = deepcopy(self.saved)
		mutate(data)
		return self.recorded(pricing_details=json.dumps(data))

	def test_round_trip_shape_and_100m_208_example(self):
		self.assertEqual(
			set(self.saved), {"version", "spec", "quantity", "unit_price", "amount", "components"}
		)
		self.assertEqual(self.saved["version"], 1)
		self.assertEqual(
			set(self.saved["spec"]),
			{
				"project_type",
				"service",
				"sample_type",
				"description",
				"species",
				"read_depth",
				"million_reads_per_sample",
				"data_analysis",
				"onsite_service",
				"onsite_location",
				"onsite_address",
				"tissue_dissociation",
				"nuclei_extraction",
				"library_qc",
				"add_on_sequencing",
				"confirmed_tbd_fee",
				"use_price_override",
				"override_unit_price",
			},
		)
		self.assertEqual(self.saved["amount"], 208)
		self.assertEqual(self.recorded(), self.rows)

	def test_all_fourteen_project_types_preserve_supplied_components(self):
		# Deliberately fixed recorded facts, not assertions about current catalog policy.
		fixtures = [
			("Bulk RNAseq", [("RNA sequencing", 99), ("Extra sequencing", 18), ("De novo assembly", 70)]),
			("WGS", [("WGS library prep", 45), ("WGS sequencing (45x at $11.8/x)", 531)]),
			("Shotgun Meta", [("Shotgun sequencing", 180), ("Shotgun metagenomics analysis", 50)]),
			("Microbial Genome Sequencing (<7M)", [("Microbial sequencing <7M", 70), ("DNA extraction", 15)]),
			("Microbial Genome Sequencing (7-20M)", [("Microbial sequencing 7-20M", 90)]),
			("Microbial Genome Sequencing (20-60M)", [("Microbial sequencing 20-60M", 120)]),
			("10x 3' Transcriptome", [("10x 3'", 2000), ("Tissue dissociation", 100)]),
			("10x 5' Transcriptome", [("10x 5'", 2200), ("Nuclei extraction", 150)]),
			("10x 5' VDJ", [("10x VDJ", 1000), ("On-site service", 500)]),
			("10x 5' Transcriptome + VDJ", [("10x combined", 3000), ("Confirmed TBD fee", 450)]),
			("Sequencing Only - 10B Lane", [("10B lane", 1500), ("Library QC", 18)]),
			("Sequencing Only - 25B Lane", [("25B lane", 3000), ("Library QC", 18)]),
			("Sequencing Only - 1M Reads", [("Sequencing (100M reads at $1.9/M)", 190), ("Library QC", 18)]),
			("Custom Project", [("Custom manual quote", 500)]),
		]
		self.assertEqual(len(fixtures), 14)
		for index, (project_type, prices) in enumerate(fixtures):
			with self.subTest(project_type=project_type):
				rows = [component(label, price) for label, price in prices]
				amount = sum(row["amount"] for row in rows)
				record = item(
					project_type=project_type,
					service=f"RECORDED-{index}",
					unit_price=amount,
					amount=amount,
					read_depth="45x" if project_type == "WGS" else None,
				)
				record["pricing_details"] = create_pricing_snapshot(record, rows)
				self.assertEqual(get_recorded_breakdown(record), rows)

	def test_wgs_45x_is_not_replaced_by_default_30x(self):
		record = item(project_type="WGS", service="WGS_30X", read_depth="45x", unit_price=576, amount=576)
		rows = [component("WGS library prep", 45), component("WGS sequencing (45x at $11.8/x)", 531)]
		record["pricing_details"] = create_pricing_snapshot(record, rows)
		for depth in ("45x", "45X", " 45.00 x ", "45"):
			with self.subTest(depth=depth):
				self.assertEqual(get_recorded_breakdown({**record, "read_depth": depth}), rows)
		for depth in (None, "", "30x", "46x"):
			with self.subTest(depth=depth):
				self.assertIsNone(get_recorded_breakdown({**record, "read_depth": depth}))

	def test_quote_reads_match_synthesized_order_depth_and_species_case(self):
		self.assertEqual(self.recorded(read_depth="100M", species=" HUMAN "), self.rows)
		self.assertEqual(self.recorded(read_depth="100.00 m", million_reads_per_sample=None), self.rows)
		self.assertEqual(self.recorded(read_depth="100M", million_reads_per_sample="100"), self.rows)
		self.assertIsNone(self.recorded(read_depth="99M"))
		self.assertIsNone(self.recorded(million_reads_per_sample=None))

	def test_fractional_reads_reordered_rows_and_non_sample_component_units(self):
		reads, quantity = 100.125, 2.5
		unit_price = reads * 1.9 + 18
		record = item(
			million_reads_per_sample=reads,
			quantity=quantity,
			unit_price=unit_price,
			amount=unit_price * quantity,
		)
		rows = [
			component("Library QC", 18, quantity),
			component("Sequencing per million", 1.9, reads * quantity),
		]
		record["pricing_details"] = create_pricing_snapshot(record, rows)
		self.assertEqual(get_recorded_breakdown({**record, "read_depth": "100.125M"}), rows)
		scaled = get_recorded_breakdown({**record, "quantity": 5, "amount": unit_price * 5})
		self.assertEqual(
			scaled, [component("Library QC", 18, 5), component("Sequencing per million", 1.9, reads * 5)]
		)

	def test_none_zero_and_check_flags_are_equivalent(self):
		fields = (
			"onsite_service",
			"tissue_dissociation",
			"nuclei_extraction",
			"library_qc",
			"add_on_sequencing",
			"use_price_override",
		)
		for value in (None, 0, 0.0, False, "0", ""):
			with self.subTest(value=value):
				self.assertEqual(self.recorded(**dict.fromkeys(fields, value)), self.rows)
		for value in (1, 1.0, True, "1"):
			record = item(library_qc=1)
			record["pricing_details"] = create_pricing_snapshot(record, self.rows)
			self.assertEqual(get_recorded_breakdown({**record, "library_qc": value}), self.rows)

	def test_optional_numeric_fields_normalize_only_absence_to_zero(self):
		for value in (None, "", 0, 0.0, "0"):
			with self.subTest(value=value):
				self.assertEqual(self.recorded(confirmed_tbd_fee=value, override_unit_price=value), self.rows)
		self.assertEqual(self.recorded(confirmed_tbd_fee=10), self.rows)

	def test_quantity_scaling_retains_rates_even_across_catalog_tiers(self):
		for quantity in (0.25, 2.5, 12, 49, 97):
			with self.subTest(quantity=quantity):
				rows = self.recorded(quantity=quantity, amount=208 * quantity)
				self.assertEqual(
					rows,
					[component(self.rows[0]["label"], 190, quantity), component("Library QC", 18, quantity)],
				)
		self.assertIsNone(self.recorded(quantity=2, amount=208))
		self.assertIsNone(self.recorded(quantity=2, amount=400, unit_price=200))
		self.assertEqual(self.recorded(unit_price=208.0001), self.rows)
		self.assertIsNone(self.recorded(quantity=0.01, unit_price=208.006, amount=2.08006))

	def test_scaling_requires_scaled_components_to_match_recorded_amount(self):
		record = item(unit_price=1, amount=1)
		rows = [component("Rounded recorded rate", 1.004)]
		record["pricing_details"] = create_pricing_snapshot(record, rows)
		self.assertEqual(get_recorded_breakdown(record), rows)
		self.assertIsNone(get_recorded_breakdown({**record, "quantity": 10, "amount": 10}))

	def test_changed_catalog_and_external_quote_reference_have_no_effect(self):
		class SavedFieldsOnly(dict):
			def get(self, key, default=None):
				if key in {"quote", "quote_item", "catalog", "rule_notes"}:
					raise AssertionError(f"Read external or derived field: {key}")
				return super().get(key, default)

		record = SavedFieldsOnly(
			{
				**self.item,
				"pricing_details": self.raw,
				"quote": "OTHER-CUSTOMER",
				"quote_item": "FOREIGN-ROW",
				"catalog": {"unit_price": 99999},
				"rule_notes": "Changed rules",
			}
		)
		with patch.dict("sys.modules", {"frappe": None}):
			self.assertEqual(get_recorded_breakdown(record), self.rows)
			self.assertEqual(create_pricing_snapshot(record, self.rows), self.raw)
		self.assertIsNone(get_recorded_breakdown(SavedFieldsOnly({**record, "pricing_details": None})))

	def test_every_price_spec_field_is_checked(self):
		changes = {
			"project_type": "Bulk RNAseq",
			"service": "OTHER",
			"sample_type": "DNA",
			"description": "Changed saved description",
			"species": "mouse",
			"read_depth": "30x",
			"million_reads_per_sample": 101,
			"data_analysis": "Mapping",
			"onsite_service": 1,
			"onsite_location": "Massachusetts",
			"onsite_address": "Boston, MA",
			"tissue_dissociation": 1,
			"nuclei_extraction": 1,
			"library_qc": 1,
			"add_on_sequencing": 1,
			"use_price_override": 1,
		}
		for field, value in changes.items():
			with self.subTest(field=field):
				self.assertIsNone(self.recorded(**{field: value}))
		for flag, field in (
			("onsite_service", "confirmed_tbd_fee"),
			("use_price_override", "override_unit_price"),
		):
			record = item(**{flag: 1, field: 208})
			record["pricing_details"] = create_pricing_snapshot(record, self.rows)
			self.assertIsNone(get_recorded_breakdown({**record, field: 209}))

	def test_defaults_are_not_inferred(self):
		record = item(project_type="WGS", service="WGS_30X", read_depth=None, million_reads_per_sample=None)
		data = json.loads(create_pricing_snapshot(record, self.rows))
		self.assertEqual(data["spec"]["read_depth"], "")
		self.assertEqual(data["spec"]["million_reads_per_sample"], 0)
		record["pricing_details"] = json.dumps(data)
		self.assertIsNone(get_recorded_breakdown({**record, "read_depth": "30x"}))

	def test_manual_discount_and_adjustment_are_saved_rows_not_new_fees(self):
		record = item(use_price_override=1, override_unit_price=200, unit_price=200, amount=200)
		rows = [*self.rows, component("Manual adjustment", -8)]
		record["pricing_details"] = create_pricing_snapshot(record, rows)
		self.assertEqual(get_recorded_breakdown(record), rows)
		self.assertEqual(
			get_recorded_breakdown({**record, "quantity": 3, "amount": 600}),
			[component(row["label"], row["unit_price"], 3) for row in rows],
		)
		self.assertEqual([row["label"] for row in self.recorded()], [row["label"] for row in self.rows])
		with self.assertRaises(ValueError):
			create_pricing_snapshot(record, self.rows)

	def test_tbd_fee_and_custom_pending_zero_total(self):
		pending = component("On-site service outside Massachusetts", None)
		record = item(
			project_type="10x 3' Transcriptome",
			onsite_service=1,
			onsite_location="Outside Massachusetts",
			onsite_address="Test City, NY",
		)
		rows = [*self.rows, pending]
		record["pricing_details"] = create_pricing_snapshot(record, rows)
		self.assertEqual(get_recorded_breakdown(record), rows)
		self.assertEqual(
			get_recorded_breakdown({**record, "quantity": 2, "amount": 416})[-1],
			component(pending["label"], None, 2),
		)
		custom = item(project_type="Custom Project", unit_price=0, amount=0)
		custom_rows = [component("Manual quote", None)]
		custom["pricing_details"] = create_pricing_snapshot(custom, custom_rows)
		self.assertEqual(get_recorded_breakdown(custom), custom_rows)
		self.assertEqual(
			get_recorded_breakdown({**custom, "quantity": 3}), [component("Manual quote", None, 3)]
		)

	def test_half_cent_tolerance_without_relative_tolerance(self):
		for amount in (207.995, 208.005):
			with self.subTest(amount=amount):
				record = item(amount=amount)
				record["pricing_details"] = create_pricing_snapshot(record, self.rows)
				self.assertEqual(get_recorded_breakdown(record), self.rows)
		for amount in (207.9949, 208.0051):
			with self.subTest(amount=amount), self.assertRaises(ValueError):
				create_pricing_snapshot(item(amount=amount), self.rows)
		self.assertIsNone(self.recorded(unit_price=1e12, amount=1e12 + 1))

	def test_labels_are_plain_preserved_text_for_consumers_to_escape(self):
		rows = [component('<b>Recorded price & "label"</b>', 208)]
		raw = create_pricing_snapshot(self.item, rows)
		self.assertEqual(self.recorded(pricing_details=raw), rows)

	def test_helpers_do_not_mutate_arguments_or_return_shared_rows(self):
		before_item, before_rows = deepcopy(self.item), deepcopy(self.rows)
		create_pricing_snapshot(self.item, self.rows)
		self.assertEqual(self.item, before_item)
		self.assertEqual(self.rows, before_rows)
		record = {**self.item, "pricing_details": self.raw}
		before = deepcopy(record)
		get_recorded_breakdown(record)[0]["label"] = "Changed return value"
		self.assertEqual(record, before)
		self.assertEqual(get_recorded_breakdown(record), self.rows)

	def test_missing_malformed_or_non_string_json_returns_none(self):
		for raw in (
			None,
			"",
			"{",
			"null",
			"[]",
			"42",
			'"text"',
			"{}",
			{},
			[],
			1,
			True,
			"[" * 2000 + "]" * 2000,
		):
			with self.subTest(raw=repr(raw)[:50]):
				self.assertIsNone(self.recorded(pricing_details=raw))
		self.assertIsNone(get_recorded_breakdown(None))
		self.assertIsNone(get_recorded_breakdown([]))

	def test_duplicate_json_fields_are_rejected(self):
		for raw in (
			self.raw.replace('"version":1', '"version":1,"version":1'),
			self.raw.replace('"species":"human"', '"species":"mouse","species":"human"'),
			self.raw.replace('"unit_price":190.0', '"unit_price":0,"unit_price":190.0'),
		):
			with self.subTest(raw=raw):
				self.assertIsNone(self.recorded(pricing_details=raw))

	def test_snapshot_structural_types_and_version_are_checked(self):
		for value in (None, "1", True, 1.0, 0, 2):
			with self.subTest(version=value):
				self.assertIsNone(self.tampered(lambda data: data.update(version=value)))
		for field in self.saved:
			with self.subTest(missing=field):
				self.assertIsNone(self.tampered(lambda data: data.pop(field)))
		self.assertIsNone(self.tampered(lambda data: data.update(unrecognized="field")))
		for value in (None, [], "{}", {**self.saved["spec"], "external_quote": "OTHER"}):
			with self.subTest(spec=value):
				self.assertIsNone(self.tampered(lambda data: data.update(spec=value)))
		for field in self.saved["spec"]:
			with self.subTest(missing_spec=field):
				self.assertIsNone(self.tampered(lambda data: data["spec"].pop(field)))

	def test_invalid_spec_types_are_rejected_on_creation_and_retrieval(self):
		for field, value in (
			("species", []),
			("service", {}),
			("description", []),
			("description", 500),
			("project_type", 1),
			("read_depth", 45),
			("onsite_service", []),
			("onsite_service", "yes"),
			("library_qc", 2),
			("million_reads_per_sample", -1),
			("confirmed_tbd_fee", False),
		):
			with self.subTest(field=field, value=value):
				with self.assertRaises(ValueError):
					create_pricing_snapshot(item(**{field: value}), self.rows)
				self.assertIsNone(self.recorded(**{field: value}))
		for field, value in (
			("species", None),
			("description", None),
			("description", []),
			("onsite_service", True),
			("million_reads_per_sample", "100"),
			("confirmed_tbd_fee", None),
		):
			with self.subTest(saved_field=field, value=value):
				self.assertIsNone(self.tampered(lambda data: data["spec"].update({field: value})))

	def test_current_quantity_prices_and_negative_totals_are_rejected(self):
		for field, values in (
			("quantity", (None, 0, -1, True, [], "bad")),
			("unit_price", (None, -1, True, [], "bad")),
			("amount", (None, -1, True, [], "bad", 209)),
		):
			for value in values:
				with self.subTest(field=field, value=value):
					self.assertIsNone(self.recorded(**{field: value}))
					with self.assertRaises(ValueError):
						create_pricing_snapshot(item(**{field: value}), self.rows)
		for field in ("quantity", "unit_price", "amount"):
			self.assertIsNone(self.tampered(lambda data: data.update({field: str(data[field])})))

	def test_components_reject_bad_structure_and_inconsistent_rates(self):
		invalid = [None, {}, [], [None], [208], ["row"], [{"label": "Missing prices"}]]
		for field, value in (
			("label", None),
			("label", ""),
			("label", "  "),
			("label", []),
			("quantity", 0),
			("quantity", -1),
			("quantity", True),
			("quantity", "1"),
			("unit_price", "208"),
			("unit_price", True),
			("amount", "208"),
			("amount", 207),
			("amount", -208),
			("display", "TBD"),
			("extra", "field"),
		):
			invalid.append([{**component("Recorded", 208), field: value}])
		for rows in invalid:
			with self.subTest(rows=rows):
				with self.assertRaises(ValueError):
					create_pricing_snapshot(self.item, rows)
				self.assertIsNone(self.tampered(lambda data: data.update(components=rows)))
		self.assertIsNone(
			self.tampered(lambda data: data["components"].reverse() or data["components"].pop())
		)

	def test_incomplete_or_misleading_tbd_rows_are_rejected(self):
		for changes in (
			{"display": None},
			{"display": 1},
			{"display": "pending"},
			{"unit_price": 0},
			{"amount": 0},
			{"quantity": 0},
		):
			rows = [*self.rows, {**component("Pending", None), **changes}]
			with self.subTest(changes=changes):
				with self.assertRaises(ValueError):
					create_pricing_snapshot(self.item, rows)
				self.assertIsNone(self.tampered(lambda data: data.update(components=rows)))

	def test_nonfinite_values_are_rejected_everywhere(self):
		for value in (math.nan, math.inf, -math.inf, "NaN", "Infinity", "1e9999", 10**400):
			for field in (
				"quantity",
				"unit_price",
				"amount",
				"million_reads_per_sample",
				"confirmed_tbd_fee",
				"override_unit_price",
				"onsite_service",
			):
				with self.subTest(field=field, value=str(value)):
					with self.assertRaises(ValueError):
						create_pricing_snapshot(item(**{field: value}), self.rows)
					self.assertIsNone(self.recorded(**{field: value}))
			for field in ("unit_price", "quantity", "amount"):
				rows = [{**component("Nonfinite", 208), field: value}]
				with self.subTest(component_field=field, value=str(value)):
					with self.assertRaises(ValueError):
						create_pricing_snapshot(self.item, rows)
					self.assertIsNone(self.tampered(lambda data: data.update(components=rows)))
		for raw_number in ("NaN", "Infinity", "-Infinity", "1e9999"):
			self.assertIsNone(
				self.recorded(pricing_details=self.raw.replace('"amount":208.0', f'"amount":{raw_number}'))
			)
		for depth in ("NaNM", "InfinityM", "-1M", "NaNx", "1e9999x"):
			with self.subTest(depth=depth), self.assertRaises(ValueError):
				create_pricing_snapshot(item(read_depth=depth), self.rows)

	def test_arithmetic_overflow_is_rejected_without_raising_during_retrieval(self):
		record = item(quantity=1e308, unit_price=1e308, amount=1e308)
		with self.assertRaises(ValueError):
			create_pricing_snapshot(record, [component("Overflow", 1e308, 1e308)])
		self.assertIsNone(get_recorded_breakdown({**record, "pricing_details": self.raw}))
		rows = [
			component("Large positive", 1e308),
			component("Large positive", 1e308),
			component("Large discount", -1e308),
			component("Large discount", -1e308),
			component("Price", 208),
		]
		with self.assertRaises(ValueError):
			create_pricing_snapshot(self.item, rows)
		self.assertIsNone(self.tampered(lambda data: data.update(components=rows)))

	def test_currency_round_trip_handles_101m_binary_float_and_db_precision(self):
		unit_price = 101 * 1.9 + 18
		self.assertNotEqual(unit_price, 209.9)
		rows = [component("Sequencing (101M reads at $1.9/M)", 101 * 1.9), component("Library QC", 18)]
		record = item(million_reads_per_sample=101, unit_price=unit_price, amount=unit_price)
		record["pricing_details"] = create_pricing_snapshot(record, rows)
		# Mimic the DECIMAL storage boundary without opening a database connection.
		for precision in ("0.01", "0.000001"):
			persisted = json.loads(json.dumps(record))
			for field in ("unit_price", "amount"):
				persisted[field] = float(Decimal(str(persisted[field])).quantize(Decimal(precision)))
			with self.subTest(precision=precision):
				self.assertEqual(persisted["unit_price"], 209.9)
				self.assertEqual(get_recorded_breakdown(persisted), rows)
				self.assertEqual(
					get_recorded_breakdown({**persisted, "quantity": 3, "amount": 629.7}),
					[component(row["label"], row["unit_price"], 3) for row in rows],
				)

	def test_numeric_spec_fields_survive_six_decimal_storage_but_not_changed_specs(self):
		record = item(
			million_reads_per_sample=100.123456789,
			onsite_service=1,
			confirmed_tbd_fee=50.987654321,
			use_price_override=1,
			override_unit_price=208.000000123,
		)
		record["pricing_details"] = create_pricing_snapshot(record, self.rows)
		persisted = json.loads(json.dumps(record))
		fields = ("million_reads_per_sample", "confirmed_tbd_fee", "override_unit_price")
		for field in fields:
			persisted[field] = float(Decimal(str(persisted[field])).quantize(Decimal("0.000001")))
		self.assertEqual(get_recorded_breakdown(persisted), self.rows)
		self.assertEqual(get_recorded_breakdown({**persisted, "read_depth": "100.123456789M"}), self.rows)
		for field in fields:
			for difference in ("0.0000011", "0.00001", "0.004"):
				value = float(Decimal(str(record[field])) + Decimal(difference))
				with self.subTest(field=field, difference=difference):
					self.assertIsNone(get_recorded_breakdown({**record, field: value}))
		for depth in ("100.123458M", "100.127M"):
			self.assertIsNone(get_recorded_breakdown({**persisted, "read_depth": depth}))
		for field, value in (
			("species", "mouse"),
			("sample_type", "DNA"),
			("onsite_service", 0),
			("use_price_override", 0),
		):
			self.assertIsNone(get_recorded_breakdown({**persisted, field: value}))

	def test_disabled_stale_negative_override_and_onsite_fee_are_ignored(self):
		record = item(
			use_price_override="0", override_unit_price=-999, onsite_service="0", confirmed_tbd_fee=-999
		)
		record["pricing_details"] = create_pricing_snapshot(record, self.rows)
		spec = json.loads(record["pricing_details"])["spec"]
		self.assertEqual(spec["override_unit_price"], 0)
		self.assertEqual(spec["confirmed_tbd_fee"], 0)
		self.assertEqual(get_recorded_breakdown(record), self.rows)
		self.assertEqual(
			get_recorded_breakdown({**record, "override_unit_price": 0, "confirmed_tbd_fee": 0}), self.rows
		)
		self.assertEqual(self.recorded(use_price_override="0", override_unit_price=-999), self.rows)
		for flag, field in (
			("use_price_override", "override_unit_price"),
			("onsite_service", "confirmed_tbd_fee"),
		):
			with self.subTest(flag=flag):
				with self.assertRaises(ValueError):
					create_pricing_snapshot(item(**{flag: "1", field: -999}), self.rows)
				self.assertIsNone(self.recorded(**{flag: "1", field: -999}))
			for value in ([], {}, True, math.inf, "not-a-number"):
				with self.subTest(field=field, value=value), self.assertRaises(ValueError):
					create_pricing_snapshot(item(**{flag: "0", field: value}), self.rows)

	def test_custom_description_changes_reject_stale_recorded_basis(self):
		description = "Custom whole-transcriptome analysis"
		record = item(project_type="Custom Project", description=description, unit_price=500, amount=500)
		rows = [component(description, 500)]
		record["pricing_details"] = create_pricing_snapshot(record, rows)
		self.assertEqual(json.loads(record["pricing_details"])["spec"]["description"], description)
		self.assertEqual(get_recorded_breakdown(record), rows)
		self.assertEqual(
			get_recorded_breakdown({**record, "quantity": 2, "amount": 1000}),
			[component(description, 500, 2)],
		)
		for changed in ("Custom targeted analysis", description.upper(), None, ""):
			with self.subTest(description=changed):
				self.assertIsNone(get_recorded_breakdown({**record, "description": changed}))
				self.assertIsNone(
					get_recorded_breakdown({**record, "description": changed, "quantity": 2, "amount": 1000})
				)

	def test_empty_descriptions_normalize_and_legacy_missing_description_is_rejected(self):
		for description in (None, "", "  "):
			with self.subTest(description=description):
				self.assertEqual(self.recorded(description=description), self.rows)
		self.assertEqual(self.saved["spec"]["description"], "")
		self.assertIsNone(self.tampered(lambda data: data["spec"].pop("description")))


if __name__ == "__main__":
	unittest.main()
