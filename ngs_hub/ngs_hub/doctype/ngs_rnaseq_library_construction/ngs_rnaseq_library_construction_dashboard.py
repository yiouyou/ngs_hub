from frappe import _


def get_data():
	return {
		"fieldname": "rnaseq_library_construction",
		"transactions": [
			{"label": _("Related RNAseq Sequencing Outsource"), "items": ["NGS RNAseq Sequencing Outsource"]}
		],
	}
