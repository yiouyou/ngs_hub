from frappe import _


def get_data():
	return {
		"fieldname": "sample_info",
		"transactions": [
			{
				"label": _("Related Processes"),
				"items": [
					"NGS SingleCell 10x Preprocess",
					"NGS SingleCell Library Construction",
					"NGS SingleCell Sequencing Outsource",
					"NGS RNAseq Sample Extraction",
					"NGS RNAseq Library Construction",
					"NGS RNAseq Sequencing Outsource",
					"NGS DNAseq Sample Extraction",
					"NGS DNAseq Library Construction",
					"NGS DNAseq Sequencing Outsource",
					"NGS Meta Sample Extraction",
					"NGS Meta Library Construction",
					"NGS Meta Sequencing Outsource",
				],
			}
		],
	}
