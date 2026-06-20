import frappe


def execute():
	if not frappe.db.has_column("NGS Quote Item", "reads_per_sample_million"):
		return
	if frappe.db.has_column("NGS Quote Item", "million_reads_per_sample"):
		frappe.db.sql(
			"""
			UPDATE `tabNGS Quote Item`
			SET million_reads_per_sample = reads_per_sample_million
			WHERE COALESCE(million_reads_per_sample, 0) = 0
				AND COALESCE(reads_per_sample_million, 0) != 0
			"""
		)
	frappe.db.sql_ddl("ALTER TABLE `tabNGS Quote Item` DROP COLUMN `reads_per_sample_million`")
