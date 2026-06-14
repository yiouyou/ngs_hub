import frappe

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/ngs_account"
		raise frappe.Redirect
	if frappe.db.get_value("User", frappe.session.user, "user_type") == "System User":
		frappe.local.flags.redirect_location = "/app"
		raise frappe.Redirect
	context.title = "Athenomics Account"
