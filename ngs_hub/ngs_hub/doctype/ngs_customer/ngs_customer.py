# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname

from ngs_hub.api.user_utils import create_ngs_customer_user


class NGSCustomer(Document):
	def before_insert(self):
		if not self.email:
			frappe.throw(_("Email is required to generate Customer"))
		if not self.full_name:
			frappe.throw(_("Full Name is required to generate Customer ID"))
		safe_name = re.sub(r"[^\w\s-]", "", self.full_name).replace(" ", "_")
		self.customer_id = make_autoname(f"CUST-{safe_name}-.###")
		self.name = self.customer_id

	def on_submit(self):
		if not self.email:
			return
		user_name, password = create_ngs_customer_user(self.email, self.full_name)
		if not user_name:
			return
		login_url = frappe.utils.get_url("/login")
		message = f"""<!DOCTYPE html>
<html>
  <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.8; padding: 20px;">
    <div style="max-width: 640px; margin: auto; border: 1px solid #e0e0e0; border-radius: 10px; padding: 30px; background-color: #fafafa;">
      <h2 style="color: #0066cc; margin-bottom: 20px;">Welcome to Athenomics</h2>
      <p style="margin-bottom: 20px;">Dear {self.full_name or "Customer"},</p>
      <p style="margin-bottom: 20px;">
        Your Athenomics account has been successfully created. You can now log in using the following credentials:
      </p>
      <ul style="margin-bottom: 24px;">
        <li><strong>Login URL:</strong> <a href="{login_url}" style="color: #0066cc;">{login_url}</a></li>
        <li><strong>Email:</strong> {self.email}</li>
        <li><strong>Password:</strong> {password}</li>
      </ul>
      <p style="margin-bottom: 32px;"><em>Please change your password after logging in for the first time.</em></p>
      <hr style="border: none; border-top: 1px solid #ddd; margin: 40px 0;">
      <p style="margin-bottom: 0;">
        Best regards,<br>
        <strong>Your Athenomics Lab Team</strong>
      </p>
    </div>
  </body>
</html>"""
		try:
			# 发送邮件
			frappe.sendmail(
				recipients=[self.email],
				subject="Welcome to Athenomics",
				message=message,
				delayed=False,
				retry=3,
			)
			frappe.msgprint(f"Send welcome email to {self.email} ({self.full_name})", alert=True)
			frappe.logger("send_email").info(f"Send welcome email to {self.email} ({self.full_name})")
		except Exception:
			frappe.logger("send_email").error(
				f"失败：Send welcome email to {self.email} ({self.full_name})", exc_info=True
			)
