# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class NGSRNAseqSequencingOutsource(Document):
	def before_insert(self):
		if not self.customer:
			frappe.throw(_("Customer is required to generate RNAseq Sequencing Outsource"))
		if not self.project:
			frappe.throw(_("Project is required to generate RNAseq Sequencing Outsource"))
		if not self.sample_transfer:
			frappe.throw(_("Sample Transfer is required to generate RNAseq Sequencing Outsource"))
		if not self.sample_info:
			frappe.throw(_("Sample Info is required to generate RNAseq Sequencing Outsource"))
		# if not self.rnaseq_sample_extraction:
		# 	frappe.throw(_("RNAseq Sample Extraction is required to generate RNAseq Sequencing Outsource"))
		# if not self.rnaseq_library_construction:
		# 	frappe.throw(_("RNAseq Library Construction is required to generate RNAseq Sequencing Outsource"))
		self.rnaseq_sequencing_outsource_id = make_autoname("RNAseq-SEQ-.YY.-.MM.-.DD.-.###")
		self.name = self.rnaseq_sequencing_outsource_id
