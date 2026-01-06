// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("NGS SingleCell Sequencing Outsource", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('NGS SingleCell Sequencing Outsource', {
  setup(frm) {
    frm.set_query('singlecell_10x_preprocess', () => {
      return {
        filters: {
          'qc_status': ['!=', 'FAIL']
        }
      };
    });
    frm.set_query('singlecell_library_construction', () => {
      return {
        filters: {
          'qc_status': ['!=', 'FAIL']
        }
      };
    });
  },
  refresh(frm) {
    frm.add_custom_button(__('→ SingleCell Library Construction'), () => {
      if (frm.doc.singlecell_library_construction) {
        frappe.set_route('Form', 'NGS SingleCell Library Construction', frm.doc.singlecell_library_construction);
      } else {
        frappe.msgprint(__('No associated SingleCell Library Construction found.'));
      }
    });
  }
});
