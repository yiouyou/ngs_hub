import frappe
from frappe import _
from frappe.model.document import Document


class NGSPipelineRun(Document):
	def resolve_input_path(self):
		"""Return a single URL/path for downstream processing."""
		if self.source_type == "Existing Attachment":
			# assumes `existing_attachment` is the File docname
			file_doc = frappe.get_doc("File", self.existing_attachment)
			return file_doc.file_url
		if self.source_type == "Upload File":
			# frappe stores attachments as File and returns a URL
			return self.upload_csv
		if self.source_type == "S3 Path":
			return self.s3_input_path
		frappe.throw(f"Unknown source_type: {self.source_type}")
