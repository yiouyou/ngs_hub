import json
from pathlib import Path

import frappe

NGS_ITEM_GROUPS = [
	("NGS Supplies", "All Item Groups", 1),
	("NGS Services", "All Item Groups", 1),
	("Kits", "NGS Supplies", 0),
	("NGS Consumables", "NGS Supplies", 0),
	("Reagents", "NGS Supplies", 0),
	("External Packages", "NGS Services", 1),
	("Internal Workflow", "NGS Services", 1),
	("SingleCell", "External Packages", 0),
	("Meta", "External Packages", 0),
	("RNAseq", "External Packages", 0),
	("Customized Data Analysis", "External Packages", 0),
	("Sequencing Outsource", "Internal Workflow", 0),
	("SingleCell Wetlab", "Internal Workflow", 0),
	("Meta Wetlab", "Internal Workflow", 0),
	("RNAseq Wetlab", "Internal Workflow", 0),
	("SingleCell Bioinfo", "Internal Workflow", 0),
	("Meta Bioinfo", "Internal Workflow", 0),
	("RNAseq Bioinfo", "Internal Workflow", 0),
]


def prepare_install():
	ensure_ngs_defaults()


def ensure_ngs_defaults():
	create_default_uoms()
	create_ngs_roles()
	create_ngs_item_groups()
	create_ngs_service_catalog()
	ensure_erp_service_rate_precision()


def ensure_erp_service_rate_precision():
	"""Keep allocated service rates without reducing the actual library quantity."""
	from frappe.custom.doctype.property_setter.property_setter import make_property_setter

	for doctype in ("Quotation Item", "Sales Order Item"):
		if not frappe.db.exists("DocType", doctype):
			continue
		for fieldname in ("rate", "net_rate", "base_rate", "base_net_rate"):
			field = frappe.get_meta(doctype).get_field(fieldname)
			if field and int(field.precision or 0) < 9:
				make_property_setter(doctype, fieldname, "precision", "9", "Int")


def create_ngs_service_catalog():
	"""Insert missing catalog codes without replacing saved prices or enabled flags."""
	if not frappe.db.exists("DocType", "NGS Service Catalog"):
		return

	# Nested seed fixtures are not imported by Frappe's overwrite-based fixture sync.
	path = Path(frappe.get_app_path("ngs_hub", "fixtures", "catalog", "seed_ngs_service_catalog.json"))
	with path.open(encoding="utf-8") as source:
		services = json.load(source)
	for service in services:
		if frappe.db.exists("NGS Service Catalog", {"service_code": service["service_code"]}):
			continue
		frappe.get_doc(service).insert(ignore_permissions=True)


def create_default_uoms():
	if not frappe.db.exists("UOM", "Sample"):
		uom = frappe.get_doc({"doctype": "UOM", "uom_name": "Sample", "must_be_whole_number": 1})
		uom.insert(ignore_permissions=True)
		frappe.db.commit()


def create_ngs_roles():
	if frappe.db.exists("Role", "NGS External Customer"):
		frappe.db.set_value("Role", "NGS External Customer", "home_page", None)
	if frappe.db.exists("Role", "NGS Internal Staff"):
		frappe.db.set_value("Role", "NGS Internal Staff", "home_page", None)
	frappe.cache.delete_key("home_page")
	frappe.db.commit()


def create_ngs_item_groups():
	if "erpnext" not in frappe.get_installed_apps():
		return

	ensure_root_item_group()
	for item_group_name, parent_item_group, is_group in NGS_ITEM_GROUPS:
		if frappe.db.exists("Item Group", item_group_name):
			continue

		frappe.get_doc(
			{
				"doctype": "Item Group",
				"item_group_name": item_group_name,
				"parent_item_group": parent_item_group,
				"is_group": is_group,
			}
		).insert(ignore_permissions=True)

	frappe.db.commit()


def ensure_root_item_group():
	if frappe.db.exists("Item Group", "All Item Groups"):
		return

	frappe.get_doc(
		{
			"doctype": "Item Group",
			"item_group_name": "All Item Groups",
			"is_group": 1,
		}
	).insert(ignore_permissions=True)
