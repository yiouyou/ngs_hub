// Copyright (c) 2026, sz and contributors
// For license information, please see license.txt

frappe.ui.form.on('NGS Quote', {
  refresh(frm) {
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
  quantity(frm, cdt, cdn) { update_amount(frm, cdt, cdn); },
  unit_price(frm, cdt, cdn) { update_amount(frm, cdt, cdn); },
});

function update_amount(frm, cdt, cdn) {
  const row = locals[cdt][cdn];
  row.amount = (row.quantity || 0) * (row.unit_price || 0);
  frm.refresh_field('items');
}
