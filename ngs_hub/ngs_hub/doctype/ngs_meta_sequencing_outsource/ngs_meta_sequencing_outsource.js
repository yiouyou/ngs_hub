// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("NGS Meta Sequencing Outsource", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('NGS Meta Sequencing Outsource', {
  refresh(frm) {
    frm.add_custom_button(__('→ Meta Library Construction'), () => {
      if (frm.doc.meta_library_construction) {
        frappe.set_route('Form', 'NGS Meta Library Construction', frm.doc.meta_library_construction);
      } else {
        frappe.msgprint(__('No associated Meta Library Construction found.'));
      }
    });
  }
});
