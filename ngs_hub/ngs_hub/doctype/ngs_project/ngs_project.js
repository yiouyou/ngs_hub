// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("NGS Project", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on("NGS Project", {
	refresh(frm) {
		frm.add_custom_button(__("→ Customer"), () => {
			if (frm.doc.customer) {
				frappe.set_route("Form", "NGS Customer", frm.doc.customer);
			} else {
				frappe.msgprint(__("No associated Customer found."));
			}
		});
		frm.add_custom_button(__("Sequencing"), () => {
			frappe.new_doc("NGS Pipeline Run", {
				project: frm.doc.name,
				customer: frm.doc.customer,
				class_type: frm.doc.class_type,
			});
		});
		frm.add_custom_button(__("+ Sample Transfer"), () => {
			frappe.new_doc("NGS Sample Transfer", {}, (doc) => {
				doc.customer = frm.doc.customer;
				doc.class_type = frm.doc.class_type;
				doc.project = frm.doc.project_id;
				doc.save();
			});
		});
	},
});
