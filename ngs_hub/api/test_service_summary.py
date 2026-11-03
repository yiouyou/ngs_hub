"""List specification summaries must be informative, bounded, and price-independent."""

from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

from ngs_hub.api.service_summary import build_project_summary, item_summary


class TestServiceSummary(TestCase):
	def test_all_fourteen_service_types_expose_actual_specs_and_quantity(self):
		cases = [
			("Bulk RNAseq", {"million_reads_per_sample": 30}, "30M/sample"),
			("WGS", {"read_depth": "45x", "species": "human"}, "45x"),
			("Shotgun Meta", {"million_reads_per_sample": 25.5}, "25.5M/sample"),
			("Sequencing Only - 1M Reads", {"million_reads_per_sample": 100}, "100M/library"),
			("Sequencing Only - 10B Lane", {}, "10B Lane"),
			("Sequencing Only - 25B Lane", {}, "25B Lane"),
			("Microbial Genome Sequencing (<7M)", {}, "<7M"),
			("Microbial Genome Sequencing (7-20M)", {}, "7-20M"),
			("Microbial Genome Sequencing (20-60M)", {}, "20-60M"),
			("Custom Project", {"description": "Special sample processing"}, "Special sample processing"),
			("10x 3' Transcriptome", {"million_reads_per_sample": 330}, "330M/sample"),
			("10x 5' Transcriptome", {"million_reads_per_sample": 330}, "330M/sample"),
			("10x 5' VDJ", {"million_reads_per_sample": 40}, "40M/sample"),
			("10x 5' Transcriptome + VDJ", {"million_reads_per_sample": 370}, "370M/sample"),
		]
		for project, specs, expected in cases:
			with self.subTest(project=project):
				row = {"project_type": project, "sample_type": "DNA", "quantity": 6, **specs}
				summary = build_project_summary([row])
				self.assertIn(expected, summary)
				self.assertIn("x6", summary)
				self.assertIn("DNA", summary)
				self.assertLessEqual(len(summary), 140)

	def test_identical_classification_does_not_hide_distinct_reads_or_lines(self):
		rows = [
			{"project_type": "Sequencing Only - 1M Reads", "quantity": 1, "million_reads_per_sample": n}
			for n in (100, 50)
		]
		summary = build_project_summary(rows)
		self.assertIn("100M/library", summary)
		self.assertIn("50M/library", summary)
		self.assertEqual(build_project_summary(rows * 2).count("more"), 1)
		self.assertIn("+2 more", build_project_summary(rows * 2))

	def test_analysis_preprocessing_and_extraction_are_visible(self):
		row = {
			"project_type": "10x 5' VDJ",
			"quantity": 2,
			"million_reads_per_sample": 40,
			"sample_type": "Frozen Cells",
			"data_analysis": "Standard Analysis",
			"nuclei_extraction": 1,
		}
		self.assertIn("Nuclei extraction", item_summary(row))
		self.assertIn("Standard Analysis", item_summary(row))
		microbial = {
			"project_type": "Microbial Genome Sequencing (<7M)",
			"quantity": 1,
			"sample_type": "Sample for DNA Extraction",
		}
		self.assertIn("DNA Extraction", build_project_summary([microbial]))

	def test_legacy_million_read_depth_supported_without_looking_up_quote(self):
		row = {"project_type": "Sequencing Only - 1M Reads", "quantity": 1, "read_depth": "100.5M"}
		self.assertIn("100.5M/library", build_project_summary([row]))

	def test_fractional_reads_are_not_rounded_to_six_significant_digits(self):
		row = {
			"project_type": "Sequencing Only - 1M Reads",
			"quantity": 1.1234567,
			"million_reads_per_sample": 100.123456,
		}
		summary = build_project_summary([row])
		self.assertIn("100.123456M/library", summary)
		self.assertIn("x1.1234567", summary)

	def test_unknown_depth_not_inferred_from_catalog_name_and_document_is_unchanged(self):
		row = {"project_type": "WGS", "service": "WGS_30X", "quantity": 1, "sample_type": "DNA"}
		before = deepcopy(row)
		result = build_project_summary([row])
		self.assertIn("coverage not recorded", result)
		self.assertNotIn("30x", result)
		self.assertEqual(row, before)

	def test_long_text_is_bounded_and_more_lines_counted(self):
		rows = [
			{"project_type": "Custom Project", "quantity": 3, "description": "Long custom notes " * 100}
		] * 14
		result = build_project_summary(rows)
		self.assertLessEqual(len(result), 140)
		self.assertIn("x3", result)
		self.assertIn("more", result)
		self.assertEqual(build_project_summary([]), "")

	def test_summary_patch_only_changes_summary_without_validation_or_repricing(self):
		from types import SimpleNamespace

		import frappe

		from ngs_hub.patches.v16_0 import populate_service_summaries

		doc = SimpleNamespace(items=[{"project_type": "WGS", "read_depth": "45x", "quantity": 1}])
		with (
			patch.object(frappe, "get_all", side_effect=[["QUOTE"], [], ["ORDER"], []]),
			patch.object(frappe, "get_doc", return_value=doc),
			patch.object(frappe.db, "set_value") as update,
			patch.object(frappe, "clear_document_cache"),
		):
			populate_service_summaries.execute()
			self.assertEqual(update.call_count, 2)
			for call in update.call_args_list:
				self.assertEqual(call.args[2], "project_summary")
				self.assertEqual(call.kwargs, {"update_modified": False})
