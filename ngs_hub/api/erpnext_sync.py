import frappe
from frappe.utils import add_days, today

from ngs_hub.api.crm_sync import sync_ngs_customer_to_crm


DEFAULT_ITEM_CODE = "NGS_CUSTOM_PROJECT"
DEFAULT_ITEM_NAME = "NGS Custom Project"
DEFAULT_ITEM_GROUP = "NGS Services"


def sync_ngs_quote_to_quotation(quote_doc):
	"""Create or update a draft ERPNext Quotation for an NGS Quote."""
	if "erpnext" not in frappe.get_installed_apps():
		return None
	if quote_doc.get("status") == "Cancelled":
		return None

	erp_customer = get_or_create_erp_customer(quote_doc)
	quotation_name = quote_doc.get("erpnext_quotation")
	if quotation_name and not frappe.db.exists("Quotation", quotation_name):
		quotation_name = None

	if quotation_name:
		quotation = frappe.get_doc("Quotation", quotation_name)
		if quotation.docstatus != 0:
			return quotation.name
		quotation.set("items", [])
	else:
		quotation = frappe.new_doc("Quotation")

	quotation.update({
		"quotation_to": "Customer",
		"party_name": erp_customer,
		"customer_name": quote_doc.get("company_institution") or erp_customer,
		"transaction_date": today(),
		"valid_till": add_days(today(), 30),
		"order_type": "Sales",
		"company": get_default_company(),
		"currency": get_quote_currency(quote_doc),
		"conversion_rate": 1,
		"selling_price_list": get_default_selling_price_list(),
		"price_list_currency": get_quote_currency(quote_doc),
		"plc_conversion_rate": 1,
		"ignore_pricing_rule": 1,
		"contact_email": quote_doc.get("email"),
		"terms": get_quotation_terms(quote_doc),
	})

	for quote_item in quote_doc.get("items", []):
		quotation.append("items", build_quotation_item(quote_item))

	quotation.flags.ignore_permissions = True
	quotation.save()
	quote_doc.set("erpnext_quotation", quotation.name)
	quote_doc.db_set("erpnext_quotation", quotation.name, update_modified=False)
	return quotation.name


def sync_ngs_order_to_sales_order(order_doc):
	"""Create or update a draft ERPNext Sales Order for an NGS Order."""
	if "erpnext" not in frappe.get_installed_apps():
		return None
	if order_doc.get("status") == "Cancelled":
		return None
	if not order_doc.get("items"):
		return None

	erp_customer = get_or_create_erp_customer(order_doc)
	sales_order_name = order_doc.get("erpnext_sales_order")
	if sales_order_name and not frappe.db.exists("Sales Order", sales_order_name):
		sales_order_name = None

	if sales_order_name:
		sales_order = frappe.get_doc("Sales Order", sales_order_name)
		if sales_order.docstatus != 0:
			return sales_order.name
		sales_order.set("items", [])
	else:
		sales_order = frappe.new_doc("Sales Order")

	currency = get_order_currency(order_doc)
	sales_order.update({
		"customer": erp_customer,
		"transaction_date": today(),
		"delivery_date": add_days(today(), 30),
		"order_type": "Sales",
		"company": get_default_company(),
		"currency": currency,
		"conversion_rate": 1,
		"selling_price_list": get_default_selling_price_list(),
		"price_list_currency": currency,
		"plc_conversion_rate": 1,
		"ignore_pricing_rule": 1,
		"po_no": order_doc.get("po_number"),
		"po_date": today() if order_doc.get("po_number") else None,
		"terms": get_sales_order_terms(order_doc),
	})

	for order_item in order_doc.get("items", []):
		sales_order.append("items", build_sales_order_item(order_item))

	sales_order.flags.ignore_permissions = True
	sales_order.save()
	mirror_order_attachments(order_doc, sales_order)
	order_doc.set("erpnext_sales_order", sales_order.name)
	order_doc.db_set("erpnext_sales_order", sales_order.name, update_modified=False)
	return sales_order.name


def get_or_create_erp_customer(quote_doc):
	customer = frappe.get_doc("NGS Customer", quote_doc.customer)
	erp_customer = customer.get("erpnext_customer")
	if not erp_customer:
		links = sync_ngs_customer_to_crm(customer)
		erp_customer = links.get("customer") if links else None
	if not erp_customer:
		frappe.throw("ERPNext Customer could not be created for this NGS Customer")
	return erp_customer


def build_sales_order_item(order_item):
	quote_item = get_quote_item(order_item)
	item_code = get_item_code(order_item, quote_item)
	description = get_item_description(order_item, quote_item)
	return {
		"item_code": item_code,
		"item_name": frappe.db.get_value("Item", item_code, "item_name") or item_code,
		"description": description,
		"qty": order_item.get("quantity") or 1,
		"uom": get_item_uom(item_code),
		"conversion_factor": 1,
		"rate": get_order_item_rate(order_item, quote_item),
		"delivery_date": add_days(today(), 30),
	}


def build_quotation_item(quote_item):
	item_code = get_item_code(quote_item)
	description = get_item_description(quote_item)
	return {
		"item_code": item_code,
		"item_name": frappe.db.get_value("Item", item_code, "item_name") or item_code,
		"description": description,
		"qty": quote_item.get("quantity") or 1,
		"uom": get_item_uom(item_code),
		"conversion_factor": 1,
		"rate": quote_item.get("unit_price") or 0,
	}


def get_item_code(doc_item, fallback_quote_item=None):
	service_name = doc_item.get("service") or (fallback_quote_item.get("service") if fallback_quote_item else None)
	if service_name:
		service = frappe.get_doc("NGS Service Catalog", service_name)
		return ensure_item_for_service(service)
	return ensure_generic_item()


def ensure_item_for_service(service_doc):
	if service_doc.get("erpnext_item") and frappe.db.exists("Item", service_doc.erpnext_item):
		return service_doc.erpnext_item

	item_code = service_doc.service_code
	if not frappe.db.exists("Item", item_code):
		create_item(
			item_code=item_code,
			item_name=service_doc.service_name,
			uom=service_doc.get("billing_uom") or "Nos",
			description=service_doc.get("description") or service_doc.service_name,
			disabled=0 if service_doc.get("enabled") else 1,
		)
	if service_doc.get("erpnext_item") != item_code:
		service_doc.db_set("erpnext_item", item_code, update_modified=False)
	return item_code


def ensure_generic_item():
	if not frappe.db.exists("Item", DEFAULT_ITEM_CODE):
		create_item(
			item_code=DEFAULT_ITEM_CODE,
			item_name=DEFAULT_ITEM_NAME,
			uom="Nos",
			description="Custom NGS project quoted from NGS Hub",
			disabled=0,
		)
	return DEFAULT_ITEM_CODE


def create_item(item_code, item_name, uom, description, disabled):
	uom = ensure_uom(uom)
	doc = frappe.get_doc({
		"doctype": "Item",
		"item_code": item_code,
		"item_name": item_name,
		"item_group": ensure_item_group(),
		"stock_uom": uom,
		"is_stock_item": 0,
		"is_sales_item": 1,
		"is_purchase_item": 0,
		"include_item_in_manufacturing": 0,
		"disabled": disabled,
		"description": description,
	})
	doc.insert(ignore_permissions=True)


def ensure_item_group():
	if frappe.db.exists("Item Group", DEFAULT_ITEM_GROUP):
		return DEFAULT_ITEM_GROUP
	parent = "All Item Groups" if frappe.db.exists("Item Group", "All Item Groups") else None
	if not parent:
		parent = frappe.db.get_value("Item Group", {"is_group": 1}, "name")
	doc = frappe.get_doc({
		"doctype": "Item Group",
		"item_group_name": DEFAULT_ITEM_GROUP,
		"parent_item_group": parent,
		"is_group": 0,
	})
	doc.insert(ignore_permissions=True)
	return doc.name


def ensure_uom(uom):
	uom = uom or "Nos"
	if frappe.db.exists("UOM", uom):
		return uom
	doc = frappe.get_doc({
		"doctype": "UOM",
		"uom_name": uom,
		"enabled": 1,
	})
	doc.insert(ignore_permissions=True)
	return doc.name


def get_item_uom(item_code):
	return frappe.db.get_value("Item", item_code, "stock_uom") or "Nos"


def get_default_company():
	return (
		frappe.db.get_single_value("Global Defaults", "default_company")
		or frappe.db.get_value("Company", {}, "name")
	)


def get_quote_currency(quote_doc):
	for quote_item in quote_doc.get("items", []):
		if quote_item.get("service"):
			currency = frappe.db.get_value("NGS Service Catalog", quote_item.service, "currency")
			if currency:
				return currency
	return frappe.db.get_single_value("Global Defaults", "default_currency") or "USD"


def get_order_currency(order_doc):
	for order_item in order_doc.get("items", []):
		service = order_item.get("service")
		if not service and order_item.get("quote_item"):
			service = frappe.db.get_value("NGS Quote Item", order_item.quote_item, "service")
		if service:
			currency = frappe.db.get_value("NGS Service Catalog", service, "currency")
			if currency:
				return currency
	return frappe.db.get_single_value("Global Defaults", "default_currency") or "USD"


def get_default_selling_price_list():
	price_list = (
		frappe.db.get_single_value("Selling Settings", "selling_price_list")
		or frappe.db.get_value("Price List", {"selling": 1, "enabled": 1}, "name")
	)
	if price_list:
		return price_list
	return ensure_standard_selling_price_list()


def ensure_standard_selling_price_list():
	price_list_name = "Standard Selling"
	if not frappe.db.exists("Price List", price_list_name):
		frappe.get_doc(
			{
				"doctype": "Price List",
				"price_list_name": price_list_name,
				"currency": frappe.db.get_single_value("Global Defaults", "default_currency") or "USD",
				"enabled": 1,
				"selling": 1,
				"buying": 0,
			}
		).insert(ignore_permissions=True)
	else:
		frappe.db.set_value(
			"Price List",
			price_list_name,
			{"enabled": 1, "selling": 1},
			update_modified=False,
		)
	return price_list_name


def get_item_description(doc_item, fallback_quote_item=None):
	parts = [doc_item.get("description") or doc_item.get("project_type") or "NGS service"]
	for label, fieldname in (
		("Project Type", "project_type"),
		("Sample Type", "sample_type"),
		("Species", "species"),
		("Read Depth", "read_depth"),
		("Reads Per Sample (M)", "reads_per_sample_million"),
		("Data Analysis", "data_analysis"),
		("Rule Notes", "rule_notes"),
	):
		value = doc_item.get(fieldname)
		if not value and fallback_quote_item:
			value = fallback_quote_item.get(fieldname)
		if value:
			parts.append(f"{label}: {value}")
	return "<br>".join(parts)


def get_quote_item(order_item):
	if not order_item.get("quote_item"):
		return None
	if frappe.db.exists("NGS Quote Item", order_item.quote_item):
		return frappe.get_doc("NGS Quote Item", order_item.quote_item)
	return None


def get_order_item_rate(order_item, quote_item=None):
	if order_item.get("unit_price") is not None:
		return order_item.get("unit_price") or 0
	if quote_item and quote_item.get("unit_price") is not None:
		return quote_item.get("unit_price") or 0
	return 0


def get_quotation_terms(quote_doc):
	parts = [f"Generated from NGS Quote {quote_doc.name}."]
	if quote_doc.get("custom_project_description"):
		parts.append(quote_doc.custom_project_description)
	if quote_doc.get("missing_info"):
		parts.append(f"Missing information: {quote_doc.missing_info}")
	return "<br><br>".join(parts)


def get_sales_order_terms(order_doc):
	parts = [f"Generated from NGS Order {order_doc.name}."]
	if order_doc.get("quote"):
		parts.append(f"Source NGS Quote: {order_doc.quote}.")
	if order_doc.get("notes"):
		parts.append(order_doc.notes)
	if order_doc.get("po_file"):
		parts.append(f"PO file: {order_doc.po_file}")
	if order_doc.get("sample_registration_form"):
		parts.append(f"Sample registration form: {order_doc.sample_registration_form}")
	return "<br><br>".join(parts)


def mirror_order_attachments(order_doc, sales_order):
	for fieldname, label in (
		("po_file", "PO File"),
		("sample_registration_form", "Sample Registration Form"),
	):
		file_url = order_doc.get(fieldname)
		if not file_url:
			continue
		if frappe.db.exists("File", {
			"file_url": file_url,
			"attached_to_doctype": "Sales Order",
			"attached_to_name": sales_order.name,
		}):
			continue
		source_file = get_source_file(file_url)
		frappe.get_doc({
			"doctype": "File",
			"file_name": get_mirrored_file_name(source_file, label, file_url),
			"file_url": file_url,
			"is_private": source_file.get("is_private") if source_file else 1,
			"file_size": source_file.get("file_size") if source_file else 0,
			"file_type": source_file.get("file_type") if source_file else None,
			"attached_to_doctype": "Sales Order",
			"attached_to_name": sales_order.name,
			"attached_to_field": None,
		}).insert(ignore_permissions=True)


def get_source_file(file_url):
	file_name = frappe.db.get_value(
		"File",
		{"file_url": file_url},
		"name",
		order_by="creation asc",
	)
	if not file_name:
		return None
	return frappe.get_doc("File", file_name)


def get_mirrored_file_name(source_file, label, file_url):
	if source_file and source_file.get("file_name"):
		return source_file.file_name
	return file_url.rsplit("/", 1)[-1] or label
