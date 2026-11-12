from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe
from werkzeug.local import LocalProxy

from ngs_hub.api import erpnext_sync
from ngs_hub.api.erpnext_sync import _erpnext_sync_user
from ngs_hub.ngs_hub.doctype.ngs_quote.ngs_quote import NGSQuote


class TestERPNextSyncUser(TestCase):
	def setUp(self):
		self.session = frappe._dict(user="customer@example.test", sid="customer-session", data={"marker": 1})
		self.context = {
			"cache": {"marker": 1},
			"form_dict": frappe._dict(cmd="ngs_hub.api.portal.create_order", payload="original payload"),
			"jenv": object(),
			"jenv_restricted": object(),
			"jenv_unrestricted": object(),
			"role_permissions": {"marker": 1},
			"new_doc_templates": {"marker": 1},
			"user_perms": object(),
		}
		self.local = SimpleNamespace(session=self.session, **self.context)
		self.local.session_obj = SimpleNamespace(data=self.session)
		self.session_obj = self.local.session_obj
		self.source = SimpleNamespace(doctype="NGS Order", name="ORDER-TEST", customer="CUSTOMER-TEST")
		self.source.get = lambda key, default=None: getattr(self.source, key, default)
		self.source.items = [object()]
		self.saved_customer = "CUSTOMER-TEST"
		self.saved_links = {}
		self.user_customer = "CUSTOMER-TEST"
		self.user_type = "Website User"
		self.enabled = True
		self.start_patch("local", self.local)
		self.start_patch("session", LocalProxy(lambda: self.local.session))
		self.start_patch("db", SimpleNamespace(get_value=self.get_value))
		self.start_patch("_", lambda message: message)
		self.start_patch("throw", self.throw)
		self.permission = self.start_patch("has_permission", return_value=False)
		self.start_patch("get_roles", return_value=["NGS Internal Staff"])
		self.switch_user = self.start_patch("set_user", wraps=frappe.set_user)

	def start_patch(self, name, value=None, **kwargs):
		patcher = patch.object(frappe, name, value, **kwargs) if value is not None else patch.object(frappe, name, **kwargs)
		mock = patcher.start()
		self.addCleanup(patcher.stop)
		return mock

	def get_value(self, doctype, name, field):
		if doctype in {"NGS Order", "NGS Quote"}:
			return self.saved_customer if field == "customer" else self.saved_links.get(field)
		if doctype == "NGS Customer":
			return self.user_customer
		if doctype == "User":
			if field == "enabled":
				return self.enabled
			return self.user_type if field == "user_type" else "customer@example.test"
		self.fail(f"Unexpected lookup: {doctype}")

	@staticmethod
	def throw(message, exception=frappe.ValidationError):
		raise exception(message)

	def assert_restored(self):
		self.assertEqual(self.session, {"user": "customer@example.test", "sid": "customer-session", "data": {"marker": 1}})
		for key, value in self.context.items():
			self.assertIs(getattr(self.local, key), value)
		self.assertIs(self.local.session, self.session)
		self.assertIs(self.local.session_obj, self.session_obj)
		self.assertIs(self.local.session_obj.data, self.session)
		self.assertFalse(hasattr(self.local, "ngs_erpnext_sync_actor"))

	def test_restores_complete_request_after_success(self):
		with _erpnext_sync_user(self.source, "NGS Order"):
			self.assertEqual(self.local.session.user, "Administrator")
			self.assertEqual(self.local.session.sid, "Administrator")
			self.assertEqual(self.session.user, "customer@example.test")
			self.assertEqual(self.local.form_dict, {})
		self.assert_restored()

	def test_restores_context_if_identity_switch_fails(self):
		def partial_switch(user):
			self.local.session.user = user
			self.local.form_dict = {}
			raise RuntimeError("switch failed")
		self.switch_user.side_effect = partial_switch
		with self.assertRaisesRegex(RuntimeError, "switch failed"), _erpnext_sync_user(self.source, "NGS Order"):
			self.fail("A failed identity switch entered synchronization")
		self.assert_restored()

	def test_preserves_absent_context_attributes(self):
		for key in ("jenv_restricted", "jenv_unrestricted"):
			delattr(self.local, key)
		with _erpnext_sync_user(self.source, "NGS Order"):
			self.local.jenv_restricted = object()
			self.local.jenv_unrestricted = object()
		self.assertFalse(hasattr(self.local, "jenv_restricted"))
		self.assertFalse(hasattr(self.local, "jenv_unrestricted"))

	def test_rejects_disabled_user(self):
		self.enabled = False
		with self.assertRaises(frappe.PermissionError), _erpnext_sync_user(self.source, "NGS Order"):
			self.fail("Disabled user reached synchronization")
		self.switch_user.assert_not_called()

	def test_rejects_nested_synchronization(self):
		with _erpnext_sync_user(self.source, "NGS Order"):
			with self.assertRaises(frappe.PermissionError), _erpnext_sync_user(self.source, "NGS Order"):
				self.fail("Nested synchronization used elevated authority")
		self.assert_restored()

	def test_public_sync_rejects_nested_calls_before_preparation(self):
		with patch.object(frappe, "get_installed_apps", return_value=["erpnext"]), patch.object(
			erpnext_sync, "get_or_create_erp_customer"
		) as prepare_customer:
			with _erpnext_sync_user(self.source, "NGS Order"):
				for doctype, synchronize in (
					("NGS Order", erpnext_sync.sync_ngs_order_to_sales_order),
					("NGS Quote", erpnext_sync.sync_ngs_quote_to_quotation),
				):
					self.source.doctype = doctype
					with self.assertRaises(frappe.PermissionError):
						synchronize(self.source)
			prepare_customer.assert_not_called()
		self.assert_restored()

	def test_public_sync_rejects_cross_customer_before_preparation(self):
		self.user_customer = "ANOTHER-CUSTOMER"
		with patch.object(frappe, "get_installed_apps", return_value=["erpnext"]), patch.object(
			erpnext_sync, "get_or_create_erp_customer"
		) as prepare_customer:
			for doctype, synchronize in (
				("NGS Order", erpnext_sync.sync_ngs_order_to_sales_order),
				("NGS Quote", erpnext_sync.sync_ngs_quote_to_quotation),
			):
				self.source.doctype = doctype
				with self.assertRaises(frappe.PermissionError):
					synchronize(self.source)
			prepare_customer.assert_not_called()
		self.switch_user.assert_not_called()

	def test_rejects_untrusted_links_even_when_erp_customer_matches(self):
		self.local.session.user = "Administrator"
		self.local.session.sid = "Administrator"
		self.local.session.data = {}
		frappe.db.exists = Mock(return_value=True)
		with patch.object(frappe, "get_installed_apps", return_value=["erpnext"]), patch.object(
			erpnext_sync, "get_or_create_erp_customer", return_value="ERP-CUSTOMER"
		), patch.object(frappe, "get_doc") as get_target:
			for doctype, synchronize, field in (
				("NGS Order", erpnext_sync.sync_ngs_order_to_sales_order, "erpnext_sales_order"),
				("NGS Quote", erpnext_sync.sync_ngs_quote_to_quotation, "erpnext_quotation"),
			):
				self.source.doctype = doctype
				setattr(self.source, field, "ERP-TARGET")
				for saved_link in (None, "SAVED-TARGET"):
					self.saved_links[field] = saved_link
					for customer in ("ANOTHER-CUSTOMER", "ERP-CUSTOMER"):
						with self.subTest(doctype=doctype, saved_link=saved_link, customer=customer):
							target = SimpleNamespace(
								name="ERP-TARGET", docstatus=1, customer=customer, party_name=customer,
								quotation_to="Customer", save=Mock(), set=Mock(),
							)
							get_target.return_value = target
							with self.assertRaises(frappe.PermissionError):
								synchronize(self.source)
							target.save.assert_not_called()
							target.set.assert_not_called()
			get_target.assert_not_called()
		self.switch_user.assert_not_called()

	def test_rejects_clearing_persisted_links_before_customer_preparation(self):
		with patch.object(frappe, "get_installed_apps", return_value=["erpnext"]), patch.object(
			erpnext_sync, "get_or_create_erp_customer"
		) as prepare_customer:
			for doctype, synchronize, field in (
				("NGS Order", erpnext_sync.sync_ngs_order_to_sales_order, "erpnext_sales_order"),
				("NGS Quote", erpnext_sync.sync_ngs_quote_to_quotation, "erpnext_quotation"),
			):
				self.source.doctype = doctype
				self.saved_links[field] = "SAVED-TARGET"
				with self.assertRaises(frappe.PermissionError):
					synchronize(self.source)
			prepare_customer.assert_not_called()
		self.switch_user.assert_not_called()

	def test_trusted_submitted_targets_keep_historical_customer(self):
		frappe.db.exists = Mock(return_value=True)
		with patch.object(frappe, "get_installed_apps", return_value=["erpnext"]), patch.object(
			erpnext_sync, "get_or_create_erp_customer", return_value="ERP-NEW"
		) as prepare_customer, patch.object(frappe, "get_doc") as get_target:
			for doctype, synchronize, field in (
				("NGS Order", erpnext_sync.sync_ngs_order_to_sales_order, "erpnext_sales_order"),
				("NGS Quote", erpnext_sync.sync_ngs_quote_to_quotation, "erpnext_quotation"),
			):
				self.source.doctype = doctype
				setattr(self.source, field, "ERP-TARGET")
				self.saved_links[field] = "ERP-TARGET"
				target = SimpleNamespace(
					name="ERP-TARGET", docstatus=1, customer="ERP-OLD", party_name="ERP-OLD",
					quotation_to="Customer", save=Mock(), set=Mock(),
				)
				get_target.return_value = target
				self.assertEqual(synchronize(self.source), "ERP-TARGET")
				target.save.assert_not_called()
				target.set.assert_not_called()
			prepare_customer.assert_not_called()

	def test_restores_complete_request_after_failure(self):
		with self.assertRaisesRegex(RuntimeError, "sync failed"):
			with _erpnext_sync_user(self.source, "NGS Order"):
				raise RuntimeError("sync failed")
		self.assert_restored()

	def test_rejects_guest_without_switching_identity(self):
		self.session.user = "Guest"
		with self.assertRaises(frappe.PermissionError), _erpnext_sync_user(self.source, "NGS Order"):
			self.fail("Guest reached ERPNext synchronization")
		self.switch_user.assert_not_called()

	def test_rejects_another_customer_before_switching_identity(self):
		self.user_customer = "ANOTHER-CUSTOMER"
		with self.assertRaises(frappe.PermissionError), _erpnext_sync_user(self.source, "NGS Order"):
			self.fail("Cross-customer synchronization was authorized")
		self.switch_user.assert_not_called()

	def test_rejects_unpersisted_or_changed_source_customer(self):
		for saved_customer in (None, "ANOTHER-CUSTOMER"):
			with self.subTest(saved_customer=saved_customer):
				self.saved_customer = saved_customer
				with self.assertRaises(frappe.PermissionError), _erpnext_sync_user(self.source, "NGS Order"):
					self.fail("Invalid source reached ERPNext synchronization")
		self.switch_user.assert_not_called()

	def test_rejects_wrong_source_doctype(self):
		with self.assertRaises(frappe.PermissionError), _erpnext_sync_user(self.source, "NGS Quote"):
			self.fail("Wrong source doctype was authorized")
		self.switch_user.assert_not_called()

	def test_internal_user_needs_source_write_permission(self):
		self.user_type = "System User"
		with self.assertRaises(frappe.PermissionError), _erpnext_sync_user(self.source, "NGS Order"):
			self.fail("Internal source permissions were ignored")
		self.switch_user.assert_not_called()
		self.permission.return_value = True
		with _erpnext_sync_user(self.source, "NGS Order"):
			self.assertEqual(self.local.session.user, "Administrator")
		self.permission.assert_called_with("NGS Order", "write", doc=self.source, user="customer@example.test")
		self.assert_restored()

	def test_administrator_context_is_unchanged(self):
		self.session.user = "Administrator"
		with _erpnext_sync_user(self.source, "NGS Order"):
			self.assertIs(self.local.form_dict, self.context["form_dict"])
		self.switch_user.assert_not_called()


class TestQuoteERPNextLinkGuard(TestCase):
	def setUp(self):
		self.quote = NGSQuote({"doctype": "NGS Quote", "name": "QUOTE-TEST", "customer": "NGS-CUSTOMER"})
		self.saved = frappe._dict(customer="NGS-CUSTOMER", erpnext_quotation=None)
		self.quote._doc_before_save = self.saved
		for name in ("set_customer_snapshot", "apply_quote_rules", "set_project_summary", "calculate_total"):
			patcher = patch.object(NGSQuote, name)
			patcher.start()
			self.addCleanup(patcher.stop)
		for patcher in (
			patch.object(frappe, "throw", side_effect=TestERPNextSyncUser.throw),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def test_new_quote_rejects_client_link_even_for_administrator(self):
		self.quote.erpnext_quotation = "FOREIGN-QUOTATION"
		self.saved.erpnext_quotation = "FOREIGN-QUOTATION"
		with patch.object(NGSQuote, "is_new", return_value=True), self.assertRaises(frappe.PermissionError):
			self.quote.validate()
		self.quote.set_customer_snapshot.assert_not_called()

	def test_existing_quote_rejects_link_injection_replacement_and_removal(self):
		for saved_link, supplied_link in ((None, "FOREIGN"), ("SAVED", "FOREIGN"), ("SAVED", None)):
			with self.subTest(saved=saved_link, supplied=supplied_link):
				self.saved.erpnext_quotation = saved_link
				self.quote.erpnext_quotation = supplied_link
				with patch.object(NGSQuote, "is_new", return_value=False), self.assertRaises(frappe.PermissionError):
					self.quote.validate()
		self.quote.set_customer_snapshot.assert_not_called()

	def test_linked_quote_rejects_customer_reparenting(self):
		self.saved.erpnext_quotation = self.quote.erpnext_quotation = "SAVED"
		self.quote.customer = "ANOTHER-NGS-CUSTOMER"
		with patch.object(NGSQuote, "is_new", return_value=False), self.assertRaises(frappe.PermissionError):
			self.quote.validate()
		self.quote.set_customer_snapshot.assert_not_called()

	def test_unchanged_saved_link_remains_valid(self):
		self.saved.erpnext_quotation = self.quote.erpnext_quotation = "SAVED"
		with patch.object(NGSQuote, "is_new", return_value=False):
			self.quote.validate()
		self.quote.calculate_total.assert_called_once_with()

	def test_missing_presave_snapshot_cannot_authorize_supplied_link(self):
		self.quote._doc_before_save = None
		self.quote.erpnext_quotation = "FOREIGN"
		with patch.object(NGSQuote, "is_new", return_value=False), self.assertRaises(frappe.PermissionError):
			self.quote.validate()

	def test_save_snapshot_reload_discards_forged_previous_link(self):
		self.quote.erpnext_quotation = "FOREIGN"
		self.quote._doc_before_save = frappe._dict(customer=self.quote.customer, erpnext_quotation="FOREIGN")
		with patch.object(NGSQuote, "is_new", return_value=False), patch.object(
			frappe, "get_doc", return_value=self.saved
		) as get_saved:
			self.quote.load_doc_before_save(raise_exception=True)
			get_saved.assert_called_once_with("NGS Quote", "QUOTE-TEST", for_update=True)
			with self.assertRaises(frappe.PermissionError):
				self.quote.validate()

	def test_new_unlinked_quote_remains_valid(self):
		with patch.object(NGSQuote, "is_new", return_value=True):
			self.quote.validate()
		self.quote.calculate_total.assert_called_once_with()
