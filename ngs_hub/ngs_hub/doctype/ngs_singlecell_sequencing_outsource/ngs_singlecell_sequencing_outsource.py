# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class NGSSingleCellSequencingOutsource(Document):
	def before_insert(self):
		if not self.customer:
			frappe.throw(_("Customer is required to generate SingCell Sequencing Outsource"))
		if not self.sample_transfer:
			frappe.throw(_("Sample Transfer is required to generate SingCell Sequencing Outsource"))
		if not self.sample_info:
			frappe.throw(_("Sample Info is required to generate SingCell Sequencing Outsource"))
		if not self.singlecell_10x_preprocess:
			frappe.throw(_("SingCell 10x Preprocess is required to generate SingCell Sequencing Outsource"))
		if not self.singlecell_10x_chromium:
			frappe.throw(_("SingCell 10x Chromium is required to generate SingCell Sequencing Outsource"))
		if not self.singlecell_library_construction:
			frappe.throw(
				_("SingCell Library Construction is required to generate SingCell Sequencing Outsource")
			)
		autoname = make_autoname(".YY.-.MM.-.#####")
		self.singlecell_sequencing_outsource_id = f"SINGLECELL-SPL-SEQ-{autoname}"
		self.name = self.singlecell_sequencing_outsource_id
