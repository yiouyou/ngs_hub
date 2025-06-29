# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class NGSMetaSequencingOutsource(Document):
	def before_insert(self):
		if not self.customer:
			frappe.throw(_("Customer is required to generate Meta Sequencing Outsource"))
		if not self.sample_transfer:
			frappe.throw(_("Sample Transfer is required to generate Meta Sequencing Outsource"))
		if not self.sample_info:
			frappe.throw(_("Sample Info is required to generate Meta Sequencing Outsource"))
		if not self.meta_sample_extraction:
			frappe.throw(_("Meta Sample Extraction is required to generate Meta Sequencing Outsource"))
		if not self.meta_library_construction:
			frappe.throw(_("Meta Library Construction is required to generate Meta Sequencing Outsource"))
		self.meta_sequencing_outsource_id = make_autoname("Meta-SEQ-.YY.-.MM.-.DD.-.###")
		self.name = self.meta_sequencing_outsource_id
