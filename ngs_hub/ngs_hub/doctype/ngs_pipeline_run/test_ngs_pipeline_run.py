import frappe
from frappe.tests.utils import FrappeTestCase


class TestNGSPipelineRun(FrappeTestCase):
	def test_creation_and_validate_wrapper(self):
		run = frappe.get_doc(
			{
				"doctype": "NGS Pipeline Run",
				"project": "PRJ-RNAseq-24.04.29-001",
				"customer": "CUST-001",
				"class_type": "RNAseq",
				"species_type": "Human",
				"input_s3_path": "s3://bucket/sample.csv",
			}
		).insert()
		# call your wrapper
		result = frappe.get_doc("NGS Pipeline Run").run_validate(run.name)
		self.assertIn("command", result)
