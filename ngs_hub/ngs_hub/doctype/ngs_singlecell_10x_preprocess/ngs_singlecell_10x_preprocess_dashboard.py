from frappe import _


def get_data():
	return {
		"fieldname": "singlecell_10x_preprocess",
		"transactions": [
			{"label": _("Related SingleCell 10x Chromium"), "items": ["NGS SingleCell 10x Chromium"]}
		],
	}
