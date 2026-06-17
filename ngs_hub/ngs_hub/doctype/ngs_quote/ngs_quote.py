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


TENX_TISSUE_DISSOCIATION_SAMPLE_TYPES = {"Fresh Tissue or Organoid"}
TENX_NUCLEI_EXTRACTION_SAMPLE_TYPES = {"Frozen Cells", "Frozen Tissue or Organoid"}
DNA_EXTRACTION_STANDARD_SAMPLE_TYPES = {"Cells", "Tissue", "Plasma", "Serum"}
DNA_EXTRACTION_BLOOD_SALIVA_SWAB_SAMPLE_TYPES = {"Blood", "Saliva", "Swabs"}
WGS_DEFAULT_DEPTH = 30
WGS_MIN_DEPTH = 1
WGS_MAX_DEPTH = 60
WGS_LIBRARY_PREP_PRICE = 45
WGS_LOW_VOLUME_RATE_PER_X = 11.8
WGS_HIGH_VOLUME_RATE_PER_X = 10.8
WGS_HIGH_VOLUME_MIN_QUANTITY = 12
US_STATE_ABBREVIATIONS = {
	"AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
	"HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
	"MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
	"NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
	"SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
	"DC",
}
US_STATE_NAME_TO_ABBR = {
	"alabama": "AL",
	"alaska": "AK",
	"arizona": "AZ",
	"arkansas": "AR",
	"california": "CA",
	"colorado": "CO",
	"connecticut": "CT",
	"delaware": "DE",
	"florida": "FL",
	"georgia": "GA",
	"hawaii": "HI",
	"idaho": "ID",
	"illinois": "IL",
	"indiana": "IN",
	"iowa": "IA",
	"kansas": "KS",
	"kentucky": "KY",
	"louisiana": "LA",
	"maine": "ME",
	"maryland": "MD",
	"massachusetts": "MA",
	"michigan": "MI",
	"minnesota": "MN",
	"mississippi": "MS",
	"missouri": "MO",
	"montana": "MT",
	"nebraska": "NE",
	"nevada": "NV",
	"new hampshire": "NH",
	"new jersey": "NJ",
	"new mexico": "NM",
	"new york": "NY",
	"north carolina": "NC",
	"north dakota": "ND",
	"ohio": "OH",
	"oklahoma": "OK",
	"oregon": "OR",
	"pennsylvania": "PA",
	"rhode island": "RI",
	"south carolina": "SC",
	"south dakota": "SD",
	"tennessee": "TN",
	"texas": "TX",
	"utah": "UT",
	"vermont": "VT",
	"virginia": "VA",
	"washington": "WA",
	"west virginia": "WV",
	"wisconsin": "WI",
	"wyoming": "WY",
	"district of columbia": "DC",
}
STATE_ABBR_PATTERN = re.compile(
	r"(?<![A-Z])("
	+ "|".join(sorted(US_STATE_ABBREVIATIONS))
	+ r")(?:\s+\d{5}(?:-\d{4})?)?(?=\s*$|\s*,)",
	re.IGNORECASE,
)
ZIP_PATTERN = re.compile(r"\b\d{5}(?:-\d{4})?\b")


def extract_us_state(address):
	normalized = " ".join(str(address or "").replace("\n", ",").split())
	if not normalized:
		return None
	matches = [match.group(1).upper() for match in STATE_ABBR_PATTERN.finditer(normalized)]
	if matches:
		return matches[-1]
	for component in reversed([part.strip(" .") for part in normalized.split(",") if part.strip()]):
		without_zip = ZIP_PATTERN.sub("", component).strip(" .").lower()
		if without_zip in US_STATE_NAME_TO_ABBR:
			return US_STATE_NAME_TO_ABBR[without_zip]
		for state_name, abbr in sorted(US_STATE_NAME_TO_ABBR.items(), key=lambda item: len(item[0]), reverse=True):
			if without_zip.endswith(f" {state_name}"):
				return abbr
	return None



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
		self.set_project_summary()
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

	def set_project_summary(self):
		project_types = []
		seen = set()
		for item in self.items:
			project_type = item.project_type or item.description
			if not project_type or project_type in seen:
				continue
			seen.add(project_type)
			project_types.append(project_type)
		visible_project_types = project_types[:3]
		if len(project_types) > 3:
			visible_project_types.append(_("+{0} more").format(len(project_types) - 3))
		self.project_summary = " | ".join(visible_project_types)

	def apply_quote_rules(self):
		missing_info = []
		for item in self.items:
			if item.service:
				service = frappe.get_cached_doc("NGS Service Catalog", item.service)
				item.unit_price = service.get("unit_price") or 0
				item.project_type = item.project_type or service.get("project_type")
				item.description = item.description or service.get("service_name")
			if not item.quantity or item.quantity <= 0:
				frappe.throw(_("Quantity must be greater than zero for quote item {0}").format(item.idx))
			missing_info.extend(self.apply_project_defaults(item))
			item.amount = (item.quantity or 0) * (item.unit_price or 0)
		self.missing_info = "\n".join(missing_info)
		self.pricing_to_confirm = 1 if self.missing_info else 0
		if self.missing_info and self.status not in {"Cancelled", "Converted to Order"}:
			self.status = "Pending"

	def apply_project_defaults(self, item):
		project_type = item.project_type or ""
		sample_type = item.sample_type or ""
		notes = []
		pricing_notes = []
		missing_info = []
		unit_price = self.get_base_unit_price(item)
		standard_reads = self.get_standard_reads(item)
		confirmed_tbd_fee = flt(item.get("confirmed_tbd_fee"))

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

		if project_type == "Custom Project" and flt(unit_price) <= 0:
			manual_note = _("Manual pricing required for custom project.")
			notes.append(manual_note)
			missing_info.append(_("Item {0}: enter the confirmed custom project unit price, then set Status to Confirmed.").format(item.idx))
			if self.status == "Draft":
				self.status = "Pending"

		if project_type == "Bulk RNAseq" and item.data_analysis == "De novo Assembly":
			unit_price += self.get_service_price("RNA_DENOVO_ASSEMBLY")
			pricing_notes.append(_("Transcriptome de novo assembly added."))

		if project_type == "Shotgun Meta" and item.data_analysis in {"Mapping", "De novo Assembly"}:
			unit_price += self.get_service_price("SHOTGUN_ANALYSIS")
			pricing_notes.append(_("Shotgun metagenomics analysis added."))

		if project_type == "WGS":
			depth = self.get_wgs_depth(item)
			rate = self.get_wgs_rate(item)
			pricing_notes.append(_("WGS: $45 library prep + {0}x sequencing at ${1}/x.").format(f"{depth:g}", rate))
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
			if item.onsite_service:
				if not item.onsite_location:
					notes.append(_("On-site service requires a location."))
				if not item.onsite_address:
					notes.append(_("On-site service requires an address."))
				location_issue = self.get_onsite_location_issue(item)
				if location_issue:
					notes.append(location_issue)
				elif item.onsite_location == "Massachusetts":
					unit_price += self.get_service_price("ONSITE_SERVICE")
					pricing_notes.append(_("Massachusetts on-site service added."))
				elif item.onsite_location == "Outside Massachusetts":
					if confirmed_tbd_fee > 0:
						unit_price += confirmed_tbd_fee
						pricing_notes.append(_("Outside Massachusetts on-site service confirmed: ${0}.").format(f"{confirmed_tbd_fee:g}"))
					else:
						notes.append(_("On-site service outside Massachusetts is TBD and is not included in this subtotal."))
						missing_info.append(_("Item {0}: edit this row and enter the confirmed on-site amount in TBD Fee, then set Status to Confirmed.").format(item.idx))

		if project_type.startswith("Sequencing Only"):
			item.library_qc = 1
			unit_price += self.get_service_price("LIBRARY_QC")
			pricing_notes.append(_("Library QC added."))

		item.unit_price = unit_price
		item.rule_notes = "\n".join([str(note) for note in notes + pricing_notes])
		if (notes or missing_info) and self.status == "Draft":
			self.status = "Pending"
		return missing_info

	def get_onsite_location_issue(self, item):
		if not item.onsite_service or not item.onsite_location or not item.onsite_address:
			return None
		address_state = extract_us_state(item.onsite_address)
		if not address_state:
			return _("On-site address must include a US state abbreviation or state name.")
		if item.onsite_location == "Massachusetts" and address_state != "MA":
			return _("On-site location is Massachusetts, but the address appears to be {0}.").format(address_state)
		if item.onsite_location == "Outside Massachusetts" and address_state == "MA":
			return _("On-site location is Outside Massachusetts, but the address appears to be Massachusetts.")
		return None

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
			depth = self.get_wgs_depth(item)
			return WGS_LIBRARY_PREP_PRICE + depth * self.get_wgs_rate(item)
		return flt(item.unit_price)

	def get_wgs_depth(self, item):
		raw_depth = item.read_depth or WGS_DEFAULT_DEPTH
		depth = flt(str(raw_depth).lower().replace("x", "").strip())
		if depth < WGS_MIN_DEPTH or depth > WGS_MAX_DEPTH:
			frappe.throw(
				_("WGS coverage for quote item {0} must be between {1}x and {2}x.").format(
					item.idx, WGS_MIN_DEPTH, WGS_MAX_DEPTH
				)
			)
		item.read_depth = f"{depth:g}x"
		return depth

	def get_wgs_rate(self, item):
		if flt(item.quantity) >= WGS_HIGH_VOLUME_MIN_QUANTITY:
			return WGS_HIGH_VOLUME_RATE_PER_X
		return WGS_LOW_VOLUME_RATE_PER_X

	def get_standard_reads(self, item):
		if item.service:
			return flt(frappe.db.get_value("NGS Service Catalog", item.service, "standard_reads_million"))
		return 0

	def get_service_price(self, service_code):
		return flt(frappe.db.get_value("NGS Service Catalog", service_code, "unit_price"))
