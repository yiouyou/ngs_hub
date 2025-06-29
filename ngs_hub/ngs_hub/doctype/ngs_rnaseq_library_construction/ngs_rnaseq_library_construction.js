// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("NGS RNAseq Library Construction", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('NGS RNAseq Library Construction', {
  refresh(frm) {
    frm.add_custom_button(__('→ RNAseq Enrichment'), () => {
      if (frm.doc.rnaseq_enrichment) {
        frappe.set_route('Form', 'NGS RNAseq Enrichment', frm.doc.rnaseq_enrichment);
      } else {
        frappe.msgprint(__('No associated RNAseq Enrichment found.'));
      }
    });
    frm.add_custom_button(__('+ RNAseq Sequencing Out'), () => {
      frappe.new_doc('NGS RNAseq Sequencing Outsource', {}, (doc) => {
        doc.customer = frm.doc.customer
        doc.project = frm.doc.project
        doc.sample_transfer = frm.doc.sample_transfer;
        doc.sample_info = frm.doc.sample_info;
        doc.rnaseq_sample_extraction = frm.doc.rnaseq_sample_extraction;
        doc.rnaseq_enrichment = frm.doc.rnaseq_enrichment;
        doc.rnaseq_library_construction = frm.doc.rnaseq_library_construction_id;
        doc.save();
      });
    });
  }
});
