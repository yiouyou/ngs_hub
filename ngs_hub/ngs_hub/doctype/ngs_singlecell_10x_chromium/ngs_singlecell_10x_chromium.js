// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("NGS SingleCell 10x Chromium", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('NGS SingleCell 10x Chromium', {
  refresh(frm) {
    frm.add_custom_button(__('→ SingleCell 10x Pre'), () => {
      if (frm.doc.singlecell_10x_preprocess) {
        frappe.set_route('Form', 'NGS SingleCell 10x Preprocess', frm.doc.singlecell_10x_preprocess);
      } else {
        frappe.msgprint(__('No associated SingleCell 10x Preprocess found.'));
      }
    });
    frm.add_custom_button(__('+ SingleCell Lib Constr'), () => {
      frappe.new_doc('NGS SingleCell Library Construction', {}, (doc) => {
        doc.customer = frm.doc.customer
        doc.project = frm.doc.project
        doc.sample_transfer = frm.doc.sample_transfer;
        doc.sample_info = frm.doc.sample_info;
        doc.singlecell_10x_preprocess = frm.doc.singlecell_10x_preprocess;
        doc.singlecell_10x_chromium = frm.doc.singlecell_10x_chromium_id;
        doc.save();
      });
    });
  }
});
