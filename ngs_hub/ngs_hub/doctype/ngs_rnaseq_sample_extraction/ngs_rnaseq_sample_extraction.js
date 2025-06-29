// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("NGS RNAseq Sample Extraction", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('NGS RNAseq Sample Extraction', {
  refresh(frm) {
    frm.add_custom_button(__('→ Sample Info'), () => {
      if (frm.doc.sample_info) {
        frappe.set_route('Form', 'NGS Sample Info', frm.doc.sample_info);
      } else {
        frappe.msgprint(__('No associated Sample Info found.'));
      }
    });
    frm.add_custom_button(__('+ RNAseq Enrichment'), () => {
      frappe.new_doc('NGS RNAseq Enrichment', {}, (doc) => {
        doc.customer = frm.doc.customer
        doc.project = frm.doc.project
        doc.sample_transfer = frm.doc.sample_transfer;
        doc.sample_info = frm.doc.sample_info;
        doc.rnaseq_sample_extraction = frm.doc.rnaseq_sample_extraction_id;
        doc.save();
      });
    });
  }
});
