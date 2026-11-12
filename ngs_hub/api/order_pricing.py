"""Exact totals from the order's current stored financial facts, never its quote."""

from decimal import Decimal

from ngs_hub.api.sequencing_pricing import decimal_value


def order_total(order):
	return sum((decimal_value(row.get("amount")) for row in order.get("items", [])), Decimal(0))
