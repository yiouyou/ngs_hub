// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("NGS Meta Library Construction", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('NGS Meta Library Construction', {
  refresh(frm) {
    frm.add_custom_button(__('→ Meta Sample Extraction'), () => {
      if (frm.doc.meta_sample_extraction) {
        frappe.set_route('Form', 'NGS Meta Sample Extraction', frm.doc.meta_sample_extraction);
      } else {
        frappe.msgprint(__('No associated Meta Sample Extraction found.'));
      }
    });
    frm.add_custom_button(__('+ Meta Sequencing Out'), () => {
      frappe.new_doc('NGS Meta Sequencing Outsource', {}, (doc) => {
        doc.customer = frm.doc.customer
        doc.project = frm.doc.project
        doc.sample_transfer = frm.doc.sample_transfer;
        doc.sample_info = frm.doc.sample_info;
        doc.urgency = frm.doc.urgency;
        doc.meta_sample_extraction = frm.doc.meta_sample_extraction;
        doc.meta_library_construction = frm.doc.meta_library_construction_id;
        doc.save();
      });
    });
  }
});
