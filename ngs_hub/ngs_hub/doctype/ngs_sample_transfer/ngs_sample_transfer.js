// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("NGS Sample Transfer", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('NGS Sample Transfer', {
  refresh(frm) {
    frm.add_custom_button(__('→ Project'), () => {
      if (frm.doc.project) {
        frappe.set_route('Form', 'NGS Project', frm.doc.project);
      } else {
        frappe.msgprint(__('No associated Project found.'));
      }
    });
    frm.add_custom_button(__('+ Sample Info'), () => {
      frappe.new_doc('NGS Sample Info', {}, (doc) => {
        doc.customer = frm.doc.customer
        doc.project = frm.doc.project
        doc.class_type = frm.doc.class_type;
        doc.sample_transfer = frm.doc.sample_transfer_id;
        doc.save();
      });
    });
  }
});
