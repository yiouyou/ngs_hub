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

function harness(t, values = {}, siblings = []) {
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
    doc: { name: 'QUOTE-1', customer: 'CUSTOMER-1', docstatus: 0, items: [row, ...siblings] },
    is_new: () => false,
    dirty() {},
    refresh_field() {},
    toggle_display() {},
    add_child(doctype, values) {
      assert.equal(doctype, 'items');
      const added = { doctype: itemType, name: `item-${this.doc.items.length + 1}`,
        idx: this.doc.items.length + 1, ...values };
      this.doc.items.push(added);
      return added;
    },
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
      assert.ok(['get_quote_builder_options', 'preview_quote_item', 'preview_quote_items'].some(
        (method) => options.method === `ngs_hub.api.desk_quote.${method}`,
      ), `Unexpected RPC: ${options.method}`);
      let resolve;
      let reject;
      const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
      const call = {
        method: options.method.split('.').at(-1),
        args: options.args,
        item: options.args.item ? JSON.parse(options.args.item) : null,
        items: options.args.items ? JSON.parse(options.args.items) : null,
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
  function fire(field, target = row) {
    const handler = handlers.get(itemType)[field];
    assert.equal(typeof handler, 'function', `Missing ${field} handler`);
    return handler(frm, itemType, target.name);
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
      unit_price: 888, amount: 6216, rule_notes: 'Old rules', pricing_details: '{"version":1}',
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
      use_price_override: 0, override_unit_price: 0, unit_price: 0, amount: 0, rule_notes: '', pricing_details: '',
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

const seqProduct = {
  project_type: 'Sequencing Only - 1M Reads', service: 'SEQ_ONLY_1M_READS', group: 'Sequencing',
  samples: ['Library'], species: [], analyses: ['No'], reads: true, standard_reads: 1,
  depth: false, onsite: false,
};

function seqRow(name, values = {}) {
  return { doctype: itemType, name, idx: Number(name.split('-').at(-1)),
    project_type: seqProduct.project_type, service: seqProduct.service,
    quantity: 1, million_reads_per_sample: 150, unit_price: 303, amount: 303, ...values };
}

function seqHarness(t, values = {}, siblings = [seqRow('item-2')]) {
  const h = harness(t, seqRow('item-1', values), siblings);
  h.products.unshift(seqProduct);
  return h;
}

function wholePrice(call, prices, extra = {}) {
  assert.ok(call.items, 'Expected a full vector request');
  const items = call.items.map((item, index) => ({ ...item,
    unit_price: prices[index], amount: Number(item.quantity) * prices[index],
    rule_notes: `Whole quote price ${prices[index]}`, pricing_details: '{"version":1}',
  }));
  return { items, total_amount: items.reduce((sum, row) => sum + row.amount, 0),
    sequencing_pricing_version: 1, ...extra };
}

for (const outcome of ['success', 'failure']) {
  test(`cross-row stale ${outcome} is ignored and Save waits for the replacement vector`, async (t) => {
    const h = seqHarness(t);
    const old = h.fire('quantity');
    const oldCall = h.take('preview_quote_items');
    assert.equal(oldCall.items.length, 2);
    const saving = watch(h.api.validate(h.frm));
    await tick();
    const sibling = h.frm.doc.items[1];
    sibling.quantity = 3;
    const latest = h.fire('quantity', sibling);
    const latestCall = h.take('preview_quote_items');
    assert.equal(latestCall.items[1].quantity, 3);
    if (outcome === 'success') oldCall.succeed(wholePrice(oldCall, [999, 999]));
    else oldCall.fail('Obsolete sibling vector');
    await old;
    await tick();
    assert.equal(saving.settled, false);
    assert.equal(h.alerts.length, 0);
    assert.equal(h.row.unit_price, 303);
    latestCall.succeed(wholePrice(latestCall, [200, 200]));
    await latest;
    assert.deepEqual(await saving.done, { ok: true });
    assert.equal(h.row.unit_price, 200);
    assert.equal(sibling.amount, 600);
    assert.equal(h.frm.doc.total_amount, 800);
    await h.api.validate(h.frm);
  });

  test(`late cross-row ${outcome} cannot corrupt a newer successful quote`, async (t) => {
    const h = seqHarness(t);
    const old = h.fire('quantity');
    const oldCall = h.take('preview_quote_items');
    h.frm.doc.items[1].million_reads_per_sample = 400;
    const latest = h.fire('million_reads_per_sample', h.frm.doc.items[1]);
    const latestCall = h.take('preview_quote_items');
    latestCall.succeed(wholePrice(latestCall, [201, 501]));
    await latest;
    const before = JSON.stringify(h.frm.doc);
    if (outcome === 'success') oldCall.succeed(wholePrice(oldCall, [999, 999]));
    else oldCall.fail();
    await old;
    assert.equal(JSON.stringify(h.frm.doc), before);
    assert.equal(h.alerts.length, 0);
    await h.api.validate(h.frm);
  });
}

test('current whole-quote failure blocks Save until a successful sibling retry', async (t) => {
  const h = seqHarness(t);
  const pending = h.fire('quantity');
  h.take('preview_quote_items').fail();
  await pending;
  await assert.rejects(h.api.validate(h.frm), /Resolve quotation pricing preview errors/);
  h.frm.doc.items[1].quantity = 2;
  const retry = h.fire('quantity', h.frm.doc.items[1]);
  const call = h.take('preview_quote_items');
  call.succeed(wholePrice(call, [250, 250]));
  await retry;
  await h.api.validate(h.frm);
  assert.equal(h.frm.doc.total_amount, 750);
});

for (const outcome of ['success', 'failure']) {
  test(`unannounced sibling edit ignores stale ${outcome}; Save requests its current vector`, async (t) => {
    const h = seqHarness(t);
    const pending = h.fire('quantity');
    const call = h.take('preview_quote_items');
    h.frm.doc.items[1].description = 'Changed without a handler';
    if (outcome === 'success') call.succeed(wholePrice(call, [999, 999]));
    else call.fail();
    await pending;
    assert.equal(h.alerts.length, 0);
    const saving = watch(h.api.validate(h.frm));
    const replacement = h.take('preview_quote_items');
    replacement.succeed(wholePrice(replacement, [200, 200]));
    assert.deepEqual(await saving.done, { ok: true });
    assert.equal(h.frm.doc.total_amount, 400);
  });
}

test('removing a row reprices surviving sequencing rows and invalidates the old vector', async (t) => {
  const h = seqHarness(t);
  const pending = h.fire('quantity');
  const oldCall = h.take('preview_quote_items');
  const removed = h.frm.doc.items.pop();
  const removing = h.fire('items_remove', removed);
  const call = h.take('preview_quote_items');
  assert.equal(call.items.length, 1);
  call.succeed(wholePrice(call, [303]));
  await removing;
  oldCall.fail('Deleted vector');
  await pending;
  await h.api.validate(h.frm);
  assert.equal(h.alerts.length, 0);
  assert.equal(h.frm.doc.total_amount, 303);
});

test('adding a row reprices every sibling and ignores the older response', async (t) => {
  const h = seqHarness(t);
  const pending = h.fire('quantity');
  const oldCall = h.take('preview_quote_items');
  const added = h.frm.add_child('items', seqRow('item-3'));
  const adding = h.fire('items_add', added);
  const call = h.take('preview_quote_items');
  assert.equal(call.items.length, 3);
  call.succeed(wholePrice(call, [210, 210, 210]));
  await adding;
  oldCall.succeed(wholePrice(oldCall, [999, 999]));
  await pending;
  await h.api.validate(h.frm);
  assert.equal(h.frm.doc.total_amount, 630);
  assert.equal(h.row.unit_price, 210);
});

test('deleting all sequencing rows clears failed preview state without requesting an empty vector', async (t) => {
  const h = seqHarness(t);
  const pending = h.fire('quantity');
  h.take('preview_quote_items').fail();
  await pending;
  h.frm.doc.items = [];
  await h.fire('items_remove');
  await h.api.validate(h.frm);
  assert.equal(h.frm.doc.total_amount, 0);
});

test('switching away from the last sequencing product uses a full vector and clears row guards', async (t) => {
  const h = seqHarness(t, {}, []);
  // Initialize state while the row is still sequencing, as a real form refresh does.
  const first = h.fire('quantity');
  const firstCall = h.take('preview_quote_items');
  firstCall.succeed(wholePrice(firstCall, [303]));
  await first;
  h.row.project_type = 'RNA Project';
  const switching = h.fire('project_type');
  h.take('get_quote_builder_options').succeed({ products: h.products });
  await tick();
  const call = h.take('preview_quote_items');
  assert.equal(call.items[0].project_type, 'RNA Project');
  assert.equal(call.items[0].service, 'RNA service');
  call.succeed(wholePrice(call, [45]));
  await switching;
  await h.api.validate(h.frm);
  assert.equal(h.frm.doc.total_amount, 45);
});

async function openSeqDialog(h, editing = true) {
  const opening = editing ? h.api.open_dialog(h.frm, itemType, h.row.name) : h.api.open_dialog(h.frm);
  h.take('get_quote_builder_options').succeed({ products: h.products });
  const dialog = await opening;
  assert.ok(dialog);
  return dialog;
}

test('dialog replaces its current row in sibling context and applies all returned prices', async (t) => {
  const h = seqHarness(t);
  const dialog = await openSeqDialog(h);
  const initial = h.take('preview_quote_items');
  assert.equal(initial.items.length, 2, 'Current row must not be double-counted');
  initial.succeed(wholePrice(initial, [280, 280]));
  await tick();
  assert.equal(dialog.get_value('unit_price'), 280);
  assert.equal(h.row.unit_price, 303, 'Draft dialog previews must not change persisted form inputs');
  dialog.fields_dict.quantity.set_input(2);
  const edited = dialog.fields_dict.quantity.df.onchange();
  const changed = h.take('preview_quote_items');
  assert.equal(changed.items[0].quantity, 2);
  changed.succeed(wholePrice(changed, [250, 250]));
  await edited;
  assert.equal(dialog.get_value('amount'), 500);
  const submitting = dialog.options.primary_action();
  const call = h.take('preview_quote_items');
  const saving = watch(h.api.validate(h.frm));
  // validate may independently price the unchanged form, which the submission supersedes.
  const background = h.take('preview_quote_items');
  call.succeed(wholePrice(call, [250, 250]));
  await submitting;
  background.fail('Superseded form preview');
  assert.deepEqual(await saving.done, { ok: true });
  assert.equal(h.row.quantity, 2);
  assert.equal(h.frm.doc.items[1].unit_price, 250);
  assert.equal(h.frm.doc.total_amount, 750);
  assert.equal(h.alerts.length, 0);
  await h.api.validate(h.frm);
});

test('dialog Add replaces a blank placeholder without double-counting and clears prior row errors', async (t) => {
  const h = harness(t, { project_type: '', service: '', unit_price: 0, amount: 0 }, [seqRow('item-2')]);
  h.products.unshift(seqProduct);
  const old = h.fire('quantity');
  h.take('preview_quote_items').fail('Old placeholder error');
  await old;
  const dialog = await openSeqDialog(h, false);
  const initial = h.take('preview_quote_items');
  assert.equal(initial.items.length, 2);
  initial.succeed(wholePrice(initial, [20, 300]));
  await tick();
  const submission = dialog.options.primary_action();
  const call = h.take('preview_quote_items');
  call.succeed(wholePrice(call, [20, 300]));
  await submission;
  assert.equal(h.frm.doc.items.length, 2);
  assert.equal(h.frm.doc.items[0], h.row);
  await h.api.validate(h.frm);
  assert.equal(h.frm.doc.total_amount, 320);
});

test('dialog Add appends exactly one sequencing row and reprices existing siblings', async (t) => {
  const h = seqHarness(t);
  const dialog = await openSeqDialog(h, false);
  const initial = h.take('preview_quote_items');
  assert.equal(initial.items.length, 3);
  initial.succeed(wholePrice(initial, [290, 290, 20]));
  await tick();
  const submitting = dialog.options.primary_action();
  const call = h.take('preview_quote_items');
  call.succeed(wholePrice(call, [290, 290, 20]));
  await submitting;
  await h.api.validate(h.frm);
  assert.equal(h.frm.doc.items.length, 3);
  assert.equal(h.frm.doc.total_amount, 600);
});

for (const outcome of ['success', 'failure']) {
  test(`dialog ignores a stale sibling ${outcome} and refreshes its current context`, async (t) => {
    const h = seqHarness(t);
    const dialog = await openSeqDialog(h);
    const old = h.take('preview_quote_items');
    h.frm.doc.items[1].quantity = 4;
    if (outcome === 'success') old.succeed(wholePrice(old, [999, 999]));
    else old.fail('Outdated dialog sibling context');
    await tick();
    const latest = h.take('preview_quote_items');
    assert.equal(latest.items[1].quantity, 4);
    latest.succeed(wholePrice(latest, [210, 210]));
    await tick();
    assert.equal(dialog.get_value('unit_price'), 210);
    assert.equal(dialog.alert, null);
    assert.equal(h.alerts.length, 0);
    dialog.hide();
  });

  test(`dialog submission retries stale sibling ${outcome} while Save waits`, async (t) => {
    const h = seqHarness(t);
    const dialog = await openSeqDialog(h);
    const initial = h.take('preview_quote_items');
    initial.succeed(wholePrice(initial, [280, 280]));
    await tick();
    const submission = dialog.options.primary_action();
    const old = h.take('preview_quote_items');
    h.frm.doc.items[1].quantity = 3;
    if (outcome === 'success') old.succeed(wholePrice(old, [999, 999]));
    else old.fail('Obsolete submission');
    await tick();
    const latest = h.take('preview_quote_items');
    assert.equal(latest.items[1].quantity, 3);
    const saving = watch(h.api.validate(h.frm));
    const background = h.take('preview_quote_items');
    await tick();
    assert.equal(saving.settled, false);
    latest.succeed(wholePrice(latest, [220, 220]));
    await submission;
    background.fail('Superseded form vector');
    assert.deepEqual(await saving.done, { ok: true });
    assert.equal(h.frm.doc.total_amount, 880);
    assert.equal(dialog.is_visible, false);
  });
}

test('a current dialog submission failure blocks Save until Apply succeeds', async (t) => {
  const h = seqHarness(t);
  const dialog = await openSeqDialog(h);
  const initial = h.take('preview_quote_items');
  initial.succeed(wholePrice(initial, [280, 280]));
  await tick();
  const submission = dialog.options.primary_action();
  h.take('preview_quote_items').fail('Current submission failure');
  await submission;
  const saving = watch(h.api.validate(h.frm));
  const formPreview = h.take('preview_quote_items');
  formPreview.succeed(wholePrice(formPreview, [280, 280]));
  const outcome = await saving.done;
  assert.equal(outcome.ok, false);
  assert.match(outcome.error.message, /project configuration pricing errors/);
  const retry = dialog.options.primary_action();
  const retryCall = h.take('preview_quote_items');
  retryCall.succeed(wholePrice(retryCall, [280, 280]));
  await retry;
  await h.api.validate(h.frm);
  assert.equal(h.frm.doc.total_amount, 560);
});

test('sequencing manual override remains explicit and every sibling is still priced', async (t) => {
  const h = seqHarness(t);
  h.row.unit_price = 37.5;
  const pending = h.fire('unit_price');
  const call = h.take('preview_quote_items');
  assert.equal(call.items[0].use_price_override, 1);
  assert.equal(call.items[0].override_unit_price, 37.5);
  assert.equal(call.items.length, 2);
  call.succeed(wholePrice(call, [37.5, 280]));
  await pending;
  await h.api.validate(h.frm);
  assert.equal(h.row.override_unit_price, 37.5);
  assert.equal(h.frm.doc.total_amount, 317.5);
});

test('Save follows the replacement vector without waiting for the obsolete RPC to settle', async (t) => {
  const h = seqHarness(t);
  const old = h.fire('quantity');
  const oldCall = h.take('preview_quote_items');
  const saving = watch(h.api.validate(h.frm));
  await tick();
  h.frm.doc.items[1].quantity = 3;
  const latest = h.fire('quantity', h.frm.doc.items[1]);
  const call = h.take('preview_quote_items');
  call.succeed(wholePrice(call, [220, 220]));
  await latest;
  await tick();
  assert.equal(saving.settled, true, 'Obsolete requests must not hold Save open');
  assert.deepEqual(await saving.done, { ok: true });
  oldCall.fail('No longer relevant');
  await old;
  assert.equal(h.alerts.length, 0);
});

test('Save rejects a failed replacement even while the obsolete request remains pending', async (t) => {
  const h = seqHarness(t);
  const old = h.fire('quantity');
  const oldCall = h.take('preview_quote_items');
  const saving = watch(h.api.validate(h.frm));
  await tick();
  h.frm.doc.items[1].quantity = 3;
  const latest = h.fire('quantity', h.frm.doc.items[1]);
  h.take('preview_quote_items').fail('Latest vector failed');
  await latest;
  await tick();
  assert.equal(saving.settled, true);
  assert.equal((await saving.done).ok, false);
  oldCall.succeed(wholePrice(oldCall, [999, 999]));
  await old;
  await assert.rejects(h.api.validate(h.frm), /quotation pricing preview errors/);
  assert.equal(h.alerts.length, 1);
});

for (const invalid of ['missing', 'length', 'unit_price', 'amount', 'total_amount',
  'negative_price', 'negative_amount', 'quantity_mismatch', 'missing_snapshot', 'empty_snapshot',
  'invalid_snapshot', 'empty_snapshot_object']) {
  test(`invalid full-quote ${invalid} is rejected before any row is written`, async (t) => {
    const h = seqHarness(t);
    const before = JSON.stringify(h.frm.doc.items);
    const pending = h.fire('quantity');
    const call = h.take('preview_quote_items');
    const result = wholePrice(call, [40, 40]);
    if (invalid === 'missing') delete result.items;
    else if (invalid === 'length') result.items.pop();
    else if (invalid === 'total_amount') result.total_amount = 'NaN';
    else if (invalid === 'negative_price') Object.assign(result.items[1], { unit_price: -40, amount: -40 });
    else if (invalid === 'negative_amount') result.items[1].amount = -40;
    else if (invalid === 'quantity_mismatch') result.items[1].amount += 0.006;
    else if (invalid === 'missing_snapshot') delete result.items[1].pricing_details;
    else if (invalid === 'empty_snapshot') result.items[1].pricing_details = '';
    else if (invalid === 'invalid_snapshot') result.items[1].pricing_details = 'not JSON';
    else if (invalid === 'empty_snapshot_object') result.items[1].pricing_details = '{}';
    else result.items[1][invalid] = 'Infinity';
    call.succeed(result);
    await pending;
    assert.equal(JSON.stringify(h.frm.doc.items), before);
    assert.equal(h.writes.length, 0);
    await assert.rejects(h.api.validate(h.frm), /quotation pricing preview errors/);
  });
}

test('reordering siblings invalidates the response and Save preserves positional mapping', async (t) => {
  const h = seqHarness(t);
  const old = h.fire('quantity');
  const oldCall = h.take('preview_quote_items');
  h.frm.doc.items.reverse();
  oldCall.succeed(wholePrice(oldCall, [999, 999]));
  await old;
  const saving = watch(h.api.validate(h.frm));
  const call = h.take('preview_quote_items');
  call.succeed(wholePrice(call, [100, 200]));
  assert.deepEqual(await saving.done, { ok: true });
  assert.equal(h.row.unit_price, 200);
  assert.equal(h.frm.doc.items[0].unit_price, 100);
});

for (const outcome of ['success', 'failure']) {
  test(`whole-quote pricing supersedes an in-flight non-sequencing ${outcome}`, async (t) => {
    const h = harness(t);
    const old = h.fire('quantity');
    const oldCall = h.take();
    const added = h.frm.add_child('items', seqRow('item-2'));
    const adding = h.fire('items_add', added);
    const call = h.take('preview_quote_items');
    call.succeed(wholePrice(call, [10, 303]));
    await adding;
    const freshSave = watch(h.api.validate(h.frm));
    await tick();
    assert.equal(freshSave.settled, true);
    if (outcome === 'success') oldCall.succeed(priced(oldCall, 999));
    else oldCall.fail('Superseded per-row failure');
    await old;
    assert.deepEqual(await freshSave.done, { ok: true });
    assert.equal(h.row.unit_price, 10);
    assert.equal(h.frm.doc.total_amount, 313);
    assert.equal(h.alerts.length, 0);
  });
}

test('a sibling edit waits for outstanding product defaults before pricing the complete vector', async (t) => {
  const h = seqHarness(t);
  h.row.project_type = 'RNA Project';
  const reset = h.fire('project_type');
  const options = h.take('get_quote_builder_options');
  h.frm.doc.items[1].quantity = 3;
  await h.fire('quantity', h.frm.doc.items[1]);
  const saving = watch(h.api.validate(h.frm));
  await tick();
  assert.equal(saving.settled, false);
  options.succeed({ products: h.products });
  await tick();
  const call = h.take('preview_quote_items');
  assert.equal(call.items[0].project_type, 'RNA Project');
  assert.equal(call.items[0].service, 'RNA service');
  assert.equal(call.items[1].quantity, 3);
  call.succeed(wholePrice(call, [10, 303]));
  await reset;
  assert.deepEqual(await saving.done, { ok: true });
  assert.equal(h.frm.doc.total_amount, 919);
});

for (const outcome of ['success', 'failure']) {
  test(`customer changes invalidate the old whole-quote ${outcome} and reprice siblings`, async (t) => {
    const h = seqHarness(t);
    const old = h.fire('quantity');
    const oldCall = h.take('preview_quote_items');
    const oldSave = watch(h.api.validate(h.frm));
    h.frm.doc.customer = 'CUSTOMER-2';
    h.handlers.get('NGS Quote').customer(h.frm);
    const call = h.take('preview_quote_items');
    assert.equal(call.args.customer, 'CUSTOMER-2');
    call.succeed(wholePrice(call, [250, 250]));
    await tick();
    if (outcome === 'success') oldCall.succeed(wholePrice(oldCall, [999, 999]));
    else oldCall.fail('Obsolete customer context');
    await old;
    assert.equal((await oldSave.done).ok, false, 'Save from the old context must be rejected');
    await h.api.validate(h.frm);
    assert.equal(h.frm.doc.total_amount, 500);
    assert.equal(h.alerts.length, 0);
  });
}

test('dialog changing away from sequencing still reprices its siblings', async (t) => {
  const h = seqHarness(t);
  const dialog = await openSeqDialog(h);
  const initial = h.take('preview_quote_items');
  initial.succeed(wholePrice(initial, [280, 280]));
  await tick();
  dialog.fields_dict.group.set_input('RNA');
  const switching = dialog.fields_dict.group.df.onchange();
  const call = h.take('preview_quote_items');
  assert.equal(call.items.length, 2);
  assert.equal(call.items[0].project_type, 'RNA Project');
  call.succeed(wholePrice(call, [10, 303]));
  await switching;
  const submitting = dialog.options.primary_action();
  const applied = h.take('preview_quote_items');
  applied.succeed(wholePrice(applied, [10, 303]));
  await submitting;
  await h.api.validate(h.frm);
  assert.equal(h.row.project_type, 'RNA Project');
  assert.equal(h.frm.doc.items[1].unit_price, 303);
  assert.equal(h.frm.doc.total_amount, 313);
});

for (const outcome of ['success', 'failure']) {
  test(`dialog ignores late ${outcome} after its inputs are cleared without onchange`, async (t) => {
    const h = seqHarness(t);
    const dialog = await openSeqDialog(h);
    const initial = h.take('preview_quote_items');
    dialog.fields_dict.quantity.set_input('');
    if (outcome === 'success') initial.succeed(wholePrice(initial, [999, 999]));
    else initial.fail('Obsolete valid inputs');
    await tick();
    assert.notEqual(dialog.get_value('unit_price'), 999);
    assert.equal(dialog.alert, null);
    dialog.hide();
  });
}

test('whole preview carries actual row identities only as allocation hints', async (t) => {
  const h = seqHarness(t, { name: 'b', idx: 1, million_reads_per_sample: 1.05,
    use_price_override: 1, override_unit_price: 100 },
  [seqRow('a', { idx: 2, million_reads_per_sample: 1.05 })]);
  Object.assign(h.row, { parent: 'foreign', owner: 'foreign', flags: { ignore_permissions: true } });
  const pending = h.fire('quantity');
  const call = h.take('preview_quote_items');
  assert.deepEqual(call.items.map((item) => item.pricing_allocation_key), ['b', 'a']);
  for (const item of call.items) {
    for (const field of ['name', 'doctype', 'parent', 'owner', 'flags']) assert.equal(Object.hasOwn(item, field), false);
  }
  call.succeed(wholePrice(call, [100, 20]));
  await pending;
  await h.api.validate(h.frm);
  assert.equal(h.frm.doc.total_amount, 120);
  assert.deepEqual(h.frm.doc.items.map((item) => item.name), ['b', 'a']);
  assert.ok(h.frm.doc.items.every((item) => !Object.hasOwn(item, 'pricing_allocation_key')));
});

test('Configure Project retains existing-row allocation identity throughout preview and Apply', async (t) => {
  const h = seqHarness(t, { name: 'b', idx: 1 }, [seqRow('a', { idx: 2 })]);
  const dialog = await openSeqDialog(h);
  const initial = h.take('preview_quote_items');
  assert.deepEqual(initial.items.map((item) => item.pricing_allocation_key), ['b', 'a']);
  initial.succeed(wholePrice(initial, [280, 280]));
  await tick();
  const submitting = dialog.options.primary_action();
  const call = h.take('preview_quote_items');
  assert.deepEqual(call.items.map((item) => item.pricing_allocation_key), ['b', 'a']);
  call.succeed(wholePrice(call, [280, 280]));
  await submitting;
  await h.api.validate(h.frm);
  assert.deepEqual(h.frm.doc.items.map((item) => item.name), ['b', 'a']);
});

test('Add Project preserves a blank placeholder allocation identity without double-counting', async (t) => {
  const h = harness(t, { project_type: '', service: '', unit_price: 0, amount: 0 }, [seqRow('item-2')]);
  h.products.unshift(seqProduct);
  const dialog = await openSeqDialog(h, false);
  const initial = h.take('preview_quote_items');
  assert.deepEqual(initial.items.map((item) => item.pricing_allocation_key), ['item-1', 'item-2']);
  initial.succeed(wholePrice(initial, [20, 300]));
  await tick();
  const submitting = dialog.options.primary_action();
  const call = h.take('preview_quote_items');
  assert.deepEqual(call.items.map((item) => item.pricing_allocation_key), ['item-1', 'item-2']);
  call.succeed(wholePrice(call, [20, 300]));
  await submitting;
  await h.api.validate(h.frm);
  assert.equal(h.frm.doc.items.length, 2);
  assert.equal(h.row.name, 'item-1');
});

test('Add Project retains sibling hints and omits an identity for a not-yet-created row', async (t) => {
  const h = seqHarness(t);
  const dialog = await openSeqDialog(h, false);
  const initial = h.take('preview_quote_items');
  assert.deepEqual(initial.items.map((item) => item.pricing_allocation_key), ['item-1', 'item-2', undefined]);
  initial.succeed(wholePrice(initial, [290, 290, 20]));
  await tick();
  const submitting = dialog.options.primary_action();
  const call = h.take('preview_quote_items');
  assert.equal(Object.hasOwn(call.items[2], 'pricing_allocation_key'), false);
  call.succeed(wholePrice(call, [290, 290, 20]));
  await submitting;
  await h.api.validate(h.frm);
});

test('a changed row allocation identity invalidates an otherwise identical pending vector', async (t) => {
  const h = seqHarness(t);
  const pending = h.fire('quantity');
  const old = h.take('preview_quote_items');
  h.row.name = 'renamed-row';
  old.succeed(wholePrice(old, [999, 999]));
  await pending;
  assert.equal(h.row.unit_price, 303);
  const saving = watch(h.api.validate(h.frm));
  const latest = h.take('preview_quote_items');
  assert.equal(latest.items[0].pricing_allocation_key, 'renamed-row');
  latest.succeed(wholePrice(latest, [280, 280]));
  assert.deepEqual(await saving.done, { ok: true });
});

for (const delta of [0.0049, 0.005, -0.005]) {
  test(`returned amount tolerates rounding difference ${delta}`, async (t) => {
    const h = seqHarness(t);
    const pending = h.fire('quantity');
    const call = h.take('preview_quote_items');
    const result = wholePrice(call, [40, 40]);
    result.items[1].amount += delta;
    result.total_amount += delta;
    call.succeed(result);
    await pending;
    await h.api.validate(h.frm);
    assert.ok(Math.abs(h.frm.doc.total_amount - (80 + delta)) < 1e-10);
    assert.equal(h.alerts.length, 0);
  });
}

for (const invalid of ['negative_price', 'negative_amount', 'quantity_mismatch']) {
  test(`single-item preview rejects ${invalid} while keeping snapshots optional for non-sequencing`, async (t) => {
    const h = harness(t);
    const pending = h.fire('quantity');
    const call = h.take();
    const result = priced(call, 10, { pricing_details: '' });
    if (invalid === 'negative_price') Object.assign(result, { unit_price: -10, amount: -10 });
    else if (invalid === 'negative_amount') result.amount = -10;
    else result.amount += 0.006;
    call.succeed(result);
    await pending;
    await assert.rejects(h.api.validate(h.frm), /Resolve pricing preview errors/);
    const retry = h.fire('quantity');
    const retryCall = h.take();
    retryCall.succeed(priced(retryCall, 0, { pricing_details: '' }));
    await retry;
    await h.api.validate(h.frm);
    assert.equal(h.frm.doc.total_amount, 0);
  });
}

test('dialog rejects a missing sequencing snapshot without updating prices or allowing Apply to save', async (t) => {
  const h = seqHarness(t);
  const dialog = await openSeqDialog(h);
  const initial = h.take('preview_quote_items');
  const invalid = wholePrice(initial, [280, 280]);
  delete invalid.items[0].pricing_details;
  initial.succeed(invalid);
  await tick();
  assert.equal(dialog.get_value('unit_price'), '');
  assert.equal(dialog.alert.indicator, 'danger');
  const submitting = dialog.options.primary_action();
  const call = h.take('preview_quote_items');
  const invalidSubmission = wholePrice(call, [280, 280]);
  delete invalidSubmission.items[0].pricing_details;
  call.succeed(invalidSubmission);
  await submitting;
  assert.equal(h.row.unit_price, 303);
  const saving = watch(h.api.validate(h.frm));
  const form = h.take('preview_quote_items');
  form.succeed(wholePrice(form, [280, 280]));
  const result = await saving.done;
  assert.equal(result.ok, false);
  assert.match(result.error.message, /project configuration pricing errors/);
  dialog.hide();
});
