# Copyright (c) 2026, sz and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname

from ngs_hub.api.erpnext_sync import sync_ngs_order_to_sales_order
from ngs_hub.api.frappe_crm_sync import sync_ngs_order_to_frappe_crm_deal


class NGSOrder(Document):
	def before_insert(self):
		if not self.order_id:
			self.order_id = make_autoname("ORD-.YY.-.MM.-.DD.-.###")
		self.name = self.order_id

	def validate(self):
		if self.quote:
			quote = frappe.get_cached_doc("NGS Quote", self.quote)
			self.customer = self.customer or quote.customer
			if quote.customer != self.customer:
				frappe.throw(_("Quote {0} is not linked to customer {1}").format(self.quote, self.customer))
			if not self.items:
				self.copy_items_from_quote(quote)
		if not self.customer:
			frappe.throw(_("Customer is required to generate Order"))
		if not (self.po_number or self.po_file):
			frappe.throw(_("Provide either a PO number or a PO file before placing an order."))
		if not self.sample_registration_form:
			frappe.throw(_("Upload the sample registration form before placing an order."))
		for item in self.items:
			if not item.quantity or item.quantity <= 0:
				frappe.throw(_("Quantity must be greater than zero for order item {0}").format(item.idx))
			if item.onsite_service and not item.onsite_address:
				frappe.throw(_("On-site service requires an address for order item {0}").format(item.idx))
			if item.service and not item.quote_item:
				item.unit_price = frappe.db.get_value("NGS Service Catalog", item.service, "unit_price") or 0
			item.amount = (item.quantity or 0) * (item.unit_price or 0)

	def after_insert(self):
		self.attach_uploaded_files()
		self.link_quote()
		sync_ngs_order_to_sales_order(self)
		sync_ngs_order_to_frappe_crm_deal(self)

	def on_update(self):
		self.attach_uploaded_files()
		self.link_quote()
		sync_ngs_order_to_sales_order(self)
		sync_ngs_order_to_frappe_crm_deal(self)

	def copy_items_from_quote(self, quote):
		for quote_item in quote.items:
			self.append("items", {
				"quote_item": quote_item.name,
				"service": quote_item.service,
				"project_type": quote_item.project_type,
				"sample_type": quote_item.sample_type,
				"description": quote_item.description,
				"quantity": quote_item.quantity,
					"data_analysis": quote_item.data_analysis,
					"onsite_service": quote_item.onsite_service,
					"onsite_address": quote_item.onsite_address,
					"tissue_dissociation": quote_item.tissue_dissociation,
					"nuclei_extraction": quote_item.nuclei_extraction,
					"library_qc": quote_item.library_qc,
					"unit_price": quote_item.unit_price,
					"amount": quote_item.amount,
					"notes": quote_item.rule_notes,
				})

	def attach_uploaded_files(self):
		for fieldname in ("po_file", "sample_registration_form"):
			file_url = self.get(fieldname)
			if not file_url:
				continue
			file_name = frappe.db.get_value("File", {"file_url": file_url}, "name")
			if not file_name:
				continue
			file_doc = frappe.get_doc("File", file_name)
			if (
				file_doc.attached_to_doctype == self.doctype
				and file_doc.attached_to_name == self.name
				and file_doc.attached_to_field == fieldname
			):
				continue
			file_doc.db_set("attached_to_doctype", self.doctype, update_modified=False)
			file_doc.db_set("attached_to_name", self.name, update_modified=False)
			file_doc.db_set("attached_to_field", fieldname, update_modified=False)

	def link_quote(self):
		if self.quote and frappe.db.exists("NGS Quote", self.quote):
			frappe.db.set_value("NGS Quote", self.quote, "order", self.name, update_modified=False)
			frappe.db.set_value("NGS Quote", self.quote, "status", "Converted to Order", update_modified=False)
