// Run from the repository root: node --test tests/desk_item_details.test.cjs
const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const filename = path.join(__dirname, '../ngs_hub/public/js/desk_item_details.js');
const source = readFileSync(filename, 'utf8');
const usd = (value) => new Intl.NumberFormat('en-US', {
  style: 'currency', currency: 'USD',
}).format(value);

function harness(format = (value, options, formatOptions) => {
  assert.equal(options.fieldtype, 'Currency');
  assert.equal(options.options, 'USD');
  assert.equal(formatOptions.only_value, true, 'Native Currency formatting must not wrap money in HTML');
  assert.equal(typeof value, 'number');
  assert.ok(Number.isFinite(value));
  return usd(value);
}, globals = {}) {
  const frappe = {
    format,
    call() { assert.fail('Rendering must not make RPC calls'); },
    db: new Proxy({}, { get() { assert.fail('Rendering must not access the database'); } }),
    model: new Proxy({}, { get() { assert.fail('Rendering must not modify document models'); } }),
  };
  vm.runInNewContext(source, { frappe, ...globals }, { filename });
  return frappe.ngs_item_details;
}

function freeze(value) {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
}

function item(overrides = {}) {
  return {
    name: 'order-row-1', quote_item: 'quote-row-1', project_type: 'Bulk RNAseq',
    sample_type: 'Total RNA', quantity: 2, data_analysis: 'Standard Analysis',
    unit_price: 10, amount: 20, ...overrides,
  };
}

function document(doctype, row, overrides = {}) {
  return { doctype, name: 'SAVED-1', status: 'Confirmed', docstatus: 1, items: [row], ...overrides };
}

function fallback_document(rowOverrides = {}, fallbackOverrides = {}, docOverrides = {}) {
  const row = item({ details_snapshot_version: 0, ...rowOverrides });
  return document('NGS Order', row, {
    __onload: { item_detail_fallbacks: { [row.name]: {
      quote_item: row.quote_item, project_type: row.project_type,
      species: 'Legacy species', million_reads_per_sample: 50,
      source: 'Linked quote (legacy order)', ...fallbackOverrides,
    } } }, ...docOverrides,
  });
}

function data_rows(html) {
  return [...html.matchAll(/<tr>(<td>\d+<\/td><th scope="row"[\s\S]*?)<\/tr>/g)].map((row) =>
    [...row[1].matchAll(/<(?:th|td)\b[^>]*>([\s\S]*?)<\/(?:th|td)>/g)].map((cell) => cell[1]));
}

function total(html) {
  return html.match(/<tfoot>[\s\S]*?<td[^>]*>([\s\S]*?)<\/td>[\s\S]*?<\/tfoot>/)[1];
}

for (const doctype of ['NGS Quote', 'NGS Order']) {
  test(`${doctype}: saved WGS 45x is the title and coverage, never the WGS_30X catalog depth`, () => {
    const doc = freeze(document(doctype, item({
      project_type: 'WGS', service: 'WGS_30X', read_depth: '45x', species: 'Human',
      sample_type: 'DNA', quantity: 3, unit_price: 173, amount: 519,
      description: 'Client requested 45x, not 30x', notes: 'Keep this coverage',
      rule_notes: 'Saved coverage: 45x', details_snapshot_version: doctype === 'NGS Order' ? 1 : undefined,
    })));
    const before = JSON.stringify(doc);
    const html = harness().build_html(doc);
    const cells = data_rows(html)[0];
    assert.equal(cells[1], 'WGS - 45x');
    assert.doesNotMatch(html, /WGS_30X|Service:|WGS - 30x/);
    assert.equal(doc.items[0].service, 'WGS_30X');
    assert.equal(cells[2], 'Sample: DNA<br>Species: Human');
    assert.equal(cells[3], '3');
    assert.equal(cells[4], '45x');
    assert.equal(cells[5], 'Standard Analysis');
    assert.equal(cells[6], '$173.00');
    assert.equal(cells[7], '$519.00');
    assert.match(html, /Description: Client requested 45x, not 30x/);
    assert.match(html, /Notes: Keep this coverage/);
    assert.match(html, /Rule Notes: Saved coverage: 45x/);
    assert.doesNotMatch(html, /Source:/);
    assert.equal(total(html), '$519.00');
    assert.equal(JSON.stringify(doc), before);
  });

  test(`${doctype}: saved reads 100/50 and total 321 come only from row facts`, () => {
    const doc = freeze(document(doctype, null, { total_amount: 99999, quote_total_amount: 77777, items: [
      item({ project_type: 'Sequencing Only - 1M Reads', sample_type: 'Library',
        quantity: 7, million_reads_per_sample: 100, unit_price: 9, amount: 123 }),
      item({ name: 'row-2', quantity: 4, read_depth: '50M', unit_price: 8, amount: 198 }),
    ] }));
    const html = harness().build_html(doc);
    const rows = data_rows(html);
    assert.equal(rows[0][4], '100M / library');
    assert.equal(rows[1][4], '50M / sample');
    assert.equal(rows[0][6], '$9.00');
    assert.equal(rows[0][7], '$123.00');
    assert.equal(rows[1][7], '$198.00');
    assert.equal(total(html), '$321.00');
    assert.doesNotMatch(html, /99,999|77,777/);
    assert.equal(doc.total_amount, 99999);
  });
}

test('stored WGS 30x is retained; missing legacy depth/species never comes from the catalog ID', () => {
  const api = harness();
  const known = data_rows(api.build_html(document('NGS Quote', item({
    project_type: 'WGS', read_depth: '30x', species: 'Mouse',
  }))))[0];
  assert.equal(known[1], 'WGS - 30x');
  assert.equal(known[4], '30x');
  assert.match(known[2], /Species: Mouse/);
  const unknown = data_rows(api.build_html(document('NGS Order', item({
    project_type: 'WGS', service: 'WGS_30X',
  }))))[0];
  assert.equal(unknown[1], 'WGS');
  assert.equal(unknown[4], '-');
  assert.match(unknown[2], /Species: -$/);
});

test('10x full product name, options, address, fees and all notes remain visible without repricing', () => {
  const label = "10x 5' Transcriptome + VDJ";
  const html = harness().build_html(document('NGS Order', item({
    project_type: label, species: 'Human', sample_type: 'Frozen Tissue or Organoid',
    million_reads_per_sample: 20, library_qc: 1, tissue_dissociation: '1', nuclei_extraction: true,
    add_on_sequencing: 1, onsite_service: 1, onsite_location: 'Outside Massachusetts',
    onsite_address: '123 Research Road\nNew York, NY 10001', confirmed_tbd_fee: 175,
    use_price_override: 1, override_unit_price: 999, description: 'Client description',
    rule_notes: 'Rule one\nRule two', notes: 'Order-specific note',
  })));
  assert.equal(data_rows(html)[0][1], '10x 5&#39; Transcriptome + VDJ - 20M / sample');
  assert.match(html, /Project Type: 10x 5&#39; Transcriptome \+ VDJ<\/div>/);
  assert.equal(data_rows(html)[0][4], '20M / sample');
  for (const label of ['Library QC', 'Tissue Dissociation', 'Nuclei Extraction', 'Add-on Sequencing', 'On-site Service']) {
    assert.ok(html.includes(`${label}: Yes`));
  }
  assert.match(html, /On-site Location: Outside Massachusetts/);
  assert.match(html, /On-site Address: 123 Research Road<br>New York, NY 10001/);
  assert.match(html, /Confirmed TBD Fee: \$175\.00/);
  assert.match(html, /Manual Unit Price: Yes \(\$999\.00\)/);
  assert.match(html, /Description: Client description/);
  assert.match(html, /Rule Notes: Rule one<br>Rule two/);
  assert.match(html, /Notes: Order-specific note/);
  assert.equal(data_rows(html)[0][6], '$10.00');
  assert.equal(total(html), '$20.00');
});

test('arbitrarily long project names and fallback service titles are preserved in full', () => {
  const label = 'Client-defined project ' + 'full-length-name-'.repeat(40);
  const service = 'Recorded service ' + 'long-service-'.repeat(40);
  const html = harness().build_html(document('NGS Quote', item({ project_type: label, service })));
  assert.ok(html.includes(label));
  assert.ok(!html.includes(service));
  const fallback = harness().build_html(document('NGS Quote', item({ project_type: '', service })));
  assert.equal(data_rows(fallback)[0][1], service);
  assert.doesNotMatch(html, /ellipsis|truncate|<style|style=|card/);
  assert.doesNotMatch(html, /<th scope="row" class="[^"]*text-nowrap/);
});

test('Quote and Order show the service only as a single fallback title when project type is missing', () => {
  const api = harness();
  for (const doctype of ['NGS Quote', 'NGS Order']) {
    for (const project_type of [undefined, null, '', ' ']) {
      const doc = freeze(document(doctype, item({ project_type, service: 'Recorded service title' })));
      const html = api.build_html(doc);
      assert.equal(data_rows(html)[0][1], 'Recorded service title');
      assert.equal(html.split('Recorded service title').length - 1, 1);
      assert.doesNotMatch(html, /Service:/);
    }
    const html = api.build_html(document(doctype, item({ service: 'TECHNICAL_SERVICE_ID' })));
    assert.equal(data_rows(html)[0][1], 'Bulk RNAseq');
    assert.doesNotMatch(html, /TECHNICAL_SERVICE_ID|Service:/);
  }
});

test('unknown values are dashes, not fabricated quantity, coverage, species or money', () => {
  const html = harness().build_html(document('NGS Order', { project_type: 'WGS' }));
  const cells = data_rows(html)[0];
  assert.equal(cells[1], 'WGS');
  assert.equal(cells[2], 'Sample: -<br>Species: -');
  assert.deepEqual(cells.slice(3), ['-', '-', '-', '-', '-']);
  assert.equal(total(html), '-');
  assert.doesNotMatch(html, /Manual Unit Price: No|30x|20M|TBD|\$0/);
  for (const quantity of [undefined, null, '', ' ', 'invalid', Infinity]) {
    const cells = data_rows(harness().build_html(document('NGS Quote', item({ quantity }))))[0];
    assert.equal(cells[3], '-');
  }
});

test('explicit zero quantity, prices, amounts and false flags stay zero/No, not TBD', () => {
  const html = harness().build_html(document('NGS Quote', item({
    quantity: 0, unit_price: '0', amount: 0, million_reads_per_sample: 0,
    use_price_override: 0, confirmed_tbd_fee: 0, library_qc: '0',
  }), { status: 'Pending', pricing_to_confirm: 1, missing_info: 'Awaiting address' }));
  const cells = data_rows(html)[0];
  assert.equal(cells[3], '0');
  assert.equal(cells[4], '-');
  assert.equal(cells[6], '$0.00');
  assert.equal(cells[7], '$0.00');
  assert.equal(total(html), '$0.00');
  assert.match(html, /Pricing to confirm/);
  assert.match(html, /Awaiting address/);
  assert.match(html, /Manual Unit Price: No/);
  assert.match(html, /Library QC: No/);
  assert.doesNotMatch(html, />TBD<|Confirmed TBD Fee/);
});

test('pending custom zero is TBD even with override; confirmed/custom and nonCustom manual zero stay numeric', () => {
  const api = harness();
  const row = item({ project_type: 'Custom Project', unit_price: 0, amount: 0 });
  assert.deepEqual(data_rows(api.build_html(document('NGS Quote', row, { status: 'Pending' })))[0].slice(6), ['TBD', 'TBD']);
  for (const doc of [
    document('NGS Quote', row),
    document('NGS Quote', item({ unit_price: 0, amount: 0, use_price_override: 1, override_unit_price: 0 }),
      { status: 'Pending' }),
  ]) {
    assert.deepEqual(data_rows(api.build_html(doc))[0].slice(6), ['$0.00', '$0.00']);
  }
  for (const doctype of ['NGS Quote', 'NGS Order']) {
    const html = api.build_html(document(doctype,
      { ...row, use_price_override: 1, override_unit_price: 0 }, { status: 'Pending' }));
    assert.deepEqual(data_rows(html)[0].slice(6), ['TBD', 'TBD']);
    assert.match(html, /Manual Unit Price: Yes \(\$0\.00\)/);
  }
  const pending = document('NGS Quote', item({ amount: 321 }), {
    status: 'Pending', missing_info: 'Fee not confirmed', pricing_to_confirm: '1',
  });
  const html = api.build_html(pending);
  assert.match(html, /Pricing to confirm/);
  assert.equal(total(html), '$321.00');
  assert.equal(data_rows(html)[0][7], '$321.00');
});

test('each unresolved Quote signal labels the recorded sum Priced Subtotal, never a final Total', () => {
  const api = harness();
  for (const signal of [{ pricing_to_confirm: 1 }, { missing_info: 'On-site fee pending' }, { status: 'Pending' }]) {
    const quote = api.build_html(document('NGS Quote', item({ amount: 321 }), signal));
    assert.match(quote, /Pricing to confirm/);
    assert.match(quote, /<tfoot>[\s\S]*?>Priced Subtotal<\/th>/);
    assert.doesNotMatch(quote, />Total<\/th>/);
    assert.equal(total(quote), '$321.00');
    const order = api.build_html(document('NGS Order', item({ amount: 321 }), signal));
    assert.match(order, /<tfoot>[\s\S]*?>Total<\/th>/);
    assert.equal(total(order), '$321.00');
  }
  const confirmed = api.build_html(document('NGS Quote', item()));
  assert.match(confirmed, /<tfoot>[\s\S]*?>Total<\/th>/);
});

test('matching legacy fallback fills only missing facts, discloses source and never mutates frozen documents', () => {
  const doc = freeze(fallback_document({ million_reads_per_sample: 0 }, {
    confirmed_tbd_fee: 120, use_price_override: 1, override_unit_price: 456,
    quantity: 99, unit_price: 999, amount: 99999, description: 'Must not be copied',
  }));
  const before = JSON.stringify(doc);
  const html = harness().build_html(doc);
  const cells = data_rows(html)[0];
  assert.match(cells[2], /Species: Legacy species/);
  assert.equal(cells[4], '50M / sample');
  assert.equal(cells[3], '2');
  assert.equal(cells[6], '$10.00');
  assert.equal(cells[7], '$20.00');
  assert.equal(total(html), '$20.00');
  assert.match(html, /Source: Linked quote \(legacy order\)/);
  assert.match(html, /Confirmed TBD Fee: \$120\.00/);
  assert.match(html, /Manual Unit Price: Yes \(\$456\.00\)/);
  assert.doesNotMatch(html, /Must not be copied|99,999/);
  assert.equal(JSON.stringify(doc), before);
});

test('legacy fallback never replaces nonempty facts or explicit zero fee/override flags', () => {
  const doc = fallback_document({
    species: 'Saved species', million_reads_per_sample: 100,
    confirmed_tbd_fee: 0, use_price_override: 0, override_unit_price: 0,
  }, { read_depth: '50M', confirmed_tbd_fee: 120, use_price_override: 1, override_unit_price: 456 });
  const html = harness().build_html(freeze(doc));
  assert.match(html, /Species: Saved species/);
  assert.equal(data_rows(html)[0][4], '100M / sample');
  assert.match(html, /Manual Unit Price: No/);
  assert.doesNotMatch(html, /Source:|Legacy species|Confirmed TBD Fee|456|50M/);
});

for (const depth of ['45x', '100M']) {
  test(`legacy fallback cannot override or conflict with raw ${depth}`, () => {
    const doc = fallback_document({
      species: 'Saved species', read_depth: depth, million_reads_per_sample: 0,
    }, { read_depth: '30x', million_reads_per_sample: 50 });
    const html = harness().build_html(freeze(doc));
    assert.equal(data_rows(html)[0][4], depth === '100M' ? '100M / sample' : depth);
    assert.doesNotMatch(html, /Source:|30x|50M/);
  });
}

test('legacy WGS depth fallback is labeled and may supplement species without replacing a saved 45x', () => {
  const api = harness();
  const missing = api.build_html(freeze(fallback_document({ project_type: 'WGS' }, {
    read_depth: '45x', million_reads_per_sample: 0,
  })));
  assert.equal(data_rows(missing)[0][1], 'WGS - 45x');
  assert.match(missing, /Source:/);
  const saved = api.build_html(freeze(fallback_document({ project_type: 'WGS', read_depth: '45x' }, {
    read_depth: '30x', million_reads_per_sample: 0,
  })));
  assert.equal(data_rows(saved)[0][1], 'WGS - 45x');
  assert.match(saved, /Species: Legacy species/);
  assert.doesNotMatch(saved, /30x/);
});

for (const version of [1, '1', 2, -1, 'unknown']) {
  test(`snapshot version ${JSON.stringify(version)} never uses legacy fallback even with missing fields`, () => {
    const html = harness().build_html(freeze(fallback_document({ details_snapshot_version: version })));
    assert.match(html, /Species: -/);
    assert.equal(data_rows(html)[0][4], '-');
    assert.doesNotMatch(html, /Source:|Legacy species|50M/);
  });
}

test('fallback is Order-only and requires exact nonempty row, quote-item and project identities', () => {
  const docs = [
    fallback_document({}, {}, { doctype: 'NGS Quote' }),
    fallback_document({}, { quote_item: 'other-quote-row' }),
    fallback_document({}, { project_type: 'WGS' }),
    fallback_document({ quote_item: '' }),
    fallback_document({ project_type: '' }),
    fallback_document({}, {}, { __onload: { item_detail_fallbacks: {} } }),
    fallback_document({}, {}, { __onload: null }),
  ];
  const inherited = fallback_document();
  inherited.__onload.item_detail_fallbacks = Object.create(inherited.__onload.item_detail_fallbacks);
  docs.push(inherited);
  for (const doc of docs) {
    const html = harness().build_html(freeze(doc));
    assert.doesNotMatch(html, /Source:|Legacy species|50M/);
  }
});

test('legacy add-on fallback is disclosed, but never overrides a stored No or a versioned snapshot', () => {
  const api = harness();
  const html = api.build_html(fallback_document({}, { add_on_sequencing: 1 }));
  assert.match(html, /Add-on Sequencing: Yes/);
  assert.match(html, /Source: Linked quote/);
  const saved = api.build_html(fallback_document({ add_on_sequencing: 0 }, { add_on_sequencing: 1 }));
  assert.match(saved, /Add-on Sequencing: No/);
  const snapshot = api.build_html(fallback_document({ details_snapshot_version: 1 }, { add_on_sequencing: 1 }));
  assert.doesNotMatch(snapshot, /Add-on Sequencing|Source:/);
});

test('native Currency format is requested as plain text, not escaped alignment HTML', () => {
  const api = harness((value, df, options) => options?.only_value ? usd(value) :
    `<div class="text-right">${usd(value)}</div>`);
  const html = api.build_html(document('NGS Quote', item()));
  assert.equal(data_rows(html)[0][6], '$10.00');
  assert.equal(total(html), '$20.00');
  assert.doesNotMatch(html, /&lt;div/);
});

test('malicious text in all displayed fields, notes, address and fallback source is escaped', () => {
  const payload = '<img src=x onerror="alert(1)"> & \' <script>alert(2)</script>';
  const escaped = '&lt;img src=x onerror=&quot;alert(1)&quot;&gt; &amp; &#39; &lt;script&gt;alert(2)&lt;/script&gt;';
  const api = harness();
  for (const field of [
    'project_type', 'service', 'sample_type', 'species', 'read_depth', 'data_analysis',
    'description', 'rule_notes', 'notes', 'onsite_location', 'onsite_address',
  ]) {
    const row = item({ [field]: payload });
    if (field === 'service') row.project_type = '';
    const html = api.build_html(document('NGS Quote', row));
    assert.ok(html.includes(escaped), field);
    assert.doesNotMatch(html, /<img|<script|onerror="/);
  }
  const warning = api.build_html(document('NGS Quote', item(), { missing_info: payload }));
  assert.ok(warning.includes(escaped));
  const fallback = api.build_html(fallback_document({}, { source: payload }));
  assert.ok(fallback.includes(escaped));
});

test('currency input and formatter output cannot inject HTML, including document currency', () => {
  const api = harness(() => '<span onclick="alert(1)">$20.00</span>');
  const html = api.build_html(document('NGS Order', item({
    use_price_override: 1, override_unit_price: 20, confirmed_tbd_fee: 20,
  }), { currency: '"><img src=x onerror=alert(1)>' }));
  assert.match(html, /&lt;span onclick=&quot;alert\(1\)&quot;&gt;\$20\.00&lt;\/span&gt;/);
  assert.doesNotMatch(html, /<span|<img|onclick="/);
  const invalid = harness().build_html(document('NGS Quote', item({
    unit_price: '<img src=x>', amount: '<script>alert(1)</script>',
  })));
  assert.deepEqual(data_rows(invalid)[0].slice(6), ['-', '-']);
  assert.equal(total(invalid), '-');
});

test('native format_currency and no-Frappe-formatter fallback preserve USD zero', () => {
  let calls = 0;
  const api = harness(null, { format_currency(value, currency) {
    calls += 1;
    assert.equal(currency, 'USD');
    return usd(value);
  } });
  const doc = document('NGS Quote', item({ unit_price: 0, amount: 0 }));
  assert.equal(total(api.build_html(doc)), '$0.00');
  assert.equal(calls, 3);
  assert.equal(total(harness(null).build_html(doc)), '$0.00');
});

test('native responsive table has caption, scoped headers, keyboard scrolling and nonwrapping numeric cells', () => {
  const html = harness().build_html(document('NGS Order', item({ unit_price: 104, amount: 208 })));
  assert.match(html, /class="table-responsive" role="region" aria-label="Item details" tabindex="0"/);
  assert.match(html, /<table class="table table-bordered"><caption>Item details<\/caption>/);
  assert.equal([...html.matchAll(/scope="col"/g)].length, 8);
  assert.match(html, /<th scope="row"/);
  assert.equal([...html.matchAll(/class="text-right text-nowrap"/g)].length, 4);
  assert.match(html, /<td class="text-right text-nowrap">2<\/td>/);
  assert.match(html, /<td class="text-right text-nowrap">\$104\.00<\/td>/);
  assert.equal([...html.matchAll(/<td class="text-right text-nowrap">\$208\.00<\/td>/g)].length, 2);
  assert.doesNotMatch(html, /<style|style=|<img|<button|<input/);
});

test('render writes the item_details wrapper only, tolerates missing fields and reflects live row edits', () => {
  const api = harness();
  assert.deepEqual(Object.keys(api).sort(), ['build_html', 'render']);
  const writes = [];
  const doc = document('NGS Quote', item({ project_type: 'WGS', read_depth: '45x' }));
  const frm = { doc, fields_dict: { item_details: { $wrapper: {
    html(value) { writes.push(value); },
  } } } };
  api.render(frm);
  assert.equal(writes[0], api.build_html(doc));
  assert.match(writes[0], /WGS - 45x/);
  doc.items[0].read_depth = '60x';
  doc.items[0].unit_price = 7;
  doc.items[0].amount = 321;
  api.render(frm);
  assert.match(writes[1], /WGS - 60x/);
  assert.equal(data_rows(writes[1])[0][6], '$7.00');
  assert.equal(total(writes[1]), '$321.00');
  assert.equal(doc.items[0].unit_price, 7);
  assert.equal(doc.items[0].amount, 321);
  for (const value of [undefined, {}, { fields_dict: {} }, { fields_dict: { item_details: {} } }]) {
    assert.doesNotThrow(() => api.render(value));
  }
});

test('empty items render an explicit empty state and total zero; sources use LF only', () => {
  for (const doc of [{}, { items: [] }, { items: null }]) {
    const html = harness().build_html(doc);
    assert.match(html, /No items/);
    assert.equal(total(html), '$0.00');
  }
  assert.equal(source.includes('\r'), false);
  assert.equal(readFileSync(__filename, 'utf8').includes('\r'), false);
});

function with_pricing(row = item(), overrides = {}) {
  const spec = Object.fromEntries([
    'project_type', 'service', 'sample_type', 'species', 'read_depth', 'data_analysis', 'onsite_location', 'onsite_address',
    'description',
  ].map((field) => [field, String(row[field] ?? '').trim()]));
  spec.species = spec.species.toLowerCase();
  for (const field of ['onsite_service', 'tissue_dissociation', 'nuclei_extraction', 'library_qc',
    'add_on_sequencing', 'use_price_override']) {
    spec[field] = [1, '1', true].includes(row[field]) ? 1 : 0;
  }
  for (const field of ['million_reads_per_sample', 'confirmed_tbd_fee', 'override_unit_price']) {
    spec[field] = Number(row[field] ?? 0);
  }
  if (!spec.use_price_override) spec.override_unit_price = 0;
  if (!spec.onsite_service) spec.confirmed_tbd_fee = 0;
  if (spec.read_depth.toLowerCase().endsWith('m')) {
    spec.million_reads_per_sample = Number(spec.read_depth.slice(0, -1).trim());
    spec.read_depth = '';
  } else if (spec.read_depth && (spec.project_type === 'WGS' || /x$/i.test(spec.read_depth))) {
    spec.read_depth = `${Number(spec.read_depth.replace(/x$/i, '').trim())}x`;
  }
  return { ...row, pricing_details: JSON.stringify({
    version: 1, spec, quantity: Number(row.quantity), unit_price: Number(row.unit_price), amount: Number(row.amount),
    components: [{ label: 'Recorded preparation', unit_price: Number(row.unit_price),
      quantity: Number(row.quantity), amount: Number(row.amount) }], ...overrides,
  }) };
}

const service_cases = [
  ['WGS', { read_depth: '45x' }, 'WGS - 45x', 'per sample'],
  ['Bulk RNAseq', { million_reads_per_sample: 50.25 }, 'Bulk RNAseq - 50.25M / sample', 'per sample'],
  ['Shotgun Meta', { read_depth: '37.5M' }, 'Shotgun Meta - 37.5M / sample', 'per sample'],
  ["10x 3' Transcriptome", { million_reads_per_sample: 331.5 }, "10x 3' Transcriptome - 331.5M / sample", 'per sample'],
  ["10x 5' Transcriptome", { million_reads_per_sample: 335 }, "10x 5' Transcriptome - 335M / sample", 'per sample'],
  ["10x 5' VDJ", { million_reads_per_sample: 42.5 }, "10x 5' VDJ - 42.5M / sample", 'per sample'],
  ["10x 5' Transcriptome + VDJ", { million_reads_per_sample: 375 }, "10x 5' Transcriptome + VDJ - 375M / sample", 'per sample'],
  ['Sequencing Only - 1M Reads', { million_reads_per_sample: 100 }, 'Sequencing Only - 100M / library', 'per library'],
  ['Sequencing Only - 10B Lane', {}, 'Sequencing Only - 10B / lane', 'per lane'],
  ['Sequencing Only - 25B Lane', {}, 'Sequencing Only - 25B / lane', 'per lane'],
  ['Microbial Genome Sequencing (<7M)', { sample_type: 'Sample for DNA Extraction' }, 'Microbial Genome (<7M) - DNA extraction', 'per sample'],
  ['Microbial Genome Sequencing (7-20M)', { sample_type: 'DNA' }, 'Microbial Genome (7-20M) - no DNA extraction', 'per sample'],
  ['Microbial Genome Sequencing (20-60M)', { sample_type: 'Sample for DNA Extraction' }, 'Microbial Genome (20-60M) - DNA extraction', 'per sample'],
  ['Custom Project', { description: 'Targeted assay development' }, 'Custom Project - Targeted assay development', ''],
];

const escaped = (value) => String(value).replace(/[&<>"']/g, (character) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[character]);

for (const [project_type, facts, expected, unit] of service_cases) {
  test(`${project_type}: compact actual title and recorded basis have Quote/Order parity`, () => {
    const row = freeze(with_pricing(item({ project_type, ...facts })));
    const before = JSON.stringify(row);
    const api = harness();
    const quote = api.build_html(freeze(document('NGS Quote', row)));
    const order = api.build_html(freeze(document('NGS Order', row, { details_snapshot_version: 1 })));
    assert.equal(order, quote);
    assert.equal(data_rows(quote)[0][1], escaped(expected));
    assert.ok(quote.includes(`Project Type: ${escaped(project_type)}</div>`));
    assert.match(quote, /<details><summary>Pricing details<\/summary>/);
    assert.match(quote, /Recorded preparation/);
    assert.match(quote, /<caption>Recorded pricing basis<\/caption>/);
    if (unit) assert.ok(quote.includes(`Price Unit: ${unit}`));
    assert.equal(data_rows(quote)[0][6], '$10.00');
    assert.equal(total(quote), '$20.00');
    assert.equal(JSON.stringify(row), before);
    assert.doesNotMatch(quote, /<style|style=|card|<button|<input/);
  });
}

test('missing service facts never become catalog defaults, including Sequencing Only 1M category', () => {
  const api = harness();
  for (const [project_type] of service_cases) {
    const row = item({ project_type, service: 'MISLEADING_30X_330M_SERVICE', sample_type: undefined });
    const html = api.build_html(document('NGS Order', row));
    assert.equal(data_rows(html)[0][4], '-');
    assert.doesNotMatch(html, /MISLEADING|330M|30x|DNA extraction/);
    if (project_type === 'Sequencing Only - 1M Reads') {
      assert.equal(data_rows(html)[0][1], 'Sequencing Only - reads not recorded');
      assert.match(html, /Project Type: Sequencing Only - 1M Reads/);
    }
    assert.match(html, /Pricing basis not recorded\./);
  }
});

test('fractional saved reads and explicit zero depth retain their real units', () => {
  for (const doctype of ['NGS Quote', 'NGS Order']) {
    for (const [project_type, facts, expected] of [
      ['Bulk RNAseq', { million_reads_per_sample: '0.25' }, '0.25M / sample'],
      ["10x 5' VDJ", { read_depth: '.5M' }, '.5M / sample'],
      ['Sequencing Only - 1M Reads', { read_depth: '0.125m' }, '0.125M / library'],
      ['Shotgun Meta', { read_depth: '0M' }, '0M / sample'],
      ['WGS', { read_depth: '0x' }, '0x'],
    ]) {
      const html = harness().build_html(document(doctype, item({ project_type, ...facts })));
      assert.equal(data_rows(html)[0][4], expected);
      assert.ok(data_rows(html)[0][1].endsWith(expected));
    }
  }
});

test('long Custom description is compact only in its title and remains fully accessible and escaped', () => {
  const description = 'Assay <script>alert(1)</script> ' + 'recorded detail '.repeat(30) + '\nFinal line';
  const html = harness().build_html(document('NGS Quote', item({ project_type: 'Custom Project', description })));
  const cells = data_rows(html)[0];
  assert.ok(cells[1].length < 160);
  assert.match(cells[1], /\.\.\.$/);
  assert.ok(html.includes(`Description: ${escaped(description).replace(/\n/g, '<br>')}`));
  assert.match(html, /Project Type: Custom Project/);
  assert.doesNotMatch(html, /<script/);
});

test('valid recorded components are displayed literally, not recomputed or balanced against catalog prices', () => {
  const row = with_pricing(item({ quantity: '3', unit_price: '5', amount: '15' }), { components: [
    { label: 'Historical preparation', unit_price: 2, quantity: 9, amount: 17.996 },
    { label: 'Historical adjustment', unit_price: -1.5, quantity: 2, amount: -3 },
    { label: 'Waived option', unit_price: 0, quantity: 3, amount: 0 },
  ] });
  const doc = freeze(document('NGS Order', row));
  const before = JSON.stringify(doc);
  const formatted = [];
  const html = harness((value) => { formatted.push(value); return usd(value); }).build_html(doc);
  assert.match(html, /Historical preparation/);
  assert.match(html, /Historical adjustment/);
  assert.match(html, /Waived option/);
  assert.match(html, /\$18\.00/);
  assert.match(html, /-\$3\.00/);
  assert.ok(formatted.includes(17.996), 'Format the stored amount, not its recomputed product of 18');
  assert.ok(!formatted.includes(18));
  assert.deepEqual(data_rows(html)[0].slice(6), ['$5.00', '$15.00']);
  assert.equal(total(html), '$15.00');
  assert.equal(JSON.stringify(doc), before);
});

test('snapshot numeric tolerances accept rounding but reject changed or missing line totals', () => {
  const api = harness();
  const original = item({ quantity: 1, unit_price: 10, amount: 10 });
  for (const field of ['quantity', 'unit_price', 'amount']) {
    const tolerance = field === 'quantity' ? 1e-6 : 0.005;
    for (const delta of [0, tolerance * 0.98, -tolerance * 0.98, tolerance]) {
      const row = with_pricing(original, { [field]: original[field] + delta });
      assert.match(api.build_html(document('NGS Quote', row)), /Recorded preparation/, `${field}: ${delta}`);
    }
    for (const value of [original[field] + tolerance * 1.02, original[field] - tolerance * 1.02,
      null, '', true, String(original[field])]) {
      const row = with_pricing(original, { [field]: value });
      const html = api.build_html(document('NGS Quote', row));
      assert.match(html, /Recorded pricing basis unavailable \/ pending confirmation/);
      assert.doesNotMatch(html, /Recorded preparation/);
      assert.equal(total(html), '$10.00');
    }
    for (const value of [undefined, null, '', 'bad', Infinity, NaN]) {
      const row = with_pricing(original);
      row[field] = value;
      const html = api.build_html(document('NGS Order', row));
      assert.doesNotMatch(html, /Recorded preparation/);
    }
  }
});

test('malformed, nonfinite, unsupported and unrecorded pricing JSON fails closed without losing row money', () => {
  const valid = with_pricing().pricing_details;
  for (const pricing_details of [
    '{', 'null', '[]', 'false', '10', '"text"', {}, [],
    valid.replace('"version":1', '"version":2'),
    valid.replace('"version":1', '"version":"1"'),
    valid.replace('"unit_price":10', '"unit_price":1e400'),
    valid.replace('"amount":20', '"amount":-1e400'),
    valid.replace('"quantity":2', '"quantity":1e400'),
    valid.replace('"version":1,', ''),
  ]) {
    const html = harness().build_html(document('NGS Order', item({ pricing_details })));
    assert.match(html, /Recorded pricing basis unavailable \/ pending confirmation/);
    assert.doesNotMatch(html, /Recorded preparation/);
    assert.deepEqual(data_rows(html)[0].slice(6), ['$10.00', '$20.00']);
    assert.equal(total(html), '$20.00');
  }
  for (const pricing_details of [undefined, null, '', ' ']) {
    const html = harness().build_html(document('NGS Quote', item({ pricing_details })));
    assert.match(html, /Pricing basis not recorded/);
    assert.doesNotMatch(html, /Recorded preparation|unavailable/);
  }
});

test('invalid component entries reject the entire breakdown, not just its corrupt charges', () => {
  const good = { label: 'Recorded preparation', unit_price: 10, quantity: 2, amount: 20 };
  for (const components of [
    null, {}, [], [null], [1], [[], good], [{ ...good, label: '' }], [{ ...good, label: {} }],
    [{ ...good, quantity: null }], [{ ...good, quantity: '2' }], [{ ...good, quantity: Infinity }],
    [{ ...good, unit_price: NaN }], [{ ...good, unit_price: '10' }], [{ ...good, amount: null }],
    [{ ...good, amount: Infinity }], [{ ...good, display: '<img src=x>' }],
    [{ ...good, display: 'TBD' }], [{ ...good, unit_price: null, amount: null }],
    [good, { label: 'Hidden corrupt charge', unit_price: 3, quantity: 1 }],
  ]) {
    const html = harness().build_html(document('NGS Quote', with_pricing(item(), { components })));
    assert.match(html, /Recorded pricing basis unavailable/);
    assert.doesNotMatch(html, /Recorded preparation|Hidden corrupt charge|<img/);
    assert.equal(total(html), '$20.00');
  }
});

test('foreign or edited saved specifications cannot authorize a same-price breakdown', () => {
  const original = item({
    species: 'Human', read_depth: '50M', million_reads_per_sample: 50, add_on_sequencing: 1,
    library_qc: 1, onsite_service: 1, confirmed_tbd_fee: 25,
    use_price_override: 1, override_unit_price: 10, description: 'Original assay',
  });
  const saved = with_pricing(original);
  for (const [field, changed] of Object.entries({
    project_type: 'WGS', sample_type: 'DNA', species: 'Mouse', read_depth: '100M',
    million_reads_per_sample: 100, data_analysis: 'No', add_on_sequencing: 0,
    library_qc: 0, onsite_service: 0, confirmed_tbd_fee: 0,
    use_price_override: 0, override_unit_price: 0, description: '<script>foreign spec</script>',
  })) {
    const html = harness().build_html(document('NGS Order', { ...saved, [field]: changed }));
    assert.match(html, /Recorded pricing basis unavailable/, field);
    assert.doesNotMatch(html, /Recorded preparation|<script/);
  }
  for (const spec of [null, [], {}, { project_type: 'WGS' }, { project_type: 'Bulk RNAseq', species: {} },
    { project_type: 'Bulk RNAseq', injected_field: '<img src=x>' }]) {
    const html = harness().build_html(document('NGS Quote', with_pricing(original, { spec })));
    assert.match(html, /Recorded pricing basis unavailable/);
    assert.doesNotMatch(html, /Recorded preparation|<img/);
  }
});

test('legacy quote specification fallback is disclosed but never makes a foreign pricing snapshot eligible', () => {
  const doc = fallback_document({ million_reads_per_sample: 0 });
  doc.items[0] = { ...doc.items[0], pricing_details: with_pricing({ ...doc.items[0],
    species: 'Legacy species', million_reads_per_sample: 50,
  }).pricing_details };
  const html = harness().build_html(freeze(doc));
  assert.match(html, /Bulk RNAseq - 50M \/ sample/);
  assert.match(html, /specification only; not pricing/);
  assert.match(html, /Recorded pricing basis unavailable/);
  assert.doesNotMatch(html, /Recorded preparation/);
});

test('recorded component labels and Custom HTML descriptions are always escaped plain text', () => {
  const payload = '<img src=x onerror="alert(1)"> & \' <script>alert(2)</script>';
  const row = with_pricing(item({ project_type: 'Custom Project', description: payload }), {
    components: [{ label: payload, unit_price: 10, quantity: 2, amount: 20 }],
  });
  for (const doctype of ['NGS Quote', 'NGS Order']) {
    const html = harness().build_html(freeze(document(doctype, row)));
    assert.match(html, /Recorded pricing basis/);
    assert.ok(html.includes(escaped(payload)));
    assert.doesNotMatch(html, /<img|<script|onerror="/);
  }
});

test('recorded pending charges remain TBD and label only known row amounts as subtotal on both forms', () => {
  const pending = { label: 'Unconfirmed on-site fee', unit_price: null, quantity: 2, amount: null, display: 'TBD' };
  for (const doctype of ['NGS Quote', 'NGS Order']) {
    const row = with_pricing(item(), { components: [
      { label: 'Known base', unit_price: 10, quantity: 2, amount: 20 }, pending,
    ] });
    const html = harness().build_html(document(doctype, row));
    assert.match(html, /Unconfirmed on-site fee/);
    assert.equal([...html.matchAll(/>TBD<\/td>/g)].length, 2);
    assert.match(html, />Priced Subtotal<\/th>/);
    assert.equal(total(html), '$20.00');
    const custom = with_pricing(item({ project_type: 'Custom Project', unit_price: 0, amount: 0,
      use_price_override: 1, override_unit_price: 0 }), { components: [pending] });
    const mixed = harness().build_html(document(doctype, null, { status: 'Pending', items: [item(), custom] }));
    assert.deepEqual(data_rows(mixed).find((cells) => cells[1] === 'Custom Project').slice(6), ['TBD', 'TBD']);
    assert.equal(total(mixed), '$20.00');
    assert.match(mixed, />Priced Subtotal<\/th>/);
    const unknown = harness().build_html(document(doctype, custom, { status: 'Pending' }));
    assert.equal(total(unknown), '-');
    assert.match(unknown, />Priced Subtotal<\/th>/);
  }
});

test('pending Custom zero never renders recorded numeric zero as a confirmed breakdown', () => {
  const row = with_pricing(item({ project_type: 'Custom Project', quantity: 0, unit_price: 0, amount: 0,
    use_price_override: 1, override_unit_price: 0 }), {
    components: [{ label: 'Premature confirmation', quantity: 0, unit_price: 0, amount: 0 }],
  });
  const html = harness().build_html(document('NGS Order', row, { status: 'Pending' }));
  assert.deepEqual(data_rows(html)[0].slice(6), ['TBD', 'TBD']);
  assert.match(html, /Recorded pricing basis unavailable \/ pending confirmation/);
  assert.doesNotMatch(html, /Premature confirmation/);
});

test('missing amounts allow a known-only subtotal without fabricating a zero for unknown rows', () => {
  for (const doctype of ['NGS Quote', 'NGS Order']) {
    const html = harness().build_html(document(doctype, null, { items: [item(), item({ amount: undefined })] }));
    assert.equal(total(html), '$20.00');
    assert.match(html, />Priced Subtotal<\/th>/);
    assert.equal(data_rows(html).filter((cells) => cells.length === 8)[1][7], '-');
  }
});

test('zero-money recorded snapshots remain valid with positive quantities', () => {
  const row = with_pricing(item({ quantity: 2, unit_price: 0, amount: 0 }), {
    components: [{ label: 'Recorded waiver', unit_price: 0, quantity: 2, amount: 0 }],
  });
  const html = harness().build_html(freeze(document('NGS Quote', row)));
  assert.match(html, /Recorded waiver/);
  assert.doesNotMatch(html, /TBD|unavailable|not recorded/);
  assert.deepEqual(data_rows(html)[0].slice(6), ['$0.00', '$0.00']);
  assert.equal(total(html), '$0.00');
});

test('pricing fingerprint matches complete backend-normalized text, flags, reads and depth', () => {
  const api = harness();
  const cases = [
    [item({ species: ' Human ', read_depth: '50M', million_reads_per_sample: 0 }), {
      species: 'human', read_depth: '', million_reads_per_sample: 50,
    }],
    [item({ read_depth: ' .25 m ', million_reads_per_sample: 0 }), {
      read_depth: '', million_reads_per_sample: '.25',
    }],
    [item({ project_type: 'WGS', read_depth: '45x', species: 'HUMAN' }), {
      read_depth: '045.0 X', species: 'human',
    }],
    [item({ onsite_service: 1, use_price_override: 1, override_unit_price: 10,
      confirmed_tbd_fee: 0, library_qc: 0 }), {
      onsite_service: true, use_price_override: '1', override_unit_price: '10',
      confirmed_tbd_fee: '', library_qc: false,
    }],
    [item({ sample_type: ' Total RNA ', data_analysis: ' Standard Analysis ', onsite_address: '  Lab\nRoom 1  ' }), {
      sample_type: 'Total RNA', data_analysis: 'Standard Analysis', onsite_address: 'Lab\nRoom 1',
    }],
  ];
  for (const [original, changes] of cases) {
    const row = { ...with_pricing(original), ...changes };
    for (const doctype of ['NGS Quote', 'NGS Order']) {
      const html = api.build_html(freeze(document(doctype, row)));
      assert.match(html, /Recorded preparation/);
      assert.doesNotMatch(html, /unavailable|not recorded/);
    }
  }
});

test('incomplete and extra snapshot fingerprint fields fail closed, as do invalid live facts', () => {
  const row = with_pricing();
  const snapshot = JSON.parse(row.pricing_details);
  for (const field of Object.keys(snapshot.spec)) {
    const spec = { ...snapshot.spec };
    delete spec[field];
    const html = harness().build_html(document('NGS Quote', with_pricing(item(), { spec })));
    assert.match(html, /Recorded pricing basis unavailable/, field);
    assert.doesNotMatch(html, /Recorded preparation/);
  }
  const extra = { ...snapshot.spec, foreign_label: '<img src=x>' };
  assert.doesNotMatch(harness().build_html(document('NGS Order', with_pricing(item(), { spec: extra }))),
    /Recorded preparation|<img/);
  for (const changes of [
    { read_depth: '50M', million_reads_per_sample: 100 },
    { million_reads_per_sample: -1 }, { million_reads_per_sample: Infinity },
    { million_reads_per_sample: true }, { onsite_service: 1, confirmed_tbd_fee: -1 },
    { library_qc: 'yes' }, { onsite_service: 3 }, { species: 42 },
    { override_unit_price: [] }, { onsite_address: {} },
  ]) {
    const html = harness().build_html(document('NGS Order', { ...row, ...changes }));
    assert.match(html, /Recorded pricing basis unavailable/);
    assert.doesNotMatch(html, /Recorded preparation/);
  }
  for (const changes of [{ library_qc: false }, { library_qc: '0' },
    { species: null }, { million_reads_per_sample: '0' }]) {
    const html = harness().build_html(document('NGS Order', with_pricing(item(), {
      spec: { ...snapshot.spec, ...changes },
    })));
    assert.match(html, /Recorded pricing basis unavailable/);
    assert.doesNotMatch(html, /Recorded preparation/);
  }
});

test('quantity edits never scale a saved breakdown and large totals cannot widen the money tolerance', () => {
  for (const doctype of ['NGS Quote', 'NGS Order']) {
    const original = with_pricing();
    const edited = { ...original, quantity: 3, amount: 30 };
    const html = harness().build_html(document(doctype, edited));
    assert.match(html, /Recorded pricing basis unavailable/);
    assert.doesNotMatch(html, /Recorded preparation/);
    assert.deepEqual(data_rows(html)[0].slice(6), ['$10.00', '$30.00']);
    assert.equal(total(html), '$30.00');
    const large = with_pricing(item({ unit_price: 1e16, amount: 2e16 }), { unit_price: 1e16 + 2 });
    const invalid = harness().build_html(document(doctype, large));
    assert.match(invalid, /Recorded pricing basis unavailable/);
    assert.doesNotMatch(invalid, /Recorded preparation/);
  }
});

test('disabled override and on-site fees ignore finite legacy residuals on both forms without mutation', () => {
  for (const doctype of ['NGS Quote', 'NGS Order']) {
    for (const [flag, field] of [['use_price_override', 'override_unit_price'], ['onsite_service', 'confirmed_tbd_fee']]) {
      const original = with_pricing(item({ [flag]: 0, [field]: 99 }));
      assert.equal(JSON.parse(original.pricing_details).spec[field], 0);
      for (const residual of [0, 250, -1, -999.25, '123.45', '-0.25', null, '', undefined]) {
        const row = freeze({ ...original, [field]: residual });
        const before = JSON.stringify(row);
        const html = harness().build_html(freeze(document(doctype, row)));
        assert.match(html, /Recorded preparation/, `${doctype}: ${field}=${residual}`);
        assert.doesNotMatch(html, /unavailable|not recorded/);
        assert.deepEqual(data_rows(html)[0].slice(6), ['$10.00', '$20.00']);
        assert.equal(JSON.stringify(row), before);
      }
      const snapshot = JSON.parse(original.pricing_details);
      snapshot.spec[field] = 432.1;
      const html = harness().build_html(document(doctype, { ...original, [field]: -99,
        pricing_details: JSON.stringify(snapshot) }));
      assert.match(html, /Recorded preparation/, 'Saved nonnegative disabled residuals normalize too');
    }
  }
});

test('disabled numeric controls still reject malformed and nonfinite values, including saved negatives', () => {
  for (const [flag, field] of [['use_price_override', 'override_unit_price'], ['onsite_service', 'confirmed_tbd_fee']]) {
    const row = with_pricing(item({ [flag]: 0, [field]: 0 }));
    for (const value of ['bad', ' ', NaN, Infinity, -Infinity, true, false, [], {}]) {
      const html = harness().build_html(document('NGS Order', { ...row, [field]: value }));
      assert.match(html, /Recorded pricing basis unavailable/, `${field}=${String(value)}`);
      assert.doesNotMatch(html, /Recorded preparation/);
    }
    for (const value of [-1, null, '0', true, Infinity]) {
      const saved = JSON.parse(row.pricing_details);
      saved.spec[field] = value;
      const html = harness().build_html(document('NGS Quote', { ...row, pricing_details: JSON.stringify(saved) }));
      assert.match(html, /Recorded pricing basis unavailable/);
      assert.doesNotMatch(html, /Recorded preparation/);
    }
  }
});

test('enabled controls retain their values and cannot use disabled-value normalization', () => {
  for (const [flag, field] of [['use_price_override', 'override_unit_price'], ['onsite_service', 'confirmed_tbd_fee']]) {
    const row = with_pricing(item({ [flag]: 1, [field]: 25 }));
    for (const changes of [{ [field]: 0 }, { [field]: 25.01 }, { [field]: -1 }, { [flag]: 0 }]) {
      for (const doctype of ['NGS Quote', 'NGS Order']) {
        const html = harness().build_html(document(doctype, { ...row, ...changes }));
        assert.match(html, /Recorded pricing basis unavailable/);
        assert.doesNotMatch(html, /Recorded preparation/);
      }
    }
  }
});

test('numeric fingerprints tolerate one micro-unit of DB precision, never the wider price tolerance', () => {
  for (const field of ['million_reads_per_sample', 'confirmed_tbd_fee', 'override_unit_price']) {
    const row = with_pricing(item({ onsite_service: 1, use_price_override: 1, [field]: 25 }));
    for (const doctype of ['NGS Quote', 'NGS Order']) {
      for (const delta of [0, 0.0000004, -0.0000009, 0.000001, -0.000001]) {
        const html = harness().build_html(document(doctype, { ...row, [field]: String(25 + delta) }));
        assert.match(html, /Recorded preparation/, `${doctype}: ${field}+${delta}`);
      }
      for (const delta of [0.0000011, -0.0000011, 0.001, -0.001]) {
        const html = harness().build_html(document(doctype, { ...row, [field]: 25 + delta }));
        assert.match(html, /Recorded pricing basis unavailable/, `${doctype}: ${field}+${delta}`);
        assert.doesNotMatch(html, /Recorded preparation/);
      }
    }
  }
});

test('legal JSON and database round trips preserve eligible recorded pricing and source values', () => {
  const original = item({
    project_type: 'Sequencing Only - 1M Reads', sample_type: 'Library', species: ' Human ',
    read_depth: '100.1234564M', million_reads_per_sample: 100.1234564,
    onsite_service: true, confirmed_tbd_fee: 20.1234564,
    use_price_override: true, override_unit_price: 10.1234564,
    description: '  Library sequencing\nClient requested depth  ',
    unit_price: 10.1234564, amount: 20.2469128,
  });
  const saved = with_pricing(original, { components: [{
    label: 'DB recorded basis', quantity: 2, unit_price: 10.1234564, amount: 20.2469128,
  }] });
  const dbRow = JSON.parse(JSON.stringify(saved));
  Object.assign(dbRow, {
    species: 'human', onsite_service: 1, use_price_override: '1',
    read_depth: '', million_reads_per_sample: 100.123456,
    confirmed_tbd_fee: '20.123456', override_unit_price: '10.123456',
    unit_price: '10.123456', amount: '20.246913',
    description: 'Library sequencing\nClient requested depth',
  });
  const before = JSON.stringify(dbRow);
  const api = harness();
  const quote = api.build_html(freeze(document('NGS Quote', freeze(dbRow))));
  const order = api.build_html(freeze(document('NGS Order', dbRow)));
  assert.equal(quote, order);
  assert.match(quote, /DB recorded basis/);
  assert.doesNotMatch(quote, /unavailable|not recorded/);
  assert.match(quote, /100\.123456M \/ library/);
  assert.equal(total(quote), '$20.25');
  assert.equal(JSON.stringify(dbRow), before);
});

test('redundant M-depth and saved reads each stay within spec precision independently', () => {
  const row = with_pricing(item({ read_depth: '50M', million_reads_per_sample: 50 }));
  for (const doctype of ['NGS Quote', 'NGS Order']) {
    for (const changes of [
      { read_depth: '50.0000007m', million_reads_per_sample: 50.0000009 },
      { read_depth: '49.9999993M', million_reads_per_sample: '49.9999991' },
      { read_depth: '50M', million_reads_per_sample: 0 },
      { read_depth: '', million_reads_per_sample: '50.0000004' },
    ]) {
      const html = harness().build_html(document(doctype, { ...row, ...changes }));
      assert.match(html, /Recorded preparation/);
    }
    for (const changes of [
      { read_depth: '50.0000007M', million_reads_per_sample: 50.0000014 },
      { read_depth: '49.9999993M', million_reads_per_sample: 49.9999986 },
      { read_depth: '50.0000014M', million_reads_per_sample: 50.0000007 },
      { read_depth: '50.0000009M', million_reads_per_sample: 49.9999991 },
      { read_depth: '50.0000011M', million_reads_per_sample: 0 },
      { read_depth: '50M', million_reads_per_sample: 50.0000011 },
    ]) {
      const html = harness().build_html(document(doctype, { ...row, ...changes }));
      assert.match(html, /Recorded pricing basis unavailable/);
      assert.doesNotMatch(html, /Recorded preparation/);
    }
  }
});

test('Custom description is a required exact text fingerprint after trimming, with escaped display', () => {
  const row = with_pricing(item({ project_type: 'Custom Project', description: '  Assay <img src=x>\nPanel A  ' }));
  const saved = JSON.parse(row.pricing_details);
  assert.equal(saved.spec.description, 'Assay <img src=x>\nPanel A');
  for (const doctype of ['NGS Quote', 'NGS Order']) {
    const html = harness().build_html(document(doctype, { ...row, description: 'Assay <img src=x>\nPanel A' }));
    assert.match(html, /Recorded preparation/);
    assert.doesNotMatch(html, /<img/);
    for (const description of ['Assay <img src=x>\nPanel B', '', undefined, null]) {
      const changed = harness().build_html(document(doctype, { ...row, description }));
      assert.match(changed, /Recorded pricing basis unavailable/);
      assert.doesNotMatch(changed, /Recorded preparation/);
    }
  }
  delete saved.spec.description;
  const missing = harness().build_html(document('NGS Order', { ...row, pricing_details: JSON.stringify(saved) }));
  assert.match(missing, /Recorded pricing basis unavailable/);
  assert.doesNotMatch(missing, /Recorded preparation/);
});

test('free snapshot quantity 1 to 1.004 is ineligible even though all recorded prices stay zero', () => {
  const row = with_pricing(item({ quantity: 1, unit_price: 0, amount: 0 }));
  for (const doctype of ['NGS Quote', 'NGS Order']) {
    for (const quantity of [1.004, 0.996, 1.0000011, 0.9999989]) {
      const doc = freeze(document(doctype, { ...row, quantity }));
      const before = JSON.stringify(doc);
      const html = harness().build_html(doc);
      assert.match(html, /Recorded pricing basis unavailable/);
      assert.doesNotMatch(html, /Recorded preparation/);
      assert.deepEqual(data_rows(html)[0].slice(6), ['$0.00', '$0.00']);
      assert.equal(total(html), '$0.00');
      assert.equal(JSON.stringify(doc), before);
    }
    for (const quantity of [1, 1.0000009, 0.9999991, 1.000001]) {
      const html = harness().build_html(document(doctype, { ...row, quantity }));
      assert.match(html, /Recorded preparation/);
      assert.doesNotMatch(html, /unavailable/);
    }
  }
});

test('nonpositive component quantities are rejected for priced, discounted, free and TBD charges', () => {
  const known = { label: 'Known preparation', unit_price: 10, quantity: 2, amount: 20 };
  const cases = [
    [{ label: 'Double negative quantity', unit_price: -10, quantity: -2, amount: 20 }],
    [{ label: 'Negative preparation', unit_price: 22, quantity: 1, amount: 22 },
      { label: 'Negative quantity discount', unit_price: 1, quantity: -2, amount: -2 }],
    [known, { label: 'Zero quantity waiver', unit_price: 0, quantity: 0, amount: 0 }],
    [known, { label: 'Negative quantity waiver', unit_price: 0, quantity: -2, amount: 0 }],
    [known, { label: 'Zero quantity pending', unit_price: null, quantity: 0, amount: null, display: 'TBD' }],
    [known, { label: 'Negative quantity pending', unit_price: null, quantity: -2, amount: null, display: 'TBD' }],
  ];
  for (const doctype of ['NGS Quote', 'NGS Order']) {
    for (const components of cases) {
      const html = harness().build_html(freeze(document(doctype, with_pricing(item(), { components }))));
      assert.match(html, /Recorded pricing basis unavailable/);
      assert.doesNotMatch(html, /Recorded pricing basis<\/caption>/);
      for (const component of components) assert.ok(!html.includes(component.label));
      assert.deepEqual(data_rows(html)[0].slice(6), ['$10.00', '$20.00']);
      assert.equal(total(html), '$20.00');
    }
  }
});

test('component multiplication must match saved amount even if its sum and line prices agree', () => {
  for (const doctype of ['NGS Quote', 'NGS Order']) {
    for (const [amount, rate, quantity] of [[999, 10, 2], [19.994, 10, 2], [20, 1e308, 2]]) {
      const row = with_pricing(item({ quantity: 2, unit_price: amount / 2, amount }), {
        components: [{ label: 'Invalid multiplication', unit_price: rate, quantity, amount }],
      });
      const html = harness().build_html(document(doctype, row));
      assert.match(html, /Recorded pricing basis unavailable/);
      assert.doesNotMatch(html, /Invalid multiplication/);
      assert.equal(total(html), usd(amount));
    }
  }
});

test('known component sum must match the recorded line amount, excluding rather than pricing TBD rows', () => {
  const known = { label: 'Known preparation', unit_price: 10, quantity: 2, amount: 20 };
  const pending = { label: 'Pending location fee', unit_price: null, quantity: 2, amount: null, display: 'TBD' };
  for (const doctype of ['NGS Quote', 'NGS Order']) {
    for (const [amount, components, valid] of [
      [20, [known, pending], true], [20.006, [known, pending], false],
      [20.02, [known], false], [0, [pending], true], [1, [pending], false],
    ]) {
      const row = with_pricing(item({ unit_price: amount / 2, amount }), { components });
      const html = harness().build_html(document(doctype, row));
      if (valid) {
        assert.match(html, /<caption>Recorded pricing basis<\/caption>/);
        assert.match(html, /Pending location fee/);
        assert.match(html, />Priced Subtotal<\/th>/);
      } else {
        assert.match(html, /Recorded pricing basis unavailable/);
        assert.doesNotMatch(html, /Known preparation|Pending location fee/);
      }
      assert.equal(total(html), usd(amount));
    }
  }
});

test('component sum must independently match live amount without stacking snapshot rounding tolerances', () => {
  const row = with_pricing(item({ unit_price: 10.002, amount: 20.004 }), {
    components: [{ label: 'Drifting rounded charge', unit_price: 10, quantity: 2, amount: 20 }],
  });
  for (const doctype of ['NGS Quote', 'NGS Order']) {
    const original = harness().build_html(document(doctype, row));
    assert.match(original, /Drifting rounded charge/);
    const changed = harness().build_html(document(doctype, { ...row, unit_price: 10.004, amount: 20.008 }));
    assert.match(changed, /Recorded pricing basis unavailable/);
    assert.doesNotMatch(changed, /Drifting rounded charge/);
    assert.equal(total(changed), '$20.01');
  }
});

test('component products and known sums accept DB rounding only within 0.005, keeping literal amounts', () => {
  for (const doctype of ['NGS Quote', 'NGS Order']) {
    for (const delta of [0.004, -0.004, 0.005, -0.005]) {
      for (const target of ['product', 'sum']) {
        const amount = 20 + delta;
        const componentAmount = target === 'product' ? amount : 20;
        const row = with_pricing(item({ unit_price: amount / 2, amount }), {
          components: [{ label: 'Rounded recorded charge', unit_price: 10, quantity: 2, amount: componentAmount }],
        });
        const formatted = [];
        const doc = freeze(document(doctype, row));
        const before = JSON.stringify(doc);
        const html = harness((value) => { formatted.push(value); return usd(value); }).build_html(doc);
        assert.match(html, /Rounded recorded charge/);
        assert.doesNotMatch(html, /unavailable/);
        assert.ok(formatted.includes(componentAmount));
        assert.equal(total(html), usd(amount));
        assert.equal(JSON.stringify(doc), before);
      }
    }
  }
});

test('positive-quantity negative-rate discounts remain eligible, including cancellation in the known sum', () => {
  const pending = { label: 'Pending fee', unit_price: null, quantity: 2, amount: null, display: 'TBD' };
  for (const [amount, components] of [
    [20, [
      { label: 'Preparation', unit_price: 12.5, quantity: 2, amount: 25 },
      { label: 'Recorded discount', unit_price: -2.5, quantity: 2, amount: -5 }, pending,
    ]],
    [1, [
      { label: 'Large preparation', unit_price: 1e16, quantity: 1, amount: 1e16 },
      { label: 'Small preparation', unit_price: 1, quantity: 1, amount: 1 },
      { label: 'Recorded discount', unit_price: -1e16, quantity: 1, amount: -1e16 },
    ]],
  ]) {
    for (const doctype of ['NGS Quote', 'NGS Order']) {
      const doc = freeze(document(doctype, with_pricing(item({ quantity: 1, unit_price: amount, amount }), { components })));
      const before = JSON.stringify(doc);
      const html = harness().build_html(doc);
      assert.match(html, /Recorded discount/);
      assert.doesNotMatch(html, /unavailable/);
      assert.equal(total(html), usd(amount));
      assert.equal(JSON.stringify(doc), before);
    }
  }
});

test('invalid line quantities and inconsistent line multiplication cannot qualify a saved breakdown', () => {
  for (const doctype of ['NGS Quote', 'NGS Order']) {
    for (const row of [
      with_pricing(item({ quantity: 0, unit_price: 0, amount: 0 }), {
        components: [{ label: 'Invalid line charge', unit_price: 0, quantity: 1, amount: 0 }],
      }),
      with_pricing(item({ quantity: -2, unit_price: 0, amount: 0 }), {
        components: [{ label: 'Invalid line charge', unit_price: 0, quantity: 1, amount: 0 }],
      }),
      with_pricing(item({ quantity: 2, unit_price: 10, amount: 999 }), {
        components: [{ label: 'Invalid line charge', unit_price: 499.5, quantity: 2, amount: 999 }],
      }),
      with_pricing(item({ quantity: 2, unit_price: -10, amount: -20 }), {
        components: [{ label: 'Invalid line charge', unit_price: -10, quantity: 2, amount: -20 }],
      }),
    ]) {
      const html = harness().build_html(document(doctype, row));
      assert.match(html, /Recorded pricing basis unavailable/);
      assert.doesNotMatch(html, /Invalid line charge/);
      assert.equal(total(html), usd(row.amount));
    }
  }
});
