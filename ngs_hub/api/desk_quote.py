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
MAX_PREVIEW_ITEMS = 200


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
	return quote if quote_name else None


@frappe.whitelist()
def get_quote_builder_options(quote_name=None, customer=None):
	saved_quote = require_quote_editor(quote_name, customer)
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
	return {
		"products": products,
		"sequencing_pricing_version": (
			(saved_quote.get("sequencing_pricing_version") or 0) if saved_quote is not None else 1
		),
	}


@frappe.whitelist()
def preview_quote_item(item, quote_name=None, customer=None):
	saved_quote = require_quote_editor(quote_name, customer)
	values = frappe.parse_json(item) if isinstance(item, str) else item
	if not isinstance(values, dict):
		frappe.throw(_("Provide one quotation item."))
	quote = frappe.new_doc("NGS Quote")
	quote.flags.sequencing_pricing_version = (
		(saved_quote.get("sequencing_pricing_version") or 0) if saved_quote is not None else 1
	)
	if saved_quote is not None:
		quote.flags.sequencing_pricing_rates = saved_quote.get("sequencing_pricing_rates")
	row = quote.append("items", {field: values[field] for field in ITEM_FIELDS if field in values and field != "pricing_details"})
	quote.apply_quote_rules()
	return {field: row.get(field) for field in ITEM_FIELDS}


@frappe.whitelist()
def preview_quote_items(items, quote_name=None, customer=None):
	"""Normalize and price an ephemeral quote; never mutate the saved document."""
	saved_quote = require_quote_editor(quote_name, customer)
	values = frappe.parse_json(items) if isinstance(items, str) else items
	if (
		not isinstance(values, list)
		or not 1 <= len(values) <= MAX_PREVIEW_ITEMS
		or any(not isinstance(item, dict) for item in values)
	):
		frappe.throw(_("Provide between 1 and {0} quotation item objects.").format(MAX_PREVIEW_ITEMS))
	for item in values:
		if "pricing_allocation_key" in item and (
			not isinstance(item["pricing_allocation_key"], str) or len(item["pricing_allocation_key"]) > 140
		):
			frappe.throw(_("Pricing allocation keys must be strings of at most 140 characters."))
	quote = frappe.new_doc("NGS Quote")
	version = (saved_quote.get("sequencing_pricing_version") or 0) if saved_quote is not None else 1
	quote.flags.sequencing_pricing_version = version
	if saved_quote is not None:
		quote.flags.sequencing_pricing_rates = saved_quote.get("sequencing_pricing_rates")
	for item in values:
		row = quote.append("items", {
			field: item[field] for field in ITEM_FIELDS if field in item and field != "pricing_details"
		})
		if "pricing_allocation_key" in item:
			# Tie-breaking identity only; never let a hint replace child-document metadata.
			row.pricing_allocation_key = item["pricing_allocation_key"]
	quote.apply_quote_rules()
	return {
		"items": [{field: row.get(field) for field in ITEM_FIELDS} for row in quote.items],
		"total_amount": sum(flt(row.amount) for row in quote.items),
		"sequencing_pricing_version": version,
	}
