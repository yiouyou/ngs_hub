"""Read-only, permission-checked fallback for details absent from legacy orders."""

import frappe

DETAIL_FIELDS = (
	"species",
	"million_reads_per_sample",
	"read_depth",
	"confirmed_tbd_fee",
	"use_price_override",
	"override_unit_price",
	"add_on_sequencing",
)


def legacy_order_detail_fallbacks(order):
	legacy_items = [
		row
		for row in order.get("items", [])
		if not row.get("details_snapshot_version") and row.get("quote_item")
	]
	if not order.get("quote") or not legacy_items:
		return {}
	if not frappe.db.exists("NGS Quote", order.quote):
		return {}
	quote = frappe.get_doc("NGS Quote", order.quote)
	if quote.customer != order.customer or not quote.has_permission("read"):
		return {}
	quote_items = {row.name: row for row in quote.items}
	fallbacks = {}
	for row in legacy_items:
		source = quote_items.get(row.quote_item)
		if not source or source.project_type != row.project_type:
			continue
		fallbacks[row.name] = {
			"quote_item": row.quote_item,
			"project_type": row.project_type,
			"source": "Linked quote (legacy order)",
			**{field: source.get(field) for field in DETAIL_FIELDS},
		}
	return fallbacks
