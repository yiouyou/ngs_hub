// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("NGS Customer", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('NGS Customer', {
  refresh(frm) {
    frm.add_custom_button(__('+ Project'), () => {
      frappe.new_doc('NGS Project', {}, (doc) => {
        doc.customer = frm.doc.customer_id
        doc.save();
      });
    });
  }
});
