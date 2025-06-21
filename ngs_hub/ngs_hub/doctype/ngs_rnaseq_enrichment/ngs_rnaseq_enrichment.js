// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("NGS RNAseq Enrichment", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('NGS RNAseq Enrichment', {
  refresh(frm) {
    frm.add_custom_button(__('→ RNAseq Sample Ext'), () => {
      if (frm.doc.rnaseq_sample_extraction) {
        frappe.set_route('Form', 'NGS RNAseq Sample Extraction', frm.doc.rnaseq_sample_extraction);
      } else {
        frappe.msgprint(__('No associated RNAseq Sample Extraction found.'));
      }
    });
    frm.add_custom_button(__('+ RNAseq Lib Constr'), () => {
      frappe.new_doc('NGS RNAseq Library Construction', {}, (doc) => {
        doc.customer = frm.doc.customer
        doc.sample_transfer = frm.doc.sample_transfer;
        doc.sample_info = frm.doc.sample_info;
        doc.rnaseq_sample_extraction = frm.doc.rnaseq_sample_extraction;
        doc.rnaseq_enrichment = frm.doc.rnaseq_enrichment_id;
        doc.save();
      });
    });
  }
});
