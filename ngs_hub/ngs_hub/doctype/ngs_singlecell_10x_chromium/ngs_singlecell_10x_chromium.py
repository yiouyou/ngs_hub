# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class NGSSingleCell10xChromium(Document):
	def before_insert(self):
		if not self.customer:
			frappe.throw(_("Customer is required to generate SingCell 10x Chromium"))
		if not self.sample_transfer:
			frappe.throw(_("Sample Transfer is required to generate SingCell 10x Chromium"))
		if not self.sample_info:
			frappe.throw(_("Sample Info is required to generate SingCell 10x Chromium"))
		if not self.singlecell_10x_preprocess:
			frappe.throw(_("SingCell 10x Preprocess is required to generate SingCell 10x Chromium"))
		self.singlecell_10x_chromium_id = make_autoname("SINGLECELL-SPL-10x-.YY.-.MM.-.DD.-.###")
		self.name = self.singlecell_10x_chromium_id
