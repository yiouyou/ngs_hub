# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class NGSMetaLibraryConstruction(Document):
	def before_insert(self):
		if not self.customer:
			frappe.throw(_("Customer is required to generate Meta Library Construction"))
		if not self.project:
			frappe.throw(_("Project is required to generate Meta Library Construction"))
		if not self.sample_transfer:
			frappe.throw(_("Sample Transfer is required to generate Meta Library Construction"))
		if not self.sample_info:
			frappe.throw(_("Sample Info is required to generate Meta Library Construction"))
		if not self.meta_sample_extraction:
			frappe.throw(_("Meta Sample Extraction is required to generate Meta Library Construction"))
		self.meta_library_construction_id = make_autoname("META-SPL-LIB-.YY.-.MM.-.DD.-.###")
		self.name = self.meta_library_construction_id
