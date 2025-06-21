from frappe import _


def get_data():
	return {
		"fieldname": "sample_transfer",
		"transactions": [{"label": _("Related Sample Info"), "items": ["NGS Sample Info"]}],
	}
