# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class NGSRNAseqSampleExtraction(Document):
	def before_insert(self):
		if not self.customer:
			frappe.throw(_("Customer is required to generate RNAseq Sample Extraction"))
		if not self.sample_transfer:
			frappe.throw(_("Sample Transfer is required to generate RNAseq Sample Extraction"))
		if not self.sample_info:
			frappe.throw(_("Sample Info is required to generate RNAseq Sample Extraction"))
		self.rnaseq_sample_extraction_id = make_autoname("RNASEQ-SPL-EXT-.YY.-.MM.-.DD.-.####")
		self.name = self.rnaseq_sample_extraction_id
