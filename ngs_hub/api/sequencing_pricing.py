"""Whole-order progressive sequencing fees, allocated by each line's read count."""

import json
import math
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, localcontext

SEQUENCING_READS = "Sequencing Only - 1M Reads"
SMALL_FRAGMENT_DNA = "DNA Small Fragment Library Prep & Sequencing"
TIER_CODES = (
	"SEQ_ONLY_1M_READS",
	"SEQ_ONLY_1M_READS_201_600",
	"SEQ_ONLY_1M_READS_601_1200",
	"SEQ_ONLY_1M_READS_OVER_1200",
)
TIER_CAPACITIES = (Decimal(200), Decimal(400), Decimal(600), None)
TIER_LABELS = ("first 200M", "next 400M (200-600M)", "next 600M (600-1200M)", "above 1200M")
CENT = Decimal("0.01")


def catalog_price(identifier):
	"""Resolve stable service codes, while accepting document names in legacy callers."""
	import frappe

	value = frappe.db.get_value("NGS Service Catalog", {"service_code": identifier}, "unit_price")
	if value is None:
		value = frappe.db.get_value("NGS Service Catalog", identifier, "unit_price")
	return value


def decimal_value(value, *, positive=False):
	if isinstance(value, bool):
		raise ValueError("Expected a finite nonnegative number.")
	try:
		number = Decimal(str(value))
	except (ValueError, ArithmeticError):
		raise ValueError("Expected a finite nonnegative number.") from None
	if not number.is_finite() or number < 0 or (positive and number == 0):
		raise ValueError(
			"Expected a finite positive number." if positive else "Expected a finite nonnegative number."
		)
	if not math.isfinite(float(number)):
		raise ValueError("Number is outside the supported range.")
	return number


def progressive_bands(total_reads, rates):
	remaining = decimal_value(total_reads)
	if len(rates) != 4:
		raise ValueError("Four sequencing tier rates are required.")
	result = []
	with localcontext() as context:
		context.prec = 40
		for capacity, label, rate in zip(TIER_CAPACITIES, TIER_LABELS, rates, strict=True):
			rate = decimal_value(rate)
			reads = min(remaining, capacity) if capacity is not None else remaining
			if reads:
				amount = (reads * rate).quantize(CENT, rounding=ROUND_HALF_UP)
				decimal_value(amount)
				result.append({"label": label, "reads": reads, "rate": rate, "amount": amount})
			remaining -= reads
	return result


def _allocate_cents(amount, weights, keys):
	"""Largest remainders preserve the band total and do not depend on row order."""
	total = sum(weights)
	shares = [amount * weight / total for weight in weights]
	allocated = [share.quantize(CENT, rounding=ROUND_FLOOR) for share in shares]
	remaining = int((amount - sum(allocated)) / CENT)
	order = sorted(range(len(weights)), key=lambda i: (-(shares[i] - allocated[i]), keys[i]))
	for index in order[:remaining]:
		allocated[index] += CENT
	return allocated


def allocate_sequencing(items, rates, qc_fee):
	"""Return one result per input sequencing line; overrides remain explicit adjustments.

	All lines, including manual-price lines, contribute reads to the order tiers.
	Each band's cents are shared in proportion to reads; QC is per library.
	"""
	if not items:
		return []
	with localcontext() as context:
		context.prec = 40
		quantities = [decimal_value(item.get("quantity"), positive=True) for item in items]
		reads = [decimal_value(item.get("million_reads_per_sample"), positive=True) for item in items]
		weights = [quantity * value for quantity, value in zip(quantities, reads, strict=True)]
		total_reads = sum(weights)
		decimal_value(total_reads, positive=True)
		qc_fee = decimal_value(qc_fee)
		bands = progressive_bands(total_reads, rates)
		keys = [
			(
				str(reads[i]),
				str(quantities[i]),
				str(int(item.get("use_price_override") in (1, "1", True))),
				str(item.get("override_unit_price") or 0),
				str(item.get("pricing_allocation_key") or item.get("name") or ""),
			)
			for i, item in enumerate(items)
		]
		allocations = [_allocate_cents(band["amount"], weights, keys) for band in bands]
		results = []
		for index, (item, quantity) in enumerate(zip(items, quantities, strict=True)):
			components = []
			for band, allocated in zip(bands, allocations, strict=True):
				amount = allocated[index]
				components.append(
					{
						"label": f"Sequencing - order {band['label']} at ${band['rate']:g}/M (reads-proportional share)",
						"unit_price": float(amount / quantity),
						"quantity": float(quantity),
						"amount": float(amount),
					}
				)
			qc = (quantity * qc_fee).quantize(CENT, rounding=ROUND_HALF_UP)
			components.append(
				{
					"label": "Library QC",
					"unit_price": float(qc / quantity),
					"quantity": float(quantity),
					"amount": float(qc),
				}
			)
			amount = sum(Decimal(str(part["amount"])) for part in components)
			if item.get("use_price_override") in (1, "1", True):
				override = decimal_value(item.get("override_unit_price"))
				overridden_amount = (override * quantity).quantize(CENT, rounding=ROUND_HALF_UP)
				adjustment = overridden_amount - amount
				if adjustment:
					components.append(
						{
							"label": "Manual unit price adjustment",
							"unit_price": float(adjustment / quantity),
							"quantity": float(quantity),
							"amount": float(adjustment),
						}
					)
				amount = overridden_amount
			unit_price = (
				decimal_value(item.get("override_unit_price"))
				if item.get("use_price_override") in (1, "1", True)
				else amount / quantity
			)
			decimal_value(amount)
			notes = (
				f"Order-wide Sequencing Only: {total_reads:g}M reads; progressive bands; allocated by reads.\n"
				"Library QC added."
			)
			if item.get("use_price_override") in (1, "1", True):
				notes += f"\nManual unit price: ${item.get('override_unit_price')}."
			results.append(
				{
					"unit_price": float(unit_price),
					"amount": float(amount),
					"rule_notes": notes,
					"components": components,
				}
			)
		return results


def apply_document_sequencing(items, price_getter, pricing_parameters=None):
	"""Apply canonical totals after normalization; only per-read sequencing rows participate."""
	from ngs_hub.api.pricing_snapshot import create_pricing_snapshot

	rows = [item for item in items if item.get("project_type") == SEQUENCING_READS]
	if not rows:
		return pricing_parameters
	if pricing_parameters:
		parameters = json.loads(pricing_parameters)
		if (
			not isinstance(parameters, dict)
			or set(parameters) != {"version", "rates", "qc_fee"}
			or type(parameters["version"]) is not int
			or parameters["version"] != 1
			or not isinstance(parameters["rates"], list)
			or len(parameters["rates"]) != 4
		):
			raise ValueError("Invalid recorded sequencing rates.")
		rates = parameters["rates"]
		qc_fee = parameters["qc_fee"]
	else:
		rates = [price_getter(code) for code in TIER_CODES]
		qc_fee = price_getter("LIBRARY_QC")
	results = allocate_sequencing(rows, rates, qc_fee)
	for row, result in zip(rows, results, strict=True):
		row.unit_price = result["unit_price"]
		row.amount = result["amount"]
		row.library_qc = 1
		row.add_on_sequencing = 0
		row.rule_notes = result["rule_notes"]
		if row.get("doctype") == "NGS Order Item":
			row.notes = result["rule_notes"]
		row.pricing_details = create_pricing_snapshot(row, result["components"])
	return json.dumps(
		{
			"version": 1,
			"rates": [float(decimal_value(rate)) for rate in rates],
			"qc_fee": float(decimal_value(qc_fee)),
		},
		allow_nan=False,
		separators=(",", ":"),
	)
