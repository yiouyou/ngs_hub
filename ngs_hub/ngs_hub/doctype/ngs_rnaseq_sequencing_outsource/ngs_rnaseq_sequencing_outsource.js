// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("NGS RNAseq Sequencing Outsource", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('NGS RNAseq Sequencing Outsource', {
  refresh(frm) {
    frm.add_custom_button(__('→ RNAseq Library Construction'), () => {
      if (frm.doc.rnaseq_library_construction) {
        frappe.set_route('Form', 'NGS RNAseq Library Construction', frm.doc.rnaseq_library_construction);
      } else {
        frappe.msgprint(__('No associated RNAseq Library Construction found.'));
      }
    });
  }
});
