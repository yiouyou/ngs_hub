import os
import frappe
import httpx
from frappe import _


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
	# 1) Try loading as a File
	try:
		file_doc = frappe.get_doc("File", file_docname)
	except frappe.DoesNotExistError:
		# 2) Otherwise assume it's your child doctype
		attached = frappe.get_doc("NGS Project Attached File", file_docname)
		file_doc = frappe.get_doc("File", attached.attached_file)

	# file_doc.file_url is something like '/private/files/x.csv' or '/files/x.csv'
	# strip leading slash and build site path
	url = file_doc.file_url.lstrip("/")
	# bench/sites/<site_name>/<url>
	full_path = frappe.get_site_path(*url.split("/"))

	if not os.path.exists(full_path):
		frappe.throw(f"File not found: {full_path}")

	with open(full_path, "r") as f:
		return f.read()
