import frappe, httpx
from frappe import _
from frappe.utils.file_manager import get_full_path


@frappe.whitelist()
def validate_run(doc):
	"""doc will be passed as JSON from client."""
	payload = frappe._dict(doc)
	url = frappe.get_conf().pipeline_api_url or "http://localhost:8080"
	resp = httpx.post(f"{url}/pipelines/{payload.class_type}/validate", json=payload)
	resp.raise_for_status()
	return resp.json()


@frappe.whitelist()
def run(doc):
	payload = frappe._dict(doc)
	url = frappe.get_conf().pipeline_api_url or "http://localhost:8080"
	resp = httpx.post(f"{url}/pipelines/{payload.class_type}/run", json=payload)
	resp.raise_for_status()
	return resp.json()


@frappe.whitelist()
def get_csv_text(file_docname):
	"""
	Given the name of a File or a child record (NGS Project Attached File),
	return the contents of that CSV as a string.
	"""
	# 1) Try to load it as a File
	try:
		file_doc = frappe.get_doc("File", file_docname)
	except frappe.DoesNotExistError:
		# 2) If that fails, assume it's your child doctype
		attached = frappe.get_doc("NGS Project Attached File", file_docname)
		# adjust this field name if your child table uses a different field
		file_doc = frappe.get_doc("File", attached.attached_file)

	# file_doc.file_url might be "/private/files/xyz.csv" or "/files/xyz.csv"
	# get_full_path will strip the leading slash and prepend your site path
	full_path = get_full_path(file_doc.file_url)

	# now this opens bench/sites/<sitename>/private/files/xyz.csv
	with open(full_path, "r") as f:
		return f.read()
