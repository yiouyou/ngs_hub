import frappe
from frappe import _

from ngs_hub.api.erpnext_sync import sync_ngs_order_to_sales_order


INTERNAL_REVIEW_ROLES = {"System Manager", "NGS Internal Staff"}


def require_internal_reviewer():
	if set(frappe.get_roles()).isdisjoint(INTERNAL_REVIEW_ROLES):
		frappe.throw(_("Only internal NGS staff can review orders."), frappe.PermissionError)


def get_order(order_name):
	if not order_name:
		frappe.throw(_("Order is required."))
	order = frappe.get_doc("NGS Order", order_name)
	if not order.has_permission("write"):
		frappe.throw(_("Not permitted to update this order."), frappe.PermissionError)
	return order


@frappe.whitelist()
def start_review(order_name):
	require_internal_reviewer()
	order = get_order(order_name)
	if order.status not in {"Draft", "Submitted"}:
		frappe.throw(_("Only Draft or Submitted orders can be moved to In Review."))
	order.status = "In Review"
	order.save()
	return {"name": order.name, "status": order.status, "erpnext_sales_order": order.erpnext_sales_order}


@frappe.whitelist()
def accept_order(order_name):
	require_internal_reviewer()
	order = get_order(order_name)
	if order.status not in {"Submitted", "In Review"}:
		frappe.throw(_("Only Submitted or In Review orders can be accepted."))
	order.status = "Accepted"
	order.save()
	return {"name": order.name, "status": order.status, "erpnext_sales_order": order.erpnext_sales_order}


@frappe.whitelist()
def submit_erpnext_sales_order(order_name):
	require_internal_reviewer()
	order = get_order(order_name)
	if order.status != "Accepted":
		frappe.throw(_("Accept the NGS Order before submitting the ERPNext Sales Order."))
	sales_order_name = sync_ngs_order_to_sales_order(order)
	if not sales_order_name:
		frappe.throw(_("ERPNext Sales Order could not be created."))
	sales_order = frappe.get_doc("Sales Order", sales_order_name)
	if sales_order.docstatus == 1:
		return {"name": order.name, "status": order.status, "erpnext_sales_order": sales_order.name}
	if sales_order.docstatus != 0:
		frappe.throw(_("ERPNext Sales Order {0} is not a draft.").format(sales_order.name))
	sales_order.flags.ignore_permissions = True
	sales_order.submit()
	return {"name": order.name, "status": order.status, "erpnext_sales_order": sales_order.name}
