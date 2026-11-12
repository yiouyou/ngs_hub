"""Local immutable NGS invoices; no ERP posting or synchronous delivery."""

import base64
import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

import frappe
from frappe import _
from frappe.utils import add_days, now_datetime, today

from ngs_hub.api.customer_profile import normalize_invoice_email_address
from ngs_hub.api.erpnext_sync import get_order_currency
from ngs_hub.api.portal import render_pdf
from ngs_hub.api.pricing_snapshot import get_recorded_breakdown
from ngs_hub.ngs_hub.doctype.ngs_invoice.ngs_invoice import INVOICE_WRITE_TOKEN

TERMS = {"Commercial": 30, "Academic": 45}
BANK_FIELDS = ("bank_name", "account_name", "account_type", "account_number", "routing_number")
COMPANY_FIELDS = (
	"company_name",
	"company_address",
	"company_email",
	"company_phone",
	"company_website",
	"bank_note",
)


def require_invoice_staff():
	user = frappe.session.user
	if (
		not user
		or user == "Guest"
		or not frappe.db.get_value("User", user, "enabled")
		or frappe.db.get_value("User", user, "user_type") != "System User"
		or (
			user != "Administrator"
			and {"NGS Internal Staff", "System Manager"}.isdisjoint(frappe.get_roles(user))
		)
	):
		frappe.throw(_("Only internal NGS staff can access invoices."), frappe.PermissionError)


def _order_access(order_name, permission, *, lock=False):
	order = frappe.get_doc("NGS Order", order_name, for_update=lock)
	order.check_permission(permission)
	customer = frappe.get_doc("NGS Customer", order.customer)
	customer.check_permission("read")
	return order, customer


def _invoice_access(invoice_name, permission, *, lock=False, lock_invoice=True):
	# Lock Order before Invoice consistently across generation, resend and void.
	invoice = frappe.get_doc("NGS Invoice", invoice_name)
	invoice.check_permission(permission)
	order, customer = _order_access(invoice.order, "write" if lock else "read", lock=lock)
	if lock and lock_invoice:
		invoice = frappe.get_doc("NGS Invoice", invoice_name, for_update=True)
		invoice.check_permission(permission)
	if invoice.customer != customer.name:
		frappe.get_doc("NGS Customer", invoice.customer).check_permission("read")
	return invoice, order


def _number(value, label, *, positive=False):
	try:
		if isinstance(value, bool) or value is None:
			raise InvalidOperation
		number = Decimal(str(value))
	except (InvalidOperation, TypeError, ValueError):
		frappe.throw(_("{0} must be a finite nonnegative number.").format(label))
	if not number.is_finite() or number < 0 or (positive and number == 0):
		frappe.throw(
			_("{0} must be a finite {1} number.").format(label, "positive" if positive else "nonnegative")
		)
	return number


def _json(value):
	return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _financial_snapshot(order):
	from ngs_hub.api.order_pricing import order_total

	if order.docstatus == 2 or order.get("status") == "Cancelled":
		frappe.throw(_("Cancelled orders cannot be invoiced."))
	currency = get_order_currency(order)
	if not isinstance(currency, str) or not currency.strip():
		frappe.throw(_("The order currency is required."))
	items = []
	line_total = Decimal(0)
	for row in order.get("items", []):
		if (row.get("service") or row.get("quote_item")) and get_order_currency(
			frappe._dict(items=[row])
		) != currency:
			frappe.throw(_("An invoice cannot contain multiple currencies."))
		quantity = _number(row.get("quantity"), _("Quantity"), positive=True)
		unit_price = _number(row.get("unit_price"), _("Unit price"))
		amount = _number(row.get("amount"), _("Line amount"))
		components = get_recorded_breakdown(row)
		if components and any(part["amount"] is None for part in components):
			frappe.throw(_("Finalize all TBD order pricing before generating an invoice."))
		line_total += amount
		items.append(
			{
				"source_row": row.name,
				"service": row.get("service") or "",
				"description": row.get("description") or row.get("project_type") or row.get("service") or "",
				"quantity": str(quantity.normalize()),
				"unit_price": str(unit_price.normalize()),
				"amount": str(amount.normalize()),
			}
		)
	if not items:
		frappe.throw(_("Add priced order items before generating an invoice."))
	total = _number(order_total(order), _("Order total"), positive=True)
	if total.quantize(Decimal("0.01")) != line_total.quantize(Decimal("0.01")):
		frappe.throw(_("The order total does not match its current line amounts."))
	return {
		"order": order.name,
		"customer": order.customer,
		"currency": currency,
		"total_amount": float(total.quantize(Decimal("0.01"))),
		"po_number": (order.get("po_number") or "").strip(),
		"items_snapshot": _json(items),
	}


def _check_snapshot(invoice, snapshot):
	for field, value in snapshot.items():
		if field == "total_amount":
			matches = _number(invoice.get(field), _("Invoice total")) == Decimal(str(value))
		else:
			matches = (invoice.get(field) or "") == (value or "")
		if not matches:
			frappe.throw(
				_(
					"The active invoice no longer matches the order. Explicitly void it before editing or reissuing."
				)
			)


def assert_order_invoice_unchanged(order):
	"""Order validation hook: never silently change or void an issued invoice."""
	if order.is_new():
		return
	frappe.db.get_value("NGS Order", order.name, "name", for_update=True)
	name = frappe.db.get_value("NGS Invoice", {"active_order": order.name}, "name", for_update=True)
	if name:
		invoice = frappe.get_doc("NGS Invoice", name, for_update=True)
		_check_snapshot(invoice, _financial_snapshot(order))


def _company_snapshot():
	settings = frappe.get_single("NGS Billing Settings")
	values = {field: (settings.get(field) or "").strip() for field in (*COMPANY_FIELDS, *BANK_FIELDS)}
	if any(not values[field] for field in BANK_FIELDS) or not values["company_name"]:
		frappe.throw(
			_(
				"Ask a System Manager to configure company and bank details in NGS Billing Settings before generating invoices."
			)
		)
	return _json(values)


def _stored_pdf(invoice):
	if not invoice.get("pdf_file"):
		frappe.throw(_("The stored invoice PDF is missing. Contact an administrator."))
	file = frappe.get_doc("File", invoice.pdf_file, for_update=True)
	if (
		not file.is_private
		or file.attached_to_doctype != "NGS Invoice"
		or file.attached_to_name != invoice.name
		or file.file_url != invoice.pdf_url
		or not file.file_url.startswith("/private/files/")
	):
		frappe.throw(_("The invoice PDF attachment is invalid."))
	content = file.get_content()
	if not isinstance(content, bytes) or hashlib.sha256(content).hexdigest() != invoice.pdf_sha256:
		frappe.throw(_("The stored invoice PDF failed its integrity check."))
	return content


def _queue_email(invoice, content):
	email = normalize_invoice_email_address(invoice.invoice_email_address)
	if not email:
		frappe.throw(_("Invoice email address is required."))
	frappe.sendmail(
		recipients=[email],
		subject=_("Athenomics Invoice {0}").format(invoice.name),
		message=_("Please find your invoice attached."),
		attachments=[{"fname": f"{invoice.name}.pdf", "fcontent": content}],
		reference_doctype="NGS Invoice",
		reference_name=invoice.name,
		delayed=True,
		now=False,
	)


def _save_invoice_pdf(filename, content, invoice_name):
	# Let File write the bytes once; file_manager.save_file prewrites a second blob.
	return frappe.get_doc(
		{
			"doctype": "File",
			"file_name": filename,
			"content": content,
			"is_private": 1,
			"attached_to_doctype": "NGS Invoice",
			"attached_to_name": invoice_name,
			"attached_to_field": "pdf_url",
		}
	).insert(ignore_permissions=True)


def _register_pdf_rollback(file, invoice_name, directory, preexisting, content):
	digest = hashlib.sha256(content).digest()
	returned_path = Path(file.get_full_path()).resolve() if file is not None else None
	created = []
	for candidate in directory.glob(f"{invoice_name}*.pdf"):
		path = candidate.resolve()
		if path.parent != directory or path in preexisting or candidate.is_symlink() or not path.is_file():
			continue
		if hashlib.sha256(path.read_bytes()).digest() != digest:
			continue
		file_url = file.file_url if path == returned_path else "/private/files/" + path.name
		created.append((path, file_url))
	if not created:
		return

	def cleanup():
		for path, file_url in created:
			if path.is_symlink() or not path.is_file():
				continue
			if frappe.db.exists("File", {"file_url": file_url}):
				continue
			try:
				current = path.read_bytes()
			except FileNotFoundError:
				continue
			if hashlib.sha256(current).digest() == digest:
				path.unlink(missing_ok=True)

	frappe.db.after_rollback.add(cleanup)


def _result(invoice, *, existing):
	fields = (
		"name",
		"status",
		"order",
		"customer",
		"total_amount",
		"currency",
		"issue_date",
		"due_date",
		"pdf_url",
	)
	return {
		**{field: invoice.get(field) for field in fields},
		"download_url": "/api/method/ngs_hub.api.invoicing.download_invoice?"
		+ urlencode({"invoice_name": invoice.name}),
		"existing": existing,
	}


@frappe.whitelist(methods=["POST"])
def generate_invoice(order_name):
	require_invoice_staff()
	order, customer = _order_access(order_name, "write", lock=True)
	snapshot = _financial_snapshot(order)
	existing = frappe.db.get_value("NGS Invoice", {"active_order": order.name}, "name", for_update=True)
	if existing:
		invoice = frappe.get_doc("NGS Invoice", existing, for_update=True)
		invoice.check_permission("read")
		if invoice.status != "Issued":
			frappe.throw(_("The active invoice status is invalid."))
		_check_snapshot(invoice, snapshot)
		_stored_pdf(invoice)
		return _result(invoice, existing=True)
	email = normalize_invoice_email_address(customer.get("invoice_email_address"))
	if not email:
		frappe.throw(_("Invoice email address is required before generating an invoice."))
	category = customer.get("billing_category")
	if category not in TERMS:
		frappe.throw(_("Set the customer's billing category to Commercial or Academic before invoicing."))
	company = _company_snapshot()
	issue_date = today()
	invoice = frappe.get_doc(
		{
			"doctype": "NGS Invoice",
			**snapshot,
			"status": "Issued",
			"active_order": order.name,
			"invoice_customer": customer.get("organization")
			or customer.get("company_institution")
			or customer.get("full_name")
			or customer.name,
			"customer_type": category,
			"billing_address": customer.get("billing_address")
			or customer.get("address")
			or customer.get("adress")
			or "",
			"invoice_email_address": email,
			"issue_date": issue_date,
			"payment_terms_days": TERMS[category],
			"due_date": add_days(issue_date, TERMS[category]),
			"company_snapshot": company,
			"generated_by": frappe.session.user,
			"generated_at": now_datetime(),
		}
	)
	invoice.flags.invoice_write_token = INVOICE_WRITE_TOKEN
	# Invoice, File and Email Queue records share the request transaction. Never commit here.
	invoice.insert(ignore_permissions=True)
	logo = base64.b64encode(Path(frappe.get_app_path("ngs_hub", "public", "logo.png")).read_bytes()).decode(
		"ascii"
	)
	content = render_pdf(
		"templates/includes/ngs_invoice_pdf.html",
		{
			"invoice": invoice,
			"items": json.loads(invoice.items_snapshot),
			"company": json.loads(invoice.company_snapshot),
			"logo": "data:image/png;base64," + logo,
		},
	)
	if not isinstance(content, bytes) or not content.startswith(b"%PDF-"):
		frappe.throw(_("Invoice PDF generation failed."))
	directory = Path(frappe.get_site_path("private", "files")).resolve()
	preexisting = {path.resolve() for path in directory.glob(f"{invoice.name}*.pdf")}
	filename = f"{invoice.name}.pdf"
	while (directory / filename).exists() or (directory / filename).is_symlink():
		filename = f"{invoice.name}-{uuid4().hex}.pdf"
	file = None
	try:
		file = _save_invoice_pdf(filename, content, invoice.name)
		invoice.update(
			{
				"pdf_file": file.name,
				"pdf_url": file.file_url,
				"pdf_sha256": hashlib.sha256(content).hexdigest(),
			}
		)
		invoice.save(ignore_permissions=True)
		_queue_email(invoice, content)
		return _result(invoice, existing=False)
	finally:
		# Register after native attachment callbacks, including failed File insertions.
		_register_pdf_rollback(file, invoice.name, directory, preexisting, content)


@frappe.whitelist(methods=["GET"])
def download_invoice(invoice_name):
	require_invoice_staff()
	invoice, _order = _invoice_access(invoice_name, "read")
	frappe.local.response.update(
		{
			"type": "download",
			"filename": f"{invoice.name}.pdf",
			"filecontent": _stored_pdf(invoice),
			"content_type": "application/pdf",
			"display_content_as": "attachment",
		}
	)


@frappe.whitelist(methods=["POST"])
def resend_invoice(invoice_name):
	require_invoice_staff()
	invoice, _order = _invoice_access(invoice_name, "write", lock=True)
	if invoice.status != "Issued":
		frappe.throw(_("Void invoices cannot be emailed."))
	_queue_email(invoice, _stored_pdf(invoice))
	return _result(invoice, existing=True)


@frappe.whitelist(methods=["POST"])
def void_invoice(invoice_name, reason):
	require_invoice_staff()
	invoice, _order = _invoice_access(invoice_name, "write", lock=True, lock_invoice=False)
	if not isinstance(reason, str) or not reason.strip():
		frappe.throw(_("A reason is required to void an invoice."))
	_stop_queued_emails(invoice, reason.strip())
	invoice = frappe.get_doc("NGS Invoice", invoice_name, for_update=True)
	invoice.check_permission("write")
	if invoice.status == "Void":
		return _result(invoice, existing=True)
	invoice.flags.invoice_write_token = INVOICE_WRITE_TOKEN
	invoice.update(
		{
			"status": "Void",
			"active_order": None,
			"void_reason": reason.strip(),
			"voided_by": frappe.session.user,
			"voided_at": now_datetime(),
		}
	)
	invoice.save(ignore_permissions=True)
	return _result(invoice, existing=True)


def _stop_queued_emails(invoice, reason):
	# Frappe v16 has no per-message suspension. Error is excluded by normal flushing.
	names = frappe.db.get_values(
		"Email Queue",
		filters={"reference_doctype": "NGS Invoice", "reference_name": invoice.name},
		fieldname="name",
		pluck=True,
		order_by="name",
		for_update=True,
	)
	queues = [frappe.get_doc("Email Queue", name, for_update=True) for name in names]
	if any(queue.status == "Sending" for queue in queues):
		frappe.throw(_("Invoice email is currently sending. Wait for delivery to finish before voiding."))
	for queue in queues:
		if queue.status == "Sent":
			continue
		message = (
			f"NGS invoice {invoice.name} voided by {frappe.session.user}; queued delivery cancelled: {reason}"
		)
		frappe.db.set_value(
			"Email Queue",
			queue.name,
			{
				"status": "Error",
				"error": "\n".join(part for part in (queue.get("error"), message) if part),
			},
		)


class InvoiceEmailQueueGuardMixin:
	"""Keep framework retries and forced sends from delivering void invoice PDFs."""

	def send(self, *args, **kwargs):
		if self.reference_doctype == "NGS Invoice":
			if not self.reference_name:
				frappe.throw(_("An NGS invoice email requires an invoice reference."))
			if not self.name:
				frappe.throw(_("An NGS invoice email requires a saved queue record."))
			# Manual sends, scheduler flushing and void all lock Queue before Invoice.
			queue = frappe.get_doc("Email Queue", self.name, for_update=True)
			if (
				queue.reference_doctype != self.reference_doctype
				or queue.reference_name != self.reference_name
				or queue.status != self.status
				or queue.modified != self.modified
				or queue.status in ("Sending", "Sent")
			):
				frappe.throw(_("This invoice email queue has changed or is already sending or sent. Reload it."))
			# A locking read sees the latest committed state, not a worker's older snapshot.
			# Frappe marks the queue Sending and commits before opening an SMTP session.
			invoice = frappe.db.get_value(
				"NGS Invoice",
				self.reference_name,
				["status", "active_order", "order"],
				as_dict=True,
				for_update=True,
			)
			if (
				not invoice
				or invoice.status != "Issued"
				or not invoice.order
				or invoice.active_order != invoice.order
			):
				frappe.throw(
					_("This NGS invoice is void, missing, or inactive. Its email cannot be sent or retried.")
				)
		return super().send(*args, **kwargs)
