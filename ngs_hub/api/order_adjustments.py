"""Audited internal adjustments; quote documents and service specifications stay immutable."""

import json
from copy import deepcopy
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, localcontext
from html import escape

import frappe
from frappe import _

from ngs_hub.api.pricing_snapshot import create_pricing_snapshot, get_recorded_breakdown
from ngs_hub.api.sequencing_pricing import SEQUENCING_READS, decimal_value

CENT = Decimal("0.01")
STORAGE_QUANTUM = Decimal("0.000000001")
STORAGE_LIMIT = Decimal("1000000000000")
FEE_PREFIX = "Sample processing fee: "
_ADJUSTMENT_TOKEN = object()
_EDITABLE_FIELDS = {"name", "quantity", "use_price_override", "override_unit_price"}
_CONTROLLED_FIELDS = ("quantity", "use_price_override", "override_unit_price", "is_processing_fee")
_SPEC_FIELDS = (
	"quote_item", "service", "project_type", "sample_type", "description", "species", "read_depth",
	"million_reads_per_sample", "add_on_sequencing", "data_analysis", "onsite_service",
	"onsite_location", "onsite_address", "tissue_dissociation", "nuclei_extraction", "library_qc",
	"confirmed_tbd_fee", "details_snapshot_version", "is_processing_fee",
)
_PRICE_FIELDS = ("unit_price", "amount", "pricing_details")
_FLAG_FIELDS = {
	"use_price_override", "is_processing_fee", "add_on_sequencing", "onsite_service",
	"tissue_dissociation", "nuclei_extraction", "library_qc",
}
_NUMBER_FIELDS = {
	"quantity", "override_unit_price", "million_reads_per_sample", "confirmed_tbd_fee",
	"details_snapshot_version", "unit_price", "amount",
}


def require_order_adjuster():
	user = frappe.session.user
	if (
		not user or user == "Guest"
		or not frappe.db.get_value("User", user, "enabled")
		or frappe.db.get_value("User", user, "user_type") != "System User"
		or {"NGS Internal Staff", "System Manager"}.isdisjoint(frappe.get_roles(user))
	):
		frappe.throw(_("Only internal NGS staff can adjust orders."), frappe.PermissionError)


def _number(value, label, *, money=False):
	try:
		number = decimal_value(value)
		if number >= STORAGE_LIMIT:
			raise ValueError("Number exceeds the supported storage range.")
		if money and number != number.quantize(CENT, rounding=ROUND_HALF_UP):
			raise ValueError("Use at most two decimal places.")
		return number
	except (ValueError, ArithmeticError) as error:
		frappe.throw(_("Invalid {0}: {1}").format(label, str(error)))


def _stored_number(value, label):
	number = _number(value, label)
	if number != number.quantize(STORAGE_QUANTUM):
		frappe.throw(_("{0} supports at most nine decimal places.").format(label))
	return number


def _manual(item):
	return item.get("use_price_override") in (1, "1", True)


def is_controlled_adjustment(order):
	return order.flags.get("order_adjustment_token") is _ADJUSTMENT_TOKEN


def _is_fee(item):
	return (
		item.get("is_processing_fee") in (1, "1", True)
		and item.get("project_type") == "Custom Project"
		and not item.get("service") and not item.get("quote_item")
	)


def _total(items):
	return sum((_number(row.amount, "row amount") for row in items), Decimal(0)).quantize(
		CENT, rounding=ROUND_HALF_UP
	)


def _controlled_value(item, field):
	value = item.get(field)
	if field in _FLAG_FIELDS:
		if value is None or value == "":
			return 0
		if value not in (0, 1, "0", "1"):
			frappe.throw(_("Invalid order item flag: {0}").format(field))
		return int(value)
	if field not in _NUMBER_FIELDS:
		return "" if value is None else value
	if value is None or value == "":
		value = 0
	return _number(value, field).quantize(STORAGE_QUANTUM, rounding=ROUND_HALF_UP)


def validate_controlled_changes(order, previous, quote=None):
	"""An unforgeable, process-local token distinguishes this API from posted doc flags."""
	previous_sales_order = previous.get("erpnext_sales_order") if previous else None
	if (order.get("erpnext_sales_order") or "") != (previous_sales_order or ""):
		frappe.throw(_("The ERPNext Sales Order link is managed by synchronization."), frappe.PermissionError)
	if previous:
		if any((order.get(field) or "") != (previous.get(field) or "") for field in ("customer", "quote")):
			frappe.throw(_("The customer and quote of a saved order cannot be changed."))
		old = {row.name: row for row in previous.items}
		controlled = is_controlled_adjustment(order)
		if not controlled and (
			len(order.items) != len(old) or {row.name for row in order.items} != set(old)
		):
			frappe.throw(_("Use Adjust Order and provide a reason to add or remove order rows."))
		fields = _SPEC_FIELDS if controlled else (*_SPEC_FIELDS, *_CONTROLLED_FIELDS, *_PRICE_FIELDS)
		for row in order.items:
			if row.name not in old:
				if not _is_fee(row):
					frappe.throw(_("Only an explicit processing fee may be added to a saved order."))
				continue
			if any(_controlled_value(row, field) != _controlled_value(old[row.name], field) for field in fields):
				frappe.throw(_("Saved order billing and specifications cannot be changed directly. Use Adjust Order for quantities and manual prices."))
		return
	# New portal orders legitimately carry the quote's existing override fields.
	if quote and quote.get("customer") != order.customer:
		frappe.throw(_("The quote must belong to the order customer."), frappe.PermissionError)
	quoted = {row.name: row for row in quote.get("items", [])} if quote else {}
	for row in order.items:
		source = quoted.get(row.get("quote_item"))
		if row.get("quote_item") and not source:
			frappe.throw(_("Every quoted order row must belong to the order's quote."), frappe.PermissionError)
		if not _manual(row):
			continue
		if not source or not _manual(source) or (
			_number(row.get("override_unit_price"), "manual unit price")
			!= _number(source.get("override_unit_price"), "quoted manual unit price")
		):
			frappe.throw(_("New orders may only inherit manual prices from their quote."), frappe.PermissionError)


def record_nonsequencing_pricing(item, previous=None):
	"""Keep recorded component rates and make any difference an explicit adjustment."""
	if item.get("project_type") == SEQUENCING_READS:
		return
	changed = previous is None or any(
		item.get(field) != previous.get(field)
		for field in (*_CONTROLLED_FIELDS, "unit_price", "amount")
	)
	if not changed:
		return
	quantity = _number(item.quantity, "quantity")
	components = get_recorded_breakdown(previous) if previous else None
	if components:
		ratio = quantity / _number(previous.quantity, "previous quantity")
		components = deepcopy(components)
		for part in components:
			part["quantity"] = float(Decimal(str(part["quantity"])) * ratio)
			if part["amount"] is not None:
				part["amount"] = float(Decimal(str(part["unit_price"])) * Decimal(str(part["quantity"])))
	else:
		base = _number(previous.unit_price if previous else item.unit_price, "recorded unit price")
		components = [{
			"label": "Recorded service price" if previous else item.description or "Manual service price",
			"quantity": float(quantity), "unit_price": float(base), "amount": float(base * quantity),
		}]
	if _manual(item):
		components = [part for part in components if part["amount"] is not None]
	amount = _number(item.amount, "row amount")
	base_amount = sum((Decimal(str(part["amount"])) for part in components if part["amount"] is not None), Decimal(0))
	difference = amount - base_amount
	if difference or _manual(item):
		components.append({
			"label": "Manual unit price adjustment" if _manual(item) else "Recorded price adjustment",
			"quantity": float(quantity), "unit_price": float(difference / quantity), "amount": float(difference),
		})
	try:
		item.pricing_details = create_pricing_snapshot(item, components)
	except ValueError as error:
		frappe.throw(_("Invalid adjusted pricing facts: {0}").format(str(error)))


def _patch_items(order, items):
	if items is None:
		return
	try:
		values = frappe.parse_json(items) if isinstance(items, str) else items
	except (ValueError, TypeError):
		frappe.throw(_("Provide order item adjustments as a list."))
	if not isinstance(values, list) or len(values) > 200:
		frappe.throw(_("Provide at most 200 order item adjustments."))
	known = {row.name: row for row in order.items}
	seen = set()
	for value in values:
		if (
			not isinstance(value, dict) or set(value) - _EDITABLE_FIELDS
			or not isinstance(value.get("name"), str)
			or value["name"] not in known or value["name"] in seen
		):
			frappe.throw(_("Only quantities and manual prices of this order's saved rows may be adjusted."))
		seen.add(value["name"])
		row = known[value["name"]]
		if "quantity" in value:
			quantity = _stored_number(value["quantity"], "quantity")
			if not quantity:
				order.remove(row)
				continue
			row.quantity = float(quantity)
		if "use_price_override" in value:
			flag = value["use_price_override"]
			if not isinstance(flag, int | str | bool) or flag not in (0, 1, "0", "1"):
				frappe.throw(_("Manual unit price must be enabled or disabled."))
			row.use_price_override = int(flag)
		if "override_unit_price" in value:
			row.override_unit_price = float(_stored_number(value["override_unit_price"], "manual unit price"))
			if not _manual(row) and row.override_unit_price:
				frappe.throw(_("Enable Manual Unit Price to set a manual price."))
		if _manual(row):
			_number(row.get("override_unit_price"), "manual unit price")


def allocate_final_total(items, final_total):
	"""Largest remainders allocate exact cents; processing fees are fixed, not discounted."""
	target = _number(final_total, "final total", money=True)
	fixed = _total([row for row in items if _is_fee(row)])
	if target < fixed:
		frappe.throw(_("Final total cannot be less than the explicit processing fees."))
	rows = [row for row in items if not _is_fee(row)]
	if not rows:
		if target != fixed:
			frappe.throw(_("No service rows remain to allocate the final total."))
		return
	with localcontext() as context:
		context.prec = 40
		weights = [_number(row.amount, "row amount") for row in rows]
		if not sum(weights):
			weights = [_number(row.quantity, "quantity") for row in rows]
		shares = [(target - fixed) * weight / sum(weights) for weight in weights]
		amounts = [value.quantize(CENT, rounding=ROUND_FLOOR) for value in shares]
		remaining = int((target - fixed - sum(amounts)) / CENT)
		indices = sorted(range(len(rows)), key=lambda i: (-(shares[i] - amounts[i]), rows[i].name))
		for index in indices[:remaining]:
			amounts[index] += CENT
		for row, amount in zip(rows, amounts, strict=True):
			quantity = _number(row.quantity, "quantity")
			rate = (amount / quantity).quantize(STORAGE_QUANTUM, rounding=ROUND_HALF_UP)
			if (rate * quantity).quantize(CENT, rounding=ROUND_HALF_UP) != amount:
				frappe.throw(_("The final total cannot be represented at stored unit-price precision."))
			_number(rate, "allocated unit price")
			row.use_price_override = 1
			row.override_unit_price = float(rate)


def _audit_state(order):
	fields = ("name", "quote_item", "project_type", "description", "quantity", "is_processing_fee", "use_price_override",
		"override_unit_price", "unit_price", "amount", "pricing_details")
	return {"total_amount": float(_total(order.items)), "items": [
		{field: row.get(field) for field in fields} for row in order.items
	]}


@frappe.whitelist(methods=["POST"])
def adjust_order(order_name, items=None, final_total=None, processing_fee=None, processing_description=None, reason=None):
	require_order_adjuster()
	if not isinstance(order_name, str) or not order_name.strip():
		frappe.throw(_("A saved order is required."))
	if not isinstance(reason, str) or not reason.strip():
		frappe.throw(_("An adjustment reason is required."))
	reason = reason.strip()
	order = frappe.get_doc("NGS Order", order_name, for_update=True)
	order.check_permission("write")
	frappe.get_doc("NGS Customer", order.customer).check_permission("read")
	if order.docstatus != 0 or order.status == "Cancelled":
		frappe.throw(_("Only non-cancelled draft orders can be adjusted."))
	if order.get("erpnext_sales_order"):
		status = frappe.db.get_value("Sales Order", order.erpnext_sales_order, "docstatus", for_update=True)
		if status is None or status != 0:
			frappe.throw(_("Financial changes require a draft linked ERPNext Sales Order."))
	fee = _number(processing_fee, "processing fee", money=True) if processing_fee is not None else Decimal(0)
	if processing_description is not None and not isinstance(processing_description, str):
		frappe.throw(_("Processing description must be text."))
	before = _audit_state(order)
	order.load_doc_before_save(raise_exception=True)
	order.flags.order_adjustment_token = _ADJUSTMENT_TOKEN
	try:
		_patch_items(order, items)
		if fee:
			order.append("items", {
				"project_type": "Custom Project", "quantity": 1,
				"description": FEE_PREFIX + ((processing_description or "").strip() or reason),
				"use_price_override": 1, "override_unit_price": float(fee),
				"unit_price": float(fee), "amount": float(fee), "details_snapshot_version": 1,
				"is_processing_fee": 1,
			})
		if not order.items:
			frappe.throw(_("Retain a service row or add an explicit processing fee."))
		# Normalize once after row edits so final-total weights use actual, pooled pricing.
		order.validate()
		if final_total is not None:
			allocate_final_total(order.items, final_total)
		order.save()
		if final_total is not None and _total(order.items) != _number(final_total, "final total", money=True):
			frappe.throw(_("Adjusted prices cannot represent the requested final total exactly."))
		after = _audit_state(order)
		payload = json.dumps({"reason": reason, "actor": frappe.session.user, "before": before, "after": after},
			allow_nan=False, default=str, separators=(",", ":"))
		order.add_comment("Comment", "Order pricing adjustment<pre>" + escape(payload) + "</pre>")
		return {"name": order.name, **after}
	finally:
		order.flags.pop("order_adjustment_token", None)
