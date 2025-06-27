import random
import string

import frappe
from frappe.utils.password import update_password


def generate_random_password(length=10):
	chars = string.ascii_letters + string.digits
	return "".join(random.choice(chars) for _ in range(length))


def create_ngs_customer_user(email, full_name):
	# 如果用户已存在，返回 None
	if frappe.db.exists("User", email):
		return None, None
	password = generate_random_password()
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": full_name or "NGS Customer",
			"role_profile_name": "NGS Customer",
			"module_profile": "NGS Customer",
			"enabled": 1,
		}
	)
	user.insert(ignore_permissions=True)
	update_password(user.name, password)
	return user.name, password
