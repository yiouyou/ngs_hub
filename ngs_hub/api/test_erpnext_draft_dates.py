from contextlib import nullcontext
from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from ngs_hub.api import erpnext_sync


def mock_document(**fields):
	doc = SimpleNamespace(flags=SimpleNamespace(), **fields)
	doc.get = lambda key, default=None: getattr(doc, key, default)
	doc.set = Mock(side_effect=lambda key, value: setattr(doc, key, value))
	doc.update = Mock(side_effect=lambda values: doc.__dict__.update(values))
	doc.append = Mock(side_effect=lambda key, value: getattr(doc, key).append(value))
	doc.save = Mock()
	doc.db_set = Mock()
	return doc


class TestERPNextDraftDates(TestCase):
	def setUp(self):
		self.frappe = SimpleNamespace(
			local=SimpleNamespace(),
			session=SimpleNamespace(user="Administrator"),
			PermissionError=frappe.PermissionError,
			throw=Mock(side_effect=self.throw),
			get_installed_apps=Mock(return_value=["erpnext"]),
			db=SimpleNamespace(exists=Mock(return_value=True), get_value=Mock(return_value="NGS Service")),
			get_doc=Mock(),
			new_doc=Mock(),
		)
		self.start_patch("frappe", self.frappe)
		self.start_patch("today", return_value="2026-11-12")
		self.start_patch("_erpnext_sync_user", side_effect=lambda *args: nullcontext())
		self.profile_customer = self.start_patch("get_or_create_erp_customer", return_value="ERP-CUSTOMER")
		self.start_patch("get_default_company", return_value="NGS Company")
		self.start_patch("get_default_selling_price_list", return_value="Standard Selling")
		self.start_patch("get_quote_currency", return_value="EUR")
		self.start_patch("get_order_currency", return_value="EUR")
		self.start_patch("get_item_code", return_value="NGS-SERVICE")
		self.start_patch("get_item_uom", return_value="Nos")
		self.start_patch("get_quote_item", return_value=None)
		self.mirror = self.start_patch("mirror_order_attachments")

	@staticmethod
	def throw(message, exception=frappe.ValidationError):
		raise exception(message)

	def start_patch(self, name, *args, **kwargs):
		patcher = patch.object(erpnext_sync, name, *args, **kwargs)
		mock = patcher.start()
		self.addCleanup(patcher.stop)
		return mock

	def cases(self):
		return (
			("Quotation", "NGS Quote", "erpnext_quotation", erpnext_sync.sync_ngs_quote_to_quotation),
			("Sales Order", "NGS Order", "erpnext_sales_order", erpnext_sync.sync_ngs_order_to_sales_order),
		)

	def documents(self, erp_doctype, source_doctype, link_field, link="ERP-DRAFT", docstatus=0):
		source = mock_document(
			doctype=source_doctype,
			name="NGS-SOURCE",
			customer="NGS-CUSTOMER",
			items=[{"quantity": 2, "unit_price": 125, "description": "Updated service"}],
			po_number="PO-UPDATED",
			notes="Updated order contents",
			custom_project_description="Updated quote contents",
			**{link_field: link},
		)
		target = mock_document(
			doctype=erp_doctype,
			name="ERP-DRAFT",
			docstatus=docstatus,
			customer="ERP-CUSTOMER",
			quotation_to="Customer",
			party_name="ERP-CUSTOMER",
			transaction_date="2026-11-11",
			valid_till="2026-11-25",
			delivery_date="2026-11-26",
			items=[{"item_code": "OLD-ITEM", "rate": 10, "delivery_date": "2026-11-26"}],
			payment_terms_template="40-60 Instalments",
			payment_schedule=[
				{"due_date": "2026-11-11", "invoice_portion": 40, "payment_amount": 40},
				{"due_date": "2027-01-10", "invoice_portion": 60, "payment_amount": 60},
			],
		)
		self.frappe.get_doc.return_value = target
		self.frappe.new_doc.return_value = target
		saved_source = {"customer": source.customer, link_field: link}
		self.frappe.db.get_value.side_effect = lambda doctype, name, field: (
			saved_source.get(field) if doctype == source_doctype else "NGS Service"
		)
		source.db_set.side_effect = lambda field, value, **kwargs: saved_source.update({field: value})
		return source, target

	def assert_financial_sync(self, source, target, link_field):
		self.assertEqual(target.currency, "EUR")
		self.assertEqual(target.price_list_currency, "EUR")
		self.assertEqual(target.company, "NGS Company")
		self.assertEqual(target.conversion_rate, 1)
		self.assertEqual(target.selling_price_list, "Standard Selling")
		self.assertEqual(len(target.items), 1)
		self.assertEqual(target.items[0]["item_code"], "NGS-SERVICE")
		self.assertEqual(target.items[0]["qty"], 2)
		self.assertEqual(target.items[0]["rate"], 125)
		self.assertEqual(target.items[0]["description"], "Updated service")
		self.assertTrue(target.flags.ignore_permissions)
		target.save.assert_called_once_with()
		source.db_set.assert_called_once_with(link_field, target.name, update_modified=False)
		if target.doctype == "Sales Order":
			self.assertEqual(target.po_no, "PO-UPDATED")
			self.assertIn("Updated order contents", target.terms)
			self.mirror.assert_called_with(source, target)
		else:
			self.assertIn("Updated quote contents", target.terms)

	def test_new_documents_initialize_dates(self):
		for erp_doctype, source_doctype, link_field, synchronize in self.cases():
			for link in (None, "MISSING-ERP-DRAFT"):
				with self.subTest(doctype=erp_doctype, link=link):
					source, target = self.documents(erp_doctype, source_doctype, link_field, link=link)
					self.frappe.db.exists.return_value = False
					target.items = []
					target.payment_schedule = []
					self.assertEqual(synchronize(source), target.name)
					self.frappe.new_doc.assert_called_with(erp_doctype)
					self.assertEqual(target.transaction_date, "2026-11-12")
					if erp_doctype == "Quotation":
						self.assertEqual(target.valid_till, "2026-12-12")
					else:
						self.assertEqual(target.delivery_date, "2026-12-12")
						self.assertEqual(target.items[0]["delivery_date"], target.delivery_date)
					self.assert_financial_sync(source, target, link_field)

	def test_next_day_edits_preserve_dates_and_payment_plan(self):
		for erp_doctype, source_doctype, link_field, synchronize in self.cases():
			with self.subTest(doctype=erp_doctype):
				source, target = self.documents(erp_doctype, source_doctype, link_field)
				schedule = target.payment_schedule
				original_schedule = deepcopy(schedule)
				self.assertEqual(synchronize(source), target.name)
				self.assertEqual(target.transaction_date, "2026-11-11")
				self.assertEqual(target.valid_till, "2026-11-25")
				self.assertEqual(target.delivery_date, "2026-11-26")
				self.assertIs(target.payment_schedule, schedule)
				self.assertEqual(target.payment_schedule, original_schedule)
				self.assertEqual(target.payment_terms_template, "40-60 Instalments")
				for row in target.payment_schedule:
					self.assertGreaterEqual(row["due_date"], target.transaction_date)
				if erp_doctype == "Sales Order":
					self.assertEqual(target.items[0]["delivery_date"], "2026-11-26")
				self.assert_financial_sync(source, target, link_field)
				self.frappe.new_doc.assert_not_called()

	def test_profile_change_keeps_existing_party_and_new_documents_use_new_party(self):
		for erp_doctype, source_doctype, link_field, synchronize in self.cases():
			with self.subTest(doctype=erp_doctype):
				self.profile_customer.return_value = "ERP-OLD"
				source, target = self.documents(erp_doctype, source_doctype, link_field)
				target.customer = target.party_name = "ERP-OLD"
				party_field = "party_name" if erp_doctype == "Quotation" else "customer"
				synchronize(source)
				self.assertEqual(getattr(target, party_field), "ERP-OLD")
				self.profile_customer.return_value = "ERP-NEW"
				self.profile_customer.reset_mock()
				target.save.reset_mock()
				source.db_set.reset_mock()
				self.assertEqual(synchronize(source), target.name)
				self.assertEqual(getattr(target, party_field), "ERP-OLD")
				self.assertEqual(target.transaction_date, "2026-11-11")
				self.assertEqual(target.payment_schedule[0]["due_date"], "2026-11-11")
				self.assert_financial_sync(source, target, link_field)
				self.profile_customer.assert_not_called()
				new_source, new_target = self.documents(erp_doctype, source_doctype, link_field, link=None)
				new_target.items = []
				synchronize(new_source)
				self.assertEqual(getattr(new_target, party_field), "ERP-NEW")
				self.profile_customer.assert_called_once_with(new_source)
				self.assert_financial_sync(new_source, new_target, link_field)

	def test_invalid_target_party_is_rejected_without_saving(self):
		for erp_doctype, source_doctype, link_field, synchronize in self.cases():
			with self.subTest(doctype=erp_doctype):
				source, target = self.documents(erp_doctype, source_doctype, link_field)
				target.customer = target.party_name = None
				with self.assertRaises(frappe.ValidationError):
					synchronize(source)
				target.save.assert_not_called()
				target.set.assert_not_called()
				source.db_set.assert_not_called()

	def test_lead_quotation_is_rejected_even_with_trusted_link(self):
		case = self.cases()[0]
		source, target = self.documents(*case[:3])
		target.quotation_to = "Lead"
		with self.assertRaises(frappe.ValidationError):
			case[3](source)
		target.save.assert_not_called()
		target.set.assert_not_called()

	def test_existing_date_objects_and_expired_dates_are_not_rebased(self):
		for erp_doctype, source_doctype, link_field, synchronize in self.cases():
			with self.subTest(doctype=erp_doctype):
				source, target = self.documents(erp_doctype, source_doctype, link_field)
				target.transaction_date = date(2026, 9, 1)
				target.valid_till = date(2026, 10, 1)
				target.delivery_date = date(2026, 10, 2)
				synchronize(source)
				self.assertEqual(target.transaction_date, date(2026, 9, 1))
				self.assertEqual(target.valid_till, date(2026, 10, 1))
				self.assertEqual(target.delivery_date, date(2026, 10, 2))
				if erp_doctype == "Sales Order":
					self.assertEqual(target.items[0]["delivery_date"], date(2026, 10, 2))

	def test_existing_blank_dates_are_not_filled_by_sync(self):
		for erp_doctype, source_doctype, link_field, synchronize in self.cases():
			with self.subTest(doctype=erp_doctype):
				source, target = self.documents(erp_doctype, source_doctype, link_field)
				target.transaction_date = target.valid_till = target.delivery_date = None
				synchronize(source)
				self.assertIsNone(target.transaction_date)
				self.assertIsNone(target.valid_till)
				self.assertIsNone(target.delivery_date)
				if erp_doctype == "Sales Order":
					self.assertIsNone(target.items[0]["delivery_date"])

	def test_submitted_and_cancelled_targets_are_untouched(self):
		for erp_doctype, source_doctype, link_field, synchronize in self.cases():
			for docstatus in (1, 2):
				with self.subTest(doctype=erp_doctype, docstatus=docstatus):
					source, target = self.documents(erp_doctype, source_doctype, link_field, docstatus=docstatus)
					items = deepcopy(target.items)
					schedule = deepcopy(target.payment_schedule)
					self.assertEqual(synchronize(source), target.name)
					self.assertEqual(target.transaction_date, "2026-11-11")
					self.assertEqual(target.valid_till, "2026-11-25")
					self.assertEqual(target.delivery_date, "2026-11-26")
					self.assertEqual(target.items, items)
					self.assertEqual(target.payment_schedule, schedule)
					target.set.assert_not_called()
					target.update.assert_not_called()
					target.save.assert_not_called()
					source.db_set.assert_not_called()
		self.mirror.assert_not_called()
