frappe.listview_settings['NGS Order'] = {
  add_fields: ['project_summary', 'customer', 'quote', 'status'],
  hide_name_column: false,
  has_indicator_for_draft: true,
  get_indicator(doc) {
    const status = doc.status || 'Draft';
    const colors = { Draft: 'gray', Submitted: 'blue', 'In Review': 'orange', Accepted: 'green',
      'In Progress': 'blue', Completed: 'green', Cancelled: 'red' };
    return [__(status), colors[status] || 'gray', `status,=,${status}`];
  },
};
