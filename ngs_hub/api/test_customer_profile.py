"""Invoice contact validation and owner-scoped profile updates."""

from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from ngs_hub.api import portal
from ngs_hub.api.customer_profile import normalize_invoice_email_address
from ngs_hub.ngs_hub.doctype.ngs_customer.ngs_customer import NGSCustomer


class TestInvoiceEmail(TestCase):
	def test_optional_single_email_is_trimmed_without_changing_login(self):
		for value, expected in (
			(None, ""),
			("", ""),
			("  ", ""),
			(" billing@example.com ", "billing@example.com"),
		):
			with self.subTest(value=value):
				self.assertEqual(normalize_invoice_email_address(value), expected)

	def test_invalid_multiple_and_header_addresses_are_rejected(self):
		for value in (
			123,
			[],
			{},
			False,
			"invalid",
			"a@example.com,b@example.com",
			"a@example.com;b@example.com",
			"a@example.com\nBcc: b@example.com",
			"Billing <a@example.com>",
			"a" * 141 + "@example.com",
		):
			with self.subTest(value=value), self.assertRaises(frappe.InvalidEmailAddressError):
				normalize_invoice_email_address(value)

	def test_admin_controller_uses_same_validation_and_allows_clearing(self):
		customer = NGSCustomer(
			{
				"doctype": "NGS Customer",
				"email": "login@example.com",
				"invoice_email_address": " billing@example.com ",
			}
		)
		customer.validate()
		self.assertEqual(customer.invoice_email_address, "billing@example.com")
		self.assertEqual(customer.email, "login@example.com")
		customer.invoice_email_address = "bad"
		with self.assertRaises(frappe.InvalidEmailAddressError):
			customer.validate()
		customer.invoice_email_address = ""
		customer.validate()
		self.assertEqual(customer.invoice_email_address, "")

	def test_field_is_optional_editable_and_updateable_after_submit(self):
		field = frappe.get_meta("NGS Customer").get_field("invoice_email_address")
		self.assertEqual(field.options, "Email")
		self.assertFalse(field.reqd)
		self.assertFalse(field.read_only)
		self.assertTrue(field.allow_on_submit)


class TestInvoiceProfileAPI(TestCase):
	def setUp(self):
		self.user = frappe._dict(name="login@example.com", email="login@example.com")
		self.customer = frappe._dict(
			name="OWN-CUSTOMER", email=self.user.email, invoice_email_address="admin@example.com", save=Mock()
		)
		self.payload = dict(
			first_name="Test", last_name="Customer", organization="Test Lab", address="Test address"
		)

	def save(self, **extra):
		with (
			patch.object(frappe, "session", frappe._dict(user=self.user.name)),
			patch.object(frappe.db, "get_value", return_value=self.customer.name) as lookup,
			patch.object(frappe, "get_doc", side_effect=[self.user, self.customer]),
			patch.object(portal, "ensure_ngs_external_customer_user") as roles,
			patch.object(portal, "sync_ngs_customer_to_crm"),
			patch.object(portal, "sync_ngs_customer_to_frappe_crm"),
			patch.object(frappe.cache, "hdel"),
		):
			result = portal.register_ngs_customer({**self.payload, **extra})
			lookup.assert_called_once_with("NGS Customer", {"email": self.user.email}, "name")
			roles.assert_called_once_with(self.user, save=True)
			return result

	def test_customer_updates_only_own_invoice_not_payload_account(self):
		result = self.save(
			invoice_email_address=" new@example.com ", customer="OTHER", email="other@example.com"
		)
		self.assertEqual(result["name"], self.customer.name)
		self.assertEqual(self.customer.invoice_email_address, "new@example.com")
		self.assertEqual(self.customer.email, self.user.email)
		self.customer.save.assert_called_once_with(ignore_permissions=True)

	def test_omitted_field_preserves_admin_value_but_explicit_blank_clears(self):
		self.save()
		self.assertEqual(self.customer.invoice_email_address, "admin@example.com")
		self.save(invoice_email_address="")
		self.assertEqual(self.customer.invoice_email_address, "")

	def test_submitted_legacy_fallbacks_are_not_written_as_locked_field_edits(self):
		self.customer.update(
			docstatus=1,
			full_name="Test Customer",
			first_name=None,
			last_name=None,
			organization=None,
			company_institution="Test Lab",
		)
		self.save(invoice_email_address="billing@example.com")
		self.assertIsNone(self.customer.first_name)
		self.assertIsNone(self.customer.last_name)
		self.assertIsNone(self.customer.organization)
		self.assertEqual(self.customer.invoice_email_address, "billing@example.com")

	def test_true_locked_field_edits_are_still_passed_to_standard_validation(self):
		for first_name in (None, "Existing"):
			with self.subTest(first_name=first_name):
				self.setUp()
				self.customer.update(docstatus=1, full_name="Test Customer", first_name=first_name)
				self.customer.save.side_effect = frappe.UpdateAfterSubmitError("Locked field")
				with self.assertRaises(frappe.UpdateAfterSubmitError):
					self.save(first_name="Changed", invoice_email_address="billing@example.com")
				self.assertEqual(self.customer.first_name, "Changed")

	def test_invalid_invoice_is_rejected_before_user_roles_or_profile_writes(self):
		with (
			patch.object(frappe, "session", frappe._dict(user=self.user.name)),
			patch.object(frappe.db, "get_value", return_value=self.customer.name),
			patch.object(frappe, "get_doc", return_value=self.user),
			patch.object(portal, "ensure_ngs_external_customer_user") as roles,
			self.assertRaises(frappe.InvalidEmailAddressError),
		):
			portal.register_ngs_customer({**self.payload, "invoice_email_address": "invalid"})
		roles.assert_not_called()
		self.customer.save.assert_not_called()

	def test_guest_cannot_write(self):
		with (
			patch.object(frappe, "session", frappe._dict(user="Guest")),
			self.assertRaises(frappe.PermissionError),
		):
			portal.register_ngs_customer({**self.payload, "invoice_email_address": "billing@example.com"})

	def test_billing_address_is_owner_scoped_trimmed_and_optional(self):
		self.customer.billing_address = "Admin address"
		self.save()
		self.assertEqual(self.customer.billing_address, "Admin address")
		self.save(billing_address="  Accounts payable address\nSecond line  ", billing_category="Academic")
		self.assertEqual(self.customer.billing_address, "Accounts payable address\nSecond line")
		self.assertIsNone(self.customer.get("billing_category"))
		self.save(billing_address="")
		self.assertEqual(self.customer.billing_address, "")

	def test_invalid_billing_address_fails_before_role_or_profile_writes(self):
		for value in (123, [], {}, "x" * 2001):
			with (
				self.subTest(value_type=type(value).__name__),
				patch.object(frappe, "session", frappe._dict(user=self.user.name)),
				patch.object(frappe.db, "get_value", return_value=self.customer.name),
				patch.object(frappe, "get_doc", return_value=self.user),
				patch.object(portal, "ensure_ngs_external_customer_user") as roles,
				self.assertRaises(frappe.ValidationError),
			):
				portal.register_ngs_customer({**self.payload, "billing_address": value})
			roles.assert_not_called()
		self.customer.save.assert_not_called()
