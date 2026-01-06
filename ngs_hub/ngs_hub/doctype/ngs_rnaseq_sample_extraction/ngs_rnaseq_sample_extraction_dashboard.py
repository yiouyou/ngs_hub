from frappe import _


def get_data():
	return {
		"fieldname": "rnaseq_sample_extraction",
		"transactions": [
			{
				"label": _("Related RNAseq Library Construction"),
				"items": ["Related RNAseq Library Construction"],
			}
		],
	}
