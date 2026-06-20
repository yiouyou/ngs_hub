import json

import frappe
import pdfkit
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import add_days, escape_html, flt, formatdate, get_url, getdate, validate_email_address
from frappe.utils.password import update_password

from ngs_hub.api.crm_sync import sync_ngs_customer_to_crm
from ngs_hub.api.frappe_crm_sync import sync_ngs_customer_to_frappe_crm
from ngs_hub.ngs_hub.doctype.ngs_quote.ngs_quote import (
	DNA_EXTRACTION_BLOOD_SALIVA_SWAB_SAMPLE_TYPES,
	DNA_EXTRACTION_STANDARD_SAMPLE_TYPES,
	TENX_NUCLEI_EXTRACTION_SAMPLE_TYPES,
	TENX_TISSUE_DISSOCIATION_SAMPLE_TYPES,
	WGS_HIGH_VOLUME_MIN_QUANTITY,
	WGS_HIGH_VOLUME_RATE_PER_X,
	WGS_LIBRARY_PREP_PRICE,
	WGS_LOW_VOLUME_RATE_PER_X,
)


def _loads(value):
	if not value:
		return {}
	if isinstance(value, str):
		return json.loads(value)
	return value


def get_current_customer():
	if frappe.session.user == "Guest":
		frappe.throw(_("Login is required"), frappe.PermissionError)
	email = frappe.db.get_value("User", frappe.session.user, "email") or frappe.session.user
	return frappe.db.get_value("NGS Customer", {"email": email}, "name")


def get_ngs_home_page(user):
	if user == "Guest":
		return None
	if frappe.db.get_value("User", user, "user_type") != "Website User":
		return None
	email = frappe.db.get_value("User", user, "email") or user
	if frappe.db.exists("NGS Customer", {"email": email}):
		return "ngs_account"
	if "NGS External Customer" in frappe.get_roles(user):
		return "ngs_register"
	return None


@frappe.whitelist(allow_guest=True)
def get_login_target(email):
	email = (email or "").strip()
	if not email:
		return {}
	user = frappe.db.get_value("User", {"email": email}, "name") or frappe.db.get_value("User", email, "name")
	if not user:
		return {}
	if frappe.db.get_value("User", user, "user_type") == "System User":
		return {"target": "/app"}
	target = get_ngs_home_page(user)
	return {"target": f"/{target}"} if target else {}


ORDERABLE_QUOTE_STATUSES = {"Draft", "Confirmed"}
CANCELLABLE_QUOTE_STATUSES = {"Draft", "Pending"}
ORDER_EMAIL_SENDER = "order@athenomics.com"


def validate_customer_quote(quote, customer, require_orderable=False):
	if not quote:
		return None
	quote_doc = frappe.db.get_value("NGS Quote", quote, ["customer", "status", "order"], as_dict=True)
	if not quote_doc:
		frappe.throw(_("Quote {0} was not found.").format(quote))
	if quote_doc.customer != customer:
		frappe.throw(_("Quote {0} is not linked to your customer account.").format(quote), frappe.PermissionError)
	if require_orderable and quote_doc.status not in ORDERABLE_QUOTE_STATUSES:
		frappe.throw(_("Quote {0} is {1} and cannot be used to place an order.").format(quote, quote_doc.status))
	if require_orderable and quote_doc.order:
		frappe.throw(_("Quote {0} has already been converted to order {1}.").format(quote, quote_doc.order))
	if require_orderable:
		full_quote = frappe.get_doc("NGS Quote", quote)
		if quote_has_unpriced_fees(full_quote):
			frappe.throw(_("Quote {0} includes TBD pricing and must be finalized before placing an order.").format(quote))
	return quote


def validate_customer_order(order, customer):
	if not order:
		frappe.throw(_("Order is required."))
	order_doc = frappe.db.get_value("NGS Order", order, ["customer"], as_dict=True)
	if not order_doc:
		frappe.throw(_("Order {0} was not found.").format(order))
	if order_doc.customer != customer:
		frappe.throw(_("Order {0} is not linked to your customer account.").format(order), frappe.PermissionError)
	return order


def validate_order_documents(po_number=None, po_file=None, sample_registration_form=None, require_po=False):
	if require_po and not (po_number or po_file):
		frappe.throw(_("Provide either a PO number or a PO file before placing an order."))
	if not sample_registration_form:
		frappe.throw(_("Upload the sample registration form before placing an order."))


def truthy(value):
	return value in (True, 1, "1", "true", "True", "on", "yes", "Yes")


def money(value):
	return f"{flt(value):,.2f}"


def requires_manual_pricing(item):
	return item.get("project_type") == "Custom Project" and flt(item.get("unit_price")) <= 0 and flt(item.get("amount")) <= 0


def item_has_unpriced_fee(item):
	rule_notes = item.get("rule_notes") or ""
	return (
		requires_manual_pricing(item)
		or "TBD" in rule_notes
		or "actual-expense pricing" in rule_notes
		or "quoted based on actual expenses" in rule_notes
		or "not included in this subtotal" in rule_notes
		or "not included in this total" in rule_notes
	)


def quote_has_unpriced_fees(quote):
	return bool(quote.get("missing_info")) or any(item_has_unpriced_fee(item) for item in quote.items)


def customer_display_name(customer):
	return (
		customer.get("full_name")
		or " ".join(part for part in [customer.get("first_name"), customer.get("last_name")] if part)
		or customer.get("email")
		or customer.name
	)


def quote_total_label(quote):
	if quote_has_unpriced_fees(quote):
		return f"${money(quote.total_amount)} + TBD" if flt(quote.total_amount) else "TBD"
	return f"${money(quote.total_amount)}"


def email_summary_table(rows):
	if not rows:
		return ""
	body = "\n".join(
		f"""<tr>
          <td style="padding: 10px 0; border-bottom: 1px solid #e5e7eb; color: #64717c; width: 38%;">{escape_html(label)}</td>
          <td style="padding: 10px 0; border-bottom: 1px solid #e5e7eb; color: #0f1720; font-weight: 600;">{escape_html(value)}</td>
        </tr>"""
		for label, value in rows
	)
	return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse: collapse; margin: 18px 0 22px;">
        {body}
      </table>"""


def portal_email_html(greeting_name, title, intro, summary_rows=None, cta_url=None, cta_label=None, note=None):
	summary = email_summary_table(summary_rows)
	cta = ""
	if cta_url and cta_label:
		cta = f"""<p style="margin: 24px 0;">
        <a href="{escape_html(cta_url)}" style="display: inline-block; background: #153f37; color: #ffffff; text-decoration: none; font-weight: 700; padding: 12px 18px; border-radius: 6px;">
          {escape_html(cta_label)}
        </a>
      </p>"""
	note_html = f"""<p style="margin: 0 0 22px; color: #64717c;">{escape_html(note)}</p>""" if note else ""
	return f"""<!DOCTYPE html>
<html>
  <body style="margin: 0; padding: 0; background: #f6f8f7; font-family: Arial, sans-serif; color: #0f1720; line-height: 1.5;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background: #f6f8f7; padding: 24px 0;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width: 640px; background: #ffffff; border: 1px solid #d8e2df; border-radius: 8px; overflow: hidden;">
            <tr>
              <td style="padding: 22px 28px 14px; border-left: 5px solid #2c776c;">
                <div style="font-size: 12px; line-height: 1; letter-spacing: 0.12em; text-transform: uppercase; color: #2c776c; font-weight: 800;">Athenomics</div>
                <h1 style="margin: 14px 0 0; font-size: 24px; line-height: 1.25; color: #0f1720;">{escape_html(title)}</h1>
              </td>
            </tr>
            <tr>
              <td style="padding: 8px 28px 28px;">
                <p style="margin: 0 0 16px;">Hello {escape_html(greeting_name)},</p>
                <p style="margin: 0 0 14px;">{escape_html(intro)}</p>
                {summary}
                {cta}
                {note_html}
                <p style="margin: 28px 0 0; color: #0f1720;">
                  Best regards,<br>
                  <strong>Athenomics Order Team</strong>
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""


def send_order_account_email(customer_name, subject, message, reference_doctype=None, reference_name=None, attachments=None):
	customer = frappe.get_cached_doc("NGS Customer", customer_name)
	if not customer.email:
		frappe.logger("ngs_hub.email").warning(
			"Skipped NGS portal email for %s because the customer has no email", customer_name
		)
		return
	try:
		frappe.sendmail(
			recipients=[customer.email],
			sender=ORDER_EMAIL_SENDER,
			subject=subject,
			message=message,
			delayed=True,
			retry=3,
			reference_doctype=reference_doctype,
			reference_name=reference_name,
			attachments=attachments,
			expose_recipients="header",
			add_unsubscribe_link=False,
			with_container=False,
		)
		frappe.enqueue(
			"frappe.email.queue.flush",
			queue="short",
			enqueue_after_commit=True,
		)
	except Exception:
		frappe.logger("ngs_hub.email").error(
			"Failed to send %s notification %s to %s",
			reference_doctype or "NGS portal",
			reference_name or "",
			customer.email,
			exc_info=True,
		)


def notify_quote_created(quote):
	customer = frappe.get_cached_doc("NGS Customer", quote.customer)
	attachment = safe_pdf_attachment(quote_pdf_attachment, quote)
	message = portal_email_html(
		customer_display_name(customer),
		"Quote request received",
		f"We received your quote request {quote.name}. A PDF copy is attached for your records.",
		[
			("Quote", quote.name),
			("Status", quote.status),
			("Total", quote_total_label(quote)),
		],
		cta_url=get_url(f"/ngs_account?tab=quote&quote={quote.name}"),
		cta_label="Review quote",
		note="You can place an order from the NGS Portal when the quote is ready.",
	)
	send_order_account_email(
		quote.customer,
		f"Athenomics quote request received: {quote.name}",
		message,
		reference_doctype=quote.doctype,
		reference_name=quote.name,
		attachments=[attachment] if attachment else None,
	)


def notify_order_created(order):
	customer = frappe.get_cached_doc("NGS Customer", order.customer)
	attachment = safe_pdf_attachment(order_pdf_attachment, order)
	summary_rows = [("Order", order.name), ("Status", order.status)]
	if order.get("quote"):
		summary_rows.append(("Quote", order.quote))
	message = portal_email_html(
		customer_display_name(customer),
		"Order submitted",
		f"We received your order {order.name}. A PDF copy is attached for your records.",
		summary_rows,
		cta_url=get_url(f"/ngs_account?tab=order&order={order.name}"),
		cta_label="Review order",
		note="We will contact you if additional information is needed.",
	)
	send_order_account_email(
		order.customer,
		f"Athenomics order submitted: {order.name}",
		message,
		reference_doctype=order.doctype,
		reference_name=order.name,
		attachments=[attachment] if attachment else None,
	)


def price_breakdown(item):
	if requires_manual_pricing(item):
		return [{"label": "Manual quote", "unit_price": None, "quantity": item.get("quantity") or 1, "amount": None, "display": "TBD"}]
	quantity = flt(item.get("quantity") or 1)
	rows = []

	def add(label, unit_price, row_quantity=None):
		unit_price = flt(unit_price)
		row_quantity = quantity if row_quantity is None else flt(row_quantity)
		if unit_price == 0 and row_quantity == 0:
			return
		rows.append({
			"label": label,
			"unit_price": unit_price,
			"quantity": row_quantity,
			"amount": unit_price * row_quantity,
		})

	project_type = item.get("project_type") or ""
	sample_type = item.get("sample_type") or ""
	service_name = get_service_label(item)
	standard_reads = get_standard_reads(item)

	if project_type == "Custom Project":
		add(item.get("description") or "Custom project", item.get("unit_price") or 0)
		return rows

	if project_type == "WGS":
		depth = get_wgs_depth_value(item)
		rate = WGS_HIGH_VOLUME_RATE_PER_X if quantity >= WGS_HIGH_VOLUME_MIN_QUANTITY else WGS_LOW_VOLUME_RATE_PER_X
		add("WGS library prep", WGS_LIBRARY_PREP_PRICE)
		add(f"WGS sequencing ({depth:g}x at ${rate:g}/x)", depth * rate)
	elif project_type == "Sequencing Only - 1M Reads":
		reads = flt(item.get("reads_per_sample_million") or standard_reads or 1)
		rate = get_service_price("SEQ_ONLY_1M_READS")
		add(f"Sequencing ({reads:g}M reads at ${rate:g}/M)", reads * rate)
	else:
		add(service_name, get_base_unit_price(item, service_name))

	if standard_reads and project_type != "Sequencing Only - 1M Reads":
		extra_reads = flt(item.get("reads_per_sample_million")) - standard_reads
		if extra_reads > 0:
			add(f"Extra sequencing ({extra_reads:g}M reads at ${get_service_price('EXTRA_SEQUENCING_1M'):g}/M)", extra_reads * get_service_price("EXTRA_SEQUENCING_1M"))

	if project_type == "Bulk RNAseq" and item.get("data_analysis") == "De novo Assembly":
		add("Transcriptome de novo assembly", get_service_price("RNA_DENOVO_ASSEMBLY"))
	if project_type == "Shotgun Meta" and item.get("data_analysis") in {"Mapping", "De novo Assembly"}:
		add("Shotgun metagenomics analysis", get_service_price("SHOTGUN_ANALYSIS"))
	if project_type == "WGS":
		if sample_type in DNA_EXTRACTION_STANDARD_SAMPLE_TYPES:
			add("DNA extraction - cell/tissue/plasma/serum", get_service_price("DNA_EXTRACTION_STANDARD"))
		elif sample_type in DNA_EXTRACTION_BLOOD_SALIVA_SWAB_SAMPLE_TYPES:
			add("DNA extraction - blood/saliva/swab", get_service_price("DNA_EXTRACTION_BLOOD_SALIVA_SWAB"))
	if project_type.startswith("10x"):
		if sample_type in TENX_TISSUE_DISSOCIATION_SAMPLE_TYPES or item.get("tissue_dissociation"):
			add("Tissue dissociation", get_service_price("TISSUE_DISSOCIATION"))
		if sample_type in TENX_NUCLEI_EXTRACTION_SAMPLE_TYPES or item.get("nuclei_extraction"):
			add("Nuclei extraction", get_service_price("NUCLEI_EXTRACTION"))
		if "Massachusetts on-site service added." in (item.get("rule_notes") or ""):
			add("On-site service", get_service_price("ONSITE_SERVICE"))
		if item.get("confirmed_tbd_fee"):
			add("Confirmed TBD fee", item.get("confirmed_tbd_fee"))
		if "not included in this subtotal" in (item.get("rule_notes") or ""):
			rows.append({
				"label": "On-site service outside Massachusetts",
				"unit_price": None,
				"quantity": item.get("quantity") or 1,
				"amount": None,
				"display": "TBD",
			})
	if project_type.startswith("Sequencing Only") or item.get("library_qc"):
		add("Library QC", get_service_price("LIBRARY_QC"))

	breakdown_total = sum(row["amount"] for row in rows if row.get("amount") is not None)
	unit_price = flt(item.get("unit_price"))
	adjustment = unit_price - (breakdown_total / quantity if quantity else 0)
	if abs(adjustment) >= 0.005:
		add("Manual adjustment", adjustment)
	return rows


def get_service_label(item):
	if item.get("service"):
		return frappe.db.get_value("NGS Service Catalog", item.get("service"), "service_name") or item.get("project_type") or "Service"
	return item.get("description") or item.get("project_type") or "Service"


def get_base_unit_price(item, service_name):
	project_type = item.get("project_type") or ""
	quantity = flt(item.get("quantity") or 1)
	if project_type == "Bulk RNAseq":
		if quantity < 12:
			return 109
		if quantity < 49:
			return 99
		if quantity < 97:
			return 94
		return 89
	if item.get("service"):
		return get_service_price(item.get("service"))
	return item.get("unit_price") or 0


def get_standard_reads(item):
	if item.get("service"):
		return flt(frappe.db.get_value("NGS Service Catalog", item.get("service"), "standard_reads_million"))
	return 0


def get_service_price(service_code):
	return flt(frappe.db.get_value("NGS Service Catalog", service_code, "unit_price"))


def get_wgs_depth_value(item):
	return flt(str(item.get("read_depth") or "30").lower().replace("x", "").strip())


def quote_pdf_context(quote):
	created = getdate(quote.creation)
	customer_name = " ".join(part for part in [quote.get("first_name"), quote.get("last_name")] if part)
	return {
		"quote": quote,
		"items": quote.items,
		"customer_name": customer_name,
		"created_date": formatdate(created, "mm.dd.yyyy"),
		"expires_date": formatdate(add_days(created, 60), "mm.dd.yyyy"),
		"money": money,
		"requires_manual_pricing": requires_manual_pricing,
		"item_has_unpriced_fee": item_has_unpriced_fee,
		"quote_has_unpriced_fees": quote_has_unpriced_fees(quote),
		"price_breakdown": price_breakdown,
	}


def order_pdf_items(order):
	items = []
	for item in order.items:
		row = item.as_dict()
		row["rule_notes"] = row.get("notes")
		row["price_breakdown"] = price_breakdown(row)
		row["has_unpriced_fee"] = item_has_unpriced_fee(row)
		items.append(row)
	return items


def order_pdf_context(order):
	created = getdate(order.creation)
	customer = frappe.get_doc("NGS Customer", order.customer)
	customer_name = customer.get("full_name") or " ".join(
		part for part in [customer.get("first_name"), customer.get("last_name")] if part
	)
	items = order_pdf_items(order)
	quote_doc = frappe.get_doc("NGS Quote", order.quote) if order.get("quote") and frappe.db.exists("NGS Quote", order.quote) else None
	has_unpriced_fees = bool(quote_doc and quote_doc.get("missing_info")) or any(item.get("has_unpriced_fee") for item in items)
	total_amount = flt(quote_doc.get("total_amount")) if quote_doc else sum(flt(item.get("amount")) for item in items)
	return {
		"order": order,
		"quote": quote_doc,
		"items": items,
		"customer": customer,
		"customer_name": customer_name,
		"created_date": formatdate(created, "mm.dd.yyyy"),
		"money": money,
		"total_amount": total_amount,
		"has_unpriced_fees": has_unpriced_fees,
		"requires_manual_pricing": requires_manual_pricing,
		"item_has_unpriced_fee": item_has_unpriced_fee,
		"price_breakdown": price_breakdown,
	}


def render_pdf(template, context):
	html = frappe.render_template(template, context)
	return pdfkit.from_string(
		html,
		False,
		{
			"page-size": "Letter",
			"margin-top": "0.35in",
			"margin-right": "0.35in",
			"margin-bottom": "0.35in",
			"margin-left": "0.35in",
			"encoding": "UTF-8",
		},
	)


def render_quote_pdf(quote):
	return render_pdf("templates/includes/ngs_quote_pdf.html", quote_pdf_context(quote))


def render_order_pdf(order):
	return render_pdf("templates/includes/ngs_order_pdf.html", order_pdf_context(order))


def quote_pdf_attachment(quote):
	return {"fname": f"{quote.name}.pdf", "fcontent": render_quote_pdf(quote)}


def order_pdf_attachment(order):
	return {"fname": f"{order.name}.pdf", "fcontent": render_order_pdf(order)}


def safe_pdf_attachment(builder, doc):
	try:
		return builder(doc)
	except Exception:
		frappe.logger("ngs_hub.email").error(
			"Failed to generate PDF attachment for %s %s",
			doc.doctype,
			doc.name,
			exc_info=True,
		)
		return None


@frappe.whitelist()
def download_quote_pdf(quote):
	customer = get_current_customer()
	quote_name = validate_customer_quote(quote, customer)
	quote_doc = frappe.get_doc("NGS Quote", quote_name)
	frappe.local.response.filename = f"{quote_doc.name}.pdf"
	frappe.local.response.filecontent = render_quote_pdf(quote_doc)
	frappe.local.response.type = "download"


@frappe.whitelist()
def download_order_pdf(order):
	customer = get_current_customer()
	order_name = validate_customer_order(order, customer)
	order_doc = frappe.get_doc("NGS Order", order_name)
	frappe.local.response.filename = f"{order_doc.name}.pdf"
	frappe.local.response.filecontent = render_order_pdf(order_doc)
	frappe.local.response.type = "download"


def attach_project_sample_qc(projects):
	if not projects:
		return projects
	project_names = [project.name for project in projects]
	samples = frappe.get_all(
		"NGS Sample Info",
		filters={"project": ["in", project_names]},
		fields=["name", "project", "sample_name", "sample_index", "sample_wvc", "qc_fig", "modified"],
		order_by="idx asc, creation asc",
		limit=200,
	)
	samples_by_project = {}
	for sample in samples:
		samples_by_project.setdefault(sample.project, []).append(sample)
	for project in projects:
		project["sample_qc"] = samples_by_project.get(project.name, [])
	return projects


def attach_quote_items(quotes):
	if not quotes:
		return quotes
	quote_names = [quote.name for quote in quotes]
	items = frappe.get_all(
		"NGS Quote Item",
		filters={"parent": ["in", quote_names]},
		fields=[
			"parent",
			"service",
			"project_type",
			"sample_type",
			"description",
			"quantity",
			"species",
			"read_depth",
			"reads_per_sample_million",
			"add_on_sequencing",
			"data_analysis",
			"onsite_service",
			"onsite_location",
			"onsite_address",
			"tissue_dissociation",
			"nuclei_extraction",
			"library_qc",
			"confirmed_tbd_fee",
			"unit_price",
			"amount",
			"rule_notes",
		],
		order_by="idx asc",
		limit=500,
	)
	items_by_quote = {}
	for item in items:
		item["price_breakdown"] = price_breakdown(item)
		item["has_unpriced_fee"] = item_has_unpriced_fee(item)
		items_by_quote.setdefault(item.parent, []).append(item)
	for quote in quotes:
		quote["items"] = items_by_quote.get(quote.name, [])
		quote["has_unpriced_fees"] = bool(quote.get("missing_info")) or any(
			item.get("has_unpriced_fee") for item in quote["items"]
		)
	return quotes


def attach_order_items(orders):
	if not orders:
		return orders
	order_names = [order.name for order in orders]
	items = frappe.get_all(
		"NGS Order Item",
		filters={"parent": ["in", order_names]},
		fields=[
			"parent",
			"service",
			"project_type",
			"sample_type",
			"description",
			"quantity",
			"read_depth",
			"data_analysis",
			"onsite_service",
			"onsite_location",
			"onsite_address",
			"tissue_dissociation",
			"nuclei_extraction",
			"library_qc",
			"unit_price",
			"amount",
		],
		order_by="idx asc",
		limit=500,
	)
	items_by_order = {}
	for item in items:
		items_by_order.setdefault(item.parent, []).append(item)

	quote_names = [order.quote for order in orders if order.get("quote")]
	quote_summary_by_name = {}
	if quote_names:
		for quote in frappe.get_all(
			"NGS Quote",
			filters={"name": ["in", quote_names]},
			fields=["name", "status", "total_amount", "missing_info"],
			limit=len(quote_names),
		):
			quote_summary_by_name[quote.name] = quote

	for order in orders:
		order["items"] = items_by_order.get(order.name, [])
		quote_summary = quote_summary_by_name.get(order.get("quote"))
		if quote_summary:
			order["quote_status"] = quote_summary.status
			order["quote_total_amount"] = quote_summary.total_amount
			order["quote_missing_info"] = quote_summary.missing_info
			order["quote_has_unpriced_fees"] = bool(quote_summary.missing_info)
	return orders


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=5, seconds=60 * 60)
def create_ngs_user_account(payload):
	if frappe.session.user != "Guest":
		frappe.throw(_("Log out before creating a new account."))
	payload = _loads(payload)
	first_name = (payload.get("first_name") or "").strip()
	last_name = (payload.get("last_name") or "").strip()
	email = (payload.get("email") or "").strip().lower()
	password = payload.get("password") or ""
	confirm_password = payload.get("confirm_password") or ""
	if not first_name or not last_name:
		frappe.throw(_("First name and last name are required."))
	valid_email = validate_email_address(email)
	if not valid_email or "," in valid_email:
		frappe.throw(_("Enter a valid email address."))
	email = valid_email.lower()
	if frappe.db.exists("User", email) or frappe.db.exists("User", {"email": email}):
		frappe.throw(_("An account already exists for this email. Please log in or reset your password."))
	if not password or len(password) < 8:
		frappe.throw(_("Password must be at least 8 characters."))
	if password != confirm_password:
		frappe.throw(_("Passwords do not match."))
	from frappe.core.doctype.user.user import test_password_strength

	strength = test_password_strength(password, user_data=(first_name, "", last_name, email, None)) or {}
	feedback = strength.get("feedback") or {}
	if feedback and feedback.get("password_policy_validation_passed") is False:
		suggestions = " ".join(feedback.get("suggestions") or [])
		frappe.throw(suggestions or _("Choose a stronger password."))
	if not frappe.db.exists("Role", "NGS External Customer"):
		frappe.throw(_("NGS External Customer role is not configured."))
	user = frappe.get_doc({
		"doctype": "User",
		"email": email,
		"first_name": first_name,
		"last_name": last_name,
		"full_name": " ".join([first_name, last_name]),
		"user_type": "Website User",
		"send_welcome_email": 0,
		"enabled": 1,
	})
	user.append("roles", {"role": "NGS External Customer"})
	user.insert(ignore_permissions=True)
	update_password(user.name, password)
	frappe.cache.hdel("home_page", user.name)
	return {"name": user.name, "login_url": "/login?redirect-to=/ngs_register"}


@frappe.whitelist()
def get_portal_context():
	customer = get_current_customer()
	data = {"customer": None, "quotes": [], "orders": [], "projects": [], "services": []}
	if customer:
		customer_doc = frappe.get_doc("NGS Customer", customer)
		data["customer"] = {
			"name": customer_doc.name,
			"full_name": customer_doc.get("full_name"),
			"email": customer_doc.get("email"),
			"organization": customer_doc.get("organization") or customer_doc.get("company_institution"),
		}
		data["quotes"] = attach_quote_items(frappe.get_all(
			"NGS Quote",
			filters={"customer": customer},
			fields=["name", "status", "source", "total_amount", "order", "custom_project_description", "missing_info", "modified"],
			order_by="modified desc",
			limit=20,
		))
		data["orders"] = attach_order_items(frappe.get_all(
			"NGS Order",
			filters={"customer": customer},
			fields=[
				"name",
				"status",
				"quote",
				"project",
				"po_number",
				"no_po_available",
				"po_file",
				"sample_registration_form",
				"erpnext_sales_order",
				"notes",
				"modified",
			],
			order_by="modified desc",
			limit=20,
		))
		data["projects"] = attach_project_sample_qc(frappe.get_all(
			"NGS Project",
			filters={"customer": customer},
			fields=["name", "class_type", "modified"],
			order_by="modified desc",
			limit=20,
		))
	data["services"] = frappe.get_all(
		"NGS Service Catalog",
		filters={"enabled": 1},
		fields=["name", "service_code", "service_name", "project_type", "service_group", "unit_price"],
		order_by="service_group asc, service_name asc",
		limit=200,
	)
	return data


@frappe.whitelist()
def get_registration_context():
	if frappe.session.user == "Guest":
		frappe.throw(_("Login is required"), frappe.PermissionError)
	user = frappe.get_doc("User", frappe.session.user)
	customer = frappe.db.get_value("NGS Customer", {"email": user.email or user.name}, "name")
	customer_doc = frappe.get_doc("NGS Customer", customer) if customer else None
	customer_first_name = customer_doc.first_name if customer_doc else None
	customer_last_name = customer_doc.last_name if customer_doc else None
	if customer_doc and (not customer_first_name or not customer_last_name):
		name_parts = (customer_doc.full_name or "").split(None, 1)
		customer_first_name = customer_first_name or (name_parts[0] if name_parts else "")
		customer_last_name = customer_last_name or (name_parts[1] if len(name_parts) > 1 else "")
	return {
		"user": {
			"email": user.email or user.name,
			"full_name": user.full_name,
			"first_name": user.first_name,
			"last_name": user.last_name,
			"phone": user.phone or user.mobile_no,
		},
		"customer": {
			"name": customer_doc.name,
			"full_name": customer_doc.full_name,
			"first_name": customer_first_name,
			"last_name": customer_last_name,
			"email": customer_doc.email,
			"organization": customer_doc.organization or customer_doc.company_institution,
			"phone": customer_doc.phone,
			"address": customer_doc.address,
			"lab_group_name": customer_doc.lab_group_name,
			"note": customer_doc.note,
		} if customer_doc else None,
	}


@frappe.whitelist()
def register_ngs_customer(payload):
	if frappe.session.user == "Guest":
		frappe.throw(_("Login is required"), frappe.PermissionError)
	payload = _loads(payload)
	user = frappe.get_doc("User", frappe.session.user)
	email = user.email or user.name
	existing_customer = frappe.db.get_value("NGS Customer", {"email": email}, "name")
	first_name = (payload.get("first_name") or "").strip()
	last_name = (payload.get("last_name") or "").strip()
	organization = (payload.get("organization") or "").strip()
	address = (payload.get("address") or "").strip()
	full_name = " ".join(part for part in [first_name, last_name] if part).strip()
	if not first_name or not last_name:
		frappe.throw(_("First name and last name are required."))
	if not organization:
		frappe.throw(_("Organization is required."))
	if not address:
		frappe.throw(_("Address is required."))
	values = {
		"full_name": full_name,
		"first_name": first_name,
		"last_name": last_name,
		"email": email,
		"organization": organization,
		"company_institution": organization,
		"phone": (payload.get("phone") or "").strip(),
		"address": address,
		"lab_group_name": (payload.get("lab_group_name") or "").strip(),
		"note": (payload.get("note") or "").strip(),
	}
	if existing_customer:
		customer = frappe.get_doc("NGS Customer", existing_customer)
		customer.update(values)
		customer.save(ignore_permissions=True)
	else:
		customer = frappe.get_doc({"doctype": "NGS Customer", **values})
		customer.insert(ignore_permissions=True)
	sync_ngs_customer_to_crm(customer)
	sync_ngs_customer_to_frappe_crm(customer)
	if "NGS External Customer" not in {row.role for row in user.roles}:
		user.append("roles", {"role": "NGS External Customer"})
		user.save(ignore_permissions=True)
	frappe.cache.hdel("home_page", user.name)
	return {"name": customer.name, "existing": bool(existing_customer)}


@frappe.whitelist()
def create_quote(payload):
	customer = get_current_customer()
	if not customer:
		frappe.throw(_("No NGS Customer is linked to this user yet. Please contact Athenomics."))
	payload = _loads(payload)
	items = payload.get("items") or []
	if not items:
		frappe.throw(_("Add at least one quote item."))
	quote = frappe.get_doc({
		"doctype": "NGS Quote",
		"customer": customer,
		"source": "Interactive",
		"custom_project_description": payload.get("custom_project_description"),
	})
	for item in items:
		quote.append("items", {
			"service": item.get("service"),
			"project_type": item.get("project_type"),
			"sample_type": item.get("sample_type"),
			"description": item.get("description"),
			"quantity": item.get("quantity") or 1,
			"species": item.get("species"),
			"read_depth": item.get("read_depth"),
			"reads_per_sample_million": item.get("reads_per_sample_million"),
			"add_on_sequencing": item.get("add_on_sequencing"),
			"data_analysis": item.get("data_analysis"),
			"onsite_service": item.get("onsite_service"),
			"onsite_location": item.get("onsite_location"),
			"onsite_address": item.get("onsite_address"),
			"tissue_dissociation": item.get("tissue_dissociation"),
			"nuclei_extraction": item.get("nuclei_extraction"),
			"library_qc": item.get("library_qc"),
		})
	quote.insert(ignore_permissions=True)
	notify_quote_created(quote)
	return {"name": quote.name, "status": quote.status, "total_amount": quote.total_amount}


@frappe.whitelist()
def cancel_quote(quote):
	customer = get_current_customer()
	if not customer:
		frappe.throw(_("No NGS Customer is linked to this user yet. Please contact Athenomics."))
	quote_name = validate_customer_quote(quote, customer)
	quote_doc = frappe.get_doc("NGS Quote", quote_name)
	if quote_doc.status == "Converted to Order" or quote_doc.order:
		frappe.throw(_("Quote {0} has already been converted to an order and cannot be cancelled here.").format(quote_doc.name))
	if quote_doc.status not in CANCELLABLE_QUOTE_STATUSES and quote_doc.status != "Cancelled":
		frappe.throw(_("Quote {0} is {1} and cannot be cancelled here.").format(quote_doc.name, quote_doc.status))
	if quote_doc.status != "Cancelled":
		quote_doc.status = "Cancelled"
		quote_doc.save(ignore_permissions=True)
	return {"name": quote_doc.name, "status": quote_doc.status}


@frappe.whitelist()
def create_order(payload):
	customer = get_current_customer()
	if not customer:
		frappe.throw(_("No NGS Customer is linked to this user yet. Please contact Athenomics."))
	payload = _loads(payload)
	if not payload.get("quote"):
		frappe.throw(_("Select a quote before placing an order."))
	quote = validate_customer_quote(payload.get("quote"), customer, require_orderable=True)
	no_po = truthy(payload.get("no_po"))
	validate_order_documents(
		payload.get("po_number"),
		payload.get("po_file"),
		payload.get("sample_registration_form"),
		require_po=not no_po,
	)
	order = frappe.get_doc({
		"doctype": "NGS Order",
		"customer": customer,
		"quote": quote,
		"status": "Submitted",
		"po_number": payload.get("po_number"),
		"no_po_available": no_po,
		"po_file": payload.get("po_file"),
		"sample_registration_form": payload.get("sample_registration_form"),
		"notes": payload.get("notes"),
	})
	for item in payload.get("items") or []:
		order.append("items", {
			"service": item.get("service"),
			"project_type": item.get("project_type"),
			"sample_type": item.get("sample_type"),
				"description": item.get("description"),
				"quantity": item.get("quantity") or 1,
				"data_analysis": item.get("data_analysis"),
				"onsite_service": item.get("onsite_service"),
				"onsite_location": item.get("onsite_location"),
				"onsite_address": item.get("onsite_address"),
				"tissue_dissociation": item.get("tissue_dissociation"),
				"nuclei_extraction": item.get("nuclei_extraction"),
				"library_qc": item.get("library_qc"),
			})
	order.insert(ignore_permissions=True)
	notify_order_created(order)
	return {"name": order.name, "status": order.status}
