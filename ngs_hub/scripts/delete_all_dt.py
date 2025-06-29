import frappe


def run():
	doctypes_to_clear = [
		"NGS SingleCell Sequencing Outsource",
		"NGS SingleCell Library Construction",
		"NGS SingleCell 10x Chromium",
		"NGS SingleCell 10x Preprocess",
		"NGS Meta Sequencing Outsource",
		"NGS Meta Library Construction",
		"NGS Meta Sample Extraction",
		"NGS RNAseq Sequencing Outsource",
		"NGS RNAseq Library Construction",
		"NGS RNAseq Enrichment",
		"NGS RNAseq Sample Extraction",
		"NGS Sample Info",
		"NGS Sample Transfer",
		"NGS Project",
		"NGS Customer",
	]

	def safely_delete_all(doctype):
		all_docs = frappe.get_all(doctype, pluck="name")
		deleted = 0
		for name in all_docs:
			try:
				doc = frappe.get_doc(doctype, name)
				if doc.docstatus == 1:
					doc.cancel()
				frappe.delete_doc(doctype, name, ignore_permissions=True)
				deleted += 1
			except Exception as e:
				frappe.log_error(title=f"{doctype} 删除失败", message=f"{name}: {e!s}")
		frappe.db.commit()
		print(f"✅ {doctype}: 成功删除 {deleted} 条记录。")

	for dt in doctypes_to_clear:
		safely_delete_all(dt)
