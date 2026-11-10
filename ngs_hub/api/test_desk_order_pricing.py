"""Initialized-site unittest coverage with in-memory documents and no writes."""

import json
from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

import frappe
from frappe.model.document import Document

from ngs_hub.api import desk_order

PARAMETERS = json.dumps({"version": 1, "rates": [1.9, 1.25, 0.54, 1], "qc_fee": 18})


class TestDeskOrderPricing(TestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# Warm metadata before installing SQL guards; never save these documents.
		cls.template = frappe.get_doc(
			{
				"doctype": "NGS Order",
				"name": "ORDER-TEST",
				"__islocal": 0,
				"customer": "CUSTOMER-TEST",
				"sequencing_pricing_version": 1,
				"sequencing_pricing_rates": PARAMETERS,
				"items": [
					{
						"name": name,
						"project_type": "Sequencing Only - 1M Reads",
						"quantity": 1,
						"million_reads_per_sample": 100,
						"unit_price": 208,
						"amount": 208,
						"notes": "Recorded note",
						"pricing_details": "Recorded snapshot",
					}
					for name in ("ROW-A", "ROW-B")
				],
			}
		)
		cls.customer_template = frappe.get_doc({"doctype": "NGS Customer", "name": "CUSTOMER-TEST"})
		for doc in (cls.template, *cls.template.items, cls.customer_template):
			doc.meta.get_valid_fields()

	def setUp(self):
		self.order = deepcopy(self.template)
		self.customer = deepcopy(self.customer_template)
		self.events = []
		self.denied = None
		self.original_get_doc = frappe.get_doc
		self.editor = self.mock(desk_order, "require_quote_editor", side_effect=self.require_editor)
		self.documents = self.mock(frappe, "get_doc", side_effect=self.get_document)
		self.mock(Document, "check_permission", autospec=True, side_effect=self.check_permission)
		self.catalog = self.mock(frappe.db, "get_value", side_effect=self.catalog_value)
		self.guards = []
		for owner, names in (
			(frappe.db, ("sql", "commit", "set_value", "delete")),
			(Document, ("save", "insert", "db_insert", "db_update", "db_set")),
		):
			for name in names:
				self.guards.append(self.mock(owner, name, side_effect=AssertionError(f"Persistence: {name}")))

	def mock(self, owner, name, **kwargs):
		patcher = patch.object(owner, name, **kwargs)
		result = patcher.start()
		self.addCleanup(patcher.stop)
		return result

	def tearDown(self):
		for guard in self.guards:
			guard.assert_not_called()

	def require_editor(self):
		self.events.append("editor")
		if self.denied == "editor":
			raise frappe.PermissionError("Editor denied")

	def check_permission(self, doc, permission):
		stage = (doc.doctype, permission)
		self.events.append(stage)
		self.assertIn(stage, (("NGS Order", "write"), ("NGS Customer", "read")))
		if self.denied == stage:
			raise frappe.PermissionError("Document denied")

	def get_document(self, doctype, name=None):
		if isinstance(doctype, dict):
			self.assertEqual(self.events[:3], ["editor", ("NGS Order", "write"), ("NGS Customer", "read")])
			return self.original_get_doc(deepcopy(doctype))
		self.assertEqual(self.events[0], "editor")
		if doctype == "NGS Order":
			self.assertEqual(name, self.order.name)
			return self.order
		self.assertEqual((doctype, name), ("NGS Customer", self.customer.name))
		self.assertIn(("NGS Order", "write"), self.events)
		return self.customer

	def catalog_value(self, doctype, name, field):
		self.assertEqual(self.events[:3], ["editor", ("NGS Order", "write"), ("NGS Customer", "read")])
		self.assertEqual((doctype, field), ("NGS Service Catalog", "unit_price"))
		if isinstance(name, dict):
			self.assertEqual(set(name), {"service_code"})
			name = name["service_code"]
		return {
			"SEQ_ONLY_1M_READS": 2,
			"SEQ_ONLY_1M_READS_201_600": 1.5,
			"SEQ_ONLY_1M_READS_601_1200": 0.75,
			"SEQ_ONLY_1M_READS_OVER_1200": 1.25,
			"LIBRARY_QC": 30,
		}[name]

	def preview(self, values, *, encoded=False):
		original = deepcopy(self.order.as_dict())
		inputs = deepcopy(values)
		try:
			return desk_order.preview_order_sequencing(
				self.order.name, json.dumps(values) if encoded else values
			)
		finally:
			self.assertEqual(self.order.as_dict(), original, "Preview mutated the saved Order")
			self.assertEqual(values, inputs, "Preview mutated request inputs")

	def quantities(self, first=2, second=1):
		return [{"name": "ROW-A", "quantity": first}, {"name": "ROW-B", "quantity": second}]

	def test_permissions_precede_catalog_access_and_row_cloning(self):
		stages = ["editor", ("NGS Order", "write"), ("NGS Customer", "read")]
		self.order.sequencing_pricing_rates = None
		for index, denied in enumerate(stages):
			with self.subTest(denied=denied):
				self.denied = denied
				self.events.clear()
				self.documents.reset_mock()
				with self.assertRaises(frappe.PermissionError):
					self.preview(self.quantities())
				self.assertEqual(self.events, stages[: index + 1])
				self.assertEqual(self.documents.call_count, index)
				self.catalog.assert_not_called()

	def test_recorded_rates_win_over_current_catalog_and_reprice_every_row(self):
		result = self.preview(self.quantities(), encoded=True)["items"]
		self.editor.assert_called_once_with()
		self.catalog.assert_not_called()
		# Each band's cents are allocated separately, then per-library QC is added.
		self.assertEqual([row["amount"] for row in result], [372.66, 186.34])
		self.assertEqual([row["unit_price"] for row in result], [186.33, 186.34])
		for row, quantity in zip(result, (2, 1), strict=True):
			self.assertEqual(set(row), {"name", "unit_price", "amount", "pricing_details", "notes"})
			snapshot = json.loads(row["pricing_details"])
			self.assertEqual(snapshot["quantity"], quantity)
			self.assertAlmostEqual(sum(part["amount"] for part in snapshot["components"]), row["amount"])
			self.assertEqual(snapshot["components"][-1]["amount"], 18 * quantity)
			self.assertRegex(row["notes"], r"300(?:\.0+)?M reads")

	def test_missing_recorded_parameters_uses_mocked_current_catalog(self):
		self.order.sequencing_pricing_rates = None
		result = self.preview(self.quantities())["items"]
		self.assertEqual([row["amount"] for row in result], [426.67, 213.33])
		self.assertEqual(self.catalog.call_count, 5)

	def test_owned_saved_names_only_and_deleted_rows_can_be_omitted(self):
		for values in (
			[{"name": "FOREIGN-ROW", "quantity": 1}],
			[{"name": "new-local-child", "quantity": 1}],
			[{"quantity": 1}],
			[*self.quantities(), self.quantities()[0]],
		):
			with self.subTest(values=values), self.assertRaises(frappe.ValidationError):
				self.preview(values)
		self.catalog.assert_not_called()
		result = self.preview([{"name": "ROW-B", "quantity": 2}])["items"]
		self.assertEqual([(row["name"], row["amount"]) for row in result], [("ROW-B", 416)])
		self.assertEqual(self.preview([]), {"items": []})

	def test_client_specs_and_prices_cannot_override_saved_child_fields(self):
		values = self.quantities()
		values[0].update(
			project_type="Custom Project",
			million_reads_per_sample=99999,
			unit_price=0,
			amount=0,
			use_price_override=1,
			override_unit_price=0,
			pricing_details="forged",
			notes="forged",
			parent="FOREIGN-ORDER",
		)
		self.assertEqual([row["amount"] for row in self.preview(values)["items"]], [372.66, 186.34])

	def test_nonsequencing_row_uses_saved_unit_price_not_current_catalog(self):
		fixed = self.order.items[1]
		fixed.project_type = "WGS"
		fixed.unit_price = fixed.amount = 399
		result = self.preview(self.quantities(first=2, second=3))["items"]
		self.assertEqual([row["amount"] for row in result], [416, 1197])
		self.assertEqual(result[1]["unit_price"], 399)
		self.assertEqual(result[1]["notes"], "Recorded note")
		self.assertEqual(result[1]["pricing_details"], "Recorded snapshot")
		self.catalog.assert_not_called()

	def test_invalid_payload_shapes_fail_without_catalog_reads(self):
		for values in (None, {}, json.dumps("not a list"), [None], [1], self.quantities()[:1] * 201):
			with self.subTest(values=str(values)[:60]), self.assertRaises(frappe.ValidationError):
				self.preview(values)
		self.catalog.assert_not_called()

	def test_invalid_quantities_on_legacy_and_version_one_orders(self):
		for version in (0, 1):
			self.order.sequencing_pricing_version = version
			for quantity in (None, "", "bad", 0, -1, False, float("nan"), float("inf"), "Infinity"):
				with (
					self.subTest(version=version, quantity=quantity),
					self.assertRaises(frappe.ValidationError),
				):
					self.preview(self.quantities(first=quantity))
		self.catalog.assert_not_called()

	def test_true_is_not_a_quantity_for_either_pricing_version(self):
		for version in (0, 1):
			self.order.sequencing_pricing_version = version
			with self.subTest(version=version), self.assertRaises(frappe.ValidationError):
				self.preview(self.quantities(first=True))

	def test_legacy_preview_preserves_recorded_prices_notes_and_snapshots(self):
		self.order.sequencing_pricing_version = 0
		result = self.preview(self.quantities(first=2.5))["items"]
		self.assertEqual([row["amount"] for row in result], [520, 208])
		for row in result:
			self.assertEqual(row["unit_price"], 208)
			self.assertEqual(row["pricing_details"], "Recorded snapshot")
			self.assertEqual(row["notes"], "Recorded note")
		self.catalog.assert_not_called()

	def test_corrupt_recorded_rates_fail_without_catalog_fallback(self):
		for parameters in (
			"{",
			"null",
			json.dumps({"version": 1, "rates": [1.9], "qc_fee": 18}),
			json.dumps({"version": 1, "rates": [1.9, 1.25, 0.54, 1], "qc_fee": -1}),
		):
			self.order.sequencing_pricing_rates = parameters
			with self.subTest(parameters=parameters), self.assertRaises(frappe.ValidationError):
				self.preview(self.quantities())
		self.catalog.assert_not_called()
