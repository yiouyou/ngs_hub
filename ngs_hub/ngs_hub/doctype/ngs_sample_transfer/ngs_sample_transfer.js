// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("NGS Sample Transfer", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('NGS Sample Transfer', {
  refresh(frm) {
    frm.add_custom_button(__('→ Customer'), () => {
      if (frm.doc.customer) {
        frappe.set_route('Form', 'NGS Customer', frm.doc.customer);
      } else {
        frappe.msgprint(__('No associated Customer found.'));
      }
    });
    frm.add_custom_button(__('+ Sample Info'), () => {
      frappe.new_doc('NGS Sample Info', {}, (doc) => {
        doc.customer = frm.doc.customer
        doc.sample_transfer = frm.doc.sample_transfer_id;
        doc.save();
      });
    });
  }
});
