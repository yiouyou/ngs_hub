from frappe import _


def get_data():
	return {
		"fieldname": "rnaseq_enrichment",
		"transactions": [
			{"label": _("Related RNAseq Library Construction"), "items": ["NGS RNAseq Library Construction"]}
		],
	}
