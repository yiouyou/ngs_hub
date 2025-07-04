# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class NGSSampleInfo(Document):
	def before_insert(self):
		if not self.customer:
			frappe.throw(_("Customer is required to generate Sample Info"))
		if not self.project:
			frappe.throw(_("Project is required to generate Sample Info"))
		if not self.sample_transfer:
			frappe.throw(_("Sample Transfer is required to generate Sample Info"))
		if not self.class_type:
			frappe.throw(_("Class Type is required to generate Sample Info ID"))
		self.sample_info_id = make_autoname(f"SPL-{self.class_type}-.YY.-.MM.-.DD.-.###")
		self.name = self.sample_info_id
