import frappe

from ngs_hub.api.portal import get_current_customer

no_cache = 1


def get_context(context):
	context.is_guest = frappe.session.user == "Guest"
	if not context.is_guest and frappe.db.get_value("User", frappe.session.user, "user_type") == "System User":
		frappe.local.flags.redirect_location = "/app"
		raise frappe.Redirect
	context.has_customer = False if context.is_guest else bool(get_current_customer())
	context.title = "Athenomics Registration" if context.is_guest else "Athenomics Profile"
