"""Read-only quotation configuration and price previews for internal Desk users."""

import frappe
from frappe import _
from frappe.utils import flt

from ngs_hub.api.quote_products import PRODUCTS, get_product_service

ITEM_FIELDS = (
	"service",
	"project_type",
	"sample_type",
	"description",
	"quantity",
	"species",
	"read_depth",
	"million_reads_per_sample",
	"add_on_sequencing",
	"data_analysis",
	"onsite_service",
	"onsite_location",
	"onsite_address",
	"tissue_dissociation",
	"nuclei_extraction",
	"library_qc",
	"confirmed_tbd_fee",
	"use_price_override",
	"override_unit_price",
	"unit_price",
	"amount",
	"rule_notes",
	"pricing_details",
)


def require_quote_editor(quote_name=None, customer=None):
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
		frappe.throw(_("Only internal NGS staff can configure quotations."), frappe.PermissionError)
	if quote_name:
		quote = frappe.get_doc("NGS Quote", quote_name)
		quote.check_permission("write")
	else:
		frappe.has_permission("NGS Quote", "create", throw=True)
	if customer:
		frappe.get_doc("NGS Customer", customer).check_permission("read")


@frappe.whitelist()
def get_quote_builder_options(quote_name=None, customer=None):
	require_quote_editor(quote_name, customer)
	products = []
	for product in PRODUCTS:
		service = get_product_service(product, required=False)
		if product["service_code"] and not service:
			continue
		products.append(
			{
				**product,
				"service": service.name if service else None,
				"standard_reads": (flt(service.standard_reads_million) or product["standard_reads"])
				if service
				else 0,
			}
		)
	return {"products": products}


@frappe.whitelist()
def preview_quote_item(item, quote_name=None, customer=None):
	require_quote_editor(quote_name, customer)
	values = frappe.parse_json(item) if isinstance(item, str) else item
	if not isinstance(values, dict):
		frappe.throw(_("Provide one quotation item."))
	quote = frappe.new_doc("NGS Quote")
	row = quote.append("items", {field: values[field] for field in ITEM_FIELDS if field in values and field != "pricing_details"})
	quote.apply_quote_rules()
	return {field: row.get(field) for field in ITEM_FIELDS}
