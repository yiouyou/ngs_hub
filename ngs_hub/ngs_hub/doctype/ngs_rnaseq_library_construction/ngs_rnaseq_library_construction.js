// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("NGS RNAseq Library Construction", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('NGS RNAseq Library Construction', {
  setup(frm) {
    frm.set_query('rnaseq_sample_extraction', () => {
      return {
        filters: {
          'qc_status': ['!=', 'FAIL']
        }
      };
    });
    frm.set_query('rnaseq_enrichment', () => {
      return {
        filters: {
          'qc_status': ['!=', 'FAIL']
        }
      };
    });
  },
  refresh(frm) {
    frm.add_custom_button(__('→ RNAseq Enrichment'), () => {
      if (frm.doc.rnaseq_enrichment) {
        frappe.set_route('Form', 'NGS RNAseq Enrichment', frm.doc.rnaseq_enrichment);
      } else {
        frappe.msgprint(__('No associated RNAseq Enrichment found.'));
      }
    });
    frm.add_custom_button(__('+ RNAseq Sequencing Out'), () => {
      if (frm.doc.qc_status === 'PASS') {
        frappe.new_doc('NGS RNAseq Sequencing Outsource', {}, (doc) => {
          doc.customer = frm.doc.customer
          doc.project = frm.doc.project
          doc.sample_transfer = frm.doc.sample_transfer;
          doc.sample_info = frm.doc.sample_info;
          doc.urgency = frm.doc.urgency;
          doc.rnaseq_sample_extraction = frm.doc.rnaseq_sample_extraction;
          doc.rnaseq_enrichment = frm.doc.rnaseq_enrichment;
          doc.rnaseq_library_construction = frm.doc.rnaseq_library_construction_id;
          doc.save();
        });
      } else {
        frappe.msgprint(__('Cannot proceed. QC Status must be "PASS".'));
      }
    });
  }
});
