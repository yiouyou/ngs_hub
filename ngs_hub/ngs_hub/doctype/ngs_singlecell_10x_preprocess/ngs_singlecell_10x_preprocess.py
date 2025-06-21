# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class NGSSingleCell10xPreprocess(Document):
	def before_insert(self):
		if not self.customer:
			frappe.throw(_("Customer is required to generate SingCell 10x Preprocess"))
		if not self.sample_transfer:
			frappe.throw(_("Sample Transfer is required to generate SingCell 10x Preprocess"))
		if not self.sample_info:
			frappe.throw(_("Sample Info is required to generate SingCell 10x Preprocess"))
		autoname = make_autoname(".YY.-.MM.-.#####")
		self.singlecell_10x_preprocess_id = f"SINGLECELL-SPL-PRE-{autoname}"
		self.name = self.singlecell_10x_preprocess_id
