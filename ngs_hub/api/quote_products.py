"""Product choices shared by Desk quotation controls and server-side defaults."""

import math

import frappe
from frappe import _
from frappe.utils import cint, flt


def _product(project_type, service, samples, analyses, **options):
	return {
		"project_type": project_type,
		"group": project_type,
		"subtype": "",
		"service_code": service,
		"samples": samples,
		"analyses": analyses,
		"species": [],
		"standard_reads": 0,
		"reads": False,
		"depth": False,
		"onsite": False,
		**options,
	}


PRODUCTS = [
	_product(
		"Bulk RNAseq",
		"BULK_RNASEQ_20M",
		["Total RNA", "Cells", "Tissue"],
		["No", "Standard Analysis", "De novo Assembly"],
		reads=True,
		standard_reads=20,
	),
	_product(
		"WGS",
		"WGS_30X",
		["DNA", "Cells", "Tissue", "Saliva", "Blood", "Plasma", "Serum", "Swabs"],
		["No", "Standard Analysis"],
		species=["human", "mouse", "hamster"],
		depth=True,
	),
	*[
		_product(
			project_type,
			code,
			["Fresh Cells", "Fresh Tissue or Organoid", "Frozen Cells", "Frozen Tissue or Organoid"],
			["No", "Standard Analysis"],
			group="10x Single Cell",
			subtype=subtype,
			reads=True,
			standard_reads=reads,
			onsite=True,
		)
		for project_type, code, subtype, reads in (
			("10x 3' Transcriptome", "10X_3_TRANSCRIPTOME", "3' transcriptome", 330),
			("10x 5' Transcriptome", "10X_5_TRANSCRIPTOME", "5' transcriptome", 330),
			("10x 5' VDJ", "10X_5_VDJ", "5' VDJ", 40),
			("10x 5' Transcriptome + VDJ", "10X_5_TRANSCRIPTOME_VDJ", "5' transcriptome + VDJ", 370),
		)
	],
	_product(
		"Shotgun Meta",
		"SHOTGUN_META_20M",
		["Water", "Soil", "Stool", "DNA"],
		["No", "Mapping", "De novo Assembly"],
		reads=True,
		standard_reads=20,
	),
	_product(
		"DNA Small Fragment Library Prep & Sequencing",
		"DNA_SMALL_FRAGMENT_PREP",
		["DNA"],
		["No"],
		reads=True,
		standard_reads=1,
	),
	*[
		_product(
			project_type,
			code,
			["Library"],
			["No"],
			group="Sequencing Only",
			subtype=subtype,
			reads=bool(reads),
			standard_reads=reads,
		)
		for project_type, code, subtype, reads in (
			("Sequencing Only - 10B Lane", "SEQ_ONLY_10B_LANE", "10B lane", 0),
			("Sequencing Only - 25B Lane", "SEQ_ONLY_25B_LANE", "25B lane", 0),
			("Sequencing Only - 1M Reads", "SEQ_ONLY_1M_READS", "1M reads", 1),
		)
	],
	*[
		_product(
			f"Microbial Genome Sequencing ({subtype})",
			code,
			["DNA", "Sample for DNA Extraction"],
			["No"],
			group="Microbial Genome Sequencing",
			subtype=subtype,
		)
		for subtype, code in (
			("<7M", "MICROBIAL_GENOME_LT_7M"),
			("7-20M", "MICROBIAL_GENOME_7_20M"),
			("20-60M", "MICROBIAL_GENOME_20_60M"),
		)
	],
	_product("Custom Project", None, ["Other"], ["Custom Analysis"]),
]


def get_product(project_type):
	return next((product for product in PRODUCTS if product["project_type"] == project_type), None)


def get_product_service(product, required=True, allow_disabled=False):
	if not product["service_code"]:
		return None
	service = frappe.db.get_value(
		"NGS Service Catalog",
		{"service_code": product["service_code"]},
		["name", "enabled", "project_type", "standard_reads_million", "unit_price"],
		as_dict=True,
	)
	if (
		not service
		or (not service.enabled and not allow_disabled)
		or service.project_type != product["project_type"]
	):
		if not required:
			return None
		frappe.throw(
			_("The service for {0} is unavailable. Check NGS Service Catalog.").format(
				product["project_type"]
			)
		)
	return service


def normalize_quote_item(item, allow_disabled=False, existing_item=None):
	item.use_price_override = cint(item.get("use_price_override"))
	item.onsite_service = cint(item.get("onsite_service"))
	product = get_product(item.project_type)
	if not product:
		frappe.throw(_("Select a supported project type."))
	service = get_product_service(product, allow_disabled=allow_disabled)
	unchanged_product = bool(existing_item and existing_item.project_type == item.project_type)
	legacy_custom_service = bool(
		unchanged_product
		and item.project_type == "Custom Project"
		and item.service
		and item.service == existing_item.service
	)
	if item.service and (not service or item.service != service.name) and not legacy_custom_service:
		frappe.throw(_("The selected service does not match the project type."))
	if not legacy_custom_service:
		item.service = service.name if service else None
	for field, choices in (("sample_type", product["samples"]), ("data_analysis", product["analyses"])):
		value = item.get(field)
		legacy_value = unchanged_product and existing_item.get(field) == value
		if value and value not in choices and not legacy_value:
			frappe.throw(_("Invalid {0} for {1}.").format(field.replace("_", " "), item.project_type))
		item.set(field, value or choices[0])
	if product["species"]:
		item.species = item.species or product["species"][0]
		if item.species not in product["species"] and not (
			unchanged_product and existing_item.species == item.species
		):
			frappe.throw(_("Select a supported species for WGS."))
	else:
		item.species = None
	if product["reads"]:
		if item.million_reads_per_sample in (None, "", 0):
			item.million_reads_per_sample = flt(service.standard_reads_million) or product["standard_reads"]
		validate_number(item.million_reads_per_sample, _("Reads (M)"), positive=True)
		item.million_reads_per_sample = flt(item.million_reads_per_sample)
	else:
		item.million_reads_per_sample = None
		item.add_on_sequencing = 0
	if not product["depth"]:
		item.read_depth = None
	if not product["onsite"] or not item.onsite_service:
		item.onsite_service = 0
		item.onsite_location = None
		item.onsite_address = None
		item.confirmed_tbd_fee = 0
	item.tissue_dissociation = int(product["onsite"] and item.sample_type == "Fresh Tissue or Organoid")
	item.nuclei_extraction = int(
		product["onsite"] and item.sample_type in {"Frozen Cells", "Frozen Tissue or Organoid"}
	)
	item.library_qc = int(product["group"] == "Sequencing Only")
	validate_number(item.quantity, _("Quantity"), positive=True)
	item.quantity = flt(item.quantity)
	validate_number(item.get("confirmed_tbd_fee") or 0, _("TBD Fee"))
	item.confirmed_tbd_fee = flt(item.get("confirmed_tbd_fee"))
	if item.get("use_price_override"):
		validate_number(item.get("override_unit_price"), _("Manual unit price"))
		item.override_unit_price = flt(item.override_unit_price)
	elif item.project_type == "Custom Project" and not legacy_custom_service:
		validate_number(item.unit_price or 0, _("Unit price"))
		legacy_manual_price = unchanged_product and not existing_item.get("use_price_override")
		if not legacy_manual_price:
			item.unit_price = 0
			item.override_unit_price = 0
	return product


def validate_number(value, label, positive=False):
	try:
		number = float(value)
	except (TypeError, ValueError):
		frappe.throw(_("{0} must be a number.").format(label))
	if not math.isfinite(number) or number < 0 or (positive and number == 0):
		frappe.throw(
			_("{0} must be a finite {1} number.").format(
				label, _("positive") if positive else _("non-negative")
			)
		)
