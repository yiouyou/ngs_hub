"""Archived invoice file protections run before native filesystem mutations."""

from unittest import TestCase
from unittest.mock import patch

import frappe

from ngs_hub.api import invoice_files


class TestInvoiceFiles(TestCase):
	def setUp(self):
		self.native_calls = []
		calls = self.native_calls

		class NativeFile(frappe._dict):
			def get_doc_before_save(self):
				return self.get("previous")

			def validate(self):
				calls.append("validate")

			def get_content(self, encodings=None):
				return encodings

			def on_trash(self):
				calls.append("trash")

			def before_rename(self, *args, **kwargs):
				calls.append("rename")

		class GuardedFile(invoice_files.InvoiceFileGuardMixin, NativeFile):
			pass

		self.file = GuardedFile(name="FILE-TEST", is_private=1, file_url="/private/files/invoice.pdf",
			attached_to_doctype="NGS Invoice", attached_to_name="INV-TEST")
		self.invoice = frappe._dict(name="INV-TEST", pdf_file="FILE-TEST", pdf_url=self.file.file_url)
		self.exists = self.enterContext(patch.object(frappe.db, "table_exists", return_value=True))
		self.lookup = self.enterContext(patch.object(frappe.db, "get_value", return_value=self.invoice))

	def test_privacy_path_and_canonical_attachment_changes_fail_before_native_validate(self):
		for field, value in (("is_private", 0), ("is_private", "0"), ("file_url", "/files/invoice.pdf"),
			("attached_to_doctype", "NGS Order"), ("attached_to_name", "OTHER")):
			old = self.file[field]
			with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
				self.file[field] = value
				self.file.validate()
			self.file[field] = old
		self.assertEqual(self.native_calls, [])

	def test_delete_and_rename_are_blocked_for_issued_and_void_archives(self):
		for status in ("Issued", "Void"):
			self.invoice.status = status
			with self.subTest(status=status), self.assertRaises(frappe.ValidationError):
				self.file.on_trash()
			with self.assertRaises(frappe.ValidationError):
				self.file.before_rename("FILE-TEST", "OTHER")
		self.assertEqual(self.native_calls, [])

	def test_shared_blob_alias_cannot_move_or_delete_private_archive(self):
		self.file.name = "EMAIL-ATTACHMENT"
		self.file.attached_to_doctype = "Email Queue"
		self.lookup.side_effect = lambda doctype, filters, *args, **kwargs: (
			self.invoice if "pdf_url" in filters else None
		)
		self.file.validate()
		self.file.previous = frappe._dict(file_url=self.file.file_url)
		self.file.file_url = "/files/invoice.pdf"
		self.file.is_private = 0
		with self.assertRaises(frappe.ValidationError):
			self.file.validate()
		with self.assertRaises(frappe.ValidationError):
			self.file.on_trash()
		self.assertEqual(self.native_calls, ["validate"])

	def test_same_hash_at_a_different_url_cannot_change_archive_privacy(self):
		self.file.name = "HASH-ALIAS"
		self.file.file_url = "/private/files/other.pdf"
		self.file.content_hash = "shared-hash"
		self.file.is_private = 0
		self.lookup.side_effect = lambda doctype, filters, *args, **kwargs: (
			self.invoice if filters.get("pdf_file") == ["in", ["FILE-TEST"]] else None
		)
		with patch.object(frappe, "get_all", return_value=["FILE-TEST"]) as shared:
			with self.assertRaises(frappe.ValidationError):
				self.file.validate()
			shared.assert_called_with("File", filters={"content_hash": ["in", ["shared-hash"]]}, pluck="name")
		self.assertEqual(self.native_calls, [])

	def test_thumbnail_reference_cannot_delete_an_archive(self):
		self.file.name = "UNRELATED-FILE"
		self.file.file_url = "/private/files/other.pdf"
		self.file.thumbnail_url = self.invoice.pdf_url
		self.lookup.side_effect = lambda doctype, filters, *args, **kwargs: (
			self.invoice if "pdf_url" in filters and self.invoice.pdf_url in filters["pdf_url"][1] else None
		)
		with self.assertRaises(frappe.ValidationError):
			self.file.on_trash()
		self.assertEqual(self.native_calls, [])

	def test_thumbnail_deletion_matches_native_basename_resolution(self):
		self.file.name = "UNRELATED-FILE"
		self.file.file_url = "/private/files/unrelated.pdf"
		self.lookup.side_effect = lambda doctype, filters, *args, **kwargs: (
			self.invoice if "pdf_url" in filters and self.invoice.pdf_url in filters["pdf_url"][1] else None
		)
		for alias in ("/private/files/./invoice.pdf", "/different/path/invoice.pdf", "invoice.pdf",
			"https://example.invalid/invoice.pdf"):
			self.file.thumbnail_url = alias
			with self.subTest(alias=alias), self.assertRaises(frappe.ValidationError):
				self.file.on_trash()
		self.assertEqual(self.native_calls, [])

	def test_unchanged_archive_and_unrelated_files_delegate_to_native_handlers(self):
		self.file.validate()
		self.lookup.return_value = None
		self.file.validate()
		self.file.on_trash()
		self.file.before_rename("FILE-TEST", "OTHER")
		self.assertEqual(self.native_calls, ["validate", "validate", "trash", "rename"])

	def test_install_without_invoice_table_does_not_break_normal_files(self):
		self.exists.return_value = False
		self.file.validate()
		self.file.on_trash()
		self.lookup.assert_not_called()

	def test_invoice_bytes_never_go_through_native_text_decoding(self):
		self.assertEqual(self.file.get_content(), [])
		self.file.attached_to_doctype = "Email Queue"
		self.assertEqual(self.file.get_content(), [])
		self.lookup.return_value = None
		self.assertIsNone(self.file.get_content())
