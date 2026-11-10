"""Read-only whole-order price preview for authorized Desk editors."""

import frappe
from frappe import _

from ngs_hub.api.desk_quote import require_quote_editor
from ngs_hub.api.quote_products import validate_number
from ngs_hub.api.sequencing_pricing import apply_document_sequencing, catalog_price


@frappe.whitelist()
def preview_order_sequencing(order_name, items):
	require_quote_editor()
	order = frappe.get_doc("NGS Order", order_name)
	order.check_permission("write")
	frappe.get_doc("NGS Customer", order.customer).check_permission("read")
	values = frappe.parse_json(items) if isinstance(items, str) else items
	if not isinstance(values, list) or len(values) > 200:
		frappe.throw(_("Provide the order item quantities."))
	known = {row.name: row for row in order.items}
	seen = set()
	rows = []
	for value in values:
		if not isinstance(value, dict) or value.get("name") not in known or value["name"] in seen:
			frappe.throw(_("Select items belonging to this order."))
		seen.add(value["name"])
		quantity = value.get("quantity")
		if isinstance(quantity, bool):
			frappe.throw(_("Quantity must be a number."))
		validate_number(quantity, _("Quantity"), positive=True)
		row = frappe.get_doc(known[value["name"]].as_dict())
		row.quantity = float(quantity)
		row.amount = row.quantity * row.unit_price
		rows.append(row)
	if order.get("sequencing_pricing_version"):
		try:
			apply_document_sequencing(
				rows,
				catalog_price,
				order.get("sequencing_pricing_rates"),
			)
		except (ValueError, ArithmeticError) as error:
			frappe.throw(_("Invalid whole-order sequencing pricing: {0}").format(str(error)))
	fields = ("name", "unit_price", "amount", "pricing_details", "notes")
	return {"items": [{field: row.get(field) for field in fields} for row in rows]}
