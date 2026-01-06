from frappe import _


def get_data():
	return {
		"fieldname": "dnaseq_library_construction",
		"transactions": [
			{"label": _("Related DNAseq Sequencing Outsource"), "items": ["NGS DNAseq Sequencing Outsource"]}
		],
	}
