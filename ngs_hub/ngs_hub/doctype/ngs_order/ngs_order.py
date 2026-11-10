# Copyright (c) 2026, sz and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname

from ngs_hub.api.desk_item_details import legacy_order_detail_fallbacks
from ngs_hub.api.erpnext_sync import sync_ngs_order_to_sales_order
from ngs_hub.api.frappe_crm_sync import sync_ngs_order_to_frappe_crm_deal
from ngs_hub.api.sequencing_pricing import SMALL_FRAGMENT_DNA, apply_document_sequencing, catalog_price
from ngs_hub.api.service_summary import build_project_summary


class NGSOrder(Document):
	def onload(self):
		self.set_onload("item_detail_fallbacks", legacy_order_detail_fallbacks(self))

	def before_insert(self):
		if not self.order_id:
			self.order_id = make_autoname("ORD-.YY.-.MM.-.DD.-.###")
		self.name = self.order_id

	def validate(self):
		if self.is_new():
			self.sequencing_pricing_version = 1
			self.sequencing_pricing_rates = None
		else:
			previous = self.get_doc_before_save()
			if previous:
				self.sequencing_pricing_version = previous.get("sequencing_pricing_version") or 0
				self.sequencing_pricing_rates = previous.get("sequencing_pricing_rates")
		if self.quote:
			quote = frappe.get_cached_doc("NGS Quote", self.quote)
			if self.is_new():
				self.sequencing_pricing_version = quote.get("sequencing_pricing_version") or 0
				self.sequencing_pricing_rates = quote.get("sequencing_pricing_rates")
			self.customer = self.customer or quote.customer
			if quote.customer != self.customer:
				frappe.throw(_("Quote {0} is not linked to customer {1}").format(self.quote, self.customer))
			if not self.items:
				self.copy_items_from_quote(quote)
		if not self.customer:
			frappe.throw(_("Customer is required to generate Order"))
		if not self.no_po_available and not (self.po_number or self.po_file):
			frappe.throw(_("Provide either a PO number or a PO file before placing an order."))
		if not self.sample_registration_form:
			frappe.throw(_("Upload the sample registration form before placing an order."))
		for item in self.items:
			if not item.quantity or item.quantity <= 0:
				frappe.throw(_("Quantity must be greater than zero for order item {0}").format(item.idx))
			if item.onsite_service and not item.onsite_location:
				frappe.throw(_("On-site service requires a location for order item {0}").format(item.idx))
			if item.onsite_service and not item.onsite_address:
				frappe.throw(_("On-site service requires an address for order item {0}").format(item.idx))
			if item.quote_item:
				item.unit_price = frappe.db.get_value("NGS Quote Item", item.quote_item, "unit_price") or 0
			elif item.service:
				item.unit_price = frappe.db.get_value("NGS Service Catalog", item.service, "unit_price") or 0
				if item.project_type == SMALL_FRAGMENT_DNA:
					from ngs_hub.ngs_hub.doctype.ngs_quote.ngs_quote import NGSQuote
					preview = NGSQuote({"doctype": "NGS Quote", "items": [{
						"project_type": item.project_type, "service": item.service,
						"quantity": item.quantity, "million_reads_per_sample": item.million_reads_per_sample,
						"sample_type": item.sample_type, "data_analysis": item.data_analysis,
					}]})
					preview.apply_quote_rules()
					for field in ("service", "sample_type", "description", "species", "data_analysis",
						"million_reads_per_sample", "add_on_sequencing", "library_qc", "unit_price", "pricing_details"):
						item.set(field, preview.items[0].get(field))
					item.notes = preview.items[0].rule_notes
			item.amount = (item.quantity or 0) * (item.unit_price or 0)
		if self.sequencing_pricing_version:
			try:
				self.sequencing_pricing_rates = apply_document_sequencing(
					self.items, catalog_price,
					self.sequencing_pricing_rates,
				)
			except (ValueError, ArithmeticError) as error:
				frappe.throw(_("Invalid whole-order sequencing pricing: {0}").format(str(error)))
		self.project_summary = build_project_summary(self.items)

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
			read_depth = quote_item.read_depth
			if not read_depth and quote_item.million_reads_per_sample:
				read_depth = f"{quote_item.million_reads_per_sample:.15g}M"
			self.append("items", {
				"quote_item": quote_item.name,
				"service": quote_item.service,
				"project_type": quote_item.project_type,
				"sample_type": quote_item.sample_type,
				"description": quote_item.description,
				"quantity": quote_item.quantity,
				"species": quote_item.species,
				"read_depth": read_depth,
				"million_reads_per_sample": quote_item.million_reads_per_sample,
				"add_on_sequencing": quote_item.add_on_sequencing,
				"data_analysis": quote_item.data_analysis,
				"onsite_service": quote_item.onsite_service,
				"onsite_location": quote_item.onsite_location,
				"onsite_address": quote_item.onsite_address,
				"tissue_dissociation": quote_item.tissue_dissociation,
				"nuclei_extraction": quote_item.nuclei_extraction,
				"library_qc": quote_item.library_qc,
				"confirmed_tbd_fee": quote_item.confirmed_tbd_fee,
				"use_price_override": quote_item.get("use_price_override"),
				"override_unit_price": quote_item.get("override_unit_price"),
				"details_snapshot_version": 1,
				"unit_price": quote_item.unit_price,
				"amount": quote_item.amount,
				"notes": quote_item.rule_notes,
				"pricing_details": quote_item.get("pricing_details"),
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
