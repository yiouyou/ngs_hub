"""Saved quote/order specification regressions; no business records are written."""

from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import frappe

from ngs_hub.api.desk_item_details import legacy_order_detail_fallbacks
from ngs_hub.api.pricing_snapshot import create_pricing_snapshot, get_recorded_breakdown
from ngs_hub.ngs_hub.doctype.ngs_order.ngs_order import NGSOrder


class TestOrderDetailCopy(TestCase):
	def test_fractional_reads_preserve_snapshot_through_order_copy(self):
		quote = frappe.get_doc(
			{
				"doctype": "NGS Quote",
				"items": [
					{
						"name": "FRACTIONAL-ITEM",
						"project_type": "Sequencing Only - 1M Reads",
						"quantity": 1,
						"million_reads_per_sample": 100.123456,
						"unit_price": 208.2345664,
						"amount": 208.2345664,
					}
				],
			}
		)
		components = [
			{
				"label": "Saved sequencing and QC",
				"quantity": 1,
				"unit_price": 208.2345664,
				"amount": 208.2345664,
			}
		]
		quote.items[0].pricing_details = create_pricing_snapshot(quote.items[0], components)
		order = NGSOrder({"doctype": "NGS Order", "items": []})
		order.copy_items_from_quote(quote)
		self.assertEqual(order.items[0].read_depth, "100.123456M")
		self.assertEqual(get_recorded_breakdown(order.items[0]), components)

	def test_wgs_45x_is_copied_instead_of_catalog_30x_label(self):
		quote = frappe.get_doc(
			{
				"doctype": "NGS Quote",
				"items": [
					{
						"name": "QUOTE-ITEM-45X",
						"project_type": "WGS",
						"service": "WGS_30X",
						"sample_type": "DNA",
						"species": "human",
						"read_depth": "45x",
						"quantity": 1,
						"data_analysis": "Standard Analysis",
						"unit_price": 576,
						"amount": 576,
					}
				],
			}
		)
		order = NGSOrder({"doctype": "NGS Order", "items": []})
		order.copy_items_from_quote(quote)
		row = order.items[0]
		self.assertEqual(
			(row.project_type, row.service, row.species, row.read_depth), ("WGS", "WGS_30X", "human", "45x")
		)
		self.assertEqual((row.unit_price, row.amount, row.details_snapshot_version), (576, 576, 1))
		self.assertEqual(row.quote_item, "QUOTE-ITEM-45X")
		quote.set_project_summary()
		self.assertIn("WGS 45x", quote.project_summary)
		self.assertIn("x1", quote.project_summary)
		self.assertIn("DNA", quote.project_summary)

	def test_reads_preprocess_and_confirmed_manual_price_are_copied(self):
		values = {
			"name": "QUOTE-ITEM",
			"project_type": "10x 3' Transcriptome",
			"service": "10X_3_TRANSCRIPTOME",
			"sample_type": "Frozen Cells",
			"quantity": 2,
			"million_reads_per_sample": 350.5,
			"add_on_sequencing": 1,
			"nuclei_extraction": 1,
			"tissue_dissociation": 0,
			"data_analysis": "Standard Analysis",
			"onsite_service": 1,
			"onsite_location": "Outside Massachusetts",
			"onsite_address": "Test City, NY",
			"confirmed_tbd_fee": 450,
			"use_price_override": 1,
			"override_unit_price": 3000,
			"unit_price": 3000,
			"amount": 6000,
			"rule_notes": "Recorded quote notes",
		}
		quote = frappe.get_doc({"doctype": "NGS Quote", "items": [deepcopy(values)]})
		order = NGSOrder({"doctype": "NGS Order", "items": []})
		order.copy_items_from_quote(quote)
		row = order.items[0]
		for field in values.keys() - {"name", "rule_notes"}:
			with self.subTest(field=field):
				self.assertEqual(row.get(field), values[field])
		self.assertEqual((row.read_depth, row.notes), ("350.5M", "Recorded quote notes"))


class TestLegacyDetailFallback(TestCase):
	def setUp(self):
		self.row = frappe._dict(
			name="ORDER-ITEM", quote_item="QUOTE-ITEM", project_type="WGS", read_depth="45x"
		)
		self.order = frappe._dict(quote="QUOTE", customer="CUSTOMER", items=[self.row])
		self.source = frappe._dict(name="QUOTE-ITEM", project_type="WGS", read_depth="30x", species="human")
		self.quote = SimpleNamespace(customer="CUSTOMER", items=[self.source], has_permission=lambda p: True)

	def fallback(self):
		with (
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe, "get_doc", return_value=self.quote),
		):
			return legacy_order_detail_fallbacks(self.order)

	def test_legacy_specs_are_explicit_non_mutating_fallbacks(self):
		before = deepcopy(self.order)
		result = self.fallback()[self.row.name]
		self.assertEqual(result["read_depth"], "30x")
		self.assertEqual(result["species"], "human")
		self.assertEqual(result["source"], "Linked quote (legacy order)")
		self.assertEqual(self.order, before)
		self.assertEqual(self.row.read_depth, "45x")

	def test_new_snapshot_does_not_read_live_quote_even_if_missing_fields(self):
		self.row.details_snapshot_version = 1
		with patch.object(frappe, "get_doc") as lookup:
			self.assertEqual(legacy_order_detail_fallbacks(self.order), {})
			lookup.assert_not_called()

	def test_legacy_fallback_checks_customer_parent_and_permissions(self):
		for change in ("customer", "permission", "parent_item", "project"):
			with self.subTest(change=change):
				self.setUp()
				if change == "customer":
					self.quote.customer = "OTHER-CUSTOMER"
				elif change == "permission":
					self.quote.has_permission = lambda p: False
				elif change == "parent_item":
					self.source.name = "OTHER-ITEM"
				else:
					self.source.project_type = "Shotgun Meta"
				self.assertEqual(self.fallback(), {})

	def test_missing_quote_or_unlinked_item_has_no_fallback(self):
		self.order.quote = None
		self.assertEqual(legacy_order_detail_fallbacks(self.order), {})
		self.order.quote = "QUOTE"
		self.row.quote_item = None
		self.assertEqual(legacy_order_detail_fallbacks(self.order), {})


class TestPortalOrderSource(TestCase):
	def test_small_unit_override_balances_against_line_amount(self):
		from ngs_hub.api import portal

		row = frappe._dict(
			project_type="Sequencing Only - 1M Reads",
			million_reads_per_sample=100,
			quantity=10,
			unit_price=208.001,
			amount=2080.01,
			use_price_override=1,
			override_unit_price=208.001,
		)
		prices = {"SEQ_ONLY_1M_READS": 1.9, "LIBRARY_QC": 18}
		with patch.object(portal, "get_service_price", side_effect=prices.__getitem__):
			components = portal.price_breakdown(row, use_snapshot=False)
		self.assertAlmostEqual(sum(part["amount"] for part in components), 2080.01)
		self.assertEqual(components[-1]["label"], "Manual adjustment")
		row.pricing_details = create_pricing_snapshot(row, components)
		self.assertEqual(get_recorded_breakdown(row), components)

	def test_saved_quote_projection_retains_override_and_recorded_basis(self):
		from ngs_hub.api import portal

		row = frappe._dict(
			parent="QUOTE",
			project_type="Custom Project",
			description="Custom assay",
			quantity=2,
			unit_price=250,
			amount=500,
			use_price_override=1,
			override_unit_price=250,
		)
		components = [{"label": "Confirmed custom assay", "quantity": 2, "unit_price": 250, "amount": 500}]
		row.pricing_details = create_pricing_snapshot(row, components)
		quotes = [frappe._dict(name="QUOTE")]

		def projected_rows(doctype, **kwargs):
			self.assertEqual(doctype, "NGS Quote Item")
			return [frappe._dict({field: row.get(field) for field in kwargs["fields"]})]

		with (
			patch.object(frappe, "get_all", side_effect=projected_rows),
			patch.object(
				frappe.db,
				"get_value",
				side_effect=AssertionError("Saved pricing must not read the live catalog"),
			),
		):
			portal.attach_quote_items(quotes)
		self.assertEqual(quotes[0]["items"][0].price_breakdown, components)
		self.assertFalse(quotes[0].has_unpriced_fees)

	def test_customer_item_payload_cannot_replace_validated_quote_details(self):
		from ngs_hub.api import portal

		order = frappe.get_doc({"doctype": "NGS Order", "name": "TEST-ORDER", "status": "Submitted"})
		captured = []

		def capture(values):
			captured.append(values)
			return order

		with (
			patch.object(portal, "get_current_customer", return_value="CUSTOMER"),
			patch.object(portal, "validate_customer_quote", return_value="QUOTE-45X"),
			patch.object(portal, "validate_order_documents"),
			patch.object(portal, "notify_order_created"),
			patch.object(frappe, "get_doc", side_effect=capture),
			patch.object(NGSOrder, "insert") as insert,
		):
			portal.create_order(
				{
					"quote": "QUOTE-45X",
					"no_po": 1,
					"sample_registration_form": "/private/files/test.xlsx",
					"items": [
						{"project_type": "WGS", "service": "WGS_30X", "read_depth": "30x", "unit_price": 1}
					],
				}
			)
			self.assertEqual(captured[0]["quote"], "QUOTE-45X")
			self.assertNotIn("items", captured[0])
			self.assertFalse(order.items)
			insert.assert_called_once_with(ignore_permissions=True)
