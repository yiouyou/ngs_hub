import frappe

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/ngs_update_password"
		raise frappe.Redirect
	context.title = "Athenomics Password"
