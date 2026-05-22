import json
import os

import frappe
import httpx
from frappe import _


def _get_api_base():
    url = frappe.get_conf().pipeline_api_url or "http://api:8080"
    headers = {}
    auth = frappe.request.headers.get("Authorization")
    if auth:
        headers["Authorization"] = auth
    cookie = frappe.request.headers.get("cookie")
    if cookie:
        headers["Cookie"] = cookie
    return url, headers


def extract_api_config(**payload):
    payload = frappe._dict(payload)

    payload.pipeline_config = json.loads(payload.pipeline_config)

    if payload.get("s3_credentials"):
        payload.s3_credentials = json.loads(payload.s3_credentials)

    if payload.get("s3_input_config"):
        payload.s3_input_config = json.loads(payload.s3_input_config)

    if payload.get("s3_output_config"):
        payload.s3_output_config = json.loads(payload.s3_output_config)

    pipeline_type = payload.pipeline_config.get("pipeline_type", "nextflow")

    url, headers = _get_api_base()

    print(f"{url=}")
    print(f"{pipeline_type=}")
    print(f"{payload=}")

    payload.pop("cmd", None)
    if payload.get("s3_output_config") == {}:
        payload["s3_output_config"] = None

    return payload, pipeline_type, url, headers


def _post_pipeline_api(endpoint, payload, headers):
    try:
        resp = httpx.post(endpoint, json=payload, headers=headers, timeout=60.0)
    except httpx.RequestError as e:
        frappe.throw(_("Failed to reach pipeline API: {0}").format(str(e)))

    print(f"{resp.status_code=}")
    print(f"{resp.text=}")

    if resp.is_error:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        if isinstance(detail, (list, dict)):
            detail = json.dumps(detail, indent=2)
        frappe.throw(
            _("Pipeline API error ({0}): {1}").format(resp.status_code, detail)
        )

    return resp.json()


@frappe.whitelist()
def validate_run(**payload):
    payload, pipeline_type, url, headers = extract_api_config(**payload)
    return _post_pipeline_api(
        f"{url}/pipelines/{pipeline_type}/validate", payload, headers
    )


@frappe.whitelist()
def run(**payload):
    payload, pipeline_type, url, headers = extract_api_config(**payload)
    return _post_pipeline_api(f"{url}/pipelines/{pipeline_type}/run", payload, headers)


@frappe.whitelist()
def get_workflow_result(workflow_id):
    url, headers = _get_api_base()
    try:
        resp = httpx.get(
            f"{url}/pipelines/results/{workflow_id}", headers=headers, timeout=30.0
        )
    except httpx.RequestError as e:
        frappe.throw(_("Failed to reach pipeline API: {0}").format(str(e)))

    if resp.is_error:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        if isinstance(detail, (list, dict)):
            detail = json.dumps(detail, indent=2)
        frappe.throw(
            _("Pipeline API error ({0}): {1}").format(resp.status_code, detail)
        )

    return resp.json()


@frappe.whitelist()
def save_workflow_result(**kwargs):
    data = frappe._dict(kwargs)
    doc = frappe.new_doc("NGS Workflow Result")
    doc.project = data.project
    doc.workflow_id = data.workflow_id
    doc.pipeline_name = data.pipeline_name
    doc.status = data.status
    doc.created_at = data.created_at
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc.name


@frappe.whitelist()
def get_csv_text(file_docname):
    if file_docname.startswith("/"):
        urls = frappe.get_all("File", filters={"file_url": file_docname}, pluck="name")
        if not urls:
            frappe.throw(f"File not found in File doctype: {file_docname}")
        file_name = urls[0]
    else:
        file_name = file_docname

    file_doc = frappe.get_doc("File", file_name)
    full_path = file_doc.get_full_path()
    if not os.path.exists(full_path):
        frappe.throw(f"File not found on disk: {full_path}")

    with open(full_path, "r", encoding="utf-8") as f:
        return f.read()
