// Copyright (c) 2026, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("NGS DNAseq Library Construction", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('NGS DNAseq Library Construction', {
  setup(frm) {
    frm.set_query('dnaseq_sample_extraction', () => {
      return {
        filters: {
          'qc_status': ['!=', 'FAIL']
        }
      };
    });
  },
  refresh(frm) {
    frm.add_custom_button(__('→ DNAseq Sample Ext'), () => {
      if (frm.doc.dnaseq_sample_extraction) {
        frappe.set_route('Form', 'DNAseq Sample Extraction', frm.doc.dnaseq_sample_extraction);
      } else {
        frappe.msgprint(__('No associated DNAseq Sample Extraction found.'));
      }
    });
    frm.add_custom_button(__('+ DNAseq Sequencing Out'), () => {
      if (frm.doc.qc_status === 'PASS') {
        frappe.new_doc('NGS DNAseq Sequencing Outsource', {}, (doc) => {
          doc.customer = frm.doc.customer
          doc.project = frm.doc.project
          doc.sample_transfer = frm.doc.sample_transfer;
          doc.sample_info = frm.doc.sample_info;
          doc.urgency = frm.doc.urgency;
          doc.dnaseq_sample_extraction = frm.doc.dnaseq_sample_extraction;
          doc.dnaseq_library_construction = frm.doc.dnaseq_library_construction_id;
          doc.save();
        });
      } else {
        frappe.msgprint(__('Cannot proceed. QC Status must be "PASS".'));
      }
    });
  }
});
