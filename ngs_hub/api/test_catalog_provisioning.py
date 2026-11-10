"""Install-time provisioning contracts with all persistence boundaries mocked."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, call, mock_open, patch

from frappe.custom.doctype.property_setter import property_setter

from ngs_hub import install

ERP_CHILDREN = ("Quotation Item", "Sales Order Item")
RATE_FIELDS = ("rate", "net_rate", "base_rate", "base_net_rate")
UNCHANGED_FIELDS = ("amount", "net_amount", "base_amount", "base_net_amount", "qty", "stock_qty", "currency")


class ProvisioningTestCase(TestCase):
	def setUp(self):
		self.frappe = Mock(spec_set=["db", "get_meta", "get_doc", "get_app_path"])
		self.frappe.db = Mock(spec_set=["exists"])
		self.frappe.get_doc.side_effect = AssertionError(
			"Unexpected document access or System Settings change"
		)
		patcher = patch.object(install, "frappe", self.frappe)
		patcher.start()
		self.addCleanup(patcher.stop)


class TestERPServiceRatePrecision(ProvisioningTestCase):
	def setUp(self):
		super().setUp()
		self.present = set(ERP_CHILDREN)
		self.fields = {
			doctype: {field: SimpleNamespace(precision="2") for field in (*RATE_FIELDS, *UNCHANGED_FIELDS)}
			for doctype in ERP_CHILDREN
		}
		self.meta = {
			doctype: SimpleNamespace(get_field=Mock(side_effect=fields.get))
			for doctype, fields in self.fields.items()
		}
		self.frappe.db.exists.side_effect = self.exists
		self.frappe.get_meta.side_effect = self.meta.__getitem__
		patcher = patch.object(property_setter, "make_property_setter")
		self.make_property_setter = patcher.start()
		self.addCleanup(patcher.stop)

	def exists(self, doctype, name):
		self.assertEqual(doctype, "DocType")
		self.assertIn(name, ERP_CHILDREN)
		return name in self.present

	def test_sets_all_four_rate_fields_to_nine_on_both_erp_children(self):
		install.ensure_erp_service_rate_precision()
		self.assertEqual(
			self.make_property_setter.call_args_list,
			[
				call(doctype, field, "precision", "9", "Int")
				for doctype in ERP_CHILDREN
				for field in RATE_FIELDS
			],
		)
		self.assertEqual(
			self.frappe.db.exists.call_args_list, [call("DocType", doctype) for doctype in ERP_CHILDREN]
		)

	def test_absent_doctypes_are_skipped_without_metadata_or_setters(self):
		for present in (set(), {"Quotation Item"}, {"Sales Order Item"}):
			with self.subTest(present=present):
				self.present = present
				self.frappe.get_meta.reset_mock()
				self.make_property_setter.reset_mock()
				install.ensure_erp_service_rate_precision()
				self.assertEqual(
					self.make_property_setter.call_args_list,
					[
						call(doctype, field, "precision", "9", "Int")
						for doctype in ERP_CHILDREN
						if doctype in present
						for field in RATE_FIELDS
					],
				)
				self.assertEqual({args.args[0] for args in self.frappe.get_meta.call_args_list}, present)

	def test_precision_already_nine_or_higher_is_not_rewritten_or_downgraded(self):
		for precision in (9, "9", 12, "12"):
			with self.subTest(precision=precision):
				for fields in self.fields.values():
					for field in RATE_FIELDS:
						fields[field].precision = precision
				install.ensure_erp_service_rate_precision()
		self.make_property_setter.assert_not_called()

	def test_missing_fields_and_mixed_existing_precisions_are_handled_individually(self):
		self.fields["Quotation Item"]["rate"].precision = None
		self.fields["Quotation Item"]["net_rate"].precision = "9"
		self.fields["Quotation Item"]["base_rate"].precision = ""
		del self.fields["Quotation Item"]["base_net_rate"]
		self.fields["Sales Order Item"]["rate"].precision = 10
		self.fields["Sales Order Item"]["net_rate"].precision = 8
		self.fields["Sales Order Item"]["base_rate"].precision = 9
		self.fields["Sales Order Item"]["base_net_rate"].precision = 0
		install.ensure_erp_service_rate_precision()
		self.assertEqual(
			self.make_property_setter.call_args_list,
			[
				call("Quotation Item", "rate", "precision", "9", "Int"),
				call("Quotation Item", "base_rate", "precision", "9", "Int"),
				call("Sales Order Item", "net_rate", "precision", "9", "Int"),
				call("Sales Order Item", "base_net_rate", "precision", "9", "Int"),
			],
		)

	def test_repeat_run_is_idempotent_after_property_setters_take_effect(self):
		def set_precision(doctype, field, property_name, value, property_type):
			self.assertEqual((property_name, property_type), ("precision", "Int"))
			self.fields[doctype][field].precision = value

		self.make_property_setter.side_effect = set_precision
		install.ensure_erp_service_rate_precision()
		self.assertEqual(self.make_property_setter.call_count, 8)
		install.ensure_erp_service_rate_precision()
		self.assertEqual(self.make_property_setter.call_count, 8)

	def test_amount_quantity_currency_and_system_settings_are_untouched(self):
		before = deepcopy(self.fields)
		install.ensure_erp_service_rate_precision()
		for doctype in ERP_CHILDREN:
			self.assertEqual(
				self.meta[doctype].get_field.call_args_list, [call(field) for field in RATE_FIELDS]
			)
			for field in UNCHANGED_FIELDS:
				self.assertEqual(vars(self.fields[doctype][field]), vars(before[doctype][field]))
		self.frappe.get_doc.assert_not_called()
		self.frappe.get_app_path.assert_not_called()
		self.assertCountEqual(
			self.frappe.db.mock_calls, [call.exists("DocType", doctype) for doctype in ERP_CHILDREN]
		)
		self.assertTrue(
			all(
				args.args[0] in ERP_CHILDREN and args.args[1] in RATE_FIELDS
				for args in self.make_property_setter.call_args_list
			)
		)


class TestInsertOnlyCatalogProvisioning(ProvisioningTestCase):
	def setUp(self):
		super().setUp()
		self.seeds = [
			{
				"doctype": "NGS Service Catalog",
				"name": "DNA_SMALL_FRAGMENT_PREP",
				"service_code": "DNA_SMALL_FRAGMENT_PREP",
				"unit_price": 45,
				"enabled": 1,
			},
			{
				"doctype": "NGS Service Catalog",
				"name": "DNA_SMALL_FRAGMENT_1M",
				"service_code": "DNA_SMALL_FRAGMENT_1M",
				"unit_price": 1.5,
				"enabled": 1,
			},
		]
		self.existing = {
			"DNA_SMALL_FRAGMENT_PREP": {
				"name": "RENAMED-PREP",
				"service_code": "DNA_SMALL_FRAGMENT_PREP",
				"unit_price": 37,
				"enabled": 0,
			},
		}
		self.present = True
		self.frappe.db.exists.side_effect = self.exists
		self.frappe.get_app_path.return_value = "mock-seed.json"
		self.path = Mock(spec_set=["open"])
		self.path.open.side_effect = mock_open(read_data=json.dumps(self.seeds))
		patcher = patch.object(install, "Path", return_value=self.path)
		self.path_type = patcher.start()
		self.addCleanup(patcher.stop)

	def exists(self, doctype, filters):
		if doctype == "DocType":
			self.assertEqual(filters, "NGS Service Catalog")
			return self.present
		self.assertEqual(doctype, "NGS Service Catalog")
		self.assertIsInstance(filters, dict)
		self.assertEqual(set(filters), {"service_code"})
		row = self.existing.get(filters["service_code"])
		return row["name"] if row else None

	def test_absent_catalog_doctype_does_not_load_seed_or_insert_records(self):
		self.present = False
		install.create_ngs_service_catalog()
		self.frappe.db.exists.assert_called_once_with("DocType", "NGS Service Catalog")
		self.frappe.get_app_path.assert_not_called()
		self.path_type.assert_not_called()
		self.frappe.get_doc.assert_not_called()

	def test_existing_service_code_preserves_custom_price_disabled_flag_and_renamed_identity(self):
		self.existing["DNA_SMALL_FRAGMENT_1M"] = {**self.seeds[1], "unit_price": 2.75, "enabled": 0}
		before = deepcopy(self.existing)
		install.create_ngs_service_catalog()
		install.create_ngs_service_catalog()
		self.assertEqual(self.existing, before)
		self.frappe.get_doc.assert_not_called()
		self.assertEqual(
			self.frappe.db.exists.call_args_list,
			[
				call("DocType", "NGS Service Catalog"),
				call("NGS Service Catalog", {"service_code": "DNA_SMALL_FRAGMENT_PREP"}),
				call("NGS Service Catalog", {"service_code": "DNA_SMALL_FRAGMENT_1M"}),
			]
			* 2,
		)

	def test_only_missing_code_is_inserted_once_with_seed_identity_and_second_run_is_noop(self):
		before = deepcopy(self.existing["DNA_SMALL_FRAGMENT_PREP"])
		document = Mock(spec_set=["insert"])

		def insert(*, ignore_permissions):
			self.assertTrue(ignore_permissions)
			self.existing["DNA_SMALL_FRAGMENT_1M"] = deepcopy(self.seeds[1])

		document.insert.side_effect = insert
		self.frappe.get_doc.side_effect = None
		self.frappe.get_doc.return_value = document
		install.create_ngs_service_catalog()
		install.create_ngs_service_catalog()
		self.frappe.get_doc.assert_called_once_with(self.seeds[1])
		document.insert.assert_called_once_with(ignore_permissions=True)
		self.assertEqual(self.existing["DNA_SMALL_FRAGMENT_PREP"], before)
		self.assertEqual(self.existing["DNA_SMALL_FRAGMENT_1M"], self.seeds[1])
		self.frappe.get_app_path.assert_called_with(
			"ngs_hub", "fixtures", "catalog", "seed_ngs_service_catalog.json"
		)
