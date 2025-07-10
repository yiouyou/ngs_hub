import frappe


def create_default_uoms():
	if not frappe.db.exists("UOM", "Sample"):
		uom = frappe.get_doc({"doctype": "UOM", "uom_name": "Sample", "must_be_whole_number": 1})
		uom.insert(ignore_permissions=True)
		frappe.db.commit()
