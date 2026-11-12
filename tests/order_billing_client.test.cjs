'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../ngs_hub/ngs_hub/doctype/ngs_order/ngs_order.js'), 'utf8');

function setup(roles = ['NGS Internal Staff'], state = {}) {
  const buttons = [], calls = [], routes = [], messages = [], dialogs = [], handlers = {};
  let resolve, reject;
  const frappe = {user_roles: roles, ngs_item_details: {render() {}},
    ui: {form: {on: (name, value) => { handlers[name] = value; }}, Dialog: function(options) {
      Object.assign(this, options); this.show = () => {}; this.hide = () => { this.hidden = true; };
      this.get_primary_btn = () => ({prop: (_key, value) => { this.disabled = value; }});
      dialogs.push(this);
    }},
    msgprint: value => messages.push(value), show_alert: value => messages.push(value), set_route: (...args) => routes.push(args),
    call: options => { calls.push(options); return new Promise((yes, no) => { resolve = yes; reject = no; }); },
  };
  vm.runInNewContext(source, {frappe, __: text => text, console, Promise, setTimeout, clearTimeout});
  const frm = {doc: {name: 'ORDER-TEST', docstatus: 0, status: 'Draft', customer: 'CUST', items: [
    {name: 'ROW1', description: 'Sequencing', quantity: 2, unit_price: 208},
    {name: 'ROW2', project_type: 'WGS', quantity: 1, unit_price: 399},
  ]}, is_new: () => false, is_dirty: () => false,
  reload_doc: async () => { frm.reloaded = true; }, add_custom_button: (label, click, group) => buttons.push({label, click, group})};
  Object.assign(frm.doc, state);
  handlers['NGS Order'].refresh(frm);
  return {frappe, frm, buttons, calls, routes, messages, dialogs,
    button: label => buttons.find(item => item.label === label),
    succeed: value => resolve({message: value}), fail: error => reject(error)};
}

test('billing controls are internal-only and cancelled orders cannot generate invoices', () => {
  const external = setup(['NGS External Customer']);
  assert(!external.button('Generate Invoice') && !external.button('Adjust Order'));
  const internal = setup();
  assert.equal(internal.button('Generate Invoice').group, 'Invoices');
  assert.equal(internal.button('Adjust Order').group, 'Actions');
  const cancelled = setup(['NGS Internal Staff'], {status: 'Cancelled'});
  assert(!cancelled.button('Generate Invoice') && !cancelled.button('Adjust Order'));
  const submitted = setup(['NGS Internal Staff'], {docstatus: 1});
  assert(submitted.button('Generate Invoice') && !submitted.button('Adjust Order'));
});

test('dirty order cannot generate or adjust from unsaved data', async () => {
  const env = setup(); env.frm.is_dirty = () => true;
  await env.button('Generate Invoice').click(); env.button('Adjust Order').click();
  assert.equal(env.calls.length, 0); assert.equal(env.dialogs.length, 0); assert.equal(env.messages.length, 2);
});

test('invoice generation is POST, coalesces clicks and opens the archived record', async () => {
  const env = setup();
  const pending = env.button('Generate Invoice').click();
  await env.button('Generate Invoice').click();
  assert.equal(env.calls.length, 1); assert.equal(env.calls[0].type, 'POST');
  assert.equal(env.calls[0].args.order_name, 'ORDER-TEST');
  env.succeed({name: 'INV-2026-00001'}); await pending;
  assert.equal(JSON.stringify(env.routes[0]), JSON.stringify(['Form', 'NGS Invoice', 'INV-2026-00001']));
  assert.equal(env.frm.__ngs_invoice_busy, false);
});

test('failed invoice generation releases the client guard for a deliberate retry', async () => {
  const env = setup(); const pending = env.button('Generate Invoice').click();
  env.fail(new Error('Missing invoice email')); await assert.rejects(pending, /Missing invoice email/);
  assert.equal(env.frm.__ngs_invoice_busy, false); assert.equal(env.routes.length, 0);
});

test('adjustment dialog preserves source identities and submits explicit zero/manual/remove choices', async () => {
  const env = setup(); env.button('Adjust Order').click(); const dialog = env.dialogs[0];
  const table = dialog.fields.find(field => field.fieldname === 'items');
  assert.equal(table.data[0].override_unit_price, 208);
  const pending = dialog.primary_action({items: [
    {row_name: 'ROW1', quantity: 2, remove: 1, use_price_override: 0},
    {row_name: 'ROW2', quantity: 1, use_price_override: 1, override_unit_price: 0},
  ], processing_fee: 25, processing_description: 'Handling', set_final_total: 1, final_total: 25, reason: 'Failed sample'});
  await dialog.primary_action({});
  assert.equal(env.calls.length, 1);
  const args = env.calls[0].args;
  assert.equal(args.final_total, 25); assert.equal(args.reason, 'Failed sample');
  assert.deepEqual(JSON.parse(args.items), [
    {name: 'ROW1', quantity: 0, use_price_override: 0, override_unit_price: 0},
    {name: 'ROW2', quantity: 1, use_price_override: 1, override_unit_price: 0},
  ]);
  env.succeed({}); await pending;
  assert(dialog.hidden && env.frm.reloaded); assert.equal(dialog.disabled, false);
});

test('final total is absent unless explicitly selected; failed adjustment keeps dialog and reenables button', async () => {
  const env = setup(); env.button('Adjust Order').click(); const dialog = env.dialogs[0];
  const pending = dialog.primary_action({items: [], reason: 'Correction'});
  assert(!Object.hasOwn(env.calls[0].args, 'final_total'));
  env.fail(new Error('Void invoice first')); await assert.rejects(pending, /Void invoice first/);
  assert.equal(dialog.disabled, false); assert(!dialog.hidden && !env.frm.reloaded);
});
