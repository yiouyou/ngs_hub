// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("NGS Sample Info", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('NGS Sample Info', {
  onload(frm) {
    // 判断是否客户或内部员工
    const roles = frappe.session.user_roles || [];
    const is_external = roles.includes('NGS External Customer');
    // 客户看不到 inside_section
    if (is_external) {
      frm.toggle_display('inside_section', false);
    }
  },
  refresh(frm) {
    frm.add_custom_button(__('→ Sample Transfer'), () => {
      if (frm.doc.sample_transfer) {
        frappe.set_route('Form', 'NGS Sample Transfer', frm.doc.sample_transfer);
      } else {
        frappe.msgprint(__('No associated Sample Transfer found.'));
      }
    });
    if (!frm.doc.class_type) return;
    if (frm.doc.class_type === 'SingleCell') {
      frm.add_custom_button(__('+ SingleCell 10x Pre'), () => {
        frappe.new_doc('NGS SingleCell 10x Preprocess', {}, (doc) => {
          doc.customer = frm.doc.customer
          doc.project = frm.doc.project
          doc.sample_transfer = frm.doc.sample_transfer;
          doc.sample_info = frm.doc.sample_info_id;
          doc.urgency = frm.doc.urgency;
          doc.save();
        });
      });
    }
    if (frm.doc.class_type === 'RNAseq') {
      frm.add_custom_button(__('+ RNAseq Sample Ext'), () => {
        frappe.new_doc('NGS RNAseq Sample Extraction', {}, (doc) => {
          doc.customer = frm.doc.customer
          doc.project = frm.doc.project
          doc.sample_transfer = frm.doc.sample_transfer;
          doc.sample_info = frm.doc.sample_info_id;
          doc.urgency = frm.doc.urgency;
          doc.save();
        });
      });
    }
    if (frm.doc.class_type === 'Meta') {
      frm.add_custom_button(__('+ Meta Sample Ext'), () => {
        frappe.new_doc('NGS Meta Sample Extraction', {}, (doc) => {
          doc.customer = frm.doc.customer
          doc.project = frm.doc.project
          doc.sample_transfer = frm.doc.sample_transfer;
          doc.sample_info = frm.doc.sample_info_id;
          doc.urgency = frm.doc.urgency;
          doc.save();
        });
      });
    }
  }
});
