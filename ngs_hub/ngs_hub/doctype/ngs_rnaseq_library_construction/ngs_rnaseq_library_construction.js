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
  },
  refresh(frm) {
    frm.add_custom_button(__('→ RNAseq Sample Ext'), () => {
      if (frm.doc.rnaseq_sample_extraction) {
        frappe.set_route('Form', 'RNAseq Sample Extraction', frm.doc.rnaseq_sample_extraction);
      } else {
        frappe.msgprint(__('No associated RNAseq Sample Extraction found.'));
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
          doc.rnaseq_library_construction = frm.doc.rnaseq_library_construction_id;
          doc.save();
        });
      } else {
        frappe.msgprint(__('Cannot proceed. QC Status must be "PASS".'));
      }
    });
  }
});
