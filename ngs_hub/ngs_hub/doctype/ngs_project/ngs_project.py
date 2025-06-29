# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class NGSProject(Document):
	def before_insert(self):
		if not self.customer:
			frappe.throw(_("Customer is required to generate Project"))
		if not self.class_type:
			frappe.throw(_("Class Type is required to generate Project ID"))
		self.project_id = make_autoname(f"PRJ-{self.class_type}-.YY.-.MM.-.DD.-.###")
		self.name = self.project_id
