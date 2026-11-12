import frappe


def sync_ngs_customer_to_crm(customer_doc):
	"""Create or reuse ERPNext Customer and Contact for an NGS Customer."""
	if "erpnext" not in frappe.get_installed_apps():
		return None
	customer_name = customer_doc.get("organization") or customer_doc.get("company_institution") or customer_doc.get("full_name")
	if not customer_name:
		return None
	erp_customer = customer_doc.get("erpnext_customer") or find_customer(customer_name, customer_doc.get("email"))
	if not erp_customer:
		erp_customer = create_customer(customer_doc, customer_name)
	else:
		# A customer moving organizations must not rename another customer's company.
		current_name = frappe.db.get_value("Customer", erp_customer, "customer_name")
		if current_name != customer_name:
			matching_customer = find_customer(customer_name, None)
			if matching_customer:
				erp_customer = matching_customer
			elif frappe.db.exists("NGS Customer", {
				"erpnext_customer": erp_customer, "name": ["!=", customer_doc.name],
			}):
				erp_customer = create_customer(customer_doc, customer_name)
		update_customer(erp_customer, customer_doc, customer_name)
	erp_contact = customer_doc.get("erpnext_contact") or find_contact(customer_doc.get("email"))
	if not erp_contact:
		erp_contact = create_contact(customer_doc, erp_customer)
	else:
		update_contact(erp_contact, customer_doc)
		ensure_contact_link(erp_contact, erp_customer)
	for fieldname, value in (("erpnext_customer", erp_customer), ("erpnext_contact", erp_contact)):
		if customer_doc.get(fieldname) != value:
			customer_doc.db_set(fieldname, value, update_modified=False)
	return {"customer": erp_customer, "contact": erp_contact}


def find_customer(customer_name, email):
	if customer_name:
		customer = frappe.db.get_value("Customer", {"customer_name": customer_name}, "name")
		if customer:
			return customer
		if frappe.db.exists("Customer", customer_name):
			return customer_name
	if email:
		contact = find_contact(email)
		if contact:
			links = frappe.get_all(
				"Dynamic Link",
				filters={"parenttype": "Contact", "parent": contact, "link_doctype": "Customer"},
				pluck="link_name",
				limit=1,
			)
			if links:
				return links[0]
	return None


def find_contact(email):
	if not email:
		return None
	return frappe.db.get_value("Contact Email", {"email_id": email}, "parent")


def create_customer(customer_doc, customer_name):
	doc = frappe.get_doc({
		"doctype": "Customer",
		"customer_name": customer_name,
		"customer_type": "Company" if (customer_doc.get("organization") or customer_doc.get("company_institution")) else "Individual",
		"customer_group": get_default_customer_group(),
		"territory": get_default_territory(),
	})
	doc.insert(ignore_permissions=True)
	return doc.name


def update_customer(customer, customer_doc, customer_name):
	doc = frappe.get_doc("Customer", customer)
	changed = False
	if doc.get("customer_name") != customer_name:
		doc.customer_name = customer_name
		changed = True
	customer_type = "Company" if (customer_doc.get("organization") or customer_doc.get("company_institution")) else "Individual"
	if doc.get("customer_type") != customer_type:
		doc.customer_type = customer_type
		changed = True
	if changed:
		doc.save(ignore_permissions=True)


def create_contact(customer_doc, erp_customer):
	first_name = customer_doc.get("first_name") or customer_doc.get("full_name") or erp_customer
	doc = frappe.get_doc({
		"doctype": "Contact",
		"first_name": first_name,
		"last_name": customer_doc.get("last_name"),
		"company_name": customer_doc.get("organization") or customer_doc.get("company_institution"),
		"is_primary_contact": 1,
	})
	if customer_doc.get("email"):
		doc.append("email_ids", {"email_id": customer_doc.email, "is_primary": 1})
	if customer_doc.get("phone"):
		doc.append("phone_nos", {"phone": customer_doc.phone, "is_primary_phone": 1})
	doc.append("links", {"link_doctype": "Customer", "link_name": erp_customer})
	doc.insert(ignore_permissions=True)
	return doc.name


def update_contact(contact, customer_doc):
	doc = frappe.get_doc("Contact", contact)
	changed = False
	first_name = customer_doc.get("first_name") or customer_doc.get("full_name") or doc.get("first_name")
	last_name = customer_doc.get("last_name")
	company_name = customer_doc.get("organization") or customer_doc.get("company_institution")
	for fieldname, value in {
		"first_name": first_name,
		"last_name": last_name or "",
		"company_name": company_name or "",
	}.items():
		if (doc.get(fieldname) or "") != (value or ""):
			doc.set(fieldname, value)
			changed = True
	if customer_doc.get("email"):
		changed |= sync_primary_contact_value(doc, "email_ids", "email_id", "is_primary", customer_doc.email)
	changed |= sync_primary_contact_value(doc, "phone_nos", "phone", "is_primary_phone", customer_doc.get("phone"))
	if changed:
		doc.save(ignore_permissions=True)


def has_contact_email(contact_doc, email):
	return any(row.email_id == email for row in contact_doc.get("email_ids", []))


def sync_primary_contact_value(doc, table, value_field, primary_field, value):
	"""Keep secondary contact details while selecting exactly one canonical primary."""
	rows = doc.get(table, [])
	primary = next((row for row in rows if value and row.get(value_field) == value), None)
	changed = False
	if value and primary is None:
		primary = doc.append(table, {value_field: value, primary_field: 1})
		changed = True
	for row in doc.get(table, []):
		is_primary = int(row is primary)
		if (row.get(primary_field) or 0) != is_primary:
			row.set(primary_field, is_primary)
			changed = True
	return changed


def ensure_contact_link(contact, erp_customer):
	if frappe.db.exists("Dynamic Link", {"parenttype": "Contact", "parent": contact, "link_doctype": "Customer", "link_name": erp_customer}):
		return
	doc = frappe.get_doc("Contact", contact)
	doc.append("links", {"link_doctype": "Customer", "link_name": erp_customer})
	doc.save(ignore_permissions=True)


def get_default_customer_group():
	return (
		frappe.db.get_single_value("Selling Settings", "customer_group")
		or frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
		or frappe.db.get_value("Customer Group", {}, "name")
	)


def get_default_territory():
	return (
		frappe.db.get_single_value("Selling Settings", "territory")
		or frappe.db.get_value("Territory", {"is_group": 0}, "name")
		or frappe.db.get_value("Territory", {}, "name")
	)
