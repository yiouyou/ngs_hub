# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class NGSSampleTransfer(Document):
	def before_insert(self):
		if not self.customer:
			frappe.throw(_("Customer is required to generate Sample Transfer"))
		if not self.project:
			frappe.throw(_("Project is required to generate Sample Transfer"))
		if not self.class_type:
			frappe.throw(_("Class Type is required to generate Sample Transfer ID"))
		if not self.mode:
			frappe.throw(_("Mode is required to generate Sample Transfer ID"))
		self.sample_transfer_id = make_autoname(f"TRF-{self.class_type}-{self.mode}-.YY.-.MM.-.DD.-.###")
		self.name = self.sample_transfer_id
