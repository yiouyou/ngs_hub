from frappe import _


def get_data():
	return {
		"fieldname": "singlecell_10x_preprocess",
		"transactions": [
			{
				"label": _("Related SingleCell Library Construction"),
				"items": ["NGS SingleCell Library Construction"],
			}
		],
	}
