// Copyright (c) 2026, sz and contributors
// For license information, please see license.txt

frappe.ui.form.on('NGS Quote', {
  refresh(frm) {
    toggle_custom_project_description(frm);
    frm.add_custom_button(__('→ Customer'), () => {
      if (frm.doc.customer) frappe.set_route('Form', 'NGS Customer', frm.doc.customer);
    });
    if (!frm.is_new()) {
      frm.add_custom_button(__('Create Order'), () => {
        frappe.new_doc('NGS Order', {}, (doc) => {
          doc.customer = frm.doc.customer;
          doc.quote = frm.doc.name;
        });
      });
    }
  }
});

frappe.ui.form.on('NGS Quote Item', {
  project_type(frm) { toggle_custom_project_description(frm); },
  quantity(frm, cdt, cdn) { update_amount(frm, cdt, cdn); },
  unit_price(frm, cdt, cdn) { update_amount(frm, cdt, cdn); },
  confirmed_tbd_fee(frm) { frm.dirty(); },
});

function toggle_custom_project_description(frm) {
  const has_custom_project = (frm.doc.items || []).some((row) => row.project_type === 'Custom Project');
  const has_existing_description = Boolean(frm.doc.custom_project_description);
  frm.toggle_display('custom_project_description', has_custom_project || has_existing_description);
}

function update_amount(frm, cdt, cdn) {
  const row = locals[cdt][cdn];
  row.amount = (row.quantity || 0) * (row.unit_price || 0);
  frm.refresh_field('items');
}
