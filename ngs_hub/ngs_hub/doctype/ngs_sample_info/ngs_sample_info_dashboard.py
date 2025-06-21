from frappe import _


def get_data():
	return {
		"fieldname": "sample_info",
		"transactions": [
			{
				"label": _("Related Processes"),
				"items": [
					"NGS SingleCell 10x Preprocess",
					"NGS RNAseq Sample Extraction",
					"NGS Meta Sample Extraction",
				],
			}
		],
	}
