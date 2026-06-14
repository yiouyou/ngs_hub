// Copyright (c) 2026, sz and contributors
// For license information, please see license.txt

frappe.ui.form.on('NGS Service Catalog', {
  refresh(frm) {
    frm.set_query('currency', () => ({ filters: { enabled: 1 } }));
  }
});
