import frappe
from frappe.utils import add_days, flt, today

from ngs_hub.api.crm_sync import sync_ngs_customer_to_crm


CRM_APP_NAME = "crm"
CRM_ORGANIZATION = "CRM Organization"
CRM_DEAL = "CRM Deal"
CRM_DEAL_STATUS = "CRM Deal Status"
CRM_PRODUCT = "CRM Product"


def sync_ngs_customer_to_frappe_crm(customer_doc):
	"""Create or reuse Frappe CRM records for an NGS Customer.

	Frappe CRM is optional for this app. This function exits cleanly when the
	CRM app is not installed so ERPNext-only sites keep working.
	"""
	if not is_frappe_crm_available():
		return None

	erp_links = sync_ngs_customer_to_crm(customer_doc) or {}
	organization_name = get_customer_organization_name(customer_doc)
	if not organization_name:
		return None

	crm_organization = get_existing_docname(customer_doc, "frappe_crm_organization", CRM_ORGANIZATION)
	if not crm_organization:
		crm_organization = find_crm_organization(organization_name) or create_crm_organization(customer_doc, organization_name)
	else:
		update_crm_organization(crm_organization, customer_doc, organization_name)

	crm_contact = erp_links.get("contact")
	set_if_field(customer_doc, "frappe_crm_organization", crm_organization)
	set_if_field(customer_doc, "frappe_crm_contact", crm_contact)
	return {"organization": crm_organization, "contact": crm_contact}


def sync_ngs_quote_to_frappe_crm_deal(quote_doc):
	"""Create or update a Frappe CRM Deal for an NGS Quote."""
	if not is_frappe_crm_available() or quote_doc.get("status") == "Cancelled":
		return None
	customer_doc = frappe.get_doc("NGS Customer", quote_doc.customer)
	crm_links = sync_ngs_customer_to_frappe_crm(customer_doc)
	if not crm_links:
		return None
	deal = upsert_crm_deal(
		source_doc=quote_doc,
		customer_doc=customer_doc,
		crm_links=crm_links,
		deal_field="frappe_crm_deal",
		title=quote_doc.name,
		status=get_quote_deal_status(quote_doc.get("status")),
		total=quote_doc.get("total_amount"),
		items=quote_doc.get("items", []),
	)
	set_if_field(quote_doc, "frappe_crm_deal", deal)
	return deal


def sync_ngs_order_to_frappe_crm_deal(order_doc):
	"""Create or update a Frappe CRM Deal for an NGS Order."""
	if not is_frappe_crm_available() or order_doc.get("status") == "Cancelled":
		return None
	customer_doc = frappe.get_doc("NGS Customer", order_doc.customer)
	crm_links = sync_ngs_customer_to_frappe_crm(customer_doc)
	if not crm_links:
		return None

	deal = None
	previous_order_deal = get_existing_docname(order_doc, "frappe_crm_deal", CRM_DEAL)
	if order_doc.get("quote") and frappe.db.exists("NGS Quote", order_doc.quote):
		quote_doc = frappe.get_doc("NGS Quote", order_doc.quote)
		deal = get_linked_quote_deal(quote_doc, order_doc)
		if previous_order_deal and previous_order_deal != deal:
			delete_unreferenced_crm_deal(previous_order_deal)

	if deal:
		crm_deal = frappe.get_doc(CRM_DEAL, deal)
		crm_deal.status = get_order_deal_status(order_doc.get("status"))
		crm_deal.deal_value = get_order_total(order_doc)
		crm_deal.expected_deal_value = get_order_total(order_doc)
		crm_deal.closed_date = today() if order_doc.get("status") in {"Accepted", "In Progress", "Completed"} else None
		if crm_deal.meta.has_field("products"):
			crm_deal.set("products", [])
			for item in order_doc.get("items", []):
				crm_deal.append("products", build_crm_product_row(item))
			set_crm_deal_product_totals(crm_deal)
		crm_deal.save(ignore_permissions=True)
	else:
		deal = upsert_crm_deal(
			source_doc=order_doc,
			customer_doc=customer_doc,
			crm_links=crm_links,
			deal_field="frappe_crm_deal",
			title=order_doc.name,
			status=get_order_deal_status(order_doc.get("status")),
			total=get_order_total(order_doc),
			items=order_doc.get("items", []),
		)

	set_if_field(order_doc, "frappe_crm_deal", deal)
	return deal


def get_linked_quote_deal(quote_doc, order_doc):
	quote_deal = get_existing_docname(quote_doc, "frappe_crm_deal", CRM_DEAL)
	order_deal = get_existing_docname(order_doc, "frappe_crm_deal", CRM_DEAL)
	if quote_deal:
		return quote_deal
	if order_deal:
		set_if_field(quote_doc, "frappe_crm_deal", order_deal)
		return order_deal
	return sync_ngs_quote_to_frappe_crm_deal(quote_doc)


def delete_unreferenced_crm_deal(deal):
	if not deal or not frappe.db.exists(CRM_DEAL, deal):
		return
	if has_ngs_reference_to_deal(deal):
		return
	frappe.delete_doc(CRM_DEAL, deal, ignore_permissions=True, force=True)


def has_ngs_reference_to_deal(deal):
	return bool(
		frappe.db.exists("NGS Quote", {"frappe_crm_deal": deal})
		or frappe.db.exists("NGS Order", {"frappe_crm_deal": deal})
	)


def upsert_crm_deal(source_doc, customer_doc, crm_links, deal_field, title, status, total, items):
	deal_name = get_existing_docname(source_doc, deal_field, CRM_DEAL)
	deal = frappe.get_doc(CRM_DEAL, deal_name) if deal_name else frappe.new_doc(CRM_DEAL)
	contact = crm_links.get("contact")
	currency = get_currency(items)
	deal.update({
		"organization": crm_links.get("organization"),
		"status": status,
		"deal_owner": get_deal_owner(),
		"deal_value": flt(total),
		"expected_deal_value": flt(total),
		"expected_closure_date": add_days(today(), 30),
		"currency": currency,
		"exchange_rate": 1,
		"lead_name": customer_doc.get("full_name") or title,
		"organization_name": get_customer_organization_name(customer_doc),
		"first_name": customer_doc.get("first_name"),
		"last_name": customer_doc.get("last_name"),
		"email": customer_doc.get("email"),
		"phone": customer_doc.get("phone"),
		"contact": contact,
		"next_step": f"Review {source_doc.doctype} {source_doc.name}",
	})
	if deal.meta.has_field("contacts"):
		deal.set("contacts", [])
		if contact:
			deal.append("contacts", {"contact": contact, "is_primary": 1})
	if deal.meta.has_field("products"):
		deal.set("products", [])
		for item in items:
			deal.append("products", build_crm_product_row(item))
		set_crm_deal_product_totals(deal)
	deal.flags.ignore_permissions = True
	deal.save()
	return deal.name


def build_crm_product_row(item):
	product_code = ensure_crm_product(item)
	qty = flt(item.get("quantity")) or 1
	rate = flt(item.get("unit_price"))
	amount = qty * rate
	return {
		"product_code": product_code,
		"product_name": item.get("description") or item.get("project_type") or "NGS Service",
		"qty": qty,
		"rate": rate,
		"amount": amount,
		"net_amount": amount,
	}


def set_crm_deal_product_totals(deal):
	total = sum(flt(row.get("amount")) for row in deal.get("products", []))
	net_total = sum(flt(row.get("net_amount")) for row in deal.get("products", []))
	deal.total = total
	deal.net_total = net_total or total


def ensure_crm_product(item):
	service = item.get("service")
	if service:
		service_doc = frappe.get_doc("NGS Service Catalog", service)
		code = service_doc.get("service_code") or service_doc.name
		name = service_doc.get("service_name") or service_doc.name
		description = service_doc.get("description") or name
		rate = service_doc.get("unit_price")
	else:
		code = "NGS_CUSTOM_PROJECT"
		name = item.get("description") or item.get("project_type") or "NGS Custom Project"
		description = name
		rate = item.get("unit_price")
	if not frappe.db.exists(CRM_PRODUCT, code):
		product = frappe.get_doc({
			"doctype": CRM_PRODUCT,
			"product_code": code,
			"product_name": name,
			"description": description,
			"standard_rate": flt(rate),
			"disabled": 0,
		})
		product.insert(ignore_permissions=True)
	return code


def is_frappe_crm_available():
	installed = set(frappe.get_installed_apps())
	return bool(
		CRM_APP_NAME in installed
		and frappe.db.exists("DocType", CRM_ORGANIZATION)
		and frappe.db.exists("DocType", CRM_DEAL)
	)


def find_crm_organization(organization_name):
	return frappe.db.get_value(CRM_ORGANIZATION, {"organization_name": organization_name}, "name")


def create_crm_organization(customer_doc, organization_name):
	doc = frappe.get_doc({
		"doctype": CRM_ORGANIZATION,
		"organization_name": organization_name,
		"currency": frappe.db.get_single_value("Global Defaults", "default_currency") or "USD",
		"exchange_rate": 1,
	})
	doc.insert(ignore_permissions=True)
	return doc.name


def update_crm_organization(organization, customer_doc, organization_name):
	doc = frappe.get_doc(CRM_ORGANIZATION, organization)
	changed = False
	if doc.get("organization_name") != organization_name:
		doc.organization_name = organization_name
		changed = True
	if changed:
		doc.save(ignore_permissions=True)


def get_existing_docname(doc, fieldname, doctype):
	value = doc.get(fieldname) if has_docfield(doc.doctype, fieldname) else None
	return value if value and frappe.db.exists(doctype, value) else None


def get_customer_organization_name(customer_doc):
	return customer_doc.get("organization") or customer_doc.get("company_institution") or customer_doc.get("full_name")


def get_quote_deal_status(status):
	if status == "Converted to Order":
		return ensure_deal_status("Won", "Won", 100, "green")
	if status in {"Confirmed", "Sent"}:
		return ensure_deal_status("Qualified", "Ongoing", 75, "blue")
	if status == "Pending Info":
		return ensure_deal_status("Pending Info", "On Hold", 25, "amber")
	return ensure_deal_status("Open", "Open", 10, "gray")


def get_order_deal_status(status):
	if status in {"Accepted", "In Progress", "Completed"}:
		return ensure_deal_status("Won", "Won", 100, "green")
	if status in {"Submitted", "In Review"}:
		return ensure_deal_status("Qualified", "Ongoing", 75, "blue")
	return ensure_deal_status("Open", "Open", 10, "gray")


def ensure_deal_status(status, status_type, probability, color):
	if frappe.db.exists(CRM_DEAL_STATUS, status):
		return status
	doc = frappe.get_doc({
		"doctype": CRM_DEAL_STATUS,
		"deal_status": status,
		"type": status_type,
		"probability": probability,
		"color": color,
	})
	doc.insert(ignore_permissions=True)
	return doc.name


def get_deal_owner():
	return (
		frappe.db.get_value("User", {"enabled": 1, "user_type": "System User", "name": frappe.session.user}, "name")
		or frappe.db.get_value("User", {"enabled": 1, "user_type": "System User"}, "name")
	)


def get_currency(items):
	for item in items:
		if item.get("service"):
			currency = frappe.db.get_value("NGS Service Catalog", item.service, "currency")
			if currency:
				return currency
	return frappe.db.get_single_value("Global Defaults", "default_currency") or "USD"


def get_order_total(order_doc):
	return sum(flt(item.get("amount")) for item in order_doc.get("items", []))


def has_docfield(doctype, fieldname):
	return frappe.db.exists("DocField", {"parent": doctype, "fieldname": fieldname})


def set_if_field(doc, fieldname, value):
	if not value or not has_docfield(doc.doctype, fieldname):
		return
	if doc.get(fieldname) == value:
		return
	doc.db_set(fieldname, value, update_modified=False)
