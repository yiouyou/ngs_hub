import frappe, httpx
from frappe import _


@frappe.whitelist(allowed_roles=["*"])
def validate_run(doc):
	"""doc will be passed as JSON from client."""
	payload = frappe._dict(doc)
	url = frappe.get_conf().pipeline_api_url or "http://localhost:8080"
	resp = httpx.post(f"{url}/pipelines/{payload.class_type}/validate", json=payload)
	resp.raise_for_status()
	return resp.json()


@frappe.whitelist(allowed_roles=["*"])
def run(doc):
	payload = frappe._dict(doc)
	url = frappe.get_conf().pipeline_api_url or "http://localhost:8080"
	resp = httpx.post(f"{url}/pipelines/{payload.class_type}/run", json=payload)
	resp.raise_for_status()
	return resp.json()


@frappe.whitelist()
def get_csv_text(file_docname):
	"""
	Given the name of a File or NGS Project Attached File, return
	the contents of that CSV as a string.
	"""
	# If you store attachments as File docs:
	file_record = frappe.get_doc("File", file_docname)
	# get_full_path works for private/public files
	path = file_record.get_full_path()
	with open(path, "r") as f:
		return f.read()
