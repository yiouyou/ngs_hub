"""Refresh searchable list summaries without repricing or running document hooks."""

import frappe

from ngs_hub.api.service_summary import build_project_summary


def execute():
	for doctype in ("NGS Quote", "NGS Order"):
		start = 0
		while names := frappe.get_all(
			doctype, pluck="name", order_by="name", limit_start=start, limit_page_length=200
		):
			for name in names:
				doc = frappe.get_doc(doctype, name)
				frappe.db.set_value(
					doctype, name, "project_summary", build_project_summary(doc.items), update_modified=False
				)
				frappe.clear_document_cache(doctype, name)
			start += len(names)
