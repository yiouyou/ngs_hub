// Run from the repository root: node --test tests/desk_quote_client.test.cjs
// Tests client coordination only; RPC pricing and real Frappe controls are not emulated.
const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const filename = path.join(__dirname, '../ngs_hub/ngs_hub/doctype/ngs_quote/ngs_quote.js');
const source = readFileSync(filename, 'utf8');
const itemType = 'NGS Quote Item';
const tick = () => new Promise((resolve) => setImmediate(resolve));

function harness(t, values = {}) {
  const calls = [];
  const alerts = [];
  const messages = [];
  const handlers = new Map();
  const writes = [];
  const products = [{
    project_type: 'RNA Project', service: 'RNA service', group: 'RNA',
    samples: ['RNA'], species: ['Human'], analyses: ['Standard analysis'],
    reads: true, standard_reads: 20, depth: false, onsite: false,
  }];
  const row = {
    doctype: itemType, name: 'item-1', idx: 1,
    project_type: 'RNA Project', service: 'RNA service', description: '',
    quantity: 1, unit_price: 10, amount: 10, ...values,
  };
  const frm = {
    doc: { name: 'QUOTE-1', customer: 'CUSTOMER-1', docstatus: 0, items: [row] },
    is_new: () => false,
    dirty() {},
    refresh_field() {},
    toggle_display() {},
    add_child() { assert.fail('Add Project must reuse the existing blank row'); },
    fields_dict: { items: { grid: {
      set_value(field, value, target) { writes.push({ field, value, target }); },
    } } },
  };

  // Only the control surface used by open_dialog/build_dialog is represented.
  class Dialog {
    constructor(options) {
      this.options = options;
      this.fields_dict = {};
      this.values = {};
      this.is_visible = false;
      this.disabled = false;
      for (const field of options.fields) {
        if (!field.fieldname) continue;
        this.fields_dict[field.fieldname] = {
          df: { ...field },
          set_input: (value) => { this.values[field.fieldname] = value; },
        };
      }
    }
    set_df_property(field, property, value) { this.fields_dict[field].df[property] = value; }
    get_value(field) { return this.values[field]; }
    refresh_dependency() {}
    clear_alert() { this.alert = null; }
    set_alert(message, indicator) { this.alert = { message, indicator }; }
    show() { this.is_visible = true; }
    hide() { this.is_visible = false; this.options.onhide(); }
    get_primary_btn() {
      return { prop: (property, value) => {
        assert.equal(property, 'disabled');
        this.disabled = value;
      } };
    }
  }

  const frappe = {
    ui: { form: { on: (doctype, events) => handlers.set(doctype, events) }, Dialog },
    utils: { escape_html: (value) => String(value) },
    show_alert: (alert) => alerts.push(alert),
    msgprint: (message) => messages.push(message),
    throw: (message) => { throw new Error(message); },
    call(options) {
      assert.ok(['get_quote_builder_options', 'preview_quote_item'].some(
        (method) => options.method === `ngs_hub.api.desk_quote.${method}`,
      ), `Unexpected RPC: ${options.method}`);
      let resolve;
      let reject;
      const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
      const call = {
        method: options.method.split('.').at(-1),
        args: options.args,
        item: options.args.item ? JSON.parse(options.args.item) : null,
        taken: false,
        settled: false,
        succeed(message) { this.settled = true; resolve({ message }); },
        fail(message = 'Preview unavailable') { this.settled = true; reject(new Error(message)); },
      };
      calls.push(call);
      return promise;
    },
  };
  vm.runInNewContext(source, {
    frappe,
    __: (message, args = []) => message.replace(/\{(\d+)\}/g, (_, index) => String(args[index])),
  }, { filename });
  t.after(() => {
    assert.equal(messages.length, 0, 'Unexpected project configuration error');
    assert.ok(calls.every((call) => call.taken && call.settled), 'Every RPC must be inspected and settled');
  });

  function take(method = 'preview_quote_item') {
    const call = calls.find((candidate) => !candidate.taken && candidate.method === method);
    assert.ok(call, `Expected ${method} RPC`);
    call.taken = true;
    return call;
  }
  function fire(field) {
    const handler = handlers.get(itemType)[field];
    assert.equal(typeof handler, 'function', `Missing ${field} handler`);
    return handler(frm, itemType, row.name);
  }
  return { api: frappe.ngs_quote, frm, row, products, take, fire, alerts, writes, handlers };
}

function priced(call, price, extra = {}) {
  return {
    ...call.item, unit_price: price, amount: Number(call.item.quantity) * price,
    rule_notes: `Server price ${price}`, ...extra,
  };
}

function watch(promise) {
  const state = { settled: false };
  // Attach rejection handling immediately, including when a regression rejects Save early.
  state.done = promise.then(
    () => { state.settled = true; return { ok: true }; },
    (error) => { state.settled = true; return { ok: false, error }; },
  );
  return state;
}

test('actual client exposes its helpers and registers validate', (t) => {
  const h = harness(t);
  assert.deepEqual(Object.keys(h.api).sort(), ['get_options', 'open_dialog', 'preview_row', 'validate']);
  for (const method of Object.values(h.api)) assert.equal(typeof method, 'function');
  assert.equal(h.handlers.get('NGS Quote').validate, h.api.validate);
});

test('older success cannot overwrite a newer quantity preview', async (t) => {
  const h = harness(t);
  const old = h.api.preview_row(h.frm, itemType, h.row.name);
  const oldCall = h.take();
  h.row.quantity = 3;
  const latest = h.fire('quantity');
  const latestCall = h.take();
  assert.equal(oldCall.item.quantity, 1);
  assert.equal(latestCall.item.quantity, 3);
  latestCall.succeed(priced(latestCall, 40));
  await latest;
  oldCall.succeed(priced(oldCall, 999, { description: 'obsolete' }));
  await old;
  assert.equal(h.row.quantity, 3);
  assert.equal(h.row.description, '');
  assert.equal(h.row.unit_price, 40);
  assert.equal(h.row.amount, 120);
  await h.api.validate(h.frm);
  assert.equal(h.frm.doc.total_amount, 120);
});

test('description edits trigger a new preview and preserve the newer description', async (t) => {
  const h = harness(t, { description: 'old description' });
  const old = h.fire('quantity');
  const oldCall = h.take();
  h.row.description = 'new description';
  const latest = h.fire('description');
  const latestCall = h.take();
  assert.equal(latestCall.item.description, 'new description');
  latestCall.succeed(priced(latestCall, 25));
  await latest;
  oldCall.succeed(priced(oldCall, 999));
  await old;
  assert.equal(h.row.description, 'new description');
  assert.equal(h.row.unit_price, 25);
});

test('older failure is ignored after the latest preview succeeds', async (t) => {
  const h = harness(t);
  const old = h.fire('quantity');
  const oldCall = h.take();
  h.row.quantity = 2;
  const latest = h.fire('quantity');
  const latestCall = h.take();
  latestCall.succeed(priced(latestCall, 30));
  await latest;
  oldCall.fail('Obsolete error');
  await old;
  assert.equal(h.alerts.length, 0);
  await h.api.validate(h.frm);
  assert.equal(h.frm.doc.total_amount, 60);
});

for (const outcome of ['success', 'failure']) {
  test(`validate keeps waiting for a replacement preview after an older ${outcome}`, async (t) => {
    const h = harness(t);
    const old = h.fire('quantity');
    const oldCall = h.take();
    const saving = watch(h.api.validate(h.frm));
    await tick();
    assert.equal(saving.settled, false);
    h.row.quantity = 4;
    const latest = h.fire('quantity');
    const latestCall = h.take();
    if (outcome === 'failure') oldCall.fail('Obsolete error');
    else oldCall.succeed(priced(oldCall, 999));
    await old;
    await tick();
    assert.equal(saving.settled, false, 'Save must await the latest request, not just its initial snapshot');
    assert.equal(h.alerts.length, 0);
    latestCall.succeed(priced(latestCall, 17));
    await latest;
    assert.deepEqual(await saving.done, { ok: true });
    assert.equal(h.row.quantity, 4);
    assert.equal(h.frm.doc.total_amount, 68);
  });
}

test('validate rejects a current failure and waits for a successful retry', async (t) => {
  const h = harness(t);
  const pending = h.fire('quantity');
  const call = h.take();
  const saving = watch(h.api.validate(h.frm));
  await tick();
  assert.equal(saving.settled, false);
  call.fail('Current failure');
  await pending;
  const rejected = await saving.done;
  assert.equal(rejected.ok, false);
  assert.match(rejected.error.message, /Resolve pricing preview errors in rows 1 before saving/);
  assert.equal(h.alerts.length, 1);
  await assert.rejects(h.api.validate(h.frm), /Resolve pricing preview errors/);

  h.row.quantity = 2;
  const retry = h.fire('quantity');
  const retryCall = h.take();
  const retrySave = watch(h.api.validate(h.frm));
  await tick();
  assert.equal(retrySave.settled, false);
  retryCall.succeed(priced(retryCall, 23));
  await retry;
  assert.deepEqual(await retrySave.done, { ok: true });
  assert.equal(h.frm.doc.total_amount, 46);
});

for (const outcome of ['success', 'failure']) {
  test(`deleted row ignores a pending ${outcome} and does not block validate`, async (t) => {
    const h = harness(t);
    const pending = h.fire('quantity');
    const call = h.take();
    const before = { ...h.row };
    const writesBefore = h.writes.length;
    h.frm.doc.items = [];
    // Deliberately omit items_remove: presence checks must ignore detached entries too.
    const saving = watch(h.api.validate(h.frm));
    await tick();
    assert.equal(saving.settled, true, 'Deleted-row RPC must not hold Save open');
    assert.deepEqual(await saving.done, { ok: true });
    if (outcome === 'failure') call.fail('Deleted row error');
    else call.succeed(priced(call, 999));
    await pending;
    assert.deepEqual(h.row, before);
    assert.equal(h.writes.length, writesBefore);
    assert.equal(h.alerts.length, 0);
    await h.api.validate(h.frm);
    assert.equal(h.frm.doc.total_amount, 0);
  });
}

test('a removed row with a recorded failure no longer prevents Save', async (t) => {
  const h = harness(t);
  const pending = h.fire('quantity');
  h.take().fail();
  await pending;
  await assert.rejects(h.api.validate(h.frm), /Resolve pricing preview errors/);
  h.frm.doc.items = [];
  h.fire('items_remove');
  await h.api.validate(h.frm);
  assert.equal(h.frm.doc.total_amount, 0);
});

for (const previous of ['pending', 'failed']) {
  test(`Add Project reuses a blank row with a ${previous} preview without poisoning Save`, async (t) => {
    const h = harness(t, { project_type: '', service: '', unit_price: 0, amount: 0 });
    const old = h.fire('quantity');
    const oldCall = h.take();
    if (previous === 'failed') {
      oldCall.fail('Prior blank-row error');
      await old;
      await assert.rejects(h.api.validate(h.frm), /Resolve pricing preview errors/);
    }
    const opening = h.api.open_dialog(h.frm);
    h.take('get_quote_builder_options').succeed({ products: h.products });
    const dialog = await opening;
    assert.ok(dialog);
    assert.equal(dialog.options.title, 'Add Project');
    const initial = h.take();
    initial.succeed(priced(initial, 31));
    await tick();
    assert.equal(dialog.get_value('unit_price'), 31);

    const submission = dialog.options.primary_action();
    const addCall = h.take();
    assert.equal(dialog.disabled, true);
    const saving = watch(h.api.validate(h.frm));
    await tick();
    assert.equal(saving.settled, false, 'Save must await Add Project submission');
    addCall.succeed(priced(addCall, 31));
    await submission;
    // A Save already awaiting the old RPC retains its snapshot. A new Save must
    // not capture that RPC after Add Project has invalidated the blank-row state.
    const freshSave = watch(h.api.validate(h.frm));
    await tick();
    assert.equal(freshSave.settled, true, 'Reused row must not retain the obsolete pending RPC');
    assert.deepEqual(await freshSave.done, { ok: true });
    assert.equal(h.frm.doc.items.length, 1);
    assert.equal(h.frm.doc.items[0], h.row);
    assert.equal(h.row.project_type, 'RNA Project');
    assert.equal(h.row.service, 'RNA service');
    assert.equal(h.row.unit_price, 31);
    assert.equal(h.frm.doc.total_amount, 31);
    assert.equal(dialog.is_visible, false);
    assert.equal(dialog.disabled, false);
    if (previous === 'pending') {
      oldCall.fail('Late blank-row error');
      await old;
    }
    assert.deepEqual(await saving.done, { ok: true });
    assert.equal(h.alerts.length, previous === 'failed' ? 1 : 0);
    await h.api.validate(h.frm);
    assert.equal(h.frm.doc.total_amount, 31);
  });
}

for (const field of ['project_type', 'service']) {
  test(`direct ${field} changes reset stale service, flags and defaults before pricing`, async (t) => {
    const h = harness(t, {
      project_type: 'Old Project', service: 'Old service', quantity: 7,
      description: 'Old description', sample_type: 'Old sample', species: 'Old species',
      data_analysis: 'Old analysis', million_reads_per_sample: 999, read_depth: '90x',
      add_on_sequencing: 1, onsite_service: 1, onsite_location: 'Outside Massachusetts',
      onsite_address: 'Old address', confirmed_tbd_fee: 500, tissue_dissociation: 1,
      nuclei_extraction: 1, library_qc: 1, use_price_override: 1, override_unit_price: 888,
      unit_price: 888, amount: 6216, rule_notes: 'Old rules',
    });
    h.row[field] = h.products[0][field];
    const pending = h.fire(field);
    h.take('get_quote_builder_options').succeed({ products: h.products });
    await tick();
    const call = h.take();
    assert.deepEqual(call.item, {
      project_type: 'RNA Project', service: 'RNA service', sample_type: 'RNA',
      description: '', quantity: 7, species: 'Human', read_depth: '',
      million_reads_per_sample: 20, add_on_sequencing: 0, data_analysis: 'Standard analysis',
      onsite_service: 0, onsite_location: '', onsite_address: '', tissue_dissociation: 0,
      nuclei_extraction: 0, library_qc: 0, confirmed_tbd_fee: 0,
      use_price_override: 0, override_unit_price: 0, unit_price: 0, amount: 0, rule_notes: '',
    });
    assert.equal(h.row.service, 'RNA service');
    assert.equal(h.row.use_price_override, 0);
    assert.equal(h.row.onsite_service, 0);
    call.succeed(priced(call, 50));
    await pending;
    await h.api.validate(h.frm);
    assert.equal(h.frm.doc.total_amount, 350);
  });
}

test('inline unit price override is retained in the subsequent quantity preview', async (t) => {
  const h = harness(t);
  h.row.unit_price = 37.5;
  const edited = h.fire('unit_price');
  const priceCall = h.take();
  assert.equal(priceCall.item.use_price_override, 1);
  assert.equal(priceCall.item.override_unit_price, 37.5);
  priceCall.succeed(priced(priceCall, 37.5));
  await edited;
  h.row.quantity = 4;
  const pending = h.fire('quantity');
  const quantityCall = h.take();
  assert.equal(quantityCall.item.quantity, 4);
  assert.equal(quantityCall.item.use_price_override, 1);
  assert.equal(quantityCall.item.override_unit_price, 37.5);
  quantityCall.succeed(priced(quantityCall, 37.5));
  await pending;
  await h.api.validate(h.frm);
  assert.equal(h.row.use_price_override, 1);
  assert.equal(h.row.override_unit_price, 37.5);
  assert.equal(h.row.unit_price, 37.5);
  assert.equal(h.row.amount, 150);
  assert.equal(h.frm.doc.total_amount, 150);
});
