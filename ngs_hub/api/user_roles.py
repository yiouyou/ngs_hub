import frappe
from frappe import _


NGS_EXTERNAL_CUSTOMER_ROLE = "NGS External Customer"
PROTECTED_CUSTOMER_ROLES = {"Administrator", "System Manager", "NGS Internal Staff"}


def _remove_extra_user_roles(user_name):
	extra_roles = frappe.get_all(
		"Has Role",
		filters={
			"parent": user_name,
			"parenttype": "User",
			"parentfield": "roles",
			"role": ["!=", NGS_EXTERNAL_CUSTOMER_ROLE],
		},
		pluck="name",
	)
	if extra_roles:
		frappe.db.delete("Has Role", {"name": ["in", extra_roles]})


def ensure_ngs_external_customer_user(user, save=False):
	if isinstance(user, str):
		user = frappe.get_doc("User", user)

	current_roles = {row.role for row in user.roles}
	if user.name == "Administrator" or current_roles.intersection(PROTECTED_CUSTOMER_ROLES):
		frappe.throw(
			_("User {0} has internal roles and cannot be converted to an external NGS customer.").format(
				user.name
			)
		)

	if not frappe.db.exists("Role", NGS_EXTERNAL_CUSTOMER_ROLE):
		frappe.throw(_("NGS External Customer role is not configured."))

	user.set("roles", [])
	user.append("roles", {"role": NGS_EXTERNAL_CUSTOMER_ROLE})
	user.role_profile_name = None
	user.user_type = "Website User"

	if save:
		user.save(ignore_permissions=True)
		_remove_extra_user_roles(user.name)
		frappe.db.set_value(
			"User",
			user.name,
			{"role_profile_name": None, "user_type": "Website User"},
			update_modified=False,
		)
		user.reload()

	return user
