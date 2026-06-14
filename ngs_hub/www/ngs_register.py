import frappe

from ngs_hub.api.portal import get_current_customer

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/ngs_register"
		raise frappe.Redirect
	if frappe.db.get_value("User", frappe.session.user, "user_type") == "System User":
		frappe.local.flags.redirect_location = "/app"
		raise frappe.Redirect
	context.has_customer = bool(get_current_customer())
	context.title = "Athenomics Profile"
