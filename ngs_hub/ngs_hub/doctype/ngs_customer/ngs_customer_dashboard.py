from frappe import _


def get_data():
	return {
		"fieldname": "customer",
		"transactions": [{"label": _("Related Sample Transfers"), "items": ["NGS Sample Transfer"]}],
	}
