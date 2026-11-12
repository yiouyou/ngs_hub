"""Real controllers with in-memory documents; no fixtures, commits, mail, or ERP writes."""

import json
from copy import deepcopy
from decimal import Decimal
from html import unescape
from random import Random
from unittest import TestCase
from unittest.mock import patch

import frappe
from frappe.model.document import Document

from ngs_hub.api import invoicing, order_adjustments
from ngs_hub.api.pricing_snapshot import create_pricing_snapshot, get_recorded_breakdown
from ngs_hub.ngs_hub.doctype.ngs_order.ngs_order import NGSOrder


class TestOrderAdjustments(TestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.template = NGSOrder({
			"doctype": "NGS Order", "name": "ORDER-TEST", "__islocal": 0, "docstatus": 0,
			"customer": "CUSTOMER-TEST", "quote": "QUOTE-TEST", "status": "In Review",
			"no_po_available": 1, "sample_registration_form": "/private/files/test.xlsx",
			"sequencing_pricing_version": 0,
			"items": [{
				"name": name, "quote_item": "QUOTE-" + name, "project_type": "Bulk RNAseq",
				"sample_type": "Total RNA", "species": "human", "description": "Recorded service",
				"quantity": quantity, "unit_price": price, "amount": quantity * price,
				"use_price_override": 0, "override_unit_price": 0,
				"million_reads_per_sample": 20, "add_on_sequencing": 0, "library_qc": 0,
			} for name, quantity, price in (("ROW-A", 3, 100), ("ROW-B", 1, 50))],
		})
		cls.customer_template = frappe.get_doc({"doctype": "NGS Customer", "name": "CUSTOMER-TEST"})
		for doc in (cls.template, *cls.template.items, cls.customer_template):
			doc.meta.get_valid_fields()
		for row in cls.template.items:
			row.pricing_details = create_pricing_snapshot(row, [{
				"label": "Recorded library preparation", "quantity": row.quantity,
				"unit_price": row.unit_price, "amount": row.amount,
			}])

	def setUp(self):
		self.order = deepcopy(self.template)
		self.customer = deepcopy(self.customer_template)
		self.quote = frappe._dict(customer=self.customer.name, items=deepcopy(self.order.items))
		for row in self.quote.get("items"):
			row.name = row.quote_item
		self.session = frappe._dict(user="staff@example.test")
		self.roles = ["NGS Internal Staff"]
		self.user_type = "System User"
		self.enabled = 1
		self.erp_status = 0
		self.denied = None
		self.patch(frappe, "session", self.session)
		self.patch(frappe, "get_roles", side_effect=lambda user: self.roles)
		self.lookup = self.patch(frappe.db, "get_value", side_effect=self.get_value)
		self.documents = self.patch(frappe, "get_doc", side_effect=self.get_doc)
		self.patch(frappe, "get_cached_doc", return_value=self.quote)
		self.permissions = self.patch(Document, "check_permission", autospec=True, side_effect=self.check_permission)
		self.patch(NGSOrder, "load_doc_before_save", autospec=True, side_effect=self.load_before_save)
		self.save = self.patch(NGSOrder, "save", autospec=True, side_effect=self.save_order)
		self.audit = self.patch(NGSOrder, "add_comment", autospec=True)
		self.guards = [self.patch(frappe.db, name, side_effect=AssertionError(f"Unexpected write: {name}"))
			for name in ("sql", "commit", "set_value", "delete")]

	def patch(self, owner, name, *args, **kwargs):
		patcher = patch.object(owner, name, *args, **kwargs)
		result = patcher.start()
		self.addCleanup(patcher.stop)
		return result

	def tearDown(self):
		for guard in self.guards:
			guard.assert_not_called()

	def get_value(self, doctype, name, field, **kwargs):
		if doctype == "User":
			return self.enabled if field == "enabled" else self.user_type
		if doctype == "Sales Order":
			self.assertTrue(kwargs.get("for_update"))
			return self.erp_status
		if doctype == "NGS Order":
			return self.order.name
		if doctype == "NGS Invoice":
			return None
		self.assertEqual((doctype, field), ("NGS Quote Item", "unit_price"))
		return {"QUOTE-ROW-A": 100, "QUOTE-ROW-B": 50}[name]

	def get_doc(self, doctype, name, **kwargs):
		if doctype == "NGS Order":
			self.assertEqual(name, self.order.name)
			self.assertTrue(kwargs.get("for_update"))
			return self.order
		self.assertEqual((doctype, name), ("NGS Customer", self.customer.name))
		return self.customer

	def check_permission(self, doc, permission):
		self.assertIn((doc.doctype, permission), (("NGS Order", "write"), ("NGS Customer", "read")))
		if self.denied == doc.doctype:
			raise frappe.PermissionError("Denied")

	def load_before_save(self, doc, **kwargs):
		doc._doc_before_save = deepcopy(doc)

	def save_order(self, doc):
		doc.validate()
		doc.link_quote()

	def adjust(self, **kwargs):
		return order_adjustments.adjust_order(self.order.name, reason="Failed sample review", **kwargs)

	def test_internal_authorization_rejects_guest_disabled_website_and_unprivileged_users(self):
		for user, enabled, kind, roles in (
			("Guest", 1, "System User", self.roles),
			("staff@example.test", 0, "System User", self.roles),
			("customer@example.test", 1, "Website User", ["System Manager", "NGS Internal Staff"]),
			("staff@example.test", 1, "System User", ["Customer"]),
		):
			self.session.user, self.enabled, self.user_type, self.roles = user, enabled, kind, roles
			with self.subTest(user=user, enabled=enabled, kind=kind), self.assertRaises(frappe.PermissionError):
				self.adjust()
		self.documents.assert_not_called()
		self.save.assert_not_called()

	def test_saved_order_write_and_customer_read_permissions_are_required(self):
		for doctype in ("NGS Order", "NGS Customer"):
			self.denied = doctype
			with self.subTest(doctype=doctype), self.assertRaises(frappe.PermissionError):
				self.adjust()
		self.save.assert_not_called()
		self.audit.assert_not_called()

	def test_post_only_and_reason_required(self):
		self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[order_adjustments.adjust_order], ["POST"])
		for reason in (None, "", "  ", 42):
			with self.subTest(reason=reason), self.assertRaises(frappe.ValidationError):
				order_adjustments.adjust_order(self.order.name, reason=reason)
		self.documents.assert_not_called()

	def test_patch_omission_retains_rows_and_quoted_specs_and_prices(self):
		quote = [deepcopy(row.as_dict()) for row in self.quote.get("items")]
		original = deepcopy(self.order.items[0].as_dict())
		result = self.adjust(items=json.dumps([{"name": "ROW-A", "quantity": 2}]))
		self.assertEqual(result["total_amount"], 250)
		self.assertEqual([row.name for row in self.order.items], ["ROW-A", "ROW-B"])
		for field in ("species", "description", "project_type", "million_reads_per_sample", "quote_item", "unit_price"):
			self.assertEqual(self.order.items[0].get(field), original[field])
		self.assertEqual([row.as_dict() for row in self.quote.get("items")], quote)
		self.assertEqual(get_recorded_breakdown(self.order.items[0])[0]["amount"], 200)
		self.audit.assert_called_once()
		self.assertNotIn("order_adjustment_token", self.order.flags)

	def test_explicit_zero_quantity_removes_and_does_not_recopy_quote_rows(self):
		result = self.adjust(items=[{"name": "ROW-A", "quantity": 0}])
		self.assertEqual([(row["name"], row["amount"]) for row in result["items"]], [("ROW-B", 50)])

	def test_removing_all_rows_requires_an_explicit_fee(self):
		with self.assertRaises(frappe.ValidationError):
			self.adjust(items=[{"name": row.name, "quantity": 0} for row in self.order.items])
		self.save.assert_not_called()
		self.order = deepcopy(self.template)
		result = self.adjust(items=[{"name": row.name, "quantity": 0} for row in self.order.items], processing_fee=25)
		self.assertEqual(result["total_amount"], 25)
		self.assertEqual(len(result["items"]), 1)
		self.assertEqual(self.order.items[0].is_processing_fee, 1)

	def test_manual_zero_is_explicit_not_tbd_and_preserves_prior_components(self):
		result = self.adjust(items=[{"name": "ROW-A", "use_price_override": 1, "override_unit_price": 0}])
		self.assertEqual(result["total_amount"], 50)
		parts = get_recorded_breakdown(self.order.items[0])
		self.assertEqual([part["amount"] for part in parts], [300, -300])
		self.assertIn("Manual", parts[-1]["label"])
		self.assertNotIn("TBD", self.order.items[0].pricing_details)

	def test_final_total_after_manual_edits_keeps_fee_fixed_and_exact_cents(self):
		result = self.adjust(
			items=[{"name": "ROW-A", "quantity": 3, "use_price_override": 1, "override_unit_price": 25}],
			processing_fee=10, processing_description="Failed QC", final_total=10.01,
		)
		self.assertEqual(result["total_amount"], 10.01)
		self.assertEqual([row.amount for row in self.order.items], [0.01, 0, 10])
		self.assertEqual(self.order.items[0].override_unit_price, 0.003333333)
		fee = self.order.items[-1]
		self.assertEqual((fee.quantity, fee.unit_price, fee.description), (1, 10, "Sample processing fee: Failed QC"))
		self.assertFalse(fee.get("service"))
		for row in self.order.items:
			self.assertIsNotNone(get_recorded_breakdown(row))

	def test_existing_fee_marker_not_description_controls_fixed_allocation(self):
		self.order.items[1].update({"is_processing_fee": 1, "quote_item": None, "project_type": "Custom Project",
			"description": "Historic failed-sample processing", "use_price_override": 1, "override_unit_price": 50})
		self.adjust(final_total=60)
		self.assertEqual([row.amount for row in self.order.items], [10, 50])

	def test_seeded_allocations_preserve_cents_fees_quote_and_row_order_independence(self):
		rng = Random(20261112)
		cent = Decimal("0.01")
		for case in range(24):
			base = deepcopy(self.template)
			base.set("items", [])
			for index in range(rng.randint(2, 6)):
				quantity = Decimal(rng.randint(1, 80)) / 4
				amount = cent * (100 if case % 4 == 0 else rng.randint(1, 20000))
				row = deepcopy(self.template.items[0])
				row.update({"name": f"ROW-{index}", "quote_item": f"QUOTE-ROW-{index}",
					"quantity": float(quantity), "unit_price": float(amount / quantity), "amount": float(amount)})
				row.pricing_details = create_pricing_snapshot(row, [{
					"label": "Recorded service", "quantity": row.quantity,
					"unit_price": row.unit_price, "amount": row.amount,
				}])
				base.append("items", row)
			self.quote["items"] = deepcopy(base.items)
			for row in self.quote.get("items"):
				row.name = row.quote_item
			quote_before = [deepcopy(row.as_dict()) for row in self.quote.get("items")]
			overrides = [{"name": row.name, "use_price_override": 1,
				"override_unit_price": float(cent * rng.randint(1, 5000))} for row in base.items]
			fixed = Decimal(0)
			for index in range(1 + case % 2):
				fee = cent * rng.randint(1, 5000)
				fixed += fee
				row = base.append("items", {
					"name": f"FEE-{index}", "project_type": "Custom Project", "quantity": 1,
					"description": "Fixed processing fee", "is_processing_fee": 1,
					"use_price_override": 1, "override_unit_price": float(fee),
					"unit_price": float(fee), "amount": float(fee),
				})
				row.pricing_details = create_pricing_snapshot(row, [{
					"label": "Fixed processing fee", "quantity": 1, "unit_price": float(fee), "amount": float(fee),
				}])
			fees_before = {row.name: deepcopy(row.as_dict()) for row in base.items if row.is_processing_fee}
			target = fixed + cent * (1 if case % 4 == 0 else rng.randint(1, 100000))
			steps = [(target, None), (target, None),
				(fixed + cent * rng.randint(1, 100000), overrides), (fixed, None),
				(fixed + cent * rng.randint(1, 100000), None)]
			indices = list(range(len(base.items)))
			shuffled = rng.sample(indices, len(indices))
			expected = []
			for permutation, positions in enumerate((indices, indices[::-1], shuffled)):
				self.order = deepcopy(base)
				self.order.items = [self.order.items[index] for index in positions]
				for step, (total, edits) in enumerate(steps):
					with self.subTest(case=case, permutation=permutation, step=step, target=str(total)):
						result = self.adjust(items=edits, final_total=str(total))
						amounts = [Decimal(str(row.amount)) for row in self.order.items]
						self.assertEqual(sum(amounts), total)
						self.assertTrue(all(amount >= 0 and amount == amount.quantize(cent) for amount in amounts))
						self.assertEqual(Decimal(str(result["total_amount"])), total)
						allocation = {row.name: (row.amount, row.override_unit_price) for row in self.order.items}
						if permutation == 0:
							expected.append(allocation)
						else:
							self.assertEqual(allocation, expected[step])
						if step == 1:
							self.assertEqual(allocation, expected[0])
						self.assertEqual({row.name: row.as_dict() for row in self.order.items if row.is_processing_fee}, fees_before)
						self.assertEqual([row.as_dict() for row in self.quote.get("items")], quote_before)

	def test_zero_total_and_zero_fee_do_not_create_hidden_surcharge_or_tbd(self):
		result = self.adjust(final_total=0, processing_fee=0)
		self.assertEqual(result["total_amount"], 0)
		self.assertEqual(len(result["items"]), 2)
		self.assertEqual([row.amount for row in self.order.items], [0, 0])

	def test_final_total_rejects_less_than_fee_and_allocation_uses_zero_price_quantities(self):
		with self.assertRaises(frappe.ValidationError):
			self.adjust(processing_fee=10, final_total=9)
		self.save.assert_not_called()
		self.order = deepcopy(self.template)
		result = self.adjust(items=[{"name": row.name, "use_price_override": 1, "override_unit_price": 0}
			for row in self.order.items], final_total=0.03)
		self.assertEqual([row["amount"] for row in result["items"]], [0.02, 0.01])

	def test_negative_nonfinite_boolean_and_subcent_money_are_rejected(self):
		for field in ("final_total", "processing_fee"):
			for invalid in (-1, True, "bad", "NaN", "Infinity", float("inf"), 0.001):
				self.order = deepcopy(self.template)
				with self.subTest(field=field, invalid=invalid), self.assertRaises(frappe.ValidationError):
					self.adjust(**{field: invalid})
		for field in ("quantity", "override_unit_price"):
			for invalid in (-1, True, None, "", "NaN", float("inf"), 1e12, 0.0000000001):
				self.order = deepcopy(self.template)
				with self.subTest(field=field, invalid=invalid), self.assertRaises(frappe.ValidationError):
					self.adjust(items=[{"name": "ROW-A", "use_price_override": 1, field: invalid}])
		self.save.assert_not_called()
		self.audit.assert_not_called()

	def test_unrepresentable_final_total_rejected_before_save(self):
		self.order.items[0].update({"quantity": 100000000, "unit_price": 1, "amount": 100000000})
		self.order.items[1].update({"quantity": 100000000, "unit_price": 1, "amount": 100000000})
		with self.assertRaises(frappe.ValidationError):
			self.adjust(final_total=0.01)
		self.save.assert_not_called()

	def test_foreign_duplicate_and_forged_spec_or_price_fields_rejected(self):
		for items in ({}, "{", [None], [{"name": "FOREIGN", "quantity": 1}],
			[{"name": "ROW-A"}, {"name": "ROW-A"}], [{"name": "ROW-A", "unit_price": 0}],
			[{"name": "ROW-A", "million_reads_per_sample": 999}], [{"name": "ROW-A", "is_processing_fee": 1}],
			[{"name": "ROW-A", "quantity": 1}] * 201):
			with self.subTest(items=str(items)[:60]), self.assertRaises(frappe.ValidationError):
				self.adjust(items=items)
		self.save.assert_not_called()

	def test_submitted_or_cancelled_order_and_nondraft_erp_are_locked(self):
		for status in (1, 2):
			self.order.docstatus = status
			with self.assertRaises(frappe.ValidationError):
				self.adjust(final_total=10)
		self.order.docstatus = 0
		self.order.status = "Cancelled"
		with self.assertRaises(frappe.ValidationError):
			self.adjust(final_total=10)
		self.order.status = "In Review"
		self.order.erpnext_sales_order = "SO-TEST"
		for status in (1, 2, None):
			self.erp_status = status
			with self.assertRaises(frappe.ValidationError):
				self.adjust(final_total=10)
		self.save.assert_not_called()

	def test_active_invoice_guard_runs_after_normalization_and_before_save(self):
		with patch.object(invoicing, "assert_order_invoice_unchanged", side_effect=frappe.ValidationError("Void invoice")) as guard:
			with self.assertRaises(frappe.ValidationError):
				self.adjust(items=[{"name": "ROW-A", "quantity": 2}])
			self.assertEqual(self.order.items[0].amount, 200)
			guard.assert_called_once_with(self.order)
		self.save.assert_not_called()
		self.audit.assert_not_called()

	def test_direct_saved_quantity_price_edits_and_forged_token_are_blocked(self):
		for field, value in (("quantity", 2), ("use_price_override", 1), ("override_unit_price", 4)):
			self.order = deepcopy(self.template)
			self.order._doc_before_save = deepcopy(self.order)
			self.order.items[0].set(field, value)
			self.order.flags.order_adjustment_token = "posted-token"
			with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
				self.order.validate()

	def test_po_only_validation_normalizes_persisted_defaults_without_allowing_price_changes(self):
		self.order._doc_before_save = deepcopy(self.order)
		for row, saved in zip(self.order.items, self.order._doc_before_save.items, strict=True):
			row.use_price_override = None
			row.override_unit_price = None
			row.is_processing_fee = None
			saved.use_price_override = 0
			saved.override_unit_price = 0.0
			saved.is_processing_fee = 0
		self.order.po_number = "PO-ONLY-UPDATE"
		self.order.validate()
		self.order.items[0].override_unit_price = 0.01
		with self.assertRaises(frappe.ValidationError):
			self.order.validate()

	def test_direct_saved_links_billing_and_specs_cannot_change_even_with_posted_erp(self):
		changes = {
			"quote_item": "FOREIGN-QUOTE-ROW", "service": "CHEAPER-SERVICE", "project_type": "WGS",
			"sample_type": "DNA", "description": "Different billed service", "species": "mouse",
			"read_depth": "999M", "million_reads_per_sample": 999, "data_analysis": "Custom Analysis",
			"onsite_service": 1, "onsite_location": "Massachusetts", "onsite_address": "Other address",
			"add_on_sequencing": 1, "library_qc": 1, "tissue_dissociation": 1, "nuclei_extraction": 1,
			"confirmed_tbd_fee": 10, "unit_price": 1, "amount": 3, "pricing_details": "forged",
			"details_snapshot_version": 2, "is_processing_fee": 1,
		}
		for field, value in changes.items():
			self.order = deepcopy(self.template)
			self.order.erpnext_sales_order = "POSTED-SALES-ORDER"
			self.order._doc_before_save = deepcopy(self.order)
			self.order.items[0].set(field, value)
			with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
				self.order.validate()
		self.assertFalse(any(
			(call.args[0] if call.args else call.kwargs.get("doctype"))
			in {"NGS Quote Item", "NGS Service Catalog", "Sales Order", "NGS Invoice"}
			for call in self.lookup.call_args_list
		))

	def test_saved_parent_customer_and_quote_are_immutable(self):
		for field, value in (("customer", "OTHER-CUSTOMER"), ("quote", "OTHER-QUOTE"), ("quote", None)):
			self.order = deepcopy(self.template)
			self.order._doc_before_save = deepcopy(self.order)
			self.order.set(field, value)
			with self.subTest(field=field, value=value), self.assertRaises(frappe.ValidationError):
				self.order.validate()

	def test_saved_erp_link_cannot_be_cleared_replaced_or_attached(self):
		for before, after in (("SO-POSTED", None), ("SO-POSTED", ""),
			("SO-POSTED", "SO-DRAFT"), (None, "SO-FORGED")):
			for token in (None, "posted-token", order_adjustments._ADJUSTMENT_TOKEN):
				self.order = deepcopy(self.template)
				self.order.erpnext_sales_order = before
				self.order._doc_before_save = deepcopy(self.order)
				self.order.erpnext_sales_order = after
				self.order.flags.order_adjustment_token = token
				with self.subTest(before=before, after=after, token=token), self.assertRaises(frappe.PermissionError):
					self.order.validate()
		self.lookup.assert_not_called()
		self.save.assert_not_called()
		self.audit.assert_not_called()

	def test_new_order_rejects_caller_supplied_erp_link(self):
		self.order.set("__islocal", 1)
		self.order.erpnext_sales_order = "SO-FORGED"
		with self.assertRaises(frappe.PermissionError):
			self.order.validate()
		self.lookup.assert_not_called()

	def test_unchanged_erp_link_and_empty_defaults_allow_nonfinancial_saves(self):
		for before, after in (("SO-POSTED", "SO-POSTED"), (None, ""), ("", None)):
			self.order = deepcopy(self.template)
			self.order.erpnext_sales_order = before
			self.order._doc_before_save = deepcopy(self.order)
			self.order.erpnext_sales_order = after
			self.order.po_number = "PO-ONLY"
			with self.subTest(before=before, after=after):
				self.order.validate()
				self.assertEqual(self.order.erpnext_sales_order, after)

	def test_adjustment_keeps_existing_draft_erp_link(self):
		self.order.erpnext_sales_order = "SO-DRAFT"
		result = self.adjust(items=[{"name": "ROW-A", "quantity": 2}])
		self.assertEqual(result["total_amount"], 250)
		self.assertEqual(self.order.erpnext_sales_order, "SO-DRAFT")
		self.save.assert_called_once()

	def test_fractional_quantity_at_half_cent_keeps_valid_snapshot(self):
		row = self.order.items[0]
		row.update({"quantity": 1, "unit_price": 0.7, "amount": 0.7})
		row.pricing_details = create_pricing_snapshot(row, [{
			"label": "Recorded service", "quantity": 1, "unit_price": 0.7, "amount": 0.7,
		}])
		result = self.adjust(items=[{"name": row.name, "quantity": 0.05}])
		self.assertEqual((row.quantity, row.unit_price, row.amount), (0.05, 0.7, 0.04))
		self.assertEqual(result["total_amount"], 50.04)
		self.assertIsNotNone(get_recorded_breakdown(row))
		self.save.assert_called_once()
		self.audit.assert_called_once()

	def test_new_quote_rows_must_belong_to_the_attached_quote_and_customer(self):
		for quote_name, quote_item in (("QUOTE-TEST", "FOREIGN-ROW"), (None, "QUOTE-ROW-A")):
			self.order = deepcopy(self.template)
			self.order.set("__islocal", 1)
			self.order.quote = quote_name
			self.order.items[0].quote_item = quote_item
			with self.subTest(quote=quote_name), self.assertRaises(frappe.PermissionError):
				self.order.validate()
		self.order = deepcopy(self.template)
		self.order.set("__islocal", 1)
		self.quote.customer = "OTHER-CUSTOMER"
		with self.assertRaises(frappe.ValidationError):
			self.order.validate()

	def test_po_only_save_keeps_stored_prices_after_quote_and_catalog_changes(self):
		for quoted in (True, False):
			self.order = deepcopy(self.template)
			if not quoted:
				self.order.quote = None
				for row in self.order.items:
					row.quote_item = None
					row.service = "RECORDED-SERVICE"
			self.order._doc_before_save = deepcopy(self.order)
			self.order.po_number = "PO-WITHOUT-REPRICING"
			self.order.items[0].notes = "Updated operational note"
			self.quote.get("items")[0].unit_price = 999
			with patch.object(frappe.db, "get_value", side_effect=lambda doctype, *a, **kw:
				999 if doctype in {"NGS Quote Item", "NGS Service Catalog"} else None) as lookup:
				self.order.validate()
			self.assertEqual([(row.unit_price, row.amount) for row in self.order.items], [(100, 300), (50, 50)])
			self.assertEqual(self.order.items[0].notes, "Updated operational note")
			self.assertTrue(all(call.args[0] not in {"NGS Quote Item", "NGS Service Catalog"} for call in lookup.call_args_list))
			for row, original in zip(self.order.items, self.order._doc_before_save.items, strict=True):
				self.assertEqual(row.pricing_details, original.pricing_details)

	def test_po_only_save_does_not_recompute_pooled_prices_or_replace_notes(self):
		self.order.sequencing_pricing_version = 1
		for row in self.order.items:
			row.project_type = "Sequencing Only - 1M Reads"
		self.order._doc_before_save = deepcopy(self.order)
		self.order.items[0].notes = "Updated sequencing note"
		with patch("ngs_hub.ngs_hub.doctype.ngs_order.ngs_order.apply_document_sequencing") as price:
			self.order.validate()
		price.assert_not_called()
		self.assertEqual(self.order.items[0].notes, "Updated sequencing note")

	def test_legitimate_new_quote_override_clone_survives_but_forgery_does_not(self):
		self.order.set("__islocal", 1)
		self.order.items[0].update({"use_price_override": 1, "override_unit_price": 12})
		self.quote.get("items")[0].update({"name": "QUOTE-ROW-A", "use_price_override": 1, "override_unit_price": 12})
		self.order.validate()
		self.assertEqual(self.order.items[0].unit_price, 12)
		self.order.items[0].override_unit_price = 0
		with self.assertRaises(frappe.PermissionError):
			self.order.validate()

	def test_audit_is_escaped_and_contains_reason_actor_and_before_after(self):
		order_adjustments.adjust_order(self.order.name, final_total=12.34, reason="<script>review</script>")
		args = self.audit.call_args.args
		self.assertEqual(args[:2], (self.order, "Comment"))
		self.assertNotIn("<script>", args[2])
		payload = json.loads(unescape(args[2].split("<pre>", 1)[1].removesuffix("</pre>")))
		self.assertEqual((payload["reason"], payload["actor"]), ("<script>review</script>", self.session.user))
		self.assertEqual((payload["before"]["total_amount"], payload["after"]["total_amount"]), (350, 12.34))
		self.assertIn("pricing_details", payload["before"]["items"][0])

	def test_sequencing_manual_zero_reads_still_contribute_and_no_extra_sequencing(self):
		self.order.quote = None
		self.order.sequencing_pricing_version = 1
		self.order.sequencing_pricing_rates = json.dumps({"version": 1, "rates": [1.9, 1.25, 0.54, 1], "qc_fee": 18})
		for row in self.order.items:
			row.update({"quote_item": None, "project_type": "Sequencing Only - 1M Reads", "quantity": 1,
				"million_reads_per_sample": 200, "library_qc": 1, "add_on_sequencing": 0})
		self.adjust(items=[{"name": "ROW-A", "use_price_override": 1, "override_unit_price": 0}])
		self.assertEqual([row.amount for row in self.order.items], [0, 333])
		parts = get_recorded_breakdown(self.order.items[0])
		self.assertEqual([part["amount"] for part in parts], [190, 125, 18, -333])
		self.assertEqual(self.order.items[0].add_on_sequencing, 0)
