// Copyright (c) 2026, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("NGS DNAseq Sequencing Outsource", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('NGS DNAseq Sequencing Outsource', {
  setup(frm) {
    frm.set_query('dnaseq_sample_extraction', () => {
      return {
        filters: {
          'qc_status': ['!=', 'FAIL']
        }
      };
    });
    frm.set_query('dnaseq_library_construction', () => {
      return {
        filters: {
          'qc_status': ['!=', 'FAIL']
        }
      };
    });
  },
  refresh(frm) {
    frm.add_custom_button(__('→ DNAseq Library Construction'), () => {
      if (frm.doc.dnaseq_library_construction) {
        frappe.set_route('Form', 'NGS DNAseq Library Construction', frm.doc.dnaseq_library_construction);
      } else {
        frappe.msgprint(__('No associated DNAseq Library Construction found.'));
      }
    });
  }
});
