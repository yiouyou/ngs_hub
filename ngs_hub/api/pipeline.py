import json
import frappe
import httpx
import os
from frappe import _


def extract_api_config(**payload):
	payload = frappe._dict(payload)

	# Normalize JSON string fields into Python objects
	payload.pipeline_config = json.loads(payload.pipeline_config)

	if payload.get("s3_credentials"):
		payload.s3_credentials = json.loads(payload.s3_credentials)

	if payload.get("s3_input_config"):
		payload.s3_input_config = json.loads(payload.s3_input_config)

	if payload.get("s3_output_config"):
		payload.s3_output_config = json.loads(payload.s3_output_config)

	pipeline_type = payload.pipeline_config.get("pipeline_type", "nextflow")

	url = frappe.get_conf().pipeline_api_url or "http://api:8080"

	print(f"{url=}")
	print(f"{pipeline_type=}")
	print(f"{payload=}")

	return payload, pipeline_type, url


@frappe.whitelist()
def validate_run(**payload):
	payload, pipeline_type, url = extract_api_config(**payload)

	resp = httpx.post(f"{url}/pipelines/{pipeline_type}/validate", json=payload)

	resp.raise_for_status()

	return resp.json()


@frappe.whitelist()
def run(**payload):
	payload, pipeline_type, url = extract_api_config(**payload)

	resp = httpx.post(f"{url}/pipelines/{pipeline_type}/run", json=payload)

	resp.raise_for_status()

	return resp.json()


@frappe.whitelist()
def get_csv_text(file_docname):
	# If we got a URL (Attach field), find the File record by file_url
	if file_docname.startswith("/"):
		urls = frappe.get_all("File", filters={"file_url": file_docname}, pluck="name")
		if not urls:
			frappe.throw(f"File not found in File doctype: {file_docname}")
		file_name = urls[0]
	else:
		# it’s already the File.name
		file_name = file_docname

	# Load the File document
	file_doc = frappe.get_doc("File", file_name)

	# Use the built‐in helper to get the full disk path
	full_path = file_doc.get_full_path()
	if not os.path.exists(full_path):
		frappe.throw(f"File not found on disk: {full_path}")

	# Read and return
	with open(full_path, "r", encoding="utf-8") as f:
		return f.read()
