"""Protect archived invoice blobs before File can move or remove them."""

import posixpath

import frappe
from frappe import _


def _invoice_for_file(file):
	if not frappe.db.table_exists("NGS Invoice"):
		return None
	fields = ["name", "pdf_file", "pdf_url"]
	invoice = frappe.db.get_value("NGS Invoice", {"pdf_file": file.name}, fields, as_dict=True) if file.name else None
	if invoice:
		return invoice
	previous = file.get_doc_before_save()
	urls = {
		doc.get(field) for doc in (file, previous) if doc
		for field in ("file_url", "thumbnail_url") if doc.get(field)
	}
	if urls:
		invoice = frappe.db.get_value("NGS Invoice", {"pdf_url": ["in", sorted(urls)]}, fields, as_dict=True)
		if invoice:
			return invoice
	# Native privacy changes update all Files sharing a hash, not only this URL.
	hashes = {doc.get("content_hash") for doc in (file, previous) if doc and doc.get("content_hash")}
	if hashes:
		files = frappe.get_all("File", filters={"content_hash": ["in", sorted(hashes)]}, pluck="name")
		if files:
			return frappe.db.get_value("NGS Invoice", {"pdf_file": ["in", files]}, fields, as_dict=True)
	return None


def _invoice_at_deletion_target(file):
	if not frappe.db.table_exists("NGS Invoice"):
		return None
	urls = set()
	for field in ("file_url", "thumbnail_url"):
		value = file.get(field)
		if value:
			# Match Frappe delete_file: anything other than exactly files/<name>
			# targets private/files/<basename>, including thumbnail aliases.
			parent, name = posixpath.split(value.strip("/"))
			urls.add(("/files/" if parent == "files" else "/private/files/") + name)
	if urls:
		return frappe.db.get_value("NGS Invoice", {"pdf_url": ["in", sorted(urls)]}, "name")
	return None


class InvoiceFileGuardMixin:
	def get_content(self, encodings=None):
		# File's text-encoding guesses can decode and rewrite an otherwise valid PDF.
		if self.attached_to_doctype == "NGS Invoice" or _invoice_for_file(self):
			encodings = []
		return super().get_content(encodings=encodings)

	def validate(self):
		invoice = _invoice_for_file(self)
		if invoice and (
			self.is_private not in (1, "1", True)
			or self.file_url != invoice.pdf_url
			or (
				self.name == invoice.pdf_file
				and (self.attached_to_doctype != "NGS Invoice" or self.attached_to_name != invoice.name)
			)
		):
			frappe.throw(_("Archived invoice PDFs must remain private and attached to their original invoice."))
		return super().validate()

	def on_trash(self):
		if _invoice_for_file(self) or _invoice_at_deletion_target(self):
			frappe.throw(_("Archived invoice PDFs must be retained, including after the invoice is voided."))
		return super().on_trash()

	def before_rename(self, *args, **kwargs):
		if _invoice_for_file(self):
			frappe.throw(_("Archived invoice PDF records cannot be renamed."))
		parent = getattr(super(), "before_rename", None)
		if parent:
			return parent(*args, **kwargs)
