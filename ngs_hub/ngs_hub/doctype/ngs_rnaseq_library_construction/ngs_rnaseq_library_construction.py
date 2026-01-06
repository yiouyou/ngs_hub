# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class NGSRNAseqLibraryConstruction(Document):
	def before_insert(self):
		if not self.customer:
			frappe.throw(_("Customer is required to generate RNAseq Library Construction"))
		if not self.project:
			frappe.throw(_("Project is required to generate RNAseq Library Construction"))
		if not self.sample_transfer:
			frappe.throw(_("Sample Transfer is required to generate RNAseq Library Construction"))
		if not self.sample_info:
			frappe.throw(_("Sample Info is required to generate RNAseq Library Construction"))
		if not self.rnaseq_sample_extraction:
			frappe.throw(_("RNAseq Sample Extraction is required to generate RNAseq Library Construction"))
		self.rnaseq_library_construction_id = make_autoname("RNAseq-LIB-.YY.-.MM.-.DD.-.###")
		self.name = self.rnaseq_library_construction_id
