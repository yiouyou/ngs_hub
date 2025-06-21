# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class NGSCustomer(Document):
	def before_insert(self):
		if not self.full_name:
			frappe.throw(_("Full Name is required to generate Customer ID"))
		autoname = make_autoname("CUST-.#####.-")
		safe_name = re.sub(r"[^\w\s-]", "", self.full_name).replace(" ", "_")
		self.customer_id = f"{autoname}{safe_name}"
		self.name = self.customer_id
