"""Desk quotation regressions using real documents and read-only catalog doubles.

Run in an initialized Frappe site with unittest (no fixtures are inserted). Only
catalog/session/permission boundaries are mocked; the quotation controller,
normalizer, child documents, and save-time validate method remain real.
"""

import json
from copy import deepcopy
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import frappe
from frappe.model.document import Document

from ngs_hub.api import desk_quote, quote_products
from ngs_hub.ngs_hub.doctype.ngs_quote import ngs_quote
from ngs_hub.ngs_hub.doctype.ngs_quote.ngs_quote import NGSQuote
from ngs_hub.ngs_hub.doctype.ngs_quote_item.ngs_quote_item import NGSQuoteItem

# Independent contract values: do not derive expected prices from the controller.
PRODUCT_CASES = (
	("Bulk RNAseq", "BULK_RNASEQ_20M", "Total RNA", "No", 20, 109),
	("WGS", "WGS_30X", "DNA", "No", None, 399),
	("10x 3' Transcriptome", "10X_3_TRANSCRIPTOME", "Fresh Cells", "No", 330, 2500),
	("10x 5' Transcriptome", "10X_5_TRANSCRIPTOME", "Fresh Cells", "No", 330, 2500),
	("10x 5' VDJ", "10X_5_VDJ", "Fresh Cells", "No", 40, 2500),
	("10x 5' Transcriptome + VDJ", "10X_5_TRANSCRIPTOME_VDJ", "Fresh Cells", "No", 370, 3600),
	("Shotgun Meta", "SHOTGUN_META_20M", "Water", "No", 20, 130),
	("Sequencing Only - 10B Lane", "SEQ_ONLY_10B_LANE", "Library", "No", None, 1618),
	("Sequencing Only - 25B Lane", "SEQ_ONLY_25B_LANE", "Library", "No", None, 2618),
	("Sequencing Only - 1M Reads", "SEQ_ONLY_1M_READS", "Library", "No", 1, 19.9),
	("Microbial Genome Sequencing (<7M)", "MICROBIAL_GENOME_LT_7M", "DNA", "No", None, 65),
	("Microbial Genome Sequencing (7-20M)", "MICROBIAL_GENOME_7_20M", "DNA", "No", None, 120),
	("Microbial Genome Sequencing (20-60M)", "MICROBIAL_GENOME_20_60M", "DNA", "No", None, 150),
	("Custom Project", None, "Other", "Custom Analysis", None, 0),
)
NONFINITE = (float("nan"), float("inf"), float("-inf"), "NaN", "Infinity", "-Infinity")


class DeskQuoteTestCase(TestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		with (Path(__file__).resolve().parents[1] / "fixtures" / "ngs_service_catalog.json").open() as source:
			cls.catalog_fixture = json.load(source)
		# Warm real metadata/controllers before installing the no-SQL guards. This
		# permits running against an existing site without test records or commits.
		cls.customer_template = frappe.get_doc({"doctype": "NGS Customer", "name": "CUSTOMER-TEST"})
		quote = NGSQuote({"doctype": "NGS Quote", "items": [{"quantity": 1}]})
		for doc in (quote, quote.items[0], cls.customer_template):
			doc.meta.get_valid_fields()

	def setUp(self):
		self.catalog = {row["name"]: frappe._dict(deepcopy(row)) for row in self.catalog_fixture}
		self.session = frappe._dict(user="staff@example.test")
		self.enabled = 1
		self.user_type = "System User"
		self.roles = ["NGS Internal Staff"]
		self.allow_create = self.allow_write = self.allow_customer_read = True
		self.existing_quote = self.make_quote(name="QUOTE-TEST")
		self.customer = deepcopy(self.customer_template)
		self.new_quotes = []
		self.patch(frappe, "session", self.session)
		self.patch(frappe, "get_roles", side_effect=lambda user: self.roles)
		self.lookup = self.patch(frappe.db, "get_value", side_effect=self.get_value)
		self.cached_doc = self.patch(frappe, "get_cached_doc", side_effect=self.get_cached_doc)
		self.get_doc = self.patch(frappe, "get_doc", side_effect=self.get_document)
		self.new_doc = self.patch(frappe, "new_doc", side_effect=self.new_document)
		self.permission = self.patch(frappe, "has_permission", side_effect=self.has_permission)
		self.doc_permission = self.patch(
			Document, "check_permission", autospec=True, side_effect=self.check_permission
		)
		self.write_guards = []
		for owner, names in (
			(frappe.db, ("sql", "commit", "set_value")),
			(Document, ("save", "insert", "db_insert", "db_update", "db_set")),
			(ngs_quote, ("sync_ngs_quote_to_quotation", "sync_ngs_quote_to_frappe_crm_deal")),
		):
			for name in names:
				self.write_guards.append(
					self.patch(
						owner, name, side_effect=AssertionError(f"Unexpected persistence/sync: {name}")
					)
				)

	def tearDown(self):
		for guard in self.write_guards:
			guard.assert_not_called()

	def patch(self, owner, name, *args, **kwargs):
		patcher = patch.object(owner, name, *args, **kwargs)
		result = patcher.start()
		self.addCleanup(patcher.stop)
		return result

	@staticmethod
	def make_quote(*rows, **values):
		return NGSQuote(
			{
				"doctype": "NGS Quote",
				"status": "Draft",
				"customer": "CUSTOMER-TEST",
				"__islocal": 0 if values.get("name") else 1,
				"items": [{"quantity": 1, **row} for row in rows],
				**values,
			}
		)

	def get_value(self, doctype, filters, fieldname, as_dict=False, **kwargs):
		if doctype == "User":
			self.assertEqual(filters, self.session.user)
			self.assertIn(fieldname, ("enabled", "user_type"))
			return self.enabled if fieldname == "enabled" else self.user_type
		self.assertEqual(doctype, "NGS Service Catalog")
		if isinstance(filters, dict):
			self.assertEqual(set(filters), {"service_code"})
			row = next(
				(row for row in self.catalog.values() if row.service_code == filters["service_code"]), None
			)
		else:
			row = self.catalog.get(filters)
		if row is None:
			return None
		if isinstance(fieldname, (list, tuple)):
			self.assertTrue(as_dict)
			return frappe._dict({field: row.get(field) for field in fieldname})
		return row.get(fieldname)

	def get_cached_doc(self, doctype, name):
		if doctype == "NGS Customer":
			self.assertEqual(name, self.customer.name)
			return self.customer
		self.assertEqual(doctype, "NGS Service Catalog")
		self.assertIn(name, self.catalog)
		return self.catalog[name]

	def get_document(self, doctype, name):
		if doctype == "NGS Quote":
			self.assertEqual(name, self.existing_quote.name)
			return self.existing_quote
		self.assertEqual((doctype, name), ("NGS Customer", self.customer.name))
		return self.customer

	def new_document(self, doctype):
		self.assertEqual(doctype, "NGS Quote")
		quote = self.make_quote()
		self.new_quotes.append(quote)
		return quote

	def has_permission(self, doctype, ptype, throw=False):
		self.assertEqual((doctype, ptype, throw), ("NGS Quote", "create", True))
		if not self.allow_create:
			raise frappe.PermissionError("Quotation create denied")
		return True

	def check_permission(self, doc, ptype):
		if doc.doctype == "NGS Quote":
			self.assertIs(doc, self.existing_quote)
			self.assertEqual(ptype, "write")
			allowed = self.allow_write
		else:
			self.assertIs(doc, self.customer)
			self.assertEqual(ptype, "read")
			allowed = self.allow_customer_read
		if not allowed:
			raise frappe.PermissionError(f"{doc.doctype} {ptype} denied")

	def price(self, project_type, **values):
		quote = self.make_quote({"project_type": project_type, **values})
		quote.apply_quote_rules()
		return quote, quote.items[0]

	@staticmethod
	def snapshot(quote):
		return {
			"items": [{field: row.get(field) for field in desk_quote.ITEM_FIELDS} for row in quote.items],
			**{
				field: quote.get(field)
				for field in (
					"status",
					"missing_info",
					"pricing_to_confirm",
					"total_amount",
					"project_summary",
				)
			},
		}


class TestDeskQuotePricing(DeskQuoteTestCase):
	def test_all_fourteen_canonical_products_have_real_pricing_and_defaults(self):
		self.assertEqual(len(PRODUCT_CASES), 14)
		self.assertCountEqual(
			[product["project_type"] for product in quote_products.PRODUCTS],
			[case[0] for case in PRODUCT_CASES],
		)
		for project, service, sample, analysis, reads, expected in PRODUCT_CASES:
			with self.subTest(project=project):
				quote, row = self.price(project)
				self.assertIsInstance(quote, NGSQuote)
				self.assertIsInstance(row, NGSQuoteItem)
				self.assertEqual(
					(row.service, row.sample_type, row.data_analysis), (service, sample, analysis)
				)
				self.assertEqual(row.million_reads_per_sample, reads)
				self.assertAlmostEqual(row.unit_price, expected)
				self.assertAlmostEqual(row.amount, expected)
				self.assertEqual(row.library_qc, int(project.startswith("Sequencing Only")))
				self.assertEqual(quote.pricing_to_confirm, int(project == "Custom Project"))
				if project == "WGS":
					self.assertEqual((row.read_depth, row.species), ("30x", "human"))

	def test_microbial_size_bands_and_optional_dna_extraction(self):
		for subtype, base in (("<7M", 65), ("7-20M", 120), ("20-60M", 150)):
			for sample, extraction in (("DNA", 0), ("Sample for DNA Extraction", 15)):
				with self.subTest(subtype=subtype, sample=sample):
					_, row = self.price(
						f"Microbial Genome Sequencing ({subtype})", sample_type=sample, quantity=3
					)
					self.assertEqual(row.unit_price, base + extraction)
					self.assertEqual(row.amount, (base + extraction) * 3)
					self.assertEqual(row.rule_notes.count("DNA extraction added."), int(bool(extraction)))

	def test_sequencing_150m_is_per_library_not_a_quantity_multiplier(self):
		_, row = self.price("Sequencing Only - 1M Reads", million_reads_per_sample=150, quantity=2)
		self.assertEqual(row.unit_price, 150 * 1.9 + 18)
		self.assertEqual(row.amount, 606)
		self.assertEqual((row.quantity, row.library_qc, row.add_on_sequencing), (2, 1, 0))
		self.assertEqual(row.rule_notes.count("Library QC added."), 1)

	def test_two_libraries_with_100m_and_50m_total_321_with_qc_once_each(self):
		quote = self.make_quote(
			*[
				{"project_type": "Sequencing Only - 1M Reads", "million_reads_per_sample": reads}
				for reads in (100, 50)
			]
		)
		quote.validate()
		self.assertEqual([row.unit_price for row in quote.items], [208, 113])
		self.assertEqual(quote.total_amount, 321)
		for row in quote.items:
			self.assertEqual(row.quantity, 1)
			self.assertEqual(row.rule_notes.count("Library QC added."), 1)

	def test_bulk_tier_boundaries_and_analysis(self):
		for quantity, base in (
			(1, 109),
			(11, 109),
			(12, 99),
			(48, 99),
			(49, 94),
			(96, 94),
			(97, 89),
			(200, 89),
		):
			for analysis, fee in (("No", 0), ("Standard Analysis", 0), ("De novo Assembly", 30)):
				with self.subTest(quantity=quantity, analysis=analysis):
					_, row = self.price("Bulk RNAseq", quantity=quantity, data_analysis=analysis)
					self.assertEqual(row.unit_price, base + fee)
					self.assertEqual(row.amount, (base + fee) * quantity)

	def test_shotgun_included_reads_extra_reads_and_both_analysis_options(self):
		for reads, extra in ((20, 0), (21, 1.5), (100, 120)):
			for analysis, fee in (("No", 0), ("Mapping", 30), ("De novo Assembly", 30)):
				with self.subTest(reads=reads, analysis=analysis):
					_, row = self.price(
						"Shotgun Meta", million_reads_per_sample=reads, data_analysis=analysis
					)
					self.assertEqual(row.unit_price, 130 + extra + fee)
					self.assertEqual(row.add_on_sequencing, int(reads > 20))

	def test_tenx_subtype_included_reads_extra_reads_and_300_preprocessing(self):
		for project, _, _, _, reads, base in PRODUCT_CASES[2:6]:
			for sample, dissociation, nuclei in (
				("Fresh Cells", 0, 0),
				("Fresh Tissue or Organoid", 1, 0),
				("Frozen Cells", 0, 1),
				("Frozen Tissue or Organoid", 0, 1),
			):
				for extra_reads in (0, 10):
					with self.subTest(project=project, sample=sample, extra=extra_reads):
						_, row = self.price(
							project, sample_type=sample, million_reads_per_sample=reads + extra_reads
						)
						self.assertEqual(
							row.unit_price, base + 300 * (dissociation + nuclei) + extra_reads * 1.5
						)
						self.assertEqual(
							(row.tissue_dissociation, row.nuclei_extraction), (dissociation, nuclei)
						)

	def test_wgs_formula_volume_boundary_depth_species_and_extraction(self):
		for quantity, rate in ((1, 11.8), (11, 11.8), (12, 10.8), (24, 10.8)):
			for depth in (1, 30, 60):
				for sample, extraction in (
					("DNA", 0),
					("Cells", 15),
					("Tissue", 15),
					("Plasma", 15),
					("Serum", 15),
					("Blood", 30),
					("Saliva", 30),
					("Swabs", 30),
				):
					with self.subTest(quantity=quantity, depth=depth, sample=sample):
						_, row = self.price(
							"WGS", quantity=quantity, read_depth=f"{depth}X", sample_type=sample
						)
						self.assertAlmostEqual(row.unit_price, 45 + depth * rate + extraction)
						self.assertAlmostEqual(row.amount, (45 + depth * rate + extraction) * quantity)
						self.assertEqual(row.read_depth, f"{depth}x")
		for species in ("human", "mouse", "hamster"):
			with self.subTest(species=species):
				_, row = self.price("WGS", species=species)
				self.assertEqual((row.species, row.unit_price), (species, 399))

	def test_repeated_apply_and_save_validation_are_idempotent_for_all_products(self):
		for project, _, sample, analysis, reads, _ in PRODUCT_CASES:
			with self.subTest(project=project):
				values = {
					"project_type": project,
					"sample_type": sample,
					"data_analysis": analysis,
					"quantity": 2,
				}
				if reads:
					values["million_reads_per_sample"] = reads + 10
				if project == "Custom Project":
					values.update(use_price_override=1, override_unit_price=123)
				quote = self.make_quote(values)
				# validate is the real save-time hook; persistence is deliberately forbidden.
				quote.validate()
				expected = self.snapshot(quote)
				for _ in range(3):
					quote.apply_quote_rules()
					quote.validate()
					self.assertEqual(self.snapshot(quote), expected)

	def test_manual_override_replaces_full_computed_unit_price_including_addons(self):
		for price in (0, 190, "190.25"):
			with self.subTest(price=price):
				quote, row = self.price(
					"Sequencing Only - 1M Reads",
					quantity=2,
					million_reads_per_sample=150,
					use_price_override=1,
					override_unit_price=price,
				)
				self.assertEqual(row.unit_price, float(price))
				self.assertEqual(row.amount, float(price) * 2)
				quote.apply_quote_rules()
				self.assertEqual(row.unit_price, float(price))
				self.assertEqual(row.rule_notes.count("Manual unit price:"), 1)
				self.assertEqual(row.rule_notes.count("Library QC added."), 1)

	def test_zero_custom_override_remains_pending_and_positive_override_is_confirmable(self):
		for price, pending in ((0, 1), (123, 0)):
			with self.subTest(price=price):
				quote, row = self.price("Custom Project", use_price_override=1, override_unit_price=price)
				self.assertEqual(row.unit_price, price)
				self.assertEqual(quote.pricing_to_confirm, pending)
				self.assertEqual(bool(quote.missing_info), bool(pending))

	def test_override_disabled_recomputes_forged_price_amount_and_notes(self):
		for flag in (0, "0", None):
			with self.subTest(flag=flag):
				_, row = self.price(
					"Shotgun Meta",
					million_reads_per_sample=100,
					data_analysis="Mapping",
					use_price_override=flag,
					override_unit_price=1,
					unit_price=1,
					amount=1,
					rule_notes="forged",
				)
				self.assertEqual((row.unit_price, row.amount), (280, 280))
				self.assertNotIn("forged", row.rule_notes)
				self.assertNotIn("Manual unit price:", row.rule_notes)

	def test_on_site_fees_and_unconfirmed_out_of_state_status(self):
		for location, address, fee, expected, pending in (
			("Massachusetts", "1 Main St, Boston, MA 02110", 0, 2700, 0),
			("Outside Massachusetts", "1 Main St, New York, NY 10001", 450, 2950, 0),
			("Outside Massachusetts", "1 Main St, New York, NY 10001", 0, 2500, 1),
		):
			with self.subTest(location=location, fee=fee):
				quote, row = self.price(
					"10x 3' Transcriptome",
					onsite_service=1,
					onsite_location=location,
					onsite_address=address,
					confirmed_tbd_fee=fee,
				)
				self.assertEqual(row.unit_price, expected)
				self.assertEqual(quote.pricing_to_confirm, pending)
				quote.apply_quote_rules()
				self.assertEqual(row.unit_price, expected)


class TestDeskQuoteNormalization(DeskQuoteTestCase):
	def test_service_code_resolves_to_canonical_document_name(self):
		service = self.catalog.pop("SHOTGUN_META_20M")
		service.name = "CATALOG-RENAMED-SHOTGUN"
		self.catalog[service.name] = service
		_, row = self.price("Shotgun Meta")
		self.assertEqual((row.service, row.unit_price), (service.name, 130))

	def test_mismatched_service_is_rejected_even_with_override(self):
		for project, service in (("Bulk RNAseq", "WGS_30X"), ("Custom Project", "BULK_RNASEQ_20M")):
			for override in (0, 1):
				with (
					self.subTest(project=project, override=override),
					self.assertRaises(frappe.ValidationError),
				):
					self.price(project, service=service, use_price_override=override, override_unit_price=10)

	def test_unknown_project_and_invalid_sample_analysis_species_are_rejected(self):
		for values in (
			{"project_type": "Not a product"},
			{"project_type": ""},
			{"project_type": "Bulk RNAseq", "sample_type": "Library"},
			{"project_type": "Shotgun Meta", "data_analysis": "Standard Analysis"},
			{"project_type": "Sequencing Only - 1M Reads", "data_analysis": "Mapping"},
			{"project_type": "WGS", "species": "rat"},
			{"project_type": "WGS", "species": "Human"},
		):
			with self.subTest(values=values), self.assertRaises(frappe.ValidationError):
				self.price(**values)

	def test_all_advertised_sample_and_analysis_choices_are_accepted(self):
		for product in quote_products.PRODUCTS:
			for sample in product["samples"]:
				for analysis in product["analyses"]:
					with self.subTest(project=product["project_type"], sample=sample, analysis=analysis):
						_, row = self.price(
							product["project_type"], sample_type=sample, data_analysis=analysis
						)
						self.assertEqual((row.sample_type, row.data_analysis), (sample, analysis))

	def test_quantity_reads_and_check_strings_are_coerced(self):
		_, row = self.price(
			"Sequencing Only - 1M Reads",
			quantity="2",
			million_reads_per_sample="150",
			use_price_override="0",
			override_unit_price=-1,
			onsite_service="0",
		)
		self.assertEqual((row.quantity, row.million_reads_per_sample, row.amount), (2, 150, 606))
		self.assertEqual((row.use_price_override, row.onsite_service), (0, 0))
		_, row = self.price(
			"10x 3' Transcriptome",
			onsite_service="1",
			onsite_location="Outside Massachusetts",
			onsite_address="New York, NY 10001",
			confirmed_tbd_fee="100",
			use_price_override="1",
			override_unit_price="200",
		)
		self.assertEqual(
			(row.onsite_service, row.use_price_override, row.confirmed_tbd_fee, row.unit_price),
			(1, 1, 100, 200),
		)

	def test_invalid_quantity_reads_manual_price_and_tbd_fee_are_rejected(self):
		for field, project, invalid_values, extra in (
			("quantity", "Bulk RNAseq", (None, "", 0, -1, "not-a-number", *NONFINITE), {}),
			("million_reads_per_sample", "Shotgun Meta", (-1, "not-a-number", *NONFINITE), {}),
			(
				"override_unit_price",
				"Bulk RNAseq",
				(None, "", -1, "not-a-number", *NONFINITE),
				{"use_price_override": 1},
			),
			(
				"confirmed_tbd_fee",
				"10x 3' Transcriptome",
				(-1, "not-a-number", *NONFINITE),
				{
					"onsite_service": 1,
					"onsite_location": "Outside Massachusetts",
					"onsite_address": "New York, NY",
				},
			),
		):
			for invalid in invalid_values:
				with self.subTest(field=field, invalid=invalid), self.assertRaises(frappe.ValidationError):
					self.price(project, **{field: invalid, **extra})

	def test_included_reads_cannot_be_reduced_even_with_a_price_override(self):
		for project, _, _, _, reads, _ in PRODUCT_CASES:
			if not reads or project.startswith("Sequencing Only"):
				continue
			with self.subTest(project=project), self.assertRaises(frappe.ValidationError):
				self.price(
					project, million_reads_per_sample=reads - 1, use_price_override=1, override_unit_price=1
				)

	def test_wgs_depth_outside_one_to_sixty_and_malformed_depth_are_rejected(self):
		for depth in ("0x", "0.5x", "61x", "-1x", "not-a-depth"):
			with self.subTest(depth=depth), self.assertRaises(frappe.ValidationError):
				self.price("WGS", read_depth=depth)

	def test_product_change_clears_irrelevant_flags_and_inputs(self):
		quote, row = self.price(
			"10x 3' Transcriptome",
			sample_type="Frozen Tissue or Organoid",
			onsite_service=1,
			onsite_location="Outside Massachusetts",
			onsite_address="New York, NY",
			confirmed_tbd_fee=500,
			million_reads_per_sample=400,
		)
		row.update(
			{
				"project_type": "Microbial Genome Sequencing (<7M)",
				"service": None,
				"sample_type": "DNA",
				"data_analysis": "No",
				"species": "mouse",
				"read_depth": "60x",
				"library_qc": 1,
				"tissue_dissociation": 1,
			}
		)
		quote.apply_quote_rules()
		for field in (
			"species",
			"read_depth",
			"million_reads_per_sample",
			"onsite_location",
			"onsite_address",
		):
			self.assertIsNone(row.get(field), field)
		for field in (
			"onsite_service",
			"confirmed_tbd_fee",
			"tissue_dissociation",
			"nuclei_extraction",
			"library_qc",
			"add_on_sequencing",
		):
			self.assertEqual(row.get(field), 0, field)
		self.assertEqual(row.unit_price, 65)
		self.assertNotIn("on-site service confirmed", row.rule_notes)

	def test_turning_off_onsite_and_changing_sample_clears_derived_preprocessing(self):
		quote, row = self.price(
			"10x 3' Transcriptome",
			sample_type="Fresh Tissue or Organoid",
			onsite_service=1,
			onsite_location="Massachusetts",
			onsite_address="Boston, MA",
			confirmed_tbd_fee=500,
		)
		row.update({"onsite_service": "0", "sample_type": "Fresh Cells"})
		quote.apply_quote_rules()
		self.assertEqual((row.onsite_service, row.tissue_dissociation, row.nuclei_extraction), (0, 0, 0))
		self.assertEqual((row.onsite_location, row.onsite_address, row.confirmed_tbd_fee), (None, None, 0))
		self.assertEqual(row.unit_price, 2500)

	def test_disabled_service_is_allowed_only_on_the_same_stored_row(self):
		quote, row = self.price("Bulk RNAseq")
		quote.name, row.name = "QUOTE-STORED", "ITEM-STORED"
		quote.set("__islocal", 0)
		previous = deepcopy(quote)
		self.catalog["BULK_RNASEQ_20M"].enabled = 0
		self.catalog["WGS_30X"].enabled = 0
		quote._doc_before_save = previous
		quote.apply_quote_rules()
		self.assertEqual((row.service, row.unit_price), ("BULK_RNASEQ_20M", 109))
		for scenario in ("new_quote", "new_row", "changed_product", "cleared_service", "no_previous"):
			with self.subTest(scenario=scenario):
				candidate = deepcopy(previous)
				candidate._doc_before_save = previous
				if scenario == "new_quote":
					candidate.set("__islocal", 1)
				elif scenario == "new_row":
					candidate.items[0].name = "ITEM-NEW"
				elif scenario == "changed_product":
					candidate.items[0].update(
						{"project_type": "WGS", "service": "WGS_30X", "sample_type": "DNA"}
					)
				elif scenario == "cleared_service":
					candidate.items[0].service = None
				else:
					candidate._doc_before_save = None
				with self.assertRaisesRegex(frappe.ValidationError, "unavailable"):
					candidate.apply_quote_rules()

	def test_historical_allowance_does_not_accept_missing_or_mismatched_catalog(self):
		quote, row = self.price("Bulk RNAseq")
		quote.name, row.name = "QUOTE-STORED", "ITEM-STORED"
		quote.set("__islocal", 0)
		quote._doc_before_save = deepcopy(quote)
		service = self.catalog["BULK_RNASEQ_20M"]
		service.enabled = 0
		service.project_type = "WGS"
		with self.assertRaisesRegex(frappe.ValidationError, "unavailable"):
			quote.apply_quote_rules()
		del self.catalog[service.name]
		with self.assertRaisesRegex(frappe.ValidationError, "unavailable"):
			quote.apply_quote_rules()

	def test_unchanged_legacy_custom_service_and_species_remain_saveable(self):
		for values, expected in (
			(
				{
					"project_type": "Custom Project",
					"service": "LIBRARY_QC",
					"sample_type": "Library",
					"data_analysis": "No",
				},
				18,
			),
			({"project_type": "WGS", "service": "WGS_30X", "sample_type": "DNA", "species": "Human"}, 399),
		):
			with self.subTest(values=values):
				quote = self.make_quote({"name": "LEGACY-ITEM", **values}, name="LEGACY-QUOTE")
				quote._doc_before_save = deepcopy(quote)
				quote.status = "Cancelled"
				quote.validate()
				self.assertEqual(quote.items[0].unit_price, expected)
				self.assertEqual(quote.status, "Cancelled")

	def test_new_custom_project_needs_explicit_price_not_a_carried_over_value(self):
		for values in (
			{"unit_price": 130},
			{"unit_price": 123, "use_price_override": 0, "override_unit_price": 123},
		):
			with self.subTest(values=values):
				quote, row = self.price("Custom Project", **values)
				self.assertEqual(row.unit_price, 0)
				self.assertEqual(quote.pricing_to_confirm, 1)
				self.assertTrue(quote.missing_info)

	def test_clearing_stored_custom_override_requires_new_confirmed_price(self):
		quote, row = self.price("Custom Project", use_price_override=1, override_unit_price=123)
		quote.name, row.name = "QUOTE-STORED", "ITEM-STORED"
		quote.set("__islocal", 0)
		quote._doc_before_save = deepcopy(quote)
		row.use_price_override = 0
		quote.apply_quote_rules()
		self.assertEqual(row.unit_price, 0)
		self.assertEqual(quote.pricing_to_confirm, 1)
		self.assertNotIn("Manual unit price:", row.rule_notes)

	def test_legacy_custom_manual_price_without_override_is_preserved(self):
		quote = self.make_quote(
			{"name": "LEGACY-ITEM", "project_type": "Custom Project", "unit_price": 123}, name="LEGACY-QUOTE"
		)
		quote._doc_before_save = deepcopy(quote)
		quote.validate()
		self.assertEqual(quote.items[0].unit_price, 123)
		self.assertFalse(quote.pricing_to_confirm)


class TestDeskQuoteMetadataAndPreview(DeskQuoteTestCase):
	def test_metadata_has_all_fourteen_exact_products_and_catalog_services(self):
		products = desk_quote.get_quote_builder_options()["products"]
		self.assertEqual(len(products), 14)
		by_project = {product["project_type"]: product for product in products}
		self.assertEqual(len(by_project), 14)
		for project, service, sample, analysis, reads, _ in PRODUCT_CASES:
			with self.subTest(project=project):
				product = by_project[project]
				self.assertEqual(product["service"], service)
				self.assertEqual(product["standard_reads"], reads or 0)
				self.assertIn(sample, product["samples"])
				self.assertIn(analysis, product["analyses"])
				self.assertEqual(
					product["species"], ["human", "mouse", "hamster"] if project == "WGS" else []
				)
		self.assertEqual(sum(product["group"] == "10x Single Cell" for product in products), 4)
		self.assertEqual(sum(product["group"] == "Microbial Genome Sequencing" for product in products), 3)

	def test_metadata_filters_missing_disabled_and_mismatched_catalog_choices(self):
		for state in ("missing", "disabled", "mismatched"):
			with self.subTest(state=state):
				original = self.catalog["SHOTGUN_META_20M"]
				if state == "missing":
					del self.catalog[original.name]
				else:
					self.catalog[original.name] = frappe._dict(
						{**original, **({"enabled": 0} if state == "disabled" else {"project_type": "WGS"})}
					)
				products = desk_quote.get_quote_builder_options()["products"]
				self.assertEqual(len(products), 13)
				self.assertNotIn("Shotgun Meta", [product["project_type"] for product in products])
				with self.assertRaises(frappe.ValidationError):
					desk_quote.preview_quote_item({"project_type": "Shotgun Meta", "quantity": 1})
				self.catalog[original.name] = original

	def test_metadata_uses_catalog_reads_and_falls_back_to_product_default(self):
		for catalog_reads, expected in ((25, 25), (0, 20)):
			with self.subTest(reads=catalog_reads):
				self.catalog["SHOTGUN_META_20M"].standard_reads_million = catalog_reads
				product = next(
					product
					for product in desk_quote.get_quote_builder_options()["products"]
					if product["project_type"] == "Shotgun Meta"
				)
				self.assertEqual(product["standard_reads"], expected)
				preview = desk_quote.preview_quote_item({"project_type": "Shotgun Meta", "quantity": 1})
				self.assertEqual(preview["million_reads_per_sample"], expected)

	def test_preview_matches_real_save_validation_for_all_products_without_persisting(self):
		for project, _, _, _, _, _ in PRODUCT_CASES:
			with self.subTest(project=project):
				values = {"project_type": project, "quantity": 2}
				preview = desk_quote.preview_quote_item(values)
				quote = self.make_quote(values)
				quote.validate()
				self.assertEqual(
					preview, {field: quote.items[0].get(field) for field in desk_quote.ITEM_FIELDS}
				)
				self.assertEqual(set(preview), set(desk_quote.ITEM_FIELDS))
				self.assertIsInstance(self.new_quotes[-1], NGSQuote)
				self.assertIsInstance(self.new_quotes[-1].items[0], NGSQuoteItem)

	def test_preview_json_is_idempotent_and_does_not_change_existing_quote_or_payload(self):
		self.existing_quote.append("items", {"project_type": "Bulk RNAseq", "quantity": 1})
		before = self.snapshot(self.existing_quote)
		values = {
			"project_type": "Shotgun Meta",
			"quantity": 2,
			"million_reads_per_sample": 100,
			"data_analysis": "Mapping",
		}
		original = deepcopy(values)
		preview = desk_quote.preview_quote_item(values, quote_name="QUOTE-TEST", customer="CUSTOMER-TEST")
		self.assertEqual((preview["unit_price"], preview["amount"]), (280, 560))
		for _ in range(3):
			self.assertEqual(
				desk_quote.preview_quote_item(
					json.dumps(preview), quote_name="QUOTE-TEST", customer="CUSTOMER-TEST"
				),
				preview,
			)
		self.assertEqual(values, original)
		self.assertEqual(self.snapshot(self.existing_quote), before)
		self.assertEqual(len({id(quote) for quote in self.new_quotes}), 4)

	def test_preview_ignores_non_item_fields_and_recalculates_derived_fields(self):
		preview = desk_quote.preview_quote_item(
			{
				"project_type": "Sequencing Only - 1M Reads",
				"quantity": 1,
				"million_reads_per_sample": 100,
				"doctype": "User",
				"name": "ADMIN-ROW",
				"parent": "VICTIM",
				"owner": "Administrator",
				"ignore_permissions": 1,
				"items": [],
				"unit_price": 0.01,
				"amount": 0.01,
				"library_qc": 0,
				"rule_notes": "forged",
			}
		)
		self.assertEqual((preview["unit_price"], preview["amount"], preview["library_qc"]), (208, 208, 1))
		row = self.new_quotes[-1].items[0]
		self.assertEqual(row.doctype, "NGS Quote Item")
		self.assertNotEqual(row.name, "ADMIN-ROW")
		self.assertNotEqual(row.parent, "VICTIM")
		self.assertFalse(row.get("ignore_permissions"))
		self.assertNotIn("forged", preview["rule_notes"])

	def test_preview_requires_one_object_not_array_scalar_or_null(self):
		for values in (None, [], 1, True, "null", "[]", "1", '"text"'):
			with self.subTest(values=values), self.assertRaises(frappe.ValidationError):
				desk_quote.preview_quote_item(values)
		self.new_doc.assert_not_called()

	def test_preview_of_historical_disabled_service_is_still_rejected(self):
		self.existing_quote.append(
			"items",
			{
				"name": "ITEM-STORED",
				"project_type": "Bulk RNAseq",
				"service": "BULK_RNASEQ_20M",
				"quantity": 1,
			},
		)
		self.existing_quote.validate()
		self.existing_quote._doc_before_save = deepcopy(self.existing_quote)
		before = self.snapshot(self.existing_quote)
		self.catalog["BULK_RNASEQ_20M"].enabled = 0
		for use_override in (0, 1):
			with (
				self.subTest(override=use_override),
				self.assertRaisesRegex(frappe.ValidationError, "unavailable"),
			):
				desk_quote.preview_quote_item(
					{
						"project_type": "Bulk RNAseq",
						"service": "BULK_RNASEQ_20M",
						"name": "ITEM-STORED",
						"quantity": 1,
						"use_price_override": use_override,
						"override_unit_price": 0,
						"allow_disabled": True,
						"__islocal": 0,
					},
					quote_name="QUOTE-TEST",
				)
		self.assertEqual(self.snapshot(self.existing_quote), before)


class TestDeskQuotePermissions(DeskQuoteTestCase):
	def endpoints(self, **kwargs):
		return (
			("metadata", lambda: desk_quote.get_quote_builder_options(**kwargs)),
			(
				"preview",
				lambda: desk_quote.preview_quote_item(
					{
						"project_type": "Bulk RNAseq",
						"quantity": 1,
						"use_price_override": 1,
						"override_unit_price": 0,
						"ignore_permissions": 1,
					},
					**kwargs,
				),
			),
		)

	def test_guest_website_disabled_and_unrelated_role_cannot_use_either_api(self):
		for user, user_type, enabled, roles in (
			("Guest", "System User", 1, ["NGS Internal Staff"]),
			("", "System User", 1, ["System Manager"]),
			("website@example.test", "Website User", 1, ["NGS Internal Staff", "System Manager"]),
			("unrelated@example.test", "System User", 1, ["Sales User"]),
			("disabled@example.test", "System User", 0, ["System Manager"]),
		):
			self.session.user, self.user_type, self.enabled, self.roles = user, user_type, enabled, roles
			for name, endpoint in self.endpoints():
				with self.subTest(user=user, api=name), self.assertRaises(frappe.PermissionError):
					endpoint()
		self.permission.assert_not_called()
		self.get_doc.assert_not_called()
		self.new_doc.assert_not_called()
		self.assertFalse(any(call.args[0] == "NGS Service Catalog" for call in self.lookup.call_args_list))

	def test_internal_staff_manager_and_administrator_are_allowed(self):
		for user, roles in (
			("staff@example.test", ["NGS Internal Staff"]),
			("manager@example.test", ["System Manager"]),
			("Administrator", []),
		):
			self.session.user, self.roles = user, roles
			for name, endpoint in self.endpoints(customer="CUSTOMER-TEST"):
				with self.subTest(user=user, api=name):
					self.assertIsInstance(endpoint(), dict)

	def test_api_user_without_create_permission_is_denied_before_catalog_access(self):
		self.session.user = "api-user@example.test"
		self.allow_create = False
		for name, endpoint in self.endpoints():
			with self.subTest(api=name), self.assertRaises(frappe.PermissionError):
				endpoint()
		self.assertEqual(self.permission.call_count, 2)
		self.assertFalse(any(call.args[0] == "NGS Service Catalog" for call in self.lookup.call_args_list))
		self.new_doc.assert_not_called()

	def test_existing_quote_requires_write_not_create_permission(self):
		self.allow_create = False
		for name, endpoint in self.endpoints(quote_name="QUOTE-TEST"):
			with self.subTest(api=name):
				endpoint()
		self.permission.assert_not_called()
		self.assertEqual(self.doc_permission.call_count, 2)
		self.allow_write = False
		self.lookup.reset_mock()
		for name, endpoint in self.endpoints(quote_name="QUOTE-TEST"):
			with self.subTest(denied_api=name), self.assertRaises(frappe.PermissionError):
				endpoint()
		self.assertFalse(any(call.args[0] == "NGS Service Catalog" for call in self.lookup.call_args_list))

	def test_customer_read_permission_is_required_for_create_and_existing_quote(self):
		self.allow_customer_read = False
		for quote_name in (None, "QUOTE-TEST"):
			for name, endpoint in self.endpoints(quote_name=quote_name, customer="CUSTOMER-TEST"):
				with self.subTest(quote=quote_name, api=name), self.assertRaises(frappe.PermissionError):
					endpoint()
		self.assertFalse(any(call.args[0] == "NGS Service Catalog" for call in self.lookup.call_args_list))
		self.new_doc.assert_not_called()

	def test_direct_pricing_cannot_bypass_manual_override_role_and_user_type_checks(self):
		for user, user_type, roles in (
			("Guest", "Website User", []),
			("website@example.test", "Website User", ["NGS Internal Staff", "System Manager"]),
			("unrelated@example.test", "System User", ["Sales User"]),
		):
			self.session.user, self.user_type, self.roles = user, user_type, roles
			for project in ("Bulk RNAseq", "Custom Project"):
				with self.subTest(user=user, project=project), self.assertRaises(frappe.PermissionError):
					self.price(project, use_price_override=1, override_unit_price=0)

	def test_website_cancellation_preserves_only_unchanged_internal_override(self):
		quote, row = self.price(
			"Sequencing Only - 1M Reads",
			quantity=2,
			million_reads_per_sample=150,
			use_price_override=1,
			override_unit_price=190,
		)
		row.name = "ITEM-TEST"
		quote.set("__islocal", 0)
		previous = deepcopy(quote)
		quote._doc_before_save = previous
		self.session.user, self.user_type, self.roles = "website@example.test", "Website User", []
		quote.status = "Cancelled"
		quote.validate()
		self.assertEqual((row.unit_price, row.amount), (190, 380))
		for field, changed in (
			("override_unit_price", 0),
			("quantity", 3),
			("project_type", "Custom Project"),
			("million_reads_per_sample", 200),
			("data_analysis", "Mapping"),
			("onsite_service", 1),
			("confirmed_tbd_fee", 500),
		):
			with self.subTest(field=field):
				old_value = row.get(field)
				row.set(field, changed)
				with self.assertRaises(frappe.PermissionError):
					quote.validate_price_override(row)
				row.set(field, old_value)
		row.name = "NEW-ITEM"
		with self.assertRaises(frappe.PermissionError):
			quote.validate_price_override(row)

	def test_website_can_cancel_unchanged_stored_rows_after_catalog_is_disabled(self):
		for use_override, expected in ((0, 303), (1, 190)):
			with self.subTest(override=use_override):
				self.session.user, self.user_type, self.roles = (
					"staff@example.test",
					"System User",
					["NGS Internal Staff"],
				)
				self.catalog["SEQ_ONLY_1M_READS"].enabled = 1
				quote, row = self.price(
					"Sequencing Only - 1M Reads",
					quantity=2,
					million_reads_per_sample=150,
					use_price_override=use_override,
					override_unit_price=190,
				)
				quote.name, row.name = "QUOTE-STORED", "ITEM-STORED"
				quote.set("__islocal", 0)
				quote._doc_before_save = deepcopy(quote)
				self.catalog["SEQ_ONLY_1M_READS"].enabled = 0
				self.session.user, self.user_type, self.roles = "website@example.test", "Website User", []
				quote.status = "Cancelled"
				for _ in range(3):
					quote.validate()
					self.assertEqual(
						(row.unit_price, row.amount, quote.total_amount),
						(expected, expected * 2, expected * 2),
					)
					self.assertEqual(quote.status, "Cancelled")
					self.assertEqual(row.rule_notes.count("Library QC added."), 1)
