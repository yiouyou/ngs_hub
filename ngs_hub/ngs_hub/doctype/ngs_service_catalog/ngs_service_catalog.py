# Copyright (c) 2026, sz and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class NGSServiceCatalog(Document):
	def validate(self):
		if not self.service_code:
			frappe.throw(_("Service Code is required"))
		if not self.service_name:
			frappe.throw(_("Service Name is required"))
		if self.unit_price is not None and self.unit_price < 0:
			frappe.throw(_("Unit Price cannot be negative"))
