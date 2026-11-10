"""Validation shared by customer self-service and administrator edits."""

import frappe
from frappe import _
from frappe.utils import validate_email_address


def normalize_invoice_email_address(value):
	if value is None:
		return ""
	if isinstance(value, str):
		email = value.strip()
		if not email:
			return ""
		if len(email) <= 140 and not any(char in email for char in ",;\r\n"):
			if validate_email_address(email) == email:
				return email
	frappe.throw(
		_("Invoice email address must be a single valid email address."),
		frappe.InvalidEmailAddressError,
	)
