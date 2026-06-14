import frappe


def has_app_permission():
	return bool({"System Manager", "NGS Internal Staff"} & set(frappe.get_roles()))
