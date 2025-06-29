# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class NGSSingleCellLibraryConstruction(Document):
	def before_insert(self):
		if not self.customer:
			frappe.throw(_("Customer is required to generate SingCell Library Construction"))
		if not self.project:
			frappe.throw(_("Project is required to generate SingCell Library Construction"))
		if not self.sample_transfer:
			frappe.throw(_("Sample Transfer is required to generate SingCell Library Construction"))
		if not self.sample_info:
			frappe.throw(_("Sample Info is required to generate SingCell Library Construction"))
		if not self.singlecell_10x_preprocess:
			frappe.throw(_("SingCell 10x Preprocess is required to generate SingCell Library Construction"))
		if not self.singlecell_10x_chromium:
			frappe.throw(_("SingCell 10x Chromium is required to generate SingCell Library Construction"))
		self.singlecell_library_construction_id = make_autoname("SINGLECELL-SPL-LIB-.YY.-.MM.-.DD.-.###")
		self.name = self.singlecell_library_construction_id
