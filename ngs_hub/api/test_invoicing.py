"""Focused initialized-site tests: in-memory persistence, queued mail, no SMTP."""

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import frappe
from frappe.model.document import Document
from frappe.utils import add_days, getdate

from ngs_hub.api import invoicing
from ngs_hub.api.pricing_snapshot import create_pricing_snapshot, get_recorded_breakdown
from ngs_hub.ngs_hub.doctype.ngs_invoice.ngs_invoice import (
	INVOICE_WRITE_TOKEN,
	SNAPSHOT_FIELDS,
)

PDF = b"%PDF-1.4\nsynthetic invoice bytes\n%%EOF"


class TestInvoicing(TestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		for doctype in ("NGS Order", "NGS Order Item", "NGS Customer", "NGS Invoice"):
			frappe.get_meta(doctype).get_valid_fields()
			frappe.get_doc({"doctype": doctype})

	def mock(self, owner, name, **kwargs):
		patcher = patch.object(owner, name, **kwargs)
		result = patcher.start()
		self.addCleanup(patcher.stop)
		return result

	def setUp(self):
		# Clock helpers must not load System Settings through the document mock.
		self.mock(invoicing, "today", return_value="2026-11-12")
		self.mock(invoicing, "now_datetime", return_value=datetime(2026, 11, 12, 9, 30))
		temporary = TemporaryDirectory()
		self.addCleanup(temporary.cleanup)
		self.private_directory = Path(temporary.name).resolve()
		get_site_path = frappe.get_site_path
		self.mock(
			frappe,
			"get_site_path",
			side_effect=lambda *args, **kwargs: str(self.private_directory)
			if args == ("private", "files")
			else get_site_path(*args, **kwargs),
		)
		self.rollback_callbacks = []
		self.mock(frappe.db, "after_rollback", new=frappe._dict(add=self.rollback_callbacks.append))
		self.original_get_doc = frappe.get_doc
		self.order = frappe.get_doc(
			{
				"doctype": "NGS Order",
				"name": "ORDER-INVOICE-TEST",
				"__islocal": 0,
				"customer": "CUSTOMER-INVOICE-TEST",
				"status": "Completed",
				"po_number": "PO-TEST",
				"items": [
					{
						"name": "ROW-TEST",
						"project_type": "Custom Project",
						"description": "Actual completed service",
						"quantity": 3,
						"unit_price": 10,
						"amount": 29.99,
					}
				],
			}
		)
		self.customer = frappe.get_doc(
			{
				"doctype": "NGS Customer",
				"name": self.order.customer,
				"__islocal": 0,
				"organization": "Canonical Test Organization",
				"company_institution": "Legacy Test Organization",
				"billing_category": "Commercial",
				"invoice_email_address": " billing@example.invalid ",
				"billing_address": "Separate Billing Address",
				"address": "Profile Address",
				"adress": "Legacy Address",
			}
		)
		# These are non-numeric synthetic values, never real remittance credentials.
		self.settings = frappe._dict(
			{field: "configured-" + field for field in (*invoicing.BANK_FIELDS, *invoicing.COMPANY_FIELDS)}
		)
		self.saved = {}
		self.previous = {}
		self.files = {}
		self.queues = []
		self.events = []
		self.denied = None
		self.enabled = 1
		self.user_type = "System User"
		self.roles = ["NGS Internal Staff"]
		session = patch.dict(frappe.session, {"user": "staff@example.invalid"})
		session.start()
		self.addCleanup(session.stop)
		self.mock(frappe, "get_roles", side_effect=lambda user: self.roles)
		self.values = self.mock(frappe.db, "get_value", side_effect=self.get_value)
		self.documents = self.mock(frappe, "get_doc", side_effect=self.get_document)
		self.single = self.mock(frappe, "get_single", return_value=self.settings)
		self.mock(Document, "check_permission", autospec=True, side_effect=self.check_permission)
		self.mock(
			Document,
			"get_doc_before_save",
			autospec=True,
			side_effect=lambda doc: self.previous.get(doc.name),
		)
		self.insert = self.mock(Document, "insert", autospec=True, side_effect=self.insert_document)
		self.save = self.mock(Document, "save", autospec=True, side_effect=self.save_document)
		self.file_save = self.mock(invoicing, "_save_invoice_pdf", side_effect=self.save_file)
		self.render = self.mock(invoicing, "render_pdf", return_value=PDF)
		self.mail = self.mock(frappe, "sendmail")
		self.currency = self.mock(invoicing, "get_order_currency", return_value="USD")
		self.mock(frappe, "get_all", side_effect=lambda *args, **kwargs: [q.name for q in self.queues])
		self.queue_names = self.mock(frappe.db, "get_values", side_effect=self.get_queue_names)
		self.updates = self.mock(frappe.db, "set_value", side_effect=self.update_queue)
		self.guards = [
			self.mock(frappe.db, name, side_effect=AssertionError("Unexpected DB operation: " + name))
			for name in ("commit", "delete")
		]
		self.guards.extend(
			self.mock(Document, name, side_effect=AssertionError("Unexpected persistence: " + name))
			for name in ("db_insert", "db_update", "db_set")
		)

	def tearDown(self):
		for guard in self.guards:
			guard.assert_not_called()

	def get_value(self, doctype, name, field, **kwargs):
		if doctype == "User":
			return {"enabled": self.enabled, "user_type": self.user_type}[field]
		if doctype == "NGS Order":
			self.assertTrue(kwargs.get("for_update"))
			self.events.append("order-lock")
			return self.order.name
		self.assertEqual(doctype, "NGS Invoice")
		self.assertEqual(name, {"active_order": self.order.name})
		self.assertTrue(kwargs.get("for_update"))
		self.assertIn("order-lock", self.events)
		self.events.append("invoice-lookup")
		return next((doc.name for doc in self.saved.values() if doc.active_order == self.order.name), None)

	def get_document(self, doctype, name=None, **kwargs):
		if isinstance(doctype, dict):
			return self.original_get_doc(doctype)
		if doctype == "NGS Order":
			self.assertEqual(name, self.order.name)
			if kwargs.get("for_update"):
				self.events.append("order-lock")
			return self.order
		if doctype == "NGS Customer":
			self.assertEqual(name, self.customer.name)
			return self.customer
		if doctype == "NGS Invoice":
			if kwargs.get("for_update"):
				self.events.append("invoice-lock")
			return self.saved[name]
		if doctype == "File":
			self.assertTrue(kwargs.get("for_update"))
			return self.files[name]
		self.assertEqual(doctype, "Email Queue")
		self.assertTrue(kwargs.get("for_update"))
		self.events.append("queue-lock")
		return next(q for q in self.queues if q.name == name)

	def get_queue_names(self, doctype, **kwargs):
		self.assertEqual(doctype, "Email Queue")
		self.assertEqual(kwargs["fieldname"], "name")
		self.assertTrue(kwargs["pluck"])
		self.assertTrue(kwargs["for_update"])
		self.assertEqual(kwargs["order_by"], "name")
		self.events.append("queue-enumeration")
		return [q.name for q in self.queues]

	def check_permission(self, doc, permission):
		stage = (doc.doctype, permission)
		self.events.append(stage)
		if self.denied == stage:
			raise frappe.PermissionError("Test permission denied")

	def insert_document(self, doc, **kwargs):
		self.assertTrue(kwargs.get("ignore_permissions"))
		self.assertIn(("NGS Order", "write"), self.events)
		self.assertIn(("NGS Customer", "read"), self.events)
		self.assertIn("invoice-lookup", self.events)
		doc.set("__islocal", True)
		doc.validate()
		doc.name = f"INV-2099-{len(self.saved) + 1:05d}"
		doc.set("__islocal", False)
		self.saved[doc.name] = doc
		self.previous[doc.name] = deepcopy(doc)
		return doc

	def save_document(self, doc, **kwargs):
		doc.validate()
		self.previous[doc.name] = deepcopy(doc)
		return doc

	def save_file(self, filename, content, name):
		path = self.private_directory / filename
		if path.exists():
			path = path.with_name(path.stem + "-new" + path.suffix)
		path.write_bytes(content)
		file = frappe._dict(
			name="FILE-" + name,
			file_url="/private/files/" + path.name,
			is_private=1,
			attached_to_doctype="NGS Invoice",
			attached_to_name=name,
			attached_to_field="pdf_url",
			get_content=lambda: content,
			get_full_path=lambda: str(path),
		)
		self.files[file.name] = file
		return file

	def update_queue(self, doctype, name, values):
		self.assertEqual(doctype, "Email Queue")
		next(q for q in self.queues if q.name == name).update(values)

	def generate(self):
		result = invoicing.generate_invoice(self.order.name)
		return self.saved[result["name"]], result

	def test_http_methods_are_narrow_and_guest_access_disabled(self):
		for function in (invoicing.generate_invoice, invoicing.resend_invoice, invoicing.void_invoice):
			self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[function], ["POST"])
			self.assertNotIn(function, frappe.guest_methods)
		self.assertEqual(
			frappe.allowed_http_methods_for_whitelisted_func[invoicing.download_invoice], ["GET"]
		)

	def test_staff_gate_rejects_guest_website_disabled_and_unrelated_roles_before_documents(self):
		for user, enabled, kind, roles in (
			("Guest", 1, "System User", ["System Manager"]),
			("website@example.invalid", 1, "Website User", ["NGS Internal Staff"]),
			("disabled@example.invalid", 0, "System User", ["System Manager"]),
			("other@example.invalid", 1, "System User", ["Sales User"]),
		):
			with self.subTest(user=user), patch.dict(frappe.session, {"user": user}):
				self.enabled, self.user_type, self.roles = enabled, kind, roles
				with self.assertRaises(frappe.PermissionError):
					invoicing.generate_invoice(self.order.name)
		self.documents.assert_not_called()
		self.single.assert_not_called()
		self.insert.assert_not_called()
		self.mail.assert_not_called()

	def test_order_write_and_customer_read_precede_settings_or_writes(self):
		for denied in (("NGS Order", "write"), ("NGS Customer", "read")):
			self.denied = denied
			with self.subTest(denied=denied), self.assertRaises(frappe.PermissionError):
				self.generate()
		self.single.assert_not_called()
		self.insert.assert_not_called()
		self.mail.assert_not_called()

	def test_generation_uses_actual_amount_and_private_pdf_and_queued_mail(self):
		invoice, result = self.generate()
		self.assertFalse(result["existing"])
		self.assertEqual(result["status"], "Issued")
		self.assertEqual(result["total_amount"], 29.99)
		self.assertEqual(invoice.invoice_customer, "Canonical Test Organization")
		self.assertEqual(invoice.billing_address, "Separate Billing Address")
		self.assertEqual(invoice.invoice_email_address, "billing@example.invalid")
		self.assertEqual(invoice.pdf_sha256, hashlib.sha256(PDF).hexdigest())
		self.assertEqual(json.loads(invoice.items_snapshot)[0]["amount"], "29.99")
		self.assertEqual(json.loads(invoice.items_snapshot)[0]["quantity"], "3")
		self.assertEqual(json.loads(invoice.company_snapshot)["account_number"], "configured-account_number")
		self.assertIn("download_invoice?invoice_name=", result["download_url"])
		self.assertEqual(
			set(result),
			{
				"name",
				"status",
				"order",
				"customer",
				"total_amount",
				"currency",
				"issue_date",
				"due_date",
				"pdf_url",
				"download_url",
				"existing",
			},
		)
		call = self.mail.call_args.kwargs
		self.assertIs(call["delayed"], True)
		self.assertIs(call["now"], False)
		self.assertEqual(call["recipients"], ["billing@example.invalid"])
		self.assertEqual((call["reference_doctype"], call["reference_name"]), ("NGS Invoice", invoice.name))
		self.assertEqual(call["attachments"], [{"fname": invoice.name + ".pdf", "fcontent": PDF}])
		self.assertTrue(self.render.call_args.args[1]["logo"].startswith("data:image/png;base64,"))

	def test_calendar_day_terms_for_both_categories(self):
		for category, days in (("Commercial", 30), ("Academic", 45)):
			self.customer.billing_category = category
			invoice, _result = self.generate()
			self.assertEqual(invoice.payment_terms_days, days)
			self.assertEqual(getdate(invoice.due_date), getdate(add_days(invoice.issue_date, days)))
			self.saved.clear()

	def test_calendar_terms_cross_year_leap_february_and_month_boundaries(self):
		cases = (
			("2027-12-31", "2028-01-30", "2028-02-14"),
			("2028-01-31", "2028-03-01", "2028-03-16"),
			("2027-01-31", "2027-03-02", "2027-03-17"),
			("2028-02-29", "2028-03-30", "2028-04-14"),
			("2028-04-30", "2028-05-30", "2028-06-14"),
		)
		for issue_date, due_30, due_45 in cases:
			for category, days, due_date in (("Commercial", 30, due_30), ("Academic", 45, due_45)):
				self.saved.clear()
				self.customer.billing_category = category
				with self.subTest(issue_date=issue_date, category=category), patch.object(
					invoicing, "today", return_value=issue_date
				):
					invoice, result = self.generate()
					self.assertFalse(result["existing"])
					self.assertEqual(invoice.payment_terms_days, days)
					self.assertEqual(getdate(invoice.issue_date), getdate(issue_date))
					self.assertEqual(getdate(invoice.due_date), getdate(due_date))
					self.assertEqual(getdate(result["due_date"]), getdate(due_date))

	def test_duplicate_click_returns_original_without_settings_email_or_rerender(self):
		invoice, first = self.generate()
		self.customer.invoice_email_address = ""
		self.customer.billing_category = ""
		self.settings.account_number = ""
		again = invoicing.generate_invoice(self.order.name)
		self.assertEqual(again["name"], first["name"])
		self.assertTrue(again["existing"])
		self.assertEqual(invoice.invoice_email_address, "billing@example.invalid")
		self.insert.assert_called_once()
		self.render.assert_called_once()
		self.mail.assert_called_once()
		self.single.assert_called_once()

	def test_duplicate_after_profile_and_bank_changes_preserves_all_archived_facts(self):
		invoice, first = self.generate()
		archived = deepcopy(invoice.as_dict())
		path = Path(self.files[invoice.pdf_file].get_full_path())
		original_pdf = path.read_bytes()
		self.customer.update(
			{
				"organization": "Changed Organization",
				"company_institution": "Changed Legacy Organization",
				"full_name": "Changed Customer",
				"billing_address": "Changed Billing Address",
				"address": "Changed Profile Address",
				"adress": "Changed Legacy Address",
				"invoice_email_address": "changed@example.invalid",
				"billing_category": "Academic",
			}
		)
		for field in (*invoicing.COMPANY_FIELDS, *invoicing.BANK_FIELDS):
			self.settings[field] = "changed-" + field
		with patch.object(invoicing, "today", return_value="2028-01-31"):
			again = invoicing.generate_invoice(self.order.name)
		self.assertEqual(again, {**first, "existing": True})
		self.assertEqual(invoice.as_dict(), archived)
		self.assertEqual(path.read_bytes(), original_pdf)
		self.assertEqual(invoicing._stored_pdf(invoice), original_pdf)
		self.insert.assert_called_once()
		self.save.assert_called_once()
		self.file_save.assert_called_once()
		self.render.assert_called_once()
		self.mail.assert_called_once()
		self.single.assert_called_once()

	def test_missing_or_invalid_billing_email_fails_before_write(self):
		for value in ("", None, "one@example.invalid,two@example.invalid", "bad", "one@example.invalid\n"):
			self.customer.invoice_email_address = value
			if value == "one@example.invalid\n":
				# The existing shared normalizer permits surrounding whitespace.
				continue
			with self.subTest(value=value), self.assertRaises(frappe.ValidationError):
				self.generate()
		self.insert.assert_not_called()
		self.mail.assert_not_called()

	def test_missing_category_or_bank_configuration_fails_before_write(self):
		for category in (None, "", "Enterprise"):
			self.customer.billing_category = category
			with self.subTest(category=category), self.assertRaises(frappe.ValidationError):
				self.generate()
		self.customer.billing_category = "Commercial"
		for field in invoicing.BANK_FIELDS:
			value = self.settings[field]
			self.settings[field] = ""
			with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
				self.generate()
			self.settings[field] = value
		self.insert.assert_not_called()
		self.mail.assert_not_called()

	def test_billing_address_and_organization_legacy_fallbacks(self):
		self.customer.billing_address = ""
		invoice, _result = self.generate()
		self.assertEqual(invoice.billing_address, "Profile Address")
		self.saved.clear()
		self.customer.address = ""
		self.customer.organization = ""
		invoice, _result = self.generate()
		self.assertEqual(invoice.billing_address, "Legacy Address")
		self.assertEqual(invoice.invoice_customer, "Legacy Test Organization")

	def test_currency_is_snapshotted_without_fx_and_mixed_currency_is_rejected(self):
		self.currency.return_value = "EUR"
		invoice, result = self.generate()
		self.assertEqual((invoice.currency, result["currency"], invoice.total_amount), ("EUR", "EUR", 29.99))
		self.saved.clear()
		self.order.items[0].service = "TEST-SERVICE"
		self.currency.side_effect = ["USD", "EUR"]
		with self.assertRaises(frappe.ValidationError):
			self.generate()
		self.assertEqual(self.insert.call_count, 1)

	def test_bad_numbers_empty_or_zero_orders_fail_before_write(self):
		row = self.order.items[0]
		for field in ("quantity", "unit_price", "amount"):
			original = row.get(field)
			for value in (None, -1, True, "NaN", "Infinity", "bad"):
				row.set(field, value)
				with self.subTest(field=field, value=value), self.assertRaises(frappe.ValidationError):
					self.generate()
			row.set(field, original)
		row.amount = 0
		with self.assertRaises(frappe.ValidationError):
			self.generate()
		self.order.items = []
		with self.assertRaises(frappe.ValidationError):
			self.generate()
		self.insert.assert_not_called()
		self.mail.assert_not_called()

	def test_order_validation_accepts_priced_subtotal_but_invoice_rejects_tbd_component(self):
		row = self.order.items[0]
		row.update({
			"project_type": "10x 3' Transcriptome", "amount": 30,
			"onsite_service": 1, "onsite_location": "Outside Massachusetts", "onsite_address": "Test City, NY",
		})
		row.pricing_details = create_pricing_snapshot(row, [
			{"label": "Recorded service", "quantity": 3, "unit_price": 10, "amount": 30},
			{"label": "On-site service", "quantity": 3, "unit_price": None, "amount": None, "display": "TBD"},
		])
		self.order.sample_registration_form = "/private/files/test-form.xlsx"
		self.order.set("__islocal", True)
		self.order.validate()
		self.order.set("__islocal", False)
		self.assertEqual(row.amount, 30)
		self.assertIsNone(get_recorded_breakdown(row)[-1]["amount"])
		with self.assertRaisesRegex(frappe.ValidationError, "TBD"):
			self.generate()
		self.insert.assert_not_called()
		self.file_save.assert_not_called()
		self.mail.assert_not_called()

	def test_positive_order_with_separate_unpriced_row_cannot_be_invoiced(self):
		row = self.order.append("items", {
			"name": "TBD-ROW", "project_type": "Custom Project", "description": "Pending custom service",
			"quantity": 1, "unit_price": 0, "amount": 0,
		})
		row.pricing_details = create_pricing_snapshot(row, [
			{"label": "Manual quote", "quantity": 1, "unit_price": None, "amount": None, "display": "TBD"},
		])
		self.assertIsNone(get_recorded_breakdown(row)[0]["amount"])
		with self.assertRaisesRegex(frappe.ValidationError, "TBD"):
			self.generate()
		self.insert.assert_not_called()
		self.file_save.assert_not_called()
		self.mail.assert_not_called()

	def test_positive_order_with_explicit_manual_zero_row_remains_invoiceable(self):
		row = self.order.append("items", {
			"name": "ZERO-ROW", "project_type": "Custom Project", "description": "Explicitly waived service",
			"quantity": 1, "unit_price": 0, "amount": 0, "use_price_override": 1, "override_unit_price": 0,
		})
		row.pricing_details = create_pricing_snapshot(row, [
			{"label": "Manual unit price", "quantity": 1, "unit_price": 0, "amount": 0},
		])
		self.assertEqual(get_recorded_breakdown(row)[0]["amount"], 0)
		invoice, result = self.generate()
		self.assertFalse(result["existing"])
		self.assertEqual(invoice.total_amount, 29.99)
		self.assertEqual(json.loads(invoice.items_snapshot)[-1]["amount"], "0")
		self.mail.assert_called_once()

	def test_active_invoice_mismatch_and_order_hook_fail_closed(self):
		invoice, _result = self.generate()
		for field, value in (("po_number", "NEW-PO"),):
			self.order.set(field, value)
			with self.assertRaises(frappe.ValidationError):
				self.generate()
			with self.assertRaises(frappe.ValidationError):
				invoicing.assert_order_invoice_unchanged(self.order)
			self.order.set(field, invoice.get(field))
		self.order.items[0].amount = 30
		with self.assertRaises(frappe.ValidationError):
			self.generate()
		with self.assertRaises(frappe.ValidationError):
			invoicing.assert_order_invoice_unchanged(self.order)
		self.assertEqual(invoice.total_amount, 29.99)
		self.mail.assert_called_once()

	def test_financial_snapshots_pdf_and_number_are_immutable(self):
		invoice, _result = self.generate()
		for field in (*SNAPSHOT_FIELDS, "pdf_file", "pdf_url", "pdf_sha256"):
			original = invoice.get(field)
			invoice.set(field, "changed")
			with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
				invoice.validate()
			invoice.set(field, original)
		with self.assertRaises(frappe.ValidationError):
			invoice.on_trash()
		with self.assertRaises(frappe.ValidationError):
			invoice.before_rename(invoice.name, "NEW-NUMBER")

	def test_existing_invoice_and_pdf_use_current_reads_after_order_lock(self):
		invoice, _result = self.generate()
		self.events.clear()

		def current_documents(doctype, name=None, **kwargs):
			if doctype in ("NGS Invoice", "File"):
				self.assertTrue(kwargs.get("for_update"), "A snapshot read can miss the committed invoice/PDF")
			return self.get_document(doctype, name, **kwargs)

		self.documents.side_effect = current_documents
		result = invoicing.generate_invoice(self.order.name)
		self.assertTrue(result["existing"])
		self.assertEqual(result["name"], invoice.name)
		self.assertLess(self.events.index("order-lock"), self.events.index("invoice-lock"))
		self.documents.assert_any_call("NGS Invoice", invoice.name, for_update=True)
		self.documents.assert_any_call("File", invoice.pdf_file, for_update=True)
		invoicing.assert_order_invoice_unchanged(self.order)
		self.mail.assert_called_once()

	def test_generic_creation_and_direct_void_are_denied(self):
		invoice = self.original_get_doc(
			{
				"doctype": "NGS Invoice",
				"__islocal": 1,
				"status": "Issued",
				"active_order": self.order.name,
				"order": self.order.name,
			}
		)
		with self.assertRaises(frappe.PermissionError):
			invoice.validate()
		invoice, _result = self.generate()
		invoice.flags.invoice_write_token = None
		invoice.status = "Void"
		with self.assertRaises(frappe.ValidationError):
			invoice.validate()

	def test_download_checks_permissions_and_returns_stored_bytes_not_current_render(self):
		invoice, _result = self.generate()
		for denied in (("NGS Invoice", "read"), ("NGS Order", "read"), ("NGS Customer", "read")):
			self.denied = denied
			with self.subTest(denied=denied), self.assertRaises(frappe.PermissionError):
				invoicing.download_invoice(invoice.name)
		self.denied = None
		self.order.items[0].amount = 999
		response = frappe.local.response
		original = dict(response)
		try:
			invoicing.download_invoice(invoice.name)
			self.assertEqual(response["filecontent"], PDF)
			self.assertEqual(response["filename"], invoice.name + ".pdf")
			self.assertEqual(response["content_type"], "application/pdf")
			self.assertEqual(response["display_content_as"], "attachment")
		finally:
			response.clear()
			response.update(original)
		self.render.assert_called_once()

	def test_pdf_attachment_metadata_and_digest_tampering_fail_closed(self):
		invoice, _result = self.generate()
		file = self.files[invoice.pdf_file]
		for field, value in (
			("is_private", 0),
			("attached_to_name", "ANOTHER-INVOICE"),
			("attached_to_doctype", "NGS Order"),
			("file_url", "/files/public.pdf"),
			("get_content", lambda: b"tampered"),
		):
			original = file.get(field)
			file[field] = value
			with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
				invoicing.download_invoice(invoice.name)
			file[field] = original

	def test_explicit_resend_uses_snapshot_email_and_original_pdf(self):
		invoice, _result = self.generate()
		self.customer.invoice_email_address = "changed@example.invalid"
		invoicing.resend_invoice(invoice.name)
		self.assertEqual(self.mail.call_count, 2)
		self.assertEqual(self.mail.call_args.kwargs["recipients"], ["billing@example.invalid"])
		self.assertEqual(self.mail.call_args.kwargs["attachments"][0]["fcontent"], PDF)
		self.render.assert_called_once()

	def test_resend_and_void_require_invoice_write_order_write_and_customer_read(self):
		invoice, _result = self.generate()
		for operation in (
			lambda: invoicing.resend_invoice(invoice.name),
			lambda: invoicing.void_invoice(invoice.name, "Test reason"),
		):
			for denied in (("NGS Invoice", "write"), ("NGS Order", "write"), ("NGS Customer", "read")):
				self.denied = denied
				with self.subTest(denied=denied), self.assertRaises(frappe.PermissionError):
					operation()
		self.assertEqual(invoice.status, "Issued")
		self.updates.assert_not_called()
		self.mail.assert_called_once()

	def test_void_requires_reason_retains_pdf_and_reissue_uses_new_number(self):
		invoice, _result = self.generate()
		old_name, old_file = invoice.name, invoice.pdf_file
		for reason in ("", " ", None):
			with self.subTest(reason=reason), self.assertRaises(frappe.ValidationError):
				invoicing.void_invoice(invoice.name, reason)
		result = invoicing.void_invoice(invoice.name, "Corrected PO")
		self.assertEqual(result["status"], "Void")
		self.assertIsNone(invoice.active_order)
		self.assertEqual(invoice.pdf_file, old_file)
		self.assertEqual(invoice.name, old_name)
		self.assertEqual(invoicing._stored_pdf(invoice), PDF)
		with self.assertRaises(frappe.ValidationError):
			invoicing.resend_invoice(invoice.name)
		self.order.po_number = "CORRECTED-PO"
		invoicing.assert_order_invoice_unchanged(self.order)
		new_invoice, result = self.generate()
		self.assertNotEqual(new_invoice.name, old_name)
		self.assertEqual(new_invoice.po_number, "CORRECTED-PO")
		self.assertFalse(result["existing"])
		self.assertEqual(self.mail.call_count, 2)

	def test_void_cancels_pending_queues_preserving_audit_and_sent_rows(self):
		invoice, _result = self.generate()
		self.queues = [
			frappe._dict(name=status, status=status, error="Earlier audit")
			for status in ("Not Sent", "Partially Sent", "Error", "Sent")
		]
		invoicing.void_invoice(invoice.name, "Corrected PO")
		for queue in self.queues[:-1]:
			self.assertEqual(queue.status, "Error")
			self.assertIn("Earlier audit", queue.error)
			self.assertIn("queued delivery cancelled: Corrected PO", queue.error)
		self.assertEqual(self.queues[-1].status, "Sent")
		self.assertEqual(self.queues[-1].error, "Earlier audit")
		self.assertEqual(self.updates.call_count, 3)
		self.mail.assert_called_once()

	def test_void_rejects_sending_before_mutating_invoice_or_queues(self):
		invoice, _result = self.generate()
		self.queues = [
			frappe._dict(name="pending", status="Not Sent"),
			frappe._dict(name="sending", status="Sending"),
		]
		with self.assertRaises(frappe.ValidationError):
			invoicing.void_invoice(invoice.name, "Corrected PO")
		self.assertEqual(invoice.status, "Issued")
		self.assertEqual(invoice.active_order, self.order.name)
		self.updates.assert_not_called()
		self.mail.assert_called_once()

	def test_void_uses_current_queue_enumeration_before_invoice_lock(self):
		invoice, _result = self.generate()
		self.queues = [frappe._dict(name="resent-queue", status="Not Sent")]
		self.events.clear()
		invoicing.void_invoice(invoice.name, "Corrected PO")
		self.queue_names.assert_called_once_with(
			"Email Queue",
			filters={"reference_doctype": "NGS Invoice", "reference_name": invoice.name},
			fieldname="name",
			pluck=True,
			order_by="name",
			for_update=True,
		)
		for before, after in (
			("order-lock", "queue-enumeration"),
			("queue-enumeration", "queue-lock"),
			("queue-lock", "invoice-lock"),
		):
			self.assertLess(self.events.index(before), self.events.index(after))
		self.assertEqual(self.queues[0].status, "Error")
		self.mail.assert_called_once()

	def test_queue_failure_cleanup_runs_after_native_attachment_restoration(self):
		def fail_queue(**kwargs):
			file = next(iter(self.files.values()))
			path = Path(file.get_full_path())
			self.rollback_callbacks.append(lambda: path.write_bytes(PDF))
			raise frappe.OutgoingEmailError("Test-only queue configuration failure")

		self.mail.side_effect = fail_queue
		with self.assertRaises(frappe.OutgoingEmailError):
			self.generate()
		self.assertEqual(len(self.rollback_callbacks), 2)
		with patch.object(frappe.db, "exists", return_value=False):
			for callback in self.rollback_callbacks:
				callback()
		self.assertEqual(list(self.private_directory.glob("*.pdf")), [])

	def test_success_registers_cleanup_for_a_later_request_rollback(self):
		invoice, _result = self.generate()
		path = Path(self.files[invoice.pdf_file].get_full_path())
		self.assertTrue(path.exists())
		self.assertEqual(len(self.rollback_callbacks), 1)
		with patch.object(frappe.db, "exists", return_value=False):
			self.rollback_callbacks[0]()
		self.assertFalse(path.exists())

	def test_file_insert_failure_still_registers_cleanup_without_returned_file(self):
		def failed_insert(*args):
			self.save_file(*args)
			raise frappe.ValidationError("Test-only File insert failure after writing bytes")

		self.file_save.side_effect = failed_insert
		with self.assertRaises(frappe.ValidationError):
			self.generate()
		self.mail.assert_not_called()
		self.assertEqual(len(list(self.private_directory.glob("*.pdf"))), 1)
		self.assertEqual(len(self.rollback_callbacks), 1)
		with patch.object(frappe.db, "exists", return_value=False):
			self.rollback_callbacks[0]()
		self.assertEqual(list(self.private_directory.glob("*.pdf")), [])

	def test_cleanup_preserves_preexisting_orphan_and_removes_only_new_collision_path(self):
		preexisting = self.private_directory / "INV-2099-00001.pdf"
		preexisting.write_bytes(b"preexisting orphan; not owned by this transaction")
		self.mail.side_effect = frappe.OutgoingEmailError("Test-only queue failure")
		with self.assertRaises(frappe.OutgoingEmailError):
			self.generate()
		self.assertEqual(len(self.rollback_callbacks), 1)
		with patch.object(frappe.db, "exists", return_value=False):
			self.rollback_callbacks[0]()
		self.assertEqual(list(self.private_directory.glob("*.pdf")), [preexisting])
		self.assertEqual(preexisting.read_bytes(), b"preexisting orphan; not owned by this transaction")

	def test_cleanup_does_not_register_for_deduplicated_or_outside_files(self):
		for filename in ("INV-2099-00001.pdf", "reused-other-invoice.pdf"):
			path = self.private_directory / filename
			path.write_bytes(PDF)
			file = frappe._dict(get_full_path=lambda: str(path), file_url="/private/files/" + filename)
			invoicing._register_pdf_rollback(
				file,
				"INV-2099-00001",
				self.private_directory,
				{entry.resolve() for entry in self.private_directory.glob("INV-2099-00001*.pdf")},
				PDF,
			)
			self.assertTrue(path.exists())
		with TemporaryDirectory() as outside:
			path = Path(outside) / "INV-2099-00001.pdf"
			path.write_bytes(PDF)
			file = frappe._dict(get_full_path=lambda: str(path), file_url="/private/files/" + path.name)
			invoicing._register_pdf_rollback(
				file,
				"INV-2099-00001",
				self.private_directory,
				{entry.resolve() for entry in self.private_directory.glob("INV-2099-00001*.pdf")},
				PDF,
			)
			self.assertTrue(path.exists())
		self.assertEqual(self.rollback_callbacks, [])

	def test_cleanup_removes_helper_intermediate_and_controller_renamed_pdf(self):
		def renamed_file(*args, **kwargs):
			file = self.save_file(*args, **kwargs)
			initial = Path(file.get_full_path())
			final = initial.with_name(initial.stem + "-controller-hash.pdf")
			final.write_bytes(PDF)
			file.get_full_path = lambda: str(final)
			file.file_url = "/private/files/" + final.name
			return file

		self.file_save.side_effect = renamed_file
		self.generate()
		self.assertEqual(len(list(self.private_directory.glob("*.pdf"))), 2)
		with patch.object(frappe.db, "exists", return_value=False):
			self.rollback_callbacks[0]()
		self.assertEqual(list(self.private_directory.glob("*.pdf")), [])

	def test_cleanup_preserves_surviving_file_references_and_changed_bytes(self):
		invoice, _result = self.generate()
		file = self.files[invoice.pdf_file]
		path = Path(file.get_full_path())
		with patch.object(frappe.db, "exists", return_value=True) as exists:
			self.rollback_callbacks[0]()
			exists.assert_called_once_with("File", {"file_url": file.file_url})
		self.assertTrue(path.exists())
		path.write_bytes(b"replacement not owned by the invoice generation")
		with patch.object(frappe.db, "exists", return_value=False):
			self.rollback_callbacks[0]()
		self.assertEqual(path.read_bytes(), b"replacement not owned by the invoice generation")


class TestInvoiceFilePersistence(TestCase):
	def test_native_file_writer_leaves_one_byte_identical_blob_on_success(self):
		from frappe.core.doctype.file import file as file_module
		from frappe.core.doctype.file import utils as file_utils
		from pypdf import PdfWriter

		writer = PdfWriter()
		writer.add_blank_page(width=612, height=792)
		buffer = BytesIO()
		writer.write(buffer)
		content = buffer.getvalue()
		frappe.get_doc({"doctype": "File"})  # Warm metadata before blocking database access.
		callbacks = []

		def insert_without_database(doc, **kwargs):
			self.assertEqual(doc.doctype, "File")
			self.assertEqual(kwargs, {"ignore_permissions": True})
			doc.before_insert()
			return doc

		with TemporaryDirectory(dir=frappe.get_site_path("private", "files"), prefix="invoice-native-test-") as directory:
			root = Path(directory)
			with (
				patch.object(Document, "insert", autospec=True, side_effect=insert_without_database) as insert,
				patch.object(
					file_module.File,
					"get_full_path",
					autospec=True,
					side_effect=lambda doc: str(root / (doc.file_url or doc.file_name).split("/")[-1]),
				),
				patch.object(
					file_utils, "get_files_path", side_effect=lambda name, **kwargs: str(root / name)
				),
				patch.object(file_module.File, "validate_attachment_limit"),
				patch.object(file_module.File, "check_max_file_size", return_value=len(content)),
				patch.object(file_module, "call_hook_method"),
				patch.object(file_module, "get_hook_method", return_value=None),
				patch.object(frappe.db, "get_value", return_value=None),
				patch.object(frappe.db, "sql", side_effect=AssertionError("Unexpected database access")),
				patch.object(frappe.db, "after_rollback", new=frappe._dict(add=callbacks.append)),
			):
				file = invoicing._save_invoice_pdf("INV-NATIVE.pdf", content, "INV-NATIVE")
				path = Path(file.get_full_path())
				self.assertEqual(list(root.iterdir()), [path])
				self.assertEqual(path.read_bytes(), content)
				self.assertEqual(file.file_url, "/private/files/" + path.name)
				self.assertEqual(file.attached_to_field, "pdf_url")
				insert.assert_called_once()
				self.assertTrue(callbacks)

	def test_pdf_passes_raw_bytes_to_one_native_file_insert_without_helper_write(self):
		from unittest.mock import Mock

		from frappe.utils import file_manager

		content = PDF + b"\x80\x81\x8d\xff"
		file = Mock()
		file.insert.return_value = file
		with (
			patch.object(frappe, "get_doc", return_value=file) as documents,
			patch.object(
				file_manager, "save_file", side_effect=AssertionError("Must not prewrite PDF bytes")
			) as helper,
		):
			result = invoicing._save_invoice_pdf("INV-TEST.pdf", content, "INV-TEST")
		self.assertIs(result, file)
		documents.assert_called_once_with(
			{
				"doctype": "File",
				"file_name": "INV-TEST.pdf",
				"content": content,
				"is_private": 1,
				"attached_to_doctype": "NGS Invoice",
				"attached_to_name": "INV-TEST",
				"attached_to_field": "pdf_url",
			}
		)
		self.assertIs(documents.call_args.args[0]["content"], content)
		file.insert.assert_called_once_with(ignore_permissions=True)
		helper.assert_not_called()


class TestInvoiceEmailQueueGuard(TestCase):
	def setUp(self):
		from unittest.mock import Mock

		self.sender = Mock(return_value="delegated")
		sender = self.sender

		class FrameworkSender:
			def send(self, *args, **kwargs):
				return sender(*args, **kwargs)

		class GuardedQueue(invoicing.InvoiceEmailQueueGuardMixin, FrameworkSender):
			name = "QUEUE-GUARD-TEST"
			reference_doctype = "NGS Invoice"
			reference_name = "INV-GUARD-TEST"
			status = "Not Sent"
			modified = datetime(2026, 11, 12, 9, 30)

		self.queue = GuardedQueue()
		self.stored_queue = frappe._dict(
			name=self.queue.name,
			reference_doctype=self.queue.reference_doctype,
			reference_name=self.queue.reference_name,
			status=self.queue.status,
			modified=self.queue.modified,
		)
		patcher = patch.object(frappe, "get_doc", return_value=self.stored_queue)
		self.documents = patcher.start()
		self.addCleanup(patcher.stop)
		patcher = patch.object(
			frappe.db,
			"get_value",
			return_value=frappe._dict(
				status="Issued",
				order="ORDER-GUARD-TEST",
				active_order="ORDER-GUARD-TEST",
			),
		)
		self.values = patcher.start()
		self.addCleanup(patcher.stop)
		self.calls = Mock()
		self.calls.attach_mock(self.documents, "queue")
		self.calls.attach_mock(self.values, "invoice")
		self.calls.attach_mock(self.sender, "sender")

	def test_manual_send_locks_current_queue_before_invoice_and_sender(self):
		self.assertEqual(self.queue.send(), "delegated")
		self.documents.assert_called_once_with("Email Queue", self.queue.name, for_update=True)
		self.assertTrue(self.values.call_args.kwargs["for_update"])
		self.assertEqual([call[0] for call in self.calls.mock_calls], ["queue", "invoice", "sender"])

	def test_stale_or_in_flight_queue_is_rejected_even_for_forced_send(self):
		for field, value in (
			("reference_doctype", "NGS Order"),
			("reference_name", "OTHER-INVOICE"),
			("status", "Error"),
			("status", "Sending"),
			("status", "Sent"),
			("modified", datetime(2026, 11, 12, 9, 31)),
		):
			original = self.stored_queue[field]
			self.stored_queue[field] = value
			for force in (False, True):
				with self.subTest(field=field, value=value, force=force), self.assertRaises(frappe.ValidationError):
					self.queue.send(force_send=force)
			self.stored_queue[field] = original
		for status in ("Sending", "Sent"):
			self.queue.status = self.stored_queue.status = status
			with self.subTest(status=status), self.assertRaises(frappe.ValidationError):
				self.queue.send(force_send=True)
		self.values.assert_not_called()
		self.sender.assert_not_called()

	def test_missing_queue_is_rejected_before_invoice_lock_or_delivery(self):
		self.queue.name = None
		with self.assertRaises(frappe.ValidationError):
			self.queue.send(force_send=True)
		self.documents.assert_not_called()
		self.queue.name = "DELETED-QUEUE"
		self.documents.side_effect = frappe.DoesNotExistError("Deleted queue")
		with self.assertRaises(frappe.DoesNotExistError):
			self.queue.send(force_send=True)
		self.values.assert_not_called()
		self.sender.assert_not_called()

	def test_fresh_error_queue_still_delegates_explicit_force_send(self):
		self.queue.status = self.stored_queue.status = "Error"
		self.assertEqual(self.queue.send(force_send=True), "delegated")
		self.sender.assert_called_once_with(force_send=True)

	def test_retries_and_force_send_never_reach_sender_for_void_missing_or_inactive_invoice(self):
		for invoice in (
			None,
			frappe._dict(status="Void", order="ORDER-GUARD-TEST", active_order=None),
			frappe._dict(status="Issued", order="ORDER-GUARD-TEST", active_order=None),
			frappe._dict(status="Issued", order="ORDER-GUARD-TEST", active_order="OTHER-ORDER"),
		):
			self.values.return_value = invoice
			for force in (False, True):
				with self.subTest(invoice=invoice, force=force), self.assertRaises(frappe.ValidationError):
					self.queue.send(force_send=force)
		self.sender.assert_not_called()
		self.assertEqual(self.queue.status, "Not Sent")
		self.values.assert_called_with(
			"NGS Invoice",
			self.queue.reference_name,
			["status", "active_order", "order"],
			as_dict=True,
			for_update=True,
		)

	def test_active_invoice_delegates_arguments_and_rechecks_database_on_retry(self):
		server, client = object(), object()
		self.assertEqual(self.queue.send(server, frappe_mail_client=client, force_send=True), "delegated")
		self.sender.assert_called_once_with(server, frappe_mail_client=client, force_send=True)
		self.values.return_value = frappe._dict(status="Void", order="ORDER-GUARD-TEST", active_order=None)
		with self.assertRaises(frappe.ValidationError):
			self.queue.send(force_send=True)
		self.sender.assert_called_once()
		self.assertEqual(self.values.call_count, 2)

	def test_unrelated_mail_delegates_unchanged_and_missing_invoice_reference_fails_closed(self):
		self.queue.reference_doctype = "NGS Order"
		self.assertEqual(self.queue.send(force_send=True), "delegated")
		self.sender.assert_called_once_with(force_send=True)
		self.values.assert_not_called()
		self.queue.reference_doctype = "NGS Invoice"
		self.queue.reference_name = None
		with self.assertRaises(frappe.ValidationError):
			self.queue.send(force_send=True)
		self.sender.assert_called_once()
		self.values.assert_not_called()


class TestInvoicePDF(TestCase):
	def test_native_pdf_has_letter_page_logo_invoice_metadata_and_escaped_snapshots(self):
		from pypdf import PdfReader

		invoice = frappe._dict(
			name="INV-2099-00001",
			invoice_customer="Test Organization <script>",
			invoice_email_address="billing@example.invalid",
			billing_address="Billing Address\nSecond Line",
			issue_date="2099-01-01",
			due_date="2099-01-31",
			order="ORDER-PDF-TEST",
			po_number="PO-PDF-TEST",
			currency="USD",
			total_amount=29.99,
		)
		company = frappe._dict(
			{field: "Test " + field for field in (*invoicing.COMPANY_FIELDS, *invoicing.BANK_FIELDS)}
		)
		logo = "data:image/png;base64," + invoicing.base64.b64encode(
			invoicing.Path(frappe.get_app_path("ngs_hub", "public", "logo.png")).read_bytes()
		).decode("ascii")
		context = {
			"invoice": invoice,
			"company": company,
			"logo": logo,
			"items": [
				{
					"description": "Actual completed service",
					"quantity": "3",
					"unit_price": "10",
					"amount": "29.99",
				}
			],
		}
		html = frappe.render_template("templates/includes/ngs_invoice_pdf.html", context)
		self.assertIn("&lt;script&gt;", html)
		self.assertNotIn("<script>", html)
		self.assertNotIn("Unit Price", html)
		self.assertNotIn(">Qty<", html)
		self.assertNotIn("Actual completed service", html)
		pdf = invoicing.render_pdf("templates/includes/ngs_invoice_pdf.html", context)
		reader = PdfReader(BytesIO(pdf))
		self.assertEqual(len(reader.pages), 1)
		page = reader.pages[0]
		self.assertEqual((float(page.mediabox.width), float(page.mediabox.height)), (612, 792))
		text = page.extract_text()
		for expected in (
			invoice.name,
			invoice.po_number,
			invoice.issue_date,
			invoice.due_date,
			"29.99",
			"USD",
		):
			self.assertIn(expected, text)
		resources = page["/Resources"].get_object()
		self.assertTrue(
			any(obj.get_object().get("/Subtype") == "/Image" for obj in resources["/XObject"].values())
		)
		invoice.po_number = ""
		without_po = frappe.render_template("templates/includes/ngs_invoice_pdf.html", context)
		self.assertNotIn(">PO<", without_po)
		self.assertNotIn("PO-PDF-TEST", without_po)
