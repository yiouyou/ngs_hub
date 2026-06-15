import json

import frappe
import pdfkit
from frappe import _
from frappe.utils import add_days, flt, formatdate, getdate

from ngs_hub.api.crm_sync import sync_ngs_customer_to_crm
from ngs_hub.api.frappe_crm_sync import sync_ngs_customer_to_frappe_crm


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


def validate_customer_quote(quote, customer):
	if not quote:
		return None
	quote_customer = frappe.db.get_value("NGS Quote", quote, "customer")
	if not quote_customer:
		frappe.throw(_("Quote {0} was not found.").format(quote))
	if quote_customer != customer:
		frappe.throw(_("Quote {0} is not linked to your customer account.").format(quote), frappe.PermissionError)
	return quote


def validate_order_documents(po_number=None, po_file=None, sample_registration_form=None):
	if not (po_number or po_file):
		frappe.throw(_("Provide either a PO number or a PO file before placing an order."))
	if not sample_registration_form:
		frappe.throw(_("Upload the sample registration form before placing an order."))


def money(value):
	return f"{flt(value):,.2f}"


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
	}


@frappe.whitelist()
def download_quote_pdf(quote):
	customer = get_current_customer()
	quote_name = validate_customer_quote(quote, customer)
	quote_doc = frappe.get_doc("NGS Quote", quote_name)
	html = frappe.render_template("templates/includes/ngs_quote_pdf.html", quote_pdf_context(quote_doc))
	pdf = pdfkit.from_string(
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
	frappe.local.response.filename = f"{quote_doc.name}.pdf"
	frappe.local.response.filecontent = pdf
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
			"reads_per_sample_million",
			"add_on_sequencing",
			"data_analysis",
				"onsite_service",
				"onsite_address",
				"tissue_dissociation",
				"nuclei_extraction",
				"library_qc",
				"unit_price",
				"amount",
				"rule_notes",
			],
		order_by="idx asc",
		limit=500,
	)
	items_by_quote = {}
	for item in items:
		items_by_quote.setdefault(item.parent, []).append(item)
	for quote in quotes:
		quote["items"] = items_by_quote.get(quote.name, [])
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
			"data_analysis",
				"onsite_service",
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
	for order in orders:
		order["items"] = items_by_order.get(order.name, [])
	return orders


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
			fields=["name", "status", "source", "total_amount", "order", "custom_project_description", "modified"],
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
	full_name = " ".join(part for part in [first_name, last_name] if part).strip()
	if not first_name or not last_name:
		frappe.throw(_("First name and last name are required."))
	values = {
		"full_name": full_name,
		"first_name": first_name,
		"last_name": last_name,
		"email": email,
		"organization": (payload.get("organization") or "").strip(),
		"company_institution": (payload.get("organization") or "").strip(),
		"phone": (payload.get("phone") or "").strip(),
		"address": (payload.get("address") or "").strip(),
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
			"onsite_address": item.get("onsite_address"),
			"tissue_dissociation": item.get("tissue_dissociation"),
			"nuclei_extraction": item.get("nuclei_extraction"),
			"library_qc": item.get("library_qc"),
		})
	quote.insert(ignore_permissions=True)
	return {"name": quote.name, "status": quote.status, "total_amount": quote.total_amount}


@frappe.whitelist()
def create_order(payload):
	customer = get_current_customer()
	if not customer:
		frappe.throw(_("No NGS Customer is linked to this user yet. Please contact Athenomics."))
	payload = _loads(payload)
	quote = validate_customer_quote(payload.get("quote"), customer)
	validate_order_documents(
		payload.get("po_number"),
		payload.get("po_file"),
		payload.get("sample_registration_form"),
	)
	order = frappe.get_doc({
		"doctype": "NGS Order",
		"customer": customer,
		"quote": quote,
		"status": "Submitted",
		"po_number": payload.get("po_number"),
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
				"onsite_address": item.get("onsite_address"),
				"tissue_dissociation": item.get("tissue_dissociation"),
				"nuclei_extraction": item.get("nuclei_extraction"),
				"library_qc": item.get("library_qc"),
			})
	order.insert(ignore_permissions=True)
	return {"name": order.name, "status": order.status}
