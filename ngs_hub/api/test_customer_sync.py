"""One-way customer profile synchronization; persistence and email are mocked."""

from functools import partial
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from ngs_hub.api import crm_sync, frappe_crm_sync
from ngs_hub.ngs_hub.doctype.ngs_customer import ngs_customer


class MemoryDocument(frappe._dict):
	def __init__(self, **values):
		super().__init__(values)
		self.save = Mock()
		self.insert = Mock()
		self.db_set = Mock(side_effect=lambda field, value, **kwargs: self.set(field, value))

	def set(self, field, value):
		self[field] = value

	def append(self, table, values):
		row = MemoryDocument(**values)
		self.setdefault(table, []).append(row)
		return row


def profile(**values):
	return MemoryDocument(**{
		"doctype": "NGS Customer", "name": "NGS-ALICE", "docstatus": 0,
		"first_name": "Alice", "last_name": "Smith", "full_name": "Alice Smith",
		"email": "alice@example.test", "phone": "+1 202 555 0101", "organization": "Shared Lab",
		"erpnext_customer": "ERP-LAB", "erpnext_contact": "ERP-ALICE",
		"frappe_crm_organization": "CRM-LAB", "frappe_crm_contact": "ERP-ALICE",
		**values,
	})


class TestCustomerProfileHooks(TestCase):
	def setUp(self):
		self.erp = self.enterContext(patch.object(ngs_customer, "sync_ngs_customer_to_crm"))
		self.crm = self.enterContext(patch.object(ngs_customer, "sync_ngs_customer_to_frappe_crm"))
		self.create_user = self.enterContext(patch.object(ngs_customer, "create_ngs_customer_user"))
		self.sendmail = self.enterContext(patch.object(frappe, "sendmail"))

	def run_hook(self, hook, customer, previous):
		customer.get_doc_before_save = Mock(return_value=previous)
		customer.sync_profile = partial(ngs_customer.NGSCustomer.sync_profile, customer)
		getattr(ngs_customer.NGSCustomer, hook)(customer)

	def test_draft_and_submitted_profile_changes_sync_without_users_or_email(self):
		for status, hook in ((0, "on_update"), (1, "on_update_after_submit")):
			for field in ngs_customer.CUSTOMER_PROFILE_FIELDS:
				with self.subTest(status=status, field=field):
					self.erp.reset_mock()
					self.crm.reset_mock()
					customer, previous = profile(docstatus=status), profile(docstatus=status)
					customer[field] = "Changed"
					self.run_hook(hook, customer, previous)
					self.erp.assert_called_once_with(customer)
					self.crm.assert_called_once_with(customer)
					customer.save.assert_not_called()
					customer.db_set.assert_not_called()
		self.create_user.assert_not_called()
		self.sendmail.assert_not_called()

	def test_unchanged_profiles_and_link_only_changes_do_not_sync(self):
		for hook in ("on_update", "on_update_after_submit"):
			for field in (None, "erpnext_customer", "erpnext_contact", "frappe_crm_organization", "frappe_crm_contact"):
				with self.subTest(hook=hook, field=field):
					customer, previous = profile(), profile()
					if field:
						customer[field] = "NEW-LINK"
					self.run_hook(hook, customer, previous)
		self.erp.assert_not_called()
		self.crm.assert_not_called()

	def test_insert_syncs_but_cancelled_profile_does_not(self):
		customer = profile()
		self.run_hook("on_update", customer, None)
		self.erp.assert_called_once_with(customer)
		self.crm.assert_called_once_with(customer)
		self.erp.reset_mock()
		self.crm.reset_mock()
		self.run_hook("on_update", profile(docstatus=2, phone="Changed"), profile())
		self.erp.assert_not_called()
		self.crm.assert_not_called()

	def test_submit_keeps_user_creation_and_welcome_email(self):
		customer = profile(docstatus=1)
		self.create_user.return_value = (customer.email, "test-only-password")
		with (
			patch.object(frappe.utils, "get_url", return_value="https://example.test/login"),
			patch.object(frappe, "msgprint"),
			patch.object(frappe, "logger"),
		):
			ngs_customer.NGSCustomer.on_submit(customer)
		self.erp.assert_called_once_with(customer)
		self.crm.assert_called_once_with(customer)
		self.create_user.assert_called_once_with(customer.email, customer.full_name)
		self.sendmail.assert_called_once()
		self.assertEqual(self.sendmail.call_args.kwargs["recipients"], [customer.email])

	def test_sync_error_propagates_without_user_creation_or_second_mirror(self):
		self.erp.side_effect = RuntimeError("Mirror unavailable")
		with self.assertRaisesRegex(RuntimeError, "Mirror unavailable"):
			self.run_hook("on_update", profile(phone="Changed"), profile())
		self.crm.assert_not_called()
		self.create_user.assert_not_called()
		self.sendmail.assert_not_called()


class TestCustomerMirrorSync(TestCase):
	def setUp(self):
		self.customer = profile()
		self.company = MemoryDocument(
			doctype="Customer", name="ERP-LAB", customer_name="Shared Lab", customer_type="Company"
		)
		self.contact = MemoryDocument(
			doctype="Contact", name="ERP-ALICE", first_name="Alice", last_name="Smith", company_name="Shared Lab",
			email_ids=[MemoryDocument(email_id=self.customer.email, is_primary=1)],
			phone_nos=[MemoryDocument(phone=self.customer.phone, is_primary_phone=1)],
			links=[MemoryDocument(link_doctype="Customer", link_name=self.company.name)],
		)
		self.organization = MemoryDocument(doctype="CRM Organization", name="CRM-LAB", organization_name="Shared Lab")
		self.docs = {(doc.doctype, doc.name): doc for doc in (self.company, self.contact, self.organization)}
		self.shared = False
		self.db = Mock()
		self.db.get_value.side_effect = self.get_value
		self.db.exists.side_effect = self.exists
		self.db.get_single_value.return_value = "USD"
		self.db.commit.side_effect = AssertionError("No test may commit")
		self.enterContext(patch.object(frappe, "db", self.db))
		self.get_doc = self.enterContext(patch.object(frappe, "get_doc", side_effect=self.load_document))
		self.enterContext(patch.object(frappe, "get_all", side_effect=AssertionError("Unexpected database read")))
		self.apps = self.enterContext(patch.object(frappe, "get_installed_apps", return_value=["erpnext", "crm"]))
		self.enterContext(patch.object(frappe, "sendmail", side_effect=AssertionError("No email")))
		self.enterContext(patch.object(frappe, "new_doc", side_effect=AssertionError("No lead or deal creation")))

	def get_value(self, doctype, filters, field):
		if isinstance(filters, str):
			doc = self.docs.get((doctype, filters))
			return doc.get(field) if doc else None
		if doctype == "Contact Email":
			return self.contact.name if any(row.email_id == filters["email_id"] for row in self.contact.email_ids) else None
		for (doc_type, name), doc in self.docs.items():
			if doc_type == doctype and all(doc.get(key) == value for key, value in filters.items()):
				return name if field == "name" else doc.get(field)
		return None

	def exists(self, doctype, filters):
		if doctype in {"DocType", "DocField"}:
			return True
		if doctype == "NGS Customer":
			return self.shared
		if doctype == "Dynamic Link":
			return any(row.link_name == filters["link_name"] for row in self.contact.links)
		return (doctype, filters) in self.docs

	def load_document(self, doctype, name=None):
		if isinstance(doctype, dict):
			doc = MemoryDocument(**doctype)
			doc.name = doc.get("organization_name") or doc.get("customer_name") or "NEW-CONTACT"
			self.docs[(doc.doctype, doc.name)] = doc
			return doc
		return self.docs[(doctype, name)]

	def synchronize(self):
		crm_sync.sync_ngs_customer_to_crm(self.customer)
		return frappe_crm_sync.sync_ngs_customer_to_frappe_crm(self.customer)

	def test_linked_profile_updates_reuse_contact_and_are_idempotent(self):
		self.customer.update(first_name="Alicia", last_name="Jones", email="new@example.test", phone="+1 202 555 0199")
		result = self.synchronize()
		self.assertEqual(result, {"organization": "CRM-LAB", "contact": "ERP-ALICE"})
		self.assertEqual((self.contact.first_name, self.contact.last_name), ("Alicia", "Jones"))
		self.assertEqual([r.email_id for r in self.contact.email_ids if r.is_primary], [self.customer.email])
		self.assertEqual([r.phone for r in self.contact.phone_nos if r.is_primary_phone], [self.customer.phone])
		self.assertEqual(len(self.contact.email_ids), 2)
		self.assertEqual(len(self.contact.phone_nos), 2)
		self.contact.save.assert_called_once_with(ignore_permissions=True)
		self.synchronize()
		self.contact.save.assert_called_once()
		self.customer.db_set.assert_not_called()
		self.customer.save.assert_not_called()
		self.assertEqual(len(self.docs), 3)
		self.db.commit.assert_not_called()

	def test_existing_secondary_values_become_primary_without_duplicate_rows(self):
		self.contact.append("email_ids", {"email_id": "new@example.test", "is_primary": 0})
		self.contact.append("phone_nos", {"phone": "+1 202 555 0199", "is_primary_phone": 0})
		self.customer.update(email="new@example.test", phone="+1 202 555 0199")
		self.synchronize()
		self.assertEqual(len(self.contact.email_ids), 2)
		self.assertEqual(len(self.contact.phone_nos), 2)
		self.assertEqual([r.email_id for r in self.contact.email_ids if r.is_primary], [self.customer.email])
		self.assertEqual([r.phone for r in self.contact.phone_nos if r.is_primary_phone], [self.customer.phone])

	def test_cleared_optional_contact_values_are_not_left_primary(self):
		self.customer.update(last_name="", phone="")
		self.synchronize()
		self.assertEqual(self.contact.last_name, "")
		self.assertFalse(any(r.is_primary_phone for r in self.contact.phone_nos))
		self.contact.save.assert_called_once()
		from frappe.contacts.doctype.contact.contact import Contact

		Contact.set_primary(self.contact, "phone")
		self.assertEqual(self.contact.phone, "")

	def test_primary_values_satisfy_real_contact_validation(self):
		from frappe.contacts.doctype.contact.contact import Contact

		self.customer.update(email="new@example.test", phone="+1 202 555 0199")
		self.synchronize()
		Contact.set_primary_email(self.contact)
		Contact.set_primary(self.contact, "phone")
		self.assertEqual(self.contact.email_id, self.customer.email)
		self.assertEqual(self.contact.phone, self.customer.phone)

	def test_mirror_save_failure_propagates_without_business_commit(self):
		self.customer.phone = "+1 202 555 0199"
		self.contact.save.side_effect = RuntimeError("Contact save failed")
		with self.assertRaisesRegex(RuntimeError, "Contact save failed"):
			self.synchronize()
		self.db.commit.assert_not_called()
		self.organization.save.assert_not_called()
		self.customer.db_set.assert_not_called()

	def test_private_fields_never_overwrite_shared_organization_or_company(self):
		self.shared = True
		self.customer.update(
			phone="+1 202 555 0199", address="Personal address", adress="Legacy address",
			invoice_email_address="billing@example.test", billing_category="Private", billing_address="Private billing",
			lab_group_name="Private group", note="Private note",
		)
		self.synchronize()
		self.organization.save.assert_not_called()
		self.company.save.assert_not_called()
		for field in ("phone", "email", "address", "adress", "billing_category", "billing_address", "invoice_email_address", "lab_group_name", "note"):
			self.assertNotIn(field, self.organization)
			self.assertNotIn(field, self.company)

	def test_exclusive_organization_name_edits_update_existing_links(self):
		self.customer.organization = "Renamed Lab"
		self.synchronize()
		self.assertEqual(self.company.customer_name, "Renamed Lab")
		self.assertEqual(self.organization.organization_name, "Renamed Lab")
		self.assertEqual(len(self.docs), 3)
		self.company.save.assert_called_once()
		self.organization.save.assert_called_once()
		self.synchronize()
		self.company.save.assert_called_once()
		self.organization.save.assert_called_once()

	def test_canonical_organization_change_wins_over_stale_legacy_institution(self):
		self.customer.update(organization="Canonical Lab", company_institution="Stale Institution")
		self.synchronize()
		self.assertEqual(self.company.customer_name, "Canonical Lab")
		self.assertEqual(self.organization.organization_name, "Canonical Lab")
		self.assertEqual(self.contact.company_name, "Canonical Lab")
		self.assertEqual(self.customer.erpnext_customer, "ERP-LAB")
		self.assertEqual(self.customer.frappe_crm_organization, "CRM-LAB")
		self.assertEqual(len(self.docs), 3)

	def test_shared_affiliation_change_reuses_matching_targets(self):
		self.shared = True
		new_company = MemoryDocument(doctype="Customer", name="ERP-NEW", customer_name="New Lab", customer_type="Company")
		new_org = MemoryDocument(doctype="CRM Organization", name="CRM-NEW", organization_name="New Lab")
		self.docs.update({(doc.doctype, doc.name): doc for doc in (new_company, new_org)})
		self.customer.organization = "New Lab"
		self.synchronize()
		self.assertEqual(self.customer.erpnext_customer, new_company.name)
		self.assertEqual(self.customer.frappe_crm_organization, new_org.name)
		self.assertEqual(self.customer.erpnext_contact, self.contact.name)
		self.assertEqual(self.organization.organization_name, "Shared Lab")
		self.assertEqual(self.company.customer_name, "Shared Lab")
		self.company.save.assert_not_called()
		self.organization.save.assert_not_called()
		self.assertEqual(len(self.docs), 5)

	def test_shared_affiliation_change_creates_only_missing_targets(self):
		self.shared = True
		self.customer.organization = "New Lab"
		self.synchronize()
		self.assertEqual(self.company.customer_name, "Shared Lab")
		self.assertEqual(self.organization.organization_name, "Shared Lab")
		self.assertEqual(len(self.docs), 5)
		self.synchronize()
		self.assertEqual(len(self.docs), 5)
		self.contact.insert.assert_not_called()
		self.assertEqual(self.customer.frappe_crm_contact, self.contact.name)

	def test_missing_links_reuse_existing_records_without_recursive_save(self):
		for field in ("erpnext_customer", "erpnext_contact", "frappe_crm_organization", "frappe_crm_contact"):
			self.customer[field] = None
		self.synchronize()
		self.assertEqual(self.customer.erpnext_customer, self.company.name)
		self.assertEqual(self.customer.erpnext_contact, self.contact.name)
		self.assertEqual(self.customer.frappe_crm_organization, self.organization.name)
		self.assertEqual(self.customer.frappe_crm_contact, self.contact.name)
		self.assertEqual(len(self.docs), 3)
		self.customer.save.assert_not_called()
		self.assertEqual(self.customer.db_set.call_count, 4)
		for call in self.customer.db_set.call_args_list:
			self.assertEqual(call.kwargs, {"update_modified": False})

	def test_optional_crm_absence_exits_without_reads_or_writes(self):
		self.apps.return_value = ["erpnext"]
		self.assertIsNone(frappe_crm_sync.sync_ngs_customer_to_frappe_crm(self.customer))
		self.get_doc.assert_not_called()
		self.customer.db_set.assert_not_called()
		crm_sync.sync_ngs_customer_to_crm(self.customer)
		self.assertEqual(len(self.docs), 3)

	def test_missing_crm_doctypes_exit_cleanly(self):
		self.db.exists.side_effect = lambda doctype, filters: doctype != "DocType"
		self.assertIsNone(frappe_crm_sync.sync_ngs_customer_to_frappe_crm(self.customer))
		self.get_doc.assert_not_called()
		self.customer.db_set.assert_not_called()

	def test_missing_erpnext_exits_cleanly(self):
		self.apps.return_value = []
		self.assertIsNone(crm_sync.sync_ngs_customer_to_crm(self.customer))
		self.get_doc.assert_not_called()

	def test_contact_creation_includes_phone_and_repeated_sync_reuses_it(self):
		self.customer.erpnext_contact = None
		self.customer.email = "new-contact@example.test"
		self.synchronize()
		contact = self.docs[("Contact", self.customer.erpnext_contact)]
		self.assertEqual(contact.phone_nos[0].phone, self.customer.phone)
		contact.insert.assert_called_once_with(ignore_permissions=True)
		self.synchronize()
		self.assertEqual(len(self.docs), 4)
		contact.insert.assert_called_once()
