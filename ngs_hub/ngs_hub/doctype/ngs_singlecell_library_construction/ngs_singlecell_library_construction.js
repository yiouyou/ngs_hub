// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("NGS SingleCell Library Construction", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('NGS SingleCell Library Construction', {
  refresh(frm) {
    frm.add_custom_button(__('→ SingleCell 10x Chromium'), () => {
      if (frm.doc.singlecell_10x_chromium) {
        frappe.set_route('Form', 'NGS SingleCell 10x Chromium', frm.doc.singlecell_10x_chromium);
      } else {
        frappe.msgprint(__('No associated SingleCell 10x Chromium found.'));
      }
    });
    frm.add_custom_button(__('+ SingleCell Sequencing Out'), () => {
      frappe.new_doc('NGS SingleCell Sequencing Outsource', {}, (doc) => {
        doc.customer = frm.doc.customer
        doc.sample_transfer = frm.doc.sample_transfer;
        doc.sample_info = frm.doc.sample_info;
        doc.singlecell_10x_preprocess = frm.doc.singlecell_10x_preprocess;
        doc.singlecell_10x_chromium = frm.doc.singlecell_10x_chromium;
        doc.singlecell_library_construction = frm.doc.singlecell_library_construction_id;
        doc.save();
      });
    });
  }
});
