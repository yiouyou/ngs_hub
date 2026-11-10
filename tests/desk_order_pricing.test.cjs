// Run: node --test tests/desk_order_pricing.test.cjs
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname,
  '../ngs_hub/ngs_hub/doctype/ngs_order/ngs_order.js'), 'utf8');
const copy = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

function harness(version = 1) {
  const handlers = {}, calls = [], alerts = [], renders = [];
  const rows = ['ROW-A', 'ROW-B'].map(name => ({
    name, project_type: 'Sequencing Only - 1M Reads', quantity: 1,
    million_reads_per_sample: 100, unit_price: 208, amount: 208,
    pricing_details: 'saved snapshot', notes: 'saved note',
  }));
  const frm = {
    doc: {doctype: 'NGS Order', name: 'ORDER-TEST', items: rows,
      sequencing_pricing_version: version},
    is_new: () => false, refresh_field() {},
  };
  const frappe = {
    ui: {form: {on(doctype, events) { handlers[doctype] = events; }}},
    ngs_item_details: {render() { renders.push(copy(frm.doc.items)); }},
    model: {async set_value(cdt, cdn, field, value) {
      await Promise.resolve();
      assert.equal(cdt, 'NGS Order Item');
      rows.find(row => row.name === cdn)[field] = value;
    }},
    call(options) {
      const request = deferred();
      calls.push({...request, options});
      return request.promise;
    },
    show_alert(value) { alerts.push(value); },
    throw(message) { throw new Error(message); },
  };
  const context = vm.createContext({frappe, flt: Number, __: value => value,
    locals: {'NGS Order Item': Object.fromEntries(rows.map(row => [row.name, row]))}});
  vm.runInContext(source, context);
  return {
    frm, rows, calls, alerts, renders,
    edit(quantity, row = rows[0]) {
      row.quantity = quantity;
      return handlers['NGS Order Item'].quantity(frm, 'NGS Order Item', row.name);
    },
    remove(row) {
      frm.doc.items = frm.doc.items.filter(item => item !== row);
      return handlers['NGS Order Item'].items_remove(frm);
    },
    save() { return handlers['NGS Order'].validate(frm); },
  };
}

function response(rows, amounts = [372.66, 186.34]) {
  return {message: {items: rows.map((row, index) => ({
    name: row.name, amount: amounts[index], unit_price: amounts[index] / row.quantity,
    pricing_details: `snapshot-${amounts[index]}`, notes: `note-${amounts[index]}`,
  }))}};
}

test('version 0 quantity updates locally without an RPC', async () => {
  const h = harness(0);
  await h.edit(2);
  assert.equal(h.rows[0].amount, 416);
  assert.equal(h.rows[1].amount, 208);
  assert.equal(h.calls.length, 0);
  assert.equal(h.renders.at(-1)[0].amount, 416);
  await h.save();
});

test('version 1 sends only names and quantities and reprices all rows', async () => {
  const h = harness();
  const edit = h.edit(2);
  await tick();
  assert.equal(h.calls.length, 1);
  const request = h.calls[0].options;
  assert.equal(request.method, 'ngs_hub.api.desk_order.preview_order_sequencing');
  assert.equal(request.args.order_name, 'ORDER-TEST');
  assert.deepEqual(JSON.parse(request.args.items), [
    {name: 'ROW-A', quantity: 2}, {name: 'ROW-B', quantity: 1},
  ]);
  // Servers need not return items in grid order.
  const result = response(h.frm.doc.items);
  result.message.items.reverse();
  h.calls[0].resolve(result);
  await edit;
  assert.deepEqual(h.rows.map(row => row.amount), [372.66, 186.34]);
  assert.equal(h.rows[0].unit_price, 186.33);
  assert.equal(h.rows[1].pricing_details, 'snapshot-186.34');
  assert.equal(h.rows[1].notes, 'note-186.34');
  assert.equal(h.renders.at(-1)[1].amount, 186.34);
  await h.save();
});

for (const staleOutcome of ['success', 'failure']) {
  test(`newer success wins over older ${staleOutcome}`, async () => {
    const h = harness();
    const first = h.edit(2);
    await tick();
    const oldResponse = response(h.frm.doc.items);
    const second = h.edit(3);
    await tick();
    h.calls[1].resolve(response(h.frm.doc.items, [513, 171]));
    await second;
    const winner = copy(h.frm.doc.items);
    if (staleOutcome === 'success') h.calls[0].resolve(oldResponse);
    else h.calls[0].reject(new Error('obsolete failure'));
    await first;
    assert.deepEqual(copy(h.frm.doc.items), winner);
    assert.equal(h.alerts.length, 0);
    await h.save();
  });
}

test('Save follows replacement pending after the older request settles', async () => {
  const h = harness();
  const first = h.edit(2);
  await tick();
  let saved = false;
  const save = h.save().then(() => { saved = true; });
  const second = h.edit(3);
  await tick();
  h.calls[0].resolve(response(h.frm.doc.items, [1, 1]));
  await first;
  await tick();
  const escaped = saved;
  h.calls[1].resolve(response(h.frm.doc.items, [513, 171]));
  await second;
  await save;
  assert.equal(escaped, false, 'Save must follow the current pending request');
  assert.equal(saved, true);
  assert.equal(h.rows[1].amount, 171);
});

test('Save follows replacement success without waiting for an obsolete hung RPC', async () => {
  const h = harness();
  const first = h.edit(2);
  let obsoleteSettled = false;
  first.then(() => { obsoleteSettled = true; });
  await tick();
  const obsolete = response(h.frm.doc.items);
  let saved = false;
  const save = h.save().then(() => { saved = true; });
  try {
    const second = h.edit(3);
    await tick();
    assert.equal(h.calls.length, 2);
    assert.equal(saved, false, 'Save must still wait for the replacement');
    h.calls[1].resolve(response(h.frm.doc.items, [513, 171]));
    await second;
    await tick();
    assert.equal(obsoleteSettled, false, 'Obsolete RPC must remain unresolved during this check');
    assert.equal(saved, true, 'Save must not remain blocked on an obsolete hung RPC');
    assert.equal(h.rows[1].amount, 171);
    assert.equal(h.alerts.length, 0);
  } finally {
    // Settle the obsolete request only after observing Save, including on failure.
    for (const request of h.calls) request.resolve(obsolete);
    await first;
    await save;
  }
  assert.equal(h.rows[1].amount, 171);
});

test('current failure blocks Save and successful retry clears it', async () => {
  const h = harness();
  const first = h.edit(2);
  await tick();
  h.calls[0].reject(new Error('current failure'));
  await first;
  assert.equal(h.alerts.length, 1);
  await assert.rejects(h.save(), /pricing preview error/i);
  const retry = h.edit(3);
  await tick();
  let saved = false;
  const save = h.save().then(() => { saved = true; });
  await tick();
  const escaped = saved;
  h.calls[1].resolve(response(h.frm.doc.items, [513, 171]));
  await retry;
  await save;
  assert.equal(escaped, false);
  assert.equal(saved, true);
});

test('obsolete success cannot clear a current failure', async () => {
  const h = harness();
  const first = h.edit(2);
  await tick();
  const second = h.edit(3);
  await tick();
  h.calls[1].reject(new Error('current failure'));
  await second;
  h.calls[0].resolve(response(h.frm.doc.items, [1, 1]));
  await first;
  assert.equal(h.alerts.length, 1);
  await assert.rejects(h.save(), /pricing preview error/i);
});

test('deletion starts a reduced-row preview and invalidates the older result', async () => {
  const h = harness();
  const first = h.edit(2);
  await tick();
  const obsolete = response(h.frm.doc.items);
  const removed = h.remove(h.rows[0]);
  assert.equal(h.calls.length, 2);
  assert.deepEqual(JSON.parse(h.calls[1].options.args.items), [{name: 'ROW-B', quantity: 1}]);
  h.calls[1].resolve(response(h.frm.doc.items, [208]));
  await removed;
  const winner = copy(h.frm.doc.items);
  h.calls[0].resolve(obsolete);
  await first;
  assert.deepEqual(copy(h.frm.doc.items), winner);
  assert.equal(h.alerts.length, 0);
  await h.save();
});

test('deleting last sequencing row invalidates pending failure without another RPC', async () => {
  const h = harness();
  h.frm.doc.items = [h.rows[0]];
  const edit = h.edit(2);
  await tick();
  await h.remove(h.rows[0]);
  await h.save();
  h.calls[0].reject(new Error('deleted row'));
  await edit;
  assert.equal(h.calls.length, 1);
  assert.equal(h.alerts.length, 0);
  assert.deepEqual(h.frm.doc.items, []);
  await h.save();
});

const invalidResponses = {
  'missing message': () => ({}),
  'missing row': rows => response(rows.slice(0, 1)),
  'duplicate name': rows => response([rows[0], rows[0]]),
  'foreign row': rows => response([rows[0], {...rows[1], name: 'FOREIGN'}]),
  'nonfinite amount': rows => response(rows, [372.66, Infinity]),
  'string amount': rows => response(rows, [372.66, '186.34']),
  'negative prices': rows => response(rows, [372.66, -1]),
  'nonfinite unit price': rows => {
    const result = response(rows);
    result.message.items[1].unit_price = NaN;
    return result;
  },
  'inconsistent amount': rows => {
    const result = response(rows);
    result.message.items[1].amount += 100;
    return result;
  },
  'missing pricing snapshot': rows => {
    const result = response(rows);
    delete result.message.items[1].pricing_details;
    return result;
  },
  'null response': () => null,
  'null row': () => ({message: {items: [null, null]}}),
};

for (const [label, invalid] of Object.entries(invalidResponses)) {
  test(`invalid response fails closed without partial updates: ${label}`, async () => {
    const h = harness();
    const edit = h.edit(2);
    await tick();
    const before = copy(h.frm.doc.items);
    h.calls[0].resolve(invalid(h.frm.doc.items));
    await edit;
    const saveError = await h.save().then(() => null, error => error);
    assert.deepEqual(copy(h.frm.doc.items), before);
    assert.equal(h.alerts.length, 1);
    assert(saveError, 'Invalid response must block Save');
    assert.match(saveError.message, /pricing preview error/i);
  });
}

test('Save immediately after a quantity event waits for its preview', async () => {
  const h = harness();
  const edit = h.edit(2);
  let saved = false;
  const save = h.save().then(() => { saved = true; });
  await tick();
  const escaped = saved;
  h.calls[0].resolve(response(h.frm.doc.items));
  await edit;
  await save;
  assert.equal(escaped, false, 'Save escaped before the amount continuation registered its RPC');
});
