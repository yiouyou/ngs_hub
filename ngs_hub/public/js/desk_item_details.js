// Copyright (c) 2026, sz and contributors
// For license information, please see license.txt

(() => {
  const fallback_fields = [
    'species', 'million_reads_per_sample', 'read_depth', 'confirmed_tbd_fee',
    'use_price_override', 'override_unit_price', 'add_on_sequencing',
  ];
  const pricing_text_fields = [
    'project_type', 'service', 'sample_type', 'species', 'read_depth', 'data_analysis',
    'onsite_location', 'onsite_address', 'description',
  ];
  const pricing_flag_fields = [
    'onsite_service', 'tissue_dissociation', 'nuclei_extraction', 'library_qc',
    'add_on_sequencing', 'use_price_override',
  ];
  const pricing_number_fields = ['million_reads_per_sample', 'confirmed_tbd_fee', 'override_unit_price'];

  function present(value) {
    return value != null && String(value).trim() !== '';
  }

  function escape(value) {
    return String(value ?? '').replace(/[&<>"']/g, (character) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[character]);
  }

  function text(value) {
    return present(value) ? escape(value) : '-';
  }

  function lines(value) {
    return escape(value).replace(/\r?\n/g, '<br>');
  }

  function number(value) {
    if (!present(value) || !['number', 'string'].includes(typeof value)) return null;
    const result = Number(value);
    return Number.isFinite(result) ? result : null;
  }

  function checked(value) {
    return value === true || value === 1 || value === '1';
  }

  function money(value) {
    const amount = number(value);
    if (amount === null) return '-';
    if (typeof frappe.format === 'function') {
      return escape(frappe.format(amount, { fieldtype: 'Currency', options: 'USD' }, { only_value: true }));
    }
    if (typeof format_currency === 'function') return escape(format_currency(amount, 'USD'));
    return escape(new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(amount));
  }

  function details(doc, raw) {
    const item = { ...raw };
    let source = '';
    const fallbacks = doc.__onload?.item_detail_fallbacks;
    const legacy = !present(raw.details_snapshot_version) || number(raw.details_snapshot_version) === 0;
    const fallback = doc.doctype === 'NGS Order' && legacy &&
      present(raw.name) && fallbacks && Object.hasOwn(fallbacks, raw.name) ? fallbacks[raw.name] : null;
    if (!fallback || !present(raw.quote_item) || !present(raw.project_type) ||
        fallback.quote_item !== raw.quote_item || fallback.project_type !== raw.project_type) {
      return { item, source };
    }

    // Saved units/reads take precedence over legacy quote facts, including each other.
    const explicit_depth = /\d\s*(?:m|x)\s*$/i.test(String(raw.read_depth ?? ''));
    for (const field of fallback_fields) {
      const missing = !present(raw[field]) ||
        (field === 'million_reads_per_sample' && number(raw[field]) === 0);
      if (!missing || !present(fallback[field])) continue;
      if (field === 'million_reads_per_sample' &&
          (explicit_depth || !(number(fallback[field]) > 0))) continue;
      if (field === 'read_depth' && number(raw.million_reads_per_sample) > 0) continue;
      item[field] = fallback[field];
      source = fallback.source || 'Linked quote (legacy order)';
    }
    return { item, source };
  }

  function coverage(item) {
    const unit = item.project_type === 'Sequencing Only - 1M Reads' ? ' / library' : ' / sample';
    if (present(item.read_depth)) {
      const depth = String(item.read_depth).trim();
      const reads = depth.match(/^(\d+(?:\.\d+)?|\.\d+)\s*m$/i);
      return reads ? `${reads[1]}M${unit}` : depth;
    }
    const reads = number(item.million_reads_per_sample);
    return reads > 0 ? `${reads}M${unit}` : '-';
  }

  function price_unit(item) {
    if (item.project_type === 'Sequencing Only - 1M Reads') return 'per library';
    if (/^Sequencing Only - (10B|25B) Lane$/.test(item.project_type)) return 'per lane';
    if (['Bulk RNAseq', 'WGS', 'Shotgun Meta', 'DNA Small Fragment Library Prep & Sequencing'].includes(item.project_type) ||
        /^10x /.test(item.project_type) || /^Microbial Genome Sequencing /.test(item.project_type)) {
      return 'per sample';
    }
    return '';
  }

  function title(item) {
    const type = item.project_type;
    const depth = coverage(item);
    if (type === 'Sequencing Only - 1M Reads') {
      return depth === '-' ? 'Sequencing Only - reads not recorded' : `Sequencing Only - ${depth}`;
    }
    if (type === 'DNA Small Fragment Library Prep & Sequencing') {
      return depth === '-' ? `${type} - reads not recorded` : `${type} - ${depth}`;
    }
    if (['WGS', 'Bulk RNAseq', 'Shotgun Meta'].includes(type) || /^10x /.test(type)) {
      return depth === '-' ? type : `${type} - ${depth}`;
    }
    const lane = String(type).match(/^Sequencing Only - (10B|25B) Lane$/);
    if (lane) return `Sequencing Only - ${lane[1]} / lane`;
    const microbial = String(type).match(/^Microbial Genome Sequencing \((<7M|7-20M|20-60M)\)$/);
    if (microbial) {
      const extraction = item.sample_type === 'Sample for DNA Extraction' ? ' - DNA extraction' :
        (item.sample_type === 'DNA' ? ' - no DNA extraction' : '');
      return `Microbial Genome (${microbial[1]})${extraction}`;
    }
    if (type === 'Custom Project' && present(item.description)) {
      const description = String(item.description).trim().replace(/\s+/g, ' ');
      return `Custom Project - ${description.length > 96 ? `${description.slice(0, 93)}...` : description}`;
    }
    return present(type) ? type : item.service;
  }

  function record(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
  }

  function finite(value) {
    return typeof value === 'number' && Number.isFinite(value);
  }

  function matches_number(saved, live, tolerance = 0.005) {
    const current = number(live);
    return finite(saved) && current !== null && Math.abs(saved - current) <=
      tolerance + 1e-12;
  }

  function pricing_spec(item, saved = false) {
    const fields = [...pricing_text_fields, ...pricing_flag_fields, ...pricing_number_fields];
    if (!record(item) || (saved && (Object.keys(item).length !== fields.length ||
        !fields.every((field) => Object.hasOwn(item, field))))) return null;
    const spec = {};
    for (const field of pricing_text_fields) {
      const value = item[field];
      if ((saved || value != null) && typeof value !== 'string') return null;
      spec[field] = (value ?? '').trim();
    }
    spec.species = spec.species.toLowerCase();
    for (const field of pricing_flag_fields) {
      const value = !saved && (item[field] == null || item[field] === '') ? 0 : item[field];
      if (!(saved ? [0, 1] : [0, 1, '0', '1', true, false]).includes(value)) return null;
      spec[field] = checked(value) ? 1 : 0;
    }
    for (const field of pricing_number_fields) {
      const value = !saved && (item[field] == null || item[field] === '') ? 0 : item[field];
      if (saved && !finite(value)) return null;
      const numeric = number(value);
      if (numeric === null || (saved && numeric < 0)) return null;
      const ignored = (field === 'override_unit_price' && !spec.use_price_override) ||
        (field === 'confirmed_tbd_fee' && !spec.onsite_service);
      spec[field] = ignored ? 0 : numeric;
      if (spec[field] < 0) return null;
    }
    // Same canonical fingerprint as pricing_snapshot._spec, without fetching a price.
    const depth = spec.read_depth.toLowerCase();
    if (depth.endsWith('m')) {
      const reads = number(depth.slice(0, -1).trim());
      if (!(reads > 0) || (spec.million_reads_per_sample !== 0 &&
          !matches_number(reads, spec.million_reads_per_sample, 1e-6))) return null;
      spec.million_reads_per_sample = reads;
      spec.read_depth = '';
    } else if (depth && (depth.endsWith('x') || spec.project_type === 'WGS')) {
      const coverage = number(depth.replace(/x$/, '').trim());
      if (!(coverage > 0)) return null;
      spec.read_depth = `${coverage}x`;
    }
    return spec;
  }

  function matches_spec(saved, item) {
    const current_spec = pricing_spec(item);
    const saved_spec = pricing_spec(saved, true);
    if (current_spec === null || saved_spec === null || !present(saved_spec.project_type)) return false;
    // Check the stored read count independently; M-depth normalization must not hide drift.
    const reads = number(item.million_reads_per_sample);
    if (reads !== null && reads !== 0 && !matches_number(saved_spec.million_reads_per_sample, reads, 1e-6)) {
      return false;
    }
    return [...pricing_text_fields, ...pricing_flag_fields].every((field) => saved_spec[field] === current_spec[field]) &&
      pricing_number_fields.every((field) => matches_number(saved_spec[field], current_spec[field], 1e-6));
  }

  function valid_components(components, saved_amount, current_amount) {
    if (!Array.isArray(components) || !components.length || !components.every((component) =>
      record(component) && typeof component.label === 'string' && present(component.label) &&
      finite(component.quantity) && component.quantity > 0 &&
      (component.display === 'TBD' ? component.unit_price === null && component.amount === null :
        component.display === undefined && finite(component.unit_price) && finite(component.amount) &&
        matches_number(component.unit_price * component.quantity, component.amount)))) return false;

    // Compensate cancellation from saved discounts when checking the known-only sum.
    let total = 0;
    let correction = 0;
    for (const component of components) {
      if (component.amount === null) continue;
      const next = total + component.amount;
      correction += Math.abs(total) >= Math.abs(component.amount) ?
        (total - next) + component.amount : (component.amount - next) + total;
      total = next;
    }
    return matches_number(total + correction, saved_amount) && matches_number(total + correction, current_amount);
  }

  function pricing(item) {
    if (item.pricing_details == null ||
        (typeof item.pricing_details === 'string' && !item.pricing_details.trim())) {
      return { state: 'missing', components: [] };
    }
    let saved;
    try {
      saved = typeof item.pricing_details === 'string' ? JSON.parse(item.pricing_details) : null;
    } catch {
      return { state: 'unavailable', components: [] };
    }
    // Validate against raw row facts, never the legacy specification fallback or a catalog.
    if (!record(saved) || saved.version !== 1 || !matches_spec(saved.spec, item) ||
        !matches_number(saved.quantity, item.quantity, 1e-6) || saved.quantity <= 0 || !(number(item.quantity) > 0) ||
        !['unit_price', 'amount'].every((field) => matches_number(saved[field], item[field]) &&
          saved[field] >= 0 && number(item[field]) >= 0) ||
        !matches_number(saved.unit_price * saved.quantity, saved.amount) ||
        !matches_number(number(item.unit_price) * number(item.quantity), item.amount) ||
        !valid_components(saved.components, saved.amount, item.amount)) {
      return { state: 'unavailable', components: [] };
    }
    return { state: 'recorded', components: saved.components };
  }

  function pricing_html(basis, item, manual) {
    const unit = price_unit(item);
    let body = unit ? `<div>Price Unit: ${escape(unit)}</div>` : '';
    if (basis.state !== 'recorded' || (manual && basis.components.some((component) => component.display !== 'TBD'))) {
      body += `<div class="text-muted">${basis.state === 'missing' ? 'Pricing basis not recorded.' :
        'Recorded pricing basis unavailable / pending confirmation.'}</div>`;
    } else {
      body += '<div class="table-responsive"><table class="table table-bordered">' +
        '<caption>Recorded pricing basis</caption><thead><tr>' +
        ['Component', 'Unit Price', 'Quantity', 'Amount'].map((label) => `<th scope="col">${label}</th>`).join('') +
        '</tr></thead><tbody>' + basis.components.map((component) =>
          `<tr><th scope="row" class="text-break">${escape(component.label)}</th>` +
          `<td class="text-right text-nowrap">${component.display === 'TBD' ? 'TBD' : money(component.unit_price)}</td>` +
          `<td class="text-right text-nowrap">${escape(component.quantity)}</td>` +
          `<td class="text-right text-nowrap">${component.display === 'TBD' ? 'TBD' : money(component.amount)}</td></tr>`
        ).join('') + '</tbody></table></div>';
    }
    return `<details><summary>Pricing details</summary>${body}</details>`;
  }

  function requires_manual(doc, item) {
    return item.project_type === 'Custom Project' && doc.status === 'Pending' &&
      number(item.unit_price) === 0 && number(item.amount) === 0;
  }

  function notes(item, source, product_title) {
    const output = [];
    if (present(item.project_type) && product_title !== item.project_type) {
      output.push(`<div>Project Type: ${text(item.project_type)}</div>`);
    }
    if (present(item.read_depth)) output.push(`<div>Read Depth: ${text(item.read_depth)}</div>`);
    if (number(item.million_reads_per_sample) !== null) {
      output.push(`<div>Million Reads per ${item.project_type === 'Sequencing Only - 1M Reads' ? 'Library' : 'Sample'}: ` +
        `${escape(item.million_reads_per_sample)}</div>`);
    }
    const options = [];
    for (const [field, label] of [
      ['library_qc', 'Library QC'], ['tissue_dissociation', 'Tissue Dissociation'],
      ['nuclei_extraction', 'Nuclei Extraction'], ['add_on_sequencing', 'Add-on Sequencing'],
    ]) {
      if (present(item[field])) options.push(`${label}: ${checked(item[field]) ? 'Yes' : 'No'}`);
    }
    if (present(item.onsite_service) || present(item.onsite_location) || present(item.onsite_address)) {
      const flag = present(item.onsite_service) ? (checked(item.onsite_service) ? 'Yes' : 'No') : '-';
      options.push(`On-site Service: ${flag}`);
    }
    if (options.length) output.push(`<div>Options: ${escape(options.join('; '))}</div>`);
    if (present(item.onsite_location)) output.push(`<div>On-site Location: ${text(item.onsite_location)}</div>`);
    if (present(item.onsite_address)) output.push(`<div>On-site Address: ${lines(item.onsite_address)}</div>`);
    // A default zero is not evidence that an unresolved fee was confirmed.
    if (number(item.confirmed_tbd_fee) > 0) {
      output.push(`<div>Confirmed TBD Fee: ${money(item.confirmed_tbd_fee)}</div>`);
    }
    if (present(item.use_price_override)) {
      output.push(`<div>Manual Unit Price: ${checked(item.use_price_override) ?
        `Yes (${money(item.override_unit_price)})` : 'No'}</div>`);
    }
    if (present(item.description)) output.push(`<div>Description: ${lines(item.description)}</div>`);
    if (present(item.rule_notes)) output.push(`<div>Rule Notes: ${lines(item.rule_notes)}</div>`);
    if (present(item.notes)) output.push(`<div>Notes: ${lines(item.notes)}</div>`);
    if (source) output.push(`<div class="text-muted">Source: ${text(source)} (specification only; not pricing)</div>`);
    return output.join('');
  }

  function build_html(doc = {}) {
    const items = Array.isArray(doc.items) ? doc.items : [];
    const pricing_pending = doc.doctype === 'NGS Quote' &&
      (checked(doc.pricing_to_confirm) || present(doc.missing_info) || doc.status === 'Pending');
    let warning = '';
    if (pricing_pending) {
      warning = '<div class="alert alert-warning" role="status">Pricing to confirm' +
        (present(doc.missing_info) ? `<div>${lines(doc.missing_info)}</div>` : '') + '</div>';
    }
    const rows = items.map((raw, index) => {
      const { item, source } = details(doc, raw);
      const depth = coverage(item);
      const product_title = title(item);
      const manual = requires_manual(doc, item);
      const extra = notes(item, source, product_title) + pricing_html(pricing(raw), raw, manual);
      return `<tr><td>${index + 1}</td><th scope="row" class="text-break">${text(product_title)}</th>` +
        `<td>Sample: ${text(item.sample_type)}<br>Species: ${text(item.species)}</td>` +
        `<td class="text-right text-nowrap">${number(item.quantity) === null ? '-' : escape(item.quantity)}</td>` +
        `<td>${text(depth)}</td><td>${text(item.data_analysis)}</td>` +
        `<td class="text-right text-nowrap">${manual ? 'TBD' : money(item.unit_price)}</td>` +
        `<td class="text-right text-nowrap">${manual ? 'TBD' : money(item.amount)}</td></tr>` +
        (extra ? `<tr><td colspan="8" class="text-break">${extra}</td></tr>` : '');
    }).join('');
    // Sum recorded amounts only; never multiply quantity or reuse a linked quote total.
    const known = items.filter((item) => !requires_manual(doc, item) && number(item.amount) !== null);
    const total = known.length || !items.length ? known.reduce((sum, item) => sum + number(item.amount), 0) : null;
    const partial = pricing_pending || known.length !== items.length ||
      items.some((item) => pricing(item).components.some((component) => component.display === 'TBD'));
    return warning + '<div class="table-responsive" role="region" aria-label="Item details" tabindex="0">' +
      '<table class="table table-bordered"><caption>Item details</caption><thead><tr>' +
      ['#', 'Product', 'Sample / Species', 'Quantity', 'Coverage / Reads', 'Analysis', 'Unit Price', 'Amount']
        .map((label) => `<th scope="col">${label}</th>`).join('') +
      '</tr></thead><tbody>' + (rows || '<tr><td colspan="8" class="text-muted">No items</td></tr>') +
      '</tbody><tfoot><tr><th scope="row" colspan="7" class="text-right">' +
      (partial ? 'Priced Subtotal' : 'Total') + '</th>' +
      `<td class="text-right text-nowrap">${money(total)}</td></tr></tfoot></table></div>`;
  }

  function render(frm) {
    const wrapper = frm?.fields_dict?.item_details?.$wrapper;
    if (wrapper && typeof wrapper.html === 'function') wrapper.html(build_html(frm.doc));
  }

  frappe.ngs_item_details = { build_html, render };
})();
