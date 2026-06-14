# Copyright (c) 2026, sz and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import flt

from ngs_hub.api.erpnext_sync import sync_ngs_quote_to_quotation
from ngs_hub.api.frappe_crm_sync import sync_ngs_quote_to_frappe_crm_deal


GREATER_BOSTON_ZIP_PREFIXES = ("021", "022", "024")
TENX_TISSUE_DISSOCIATION_SAMPLE_TYPES = {"Fresh Tissue or Organoid"}
TENX_NUCLEI_EXTRACTION_SAMPLE_TYPES = {"Frozen Cells", "Frozen Tissue or Organoid"}
DNA_EXTRACTION_STANDARD_SAMPLE_TYPES = {"Cells", "Tissue", "Plasma", "Serum"}
DNA_EXTRACTION_BLOOD_SALIVA_SWAB_SAMPLE_TYPES = {"Blood", "Saliva", "Swabs"}


class NGSQuote(Document):
	def before_insert(self):
		if not self.quote_id:
			self.quote_id = make_autoname("QTE-.YY.-.MM.-.DD.-.###")
		self.name = self.quote_id

	def validate(self):
		if not self.customer:
			frappe.throw(_("Customer is required to generate Quote"))
		self.set_customer_snapshot()
		self.apply_quote_rules()
		self.calculate_total()

	def after_insert(self):
		sync_ngs_quote_to_quotation(self)
		sync_ngs_quote_to_frappe_crm_deal(self)

	def on_update(self):
		sync_ngs_quote_to_quotation(self)
		sync_ngs_quote_to_frappe_crm_deal(self)

	def set_customer_snapshot(self):
		customer = frappe.get_cached_doc("NGS Customer", self.customer)
		self.email = self.email or customer.get("email")
		self.company_institution = self.company_institution or customer.get("organization") or customer.get("company_institution")
		self.address = self.address or customer.get("address") or customer.get("adress")
		self.lab_group_name = self.lab_group_name or customer.get("lab_group_name")
		if not self.first_name and not self.last_name:
			full_name = customer.get("full_name") or ""
			parts = full_name.split(None, 1)
			self.first_name = customer.get("first_name") or (parts[0] if parts else None)
			self.last_name = customer.get("last_name") or (parts[1] if len(parts) > 1 else None)

	def apply_quote_rules(self):
		for item in self.items:
			if item.service:
				service = frappe.get_cached_doc("NGS Service Catalog", item.service)
				item.unit_price = service.get("unit_price") or 0
				item.project_type = item.project_type or service.get("project_type")
				item.description = item.description or service.get("service_name")
			if not item.quantity or item.quantity <= 0:
				frappe.throw(_("Quantity must be greater than zero for quote item {0}").format(item.idx))
			self.apply_project_defaults(item)
			item.amount = (item.quantity or 0) * (item.unit_price or 0)

	def apply_project_defaults(self, item):
		project_type = item.project_type or ""
		sample_type = item.sample_type or ""
		notes = []
		pricing_notes = []
		unit_price = self.get_base_unit_price(item)
		standard_reads = self.get_standard_reads(item)

		if standard_reads:
			if not item.reads_per_sample_million:
				item.reads_per_sample_million = standard_reads
			if flt(item.reads_per_sample_million) < standard_reads:
				frappe.throw(
					_("Reads (M) for quote item {0} cannot be lower than the default {1}M reads.").format(
						item.idx, standard_reads
					)
				)
			extra_reads = flt(item.reads_per_sample_million) - standard_reads
			if extra_reads > 0:
				item.add_on_sequencing = 1
				extra_fee = extra_reads * self.get_service_price("EXTRA_SEQUENCING_1M")
				unit_price += extra_fee
				pricing_notes.append(_("Extra sequencing: {0}M reads at ${1}/M").format(extra_reads, self.get_service_price("EXTRA_SEQUENCING_1M")))
			else:
				item.add_on_sequencing = 0

		if project_type == "Bulk RNAseq" and item.data_analysis == "De novo Assembly":
			unit_price += self.get_service_price("RNA_DENOVO_ASSEMBLY")
			pricing_notes.append(_("Transcriptome de novo assembly added."))

		if project_type == "Shotgun Meta" and item.data_analysis in {"Mapping", "De novo Assembly"}:
			unit_price += self.get_service_price("SHOTGUN_ANALYSIS")
			pricing_notes.append(_("Shotgun metagenomics analysis added."))

		if project_type == "WGS":
			if sample_type in DNA_EXTRACTION_STANDARD_SAMPLE_TYPES:
				unit_price += self.get_service_price("DNA_EXTRACTION_STANDARD")
				pricing_notes.append(_("DNA extraction for cell/tissue/plasma/serum added."))
			elif sample_type in DNA_EXTRACTION_BLOOD_SALIVA_SWAB_SAMPLE_TYPES:
				unit_price += self.get_service_price("DNA_EXTRACTION_BLOOD_SALIVA_SWAB")
				pricing_notes.append(_("DNA extraction for blood/saliva/swab added."))

		if project_type.startswith("10x"):
			item.tissue_dissociation = 1 if sample_type in TENX_TISSUE_DISSOCIATION_SAMPLE_TYPES else 0
			item.nuclei_extraction = 1 if sample_type in TENX_NUCLEI_EXTRACTION_SAMPLE_TYPES else 0
			if item.tissue_dissociation:
				unit_price += self.get_service_price("TISSUE_DISSOCIATION")
				pricing_notes.append(_("Tissue dissociation added."))
			if item.nuclei_extraction:
				unit_price += self.get_service_price("NUCLEI_EXTRACTION")
				pricing_notes.append(_("Nuclei extraction added."))
			if item.onsite_service and not item.onsite_address:
				notes.append(_("On-site service requires an address."))
			elif item.onsite_service:
				if self.is_greater_boston_address(item.onsite_address):
					unit_price += self.get_service_price("ONSITE_SERVICE")
					pricing_notes.append(_("Greater Boston on-site service added."))
				else:
					notes.append(_("On-site service outside Greater Boston will be quoted based on actual expenses and is not included in this total."))

		if project_type.startswith("Sequencing Only"):
			item.library_qc = 1
			unit_price += self.get_service_price("LIBRARY_QC")
			pricing_notes.append(_("Library QC added."))

		item.unit_price = unit_price
		item.rule_notes = "\n".join([str(note) for note in notes + pricing_notes])
		if notes and self.status == "Draft":
			self.status = "Pending Info"

	def calculate_total(self):
		self.total_amount = sum((item.amount or 0) for item in self.items)

	def get_base_unit_price(self, item):
		project_type = item.project_type or ""
		quantity = flt(item.quantity)
		if project_type == "Bulk RNAseq":
			if quantity < 12:
				return 109
			if quantity < 49:
				return 99
			if quantity < 97:
				return 94
			return 89
		if project_type == "WGS":
			return 399 if quantity < 8 else 369
		return flt(item.unit_price)

	def get_standard_reads(self, item):
		if item.service:
			return flt(frappe.db.get_value("NGS Service Catalog", item.service, "standard_reads_million"))
		return 0

	def get_service_price(self, service_code):
		return flt(frappe.db.get_value("NGS Service Catalog", service_code, "unit_price"))

	def is_greater_boston_address(self, address):
		match = re.search(r"\b(\d{5})(?:-\d{4})?\b", address or "")
		if not match:
			return False
		return match.group(1).startswith(GREATER_BOSTON_ZIP_PREFIXES)
