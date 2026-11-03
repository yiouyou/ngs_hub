'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

for (const [slug, doctype, status] of [
  ['ngs_quote', 'NGS Quote', 'Converted to Order'],
  ['ngs_order', 'NGS Order', 'Submitted'],
]) {
  test(`${doctype}: native specification subject keeps document IDs and business status`, () => {
    const root = path.join(__dirname, '..', 'ngs_hub', 'ngs_hub', 'doctype', slug);
    const meta = JSON.parse(fs.readFileSync(path.join(root, `${slug}.json`), 'utf8'));
    const context = {frappe: {listview_settings: {}}, __: value => value};
    vm.runInNewContext(fs.readFileSync(path.join(root, `${slug}_list.js`), 'utf8'), context);
    const settings = context.frappe.listview_settings[doctype];
    assert.equal(meta.title_field, 'project_summary');
    const summary = meta.fields.find(field => field.fieldname === 'project_summary');
    assert.equal(summary.read_only, 1);
    assert.equal(summary.in_list_view, 1);
    assert.equal(settings.hide_name_column, false);
    assert.equal(settings.has_indicator_for_draft, true, 'Frappe must not replace business status with Draft');
    assert(settings.add_fields.includes('project_summary'));
    assert(settings.add_fields.includes('customer'));
    assert(settings.add_fields.includes('status'));
    assert.equal(settings.get_indicator({status, docstatus: 0})[0], status);
    assert.equal(settings.get_indicator({status})[2], `status,=,${status}`);
    assert(!meta.show_title_field_in_link, 'Saved reference fields must continue showing document IDs');
  });
}
