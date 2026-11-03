frappe.listview_settings['NGS Quote'] = {
  add_fields: ['project_summary', 'customer', 'status', 'pricing_to_confirm'],
  hide_name_column: false,
  has_indicator_for_draft: true,
  get_indicator(doc) {
    const status = doc.status || 'Draft';
    const colors = { Draft: 'gray', Pending: 'orange', Confirmed: 'green', 'Converted to Order': 'blue', Cancelled: 'red' };
    return [__(status), colors[status] || 'gray', `status,=,${status}`];
  },
};
