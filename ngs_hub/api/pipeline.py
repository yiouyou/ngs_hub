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
