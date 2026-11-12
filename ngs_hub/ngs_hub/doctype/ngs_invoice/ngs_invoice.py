"""Issued snapshots may only acquire their PDF or transition to Void."""

import frappe
from frappe import _
from frappe.model.document import Document

INVOICE_WRITE_TOKEN = object()
SNAPSHOT_FIELDS = (
	"order",
	"customer",
	"invoice_customer",
	"customer_type",
	"billing_address",
	"invoice_email_address",
	"issue_date",
	"due_date",
	"payment_terms_days",
	"currency",
	"total_amount",
	"po_number",
	"items_snapshot",
	"company_snapshot",
	"generated_by",
	"generated_at",
)
PDF_FIELDS = ("pdf_file", "pdf_url", "pdf_sha256")
LIFECYCLE_FIELDS = ("status", "active_order", "void_reason", "voided_by", "voided_at")


class NGSInvoice(Document):
	def validate(self):
		trusted = self.flags.get("invoice_write_token") is INVOICE_WRITE_TOKEN
		if self.is_new():
			if not trusted or self.status != "Issued" or self.active_order != self.order:
				frappe.throw(_("Generate invoices from the order invoice action."), frappe.PermissionError)
			return
		previous = self.get_doc_before_save()
		if previous is None:
			previous = frappe.get_doc("NGS Invoice", self.name)
		for field in SNAPSHOT_FIELDS:
			if str(self.get(field) or "") != str(previous.get(field) or ""):
				frappe.throw(_("Issued invoice snapshots are immutable. Void and reissue instead."))
		pdf_changed = any(self.get(field) != previous.get(field) for field in PDF_FIELDS)
		if pdf_changed and (not trusted or previous.pdf_file or previous.status != "Issued"):
			frappe.throw(_("The stored invoice PDF is immutable."))
		lifecycle_changed = any(self.get(field) != previous.get(field) for field in LIFECYCLE_FIELDS)
		if lifecycle_changed and not (
			trusted
			and previous.status == "Issued"
			and self.status == "Void"
			and not self.active_order
			and self.void_reason
			and self.void_reason.strip()
			and self.voided_by
			and self.voided_at
		):
			frappe.throw(_("Use the explicit invoice void action with a reason."))

	def on_trash(self):
		frappe.throw(_("Invoices must be retained. Void the invoice instead."))

	def before_rename(self, olddn, newdn, merge=False):
		frappe.throw(_("Invoice numbers cannot be changed."))
