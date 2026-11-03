"""Validated, database-free pricing facts for saved quote and order items."""

import json
import math
from decimal import Decimal

__all__ = ["create_pricing_snapshot", "get_recorded_breakdown"]

_TEXT_FIELDS = (
	"project_type",
	"service",
	"sample_type",
	"description",
	"species",
	"read_depth",
	"data_analysis",
	"onsite_location",
	"onsite_address",
)
_FLAG_FIELDS = (
	"onsite_service",
	"tissue_dissociation",
	"nuclei_extraction",
	"library_qc",
	"add_on_sequencing",
	"use_price_override",
)
_NUMBER_FIELDS = ("million_reads_per_sample", "confirmed_tbd_fee", "override_unit_price")
_SPEC_FIELDS = frozenset((*_TEXT_FIELDS, *_FLAG_FIELDS, *_NUMBER_FIELDS))
_SNAPSHOT_FIELDS = {"version", "spec", "quantity", "unit_price", "amount", "components"}
_COMPONENT_FIELDS = {"label", "unit_price", "quantity", "amount"}
_TOLERANCE = Decimal("0.005")
_SPEC_TOLERANCE = Decimal("0.000001")


def _get(item, field):
	getter = getattr(item, "get", None)
	if not callable(getter):
		raise ValueError("Item must expose saved fields through get().")
	return getter(field)


def _number(value, *, item_value=False):
	if isinstance(value, bool) or not isinstance(value, (int, float, str) if item_value else (int, float)):
		raise ValueError("Expected a finite number.")
	try:
		result = float(value)
	except (ValueError, OverflowError) as exc:
		raise ValueError("Expected a finite number.") from exc
	if not math.isfinite(result):
		raise ValueError("Expected a finite number.")
	return result


def _nonnegative(value, *, item_value=False):
	value = _number(value, item_value=item_value)
	if value < 0:
		raise ValueError("Expected a nonnegative number.")
	return value


def _positive(value, *, item_value=False):
	value = _number(value, item_value=item_value)
	if value <= 0:
		raise ValueError("Quantity must be positive.")
	return value


def _matches(left, right, tolerance=_TOLERANCE):
	return abs(Decimal(str(left)) - Decimal(str(right))) <= tolerance


def _spec(item):
	spec = {}
	for field in _TEXT_FIELDS:
		value = _get(item, field)
		if value is not None and not isinstance(value, str):
			raise ValueError(f"{field} must be text.")
		spec[field] = (value or "").strip()
	spec["species"] = spec["species"].casefold()
	for field in _FLAG_FIELDS:
		value = _get(item, field)
		if value is None or value == "":
			value = 0
		if not isinstance(value, bool | int | float | str) or value not in (0, 1, "0", "1"):
			raise ValueError(f"{field} must be a flag.")
		spec[field] = int(value)
	for field in _NUMBER_FIELDS:
		value = _get(item, field)
		value = _number(0 if value is None or value == "" else value, item_value=True)
		# Disabled controls can retain stale negative UI values, but not malformed numbers.
		ignored = (
			(field == "override_unit_price" and not spec["use_price_override"])
			or (field == "confirmed_tbd_fee" and not spec["onsite_service"])
		)
		spec[field] = 0 if ignored else _nonnegative(value)

	depth = spec["read_depth"].lower()
	if depth.endswith("m"):
		reads = _positive(depth[:-1].strip(), item_value=True)
		saved_reads = spec["million_reads_per_sample"]
		if saved_reads and not _matches(saved_reads, reads, _SPEC_TOLERANCE):
			raise ValueError("Read depth conflicts with saved million reads.")
		# M-depth is redundant with the explicit read count synthesized onto orders.
		spec["million_reads_per_sample"] = reads
		spec["read_depth"] = ""
	elif depth and (depth.endswith("x") or spec["project_type"] == "WGS"):
		coverage = _positive(depth.removesuffix("x").strip(), item_value=True)
		spec["read_depth"] = f"{str(coverage).removesuffix('.0')}x"
	return spec


def _prices(item, *, saved=False):
	quantity = _positive(_get(item, "quantity"), item_value=not saved)
	unit_price = _nonnegative(_get(item, "unit_price"), item_value=not saved)
	amount = _nonnegative(_get(item, "amount"), item_value=not saved)
	if not _matches(_number(quantity * unit_price), amount):
		raise ValueError("Saved amount does not match unit price and quantity.")
	return quantity, unit_price, amount


def _components(components, amount):
	if not isinstance(components, list) or not components:
		raise ValueError("Components must be a nonempty list.")
	rows = []
	for component in components:
		if not isinstance(component, dict):
			raise ValueError("Each component must be an object.")
		keys = set(component)
		if not _COMPONENT_FIELDS <= keys or keys - _COMPONENT_FIELDS - {"display"}:
			raise ValueError("Invalid component fields.")
		label = component["label"]
		if not isinstance(label, str) or not label.strip():
			raise ValueError("Each component needs a label.")
		quantity = _positive(component["quantity"])
		unit_price, row_amount = component["unit_price"], component["amount"]
		row = {"label": label, "unit_price": unit_price, "quantity": quantity, "amount": row_amount}
		if unit_price is None or row_amount is None:
			if unit_price is not None or row_amount is not None or component.get("display") != "TBD":
				raise ValueError("Unpriced components must have null prices and display TBD.")
			row["display"] = "TBD"
		else:
			row["unit_price"] = _number(unit_price)
			row["amount"] = _number(row_amount)
			if "display" in component or not _matches(_number(row["unit_price"] * quantity), row["amount"]):
				raise ValueError("Component amount does not match its saved rate and quantity.")
		rows.append(row)
	total = _number(math.fsum(row["amount"] for row in rows if row["amount"] is not None))
	if not _matches(total, amount):
		raise ValueError("Components do not sum to the saved amount.")
	return rows


def create_pricing_snapshot(item, components) -> str:
	"""Serialize supplied facts; raise ValueError rather than inventing missing prices."""
	try:
		spec = _spec(item)
		quantity, unit_price, amount = _prices(item)
		rows = _components(components, amount)
		return json.dumps(
			{
				"version": 1,
				"spec": spec,
				"quantity": quantity,
				"unit_price": unit_price,
				"amount": amount,
				"components": rows,
			},
			allow_nan=False,
			separators=(",", ":"),
		)
	except (TypeError, OverflowError, RecursionError) as exc:
		raise ValueError("Invalid pricing facts.") from exc


def _unique_object(pairs):
	result = {}
	for key, value in pairs:
		if key in result:
			raise ValueError("Duplicate JSON field.")
		result[key] = value
	return result


def _invalid_constant(value):
	raise ValueError(f"Invalid JSON number: {value}")


def _saved_spec(value):
	if not isinstance(value, dict) or set(value) != _SPEC_FIELDS:
		raise ValueError("Invalid saved specification fields.")
	for field in _TEXT_FIELDS:
		if not isinstance(value[field], str):
			raise ValueError("Invalid saved specification text.")
	for field in _FLAG_FIELDS:
		if type(value[field]) is not int or value[field] not in (0, 1):
			raise ValueError("Invalid saved specification flag.")
	for field in _NUMBER_FIELDS:
		_nonnegative(value[field])
	return _spec(value)


def _spec_matches(saved, item):
	current = _spec(item)
	# Both read representations must independently stay within storage precision.
	reads = _get(item, "million_reads_per_sample")
	if reads is not None and reads != "":
		reads = _nonnegative(reads, item_value=True)
		if reads and not _matches(saved["million_reads_per_sample"], reads, _SPEC_TOLERANCE):
			return False
	return all(saved[field] == current[field] for field in (*_TEXT_FIELDS, *_FLAG_FIELDS)) and all(
		_matches(saved[field], current[field], _SPEC_TOLERANCE) for field in _NUMBER_FIELDS
	)


def get_recorded_breakdown(item) -> list | None:
	"""Return compatible saved rows, or None so callers can use their legacy fallback.

	Quantity changes retain saved component rates, never a current catalog tier.
	Labels are plain saved text, including any existing manual adjustment label;
	consumers must escape them. This validates consistency, not authenticity.
	"""
	try:
		raw = _get(item, "pricing_details")
		if not isinstance(raw, str) or not raw:
			return None
		snapshot = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
		if not isinstance(snapshot, dict) or set(snapshot) != _SNAPSHOT_FIELDS:
			return None
		if type(snapshot["version"]) is not int or snapshot["version"] != 1:
			return None
		if not _spec_matches(_saved_spec(snapshot["spec"]), item):
			return None
		saved_quantity, saved_unit, saved_amount = _prices(snapshot, saved=True)
		rows = _components(snapshot["components"], saved_amount)
		quantity, unit_price, amount = _prices(item)
		if not _matches(saved_unit, unit_price):
			return None
		if saved_quantity == quantity:
			if not _matches(saved_amount, amount):
				return None
		else:
			ratio = _positive(quantity / saved_quantity)
			for row in rows:
				row["quantity"] = _positive(row["quantity"] * ratio)
				if row["amount"] is not None:
					row["amount"] = _number(row["unit_price"] * row["quantity"])
		return _components(rows, amount)
	except (ValueError, TypeError, AttributeError, OverflowError, RecursionError):
		return None
