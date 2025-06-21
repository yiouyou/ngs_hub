from frappe import _


def get_data():
	return {
		"fieldname": "singlecell_library_construction",
		"transactions": [
			{
				"label": _("Related SingleCell Sequencing Outsource"),
				"items": ["NGS SingleCell Sequencing Outsource"],
			}
		],
	}
