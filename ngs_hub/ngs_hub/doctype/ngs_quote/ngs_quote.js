// Copyright (c) 2026, sz and contributors
// For license information, please see license.txt

(() => {
  const api = 'ngs_hub.api.desk_quote.';
  const states = new WeakMap();
  const item_fields = [
    'project_type', 'service', 'sample_type', 'description', 'quantity', 'species',
    'read_depth', 'million_reads_per_sample', 'add_on_sequencing', 'data_analysis',
    'onsite_service', 'onsite_location', 'onsite_address', 'tissue_dissociation',
    'nuclei_extraction', 'library_qc', 'confirmed_tbd_fee', 'use_price_override',
    'override_unit_price', 'unit_price', 'amount', 'rule_notes', 'pricing_details',
  ];

  function context(frm) {
    return { quote_name: frm.is_new() ? null : frm.doc.name, customer: frm.doc.customer || null };
  }

  function get_state(frm) {
    const key = JSON.stringify(context(frm));
    let state = states.get(frm);
    if (!state || state.doc !== frm.doc || state.key !== key) {
      state = { doc: frm.doc, key, rows: new Map(), submissions: new Set(), dialog_token: 0 };
      states.set(frm, state);
    }
    return state;
  }

  function current(frm, state) {
    return states.get(frm) === state && frm.doc === state.doc &&
      state.key === JSON.stringify(context(frm)) && Number(frm.doc.docstatus || 0) === 0;
  }

  function find_row(frm, name) {
    return (frm.doc.items || []).find((row) => row.name === name);
  }

  function row_state(state, row) {
    let result = state.rows.get(row.name);
    if (!result || result.row !== row) {
      result = { row, token: 0, pending: null, error: null, reset: null };
      state.rows.set(row.name, result);
    }
    return result;
  }

  function row_current(frm, state, entry, token) {
    return current(frm, state) && find_row(frm, entry.row.name) === entry.row && entry.token === token;
  }

  function pick_item(values) {
    const item = {};
    for (const field of item_fields) {
      if (Object.prototype.hasOwnProperty.call(values, field)) item[field] = values[field];
    }
    return item;
  }

  async function get_options(frm, state = get_state(frm)) {
    if (!state.options) {
      state.options = (async () => {
        const response = await frappe.call({ method: api + 'get_quote_builder_options', args: context(frm) });
        if (!response.message || !Array.isArray(response.message.products) || !response.message.products.length) {
          throw new Error(__('No enabled quotation products are available.'));
        }
        return response.message.products;
      })().catch((error) => {
        state.options = null;
        throw error;
      });
    }
    return state.options;
  }

  async function preview(frm, item) {
    const response = await frappe.call({
      method: api + 'preview_quote_item',
      args: { ...context(frm), item: JSON.stringify(pick_item(item)) },
      silent: true,
    });
    const result = response.message;
    if (!result || !result.project_type || result.unit_price == null || result.amount == null ||
        !Number.isFinite(Number(result.unit_price)) || !Number.isFinite(Number(result.amount))) {
      throw new Error(__('The pricing preview returned an invalid item.'));
    }
    return pick_item(result);
  }

  function defaults(product) {
    return {
      project_type: product.project_type, service: product.service || '', description: '',
      quantity: 1, sample_type: product.samples[0] || '', species: product.species[0] || '',
      data_analysis: product.analyses[0] || '',
      million_reads_per_sample: product.reads ? product.standard_reads : 0,
      read_depth: product.depth ? '30x' : '', add_on_sequencing: 0,
      onsite_service: 0, onsite_location: '', onsite_address: '', confirmed_tbd_fee: 0,
      tissue_dissociation: 0, nuclei_extraction: 0, library_qc: 0,
      use_price_override: 0, override_unit_price: 0, unit_price: 0, amount: 0, rule_notes: '', pricing_details: '',
    };
  }

  function toggle_custom_project_description(frm) {
    const custom = (frm.doc.items || []).some((row) => row.project_type === 'Custom Project');
    frm.toggle_display('custom_project_description', custom || Boolean(frm.doc.custom_project_description));
  }

  function update_total(frm) {
    frm.doc.total_amount = (frm.doc.items || []).reduce((total, row) => total + (Number(row.amount) || 0), 0);
    frm.refresh_field('total_amount');
    frappe.ngs_item_details?.render(frm);
  }

  function configure_grid(frm) {
    const grid = frm.fields_dict.items.grid;
    grid.update_docfield_property('configure_project', 'formatter', () =>
      `<button type="button" class="btn btn-xs btn-default ngs-configure-project" title="${frappe.utils.escape_html(__('Edit item'))}">${frappe.utils.escape_html(__('Edit'))}</button>`);
    const wrapper = grid.wrapper.get(0);
    if (grid.ngs_configure_listener) wrapper.removeEventListener('click', grid.ngs_configure_listener, true);
    grid.ngs_configure_listener = (event) => {
      const button = event.target.closest('.ngs-configure-project');
      if (!button || !wrapper.contains(button)) return;
      // Handle the command before the standard grid cell switches into inline editing.
      event.preventDefault();
      event.stopPropagation();
      const name = button.closest('.grid-row')?.getAttribute('data-name');
      if (name) open_dialog(frm, 'NGS Quote Item', name);
    };
    wrapper.addEventListener('click', grid.ngs_configure_listener, true);
  }

  function apply_row(frm, row, values) {
    // Assign normalized fields without firing pricing handlers again.
    const grid = frm.fields_dict.items.grid;
    for (const [field, value] of Object.entries(pick_item(values))) {
      if (row[field] !== value) {
        row[field] = value;
        grid.set_value(field, value, row);
      }
    }
    frm.dirty();
    update_total(frm);
    toggle_custom_project_description(frm);
  }

  function update_amount(frm, row) {
    apply_row(frm, row, { amount: (Number(row.quantity) || 0) * (Number(row.unit_price) || 0) });
  }

  function preview_row(frm, cdt, cdn, reset = null) {
    const row = find_row(frm, cdn);
    if (!row || row.doctype !== cdt || Number(frm.doc.docstatus || 0) !== 0) return Promise.resolve();
    const state = get_state(frm);
    const entry = row_state(state, row);
    const token = ++entry.token;
    entry.reset = reset || entry.reset;
    entry.error = null;
    update_amount(frm, row);
    const task = (async () => {
      try {
        if (entry.reset) {
          const products = await get_options(frm, state);
          if (!row_current(frm, state, entry, token)) return;
          const field = entry.reset;
          const product = products.find((candidate) => candidate[field] === row[field]);
          if (!product || !row[field]) throw new Error(__('Select an enabled project or service.'));
          apply_row(frm, row, { ...defaults(product), quantity: row.quantity });
          entry.reset = null;
        }
        const inputs = pick_item(row);
        const result = await preview(frm, inputs);
        if (!row_current(frm, state, entry, token) ||
            JSON.stringify(inputs) !== JSON.stringify(pick_item(row))) return;
        apply_row(frm, row, result);
      } catch (error) {
        if (!row_current(frm, state, entry, token)) return;
        entry.error = error;
        frappe.show_alert({
          message: __('Pricing preview failed for row {0}. Edit or configure the row to retry.', [row.idx]),
          indicator: 'red',
        }, 7);
      } finally {
        if (entry.token === token) entry.pending = null;
      }
    })();
    entry.pending = task;
    return task;
  }

  async function validate(frm) {
    if (Number(frm.doc.docstatus || 0) !== 0) return;
    const state = get_state(frm);
    // A newer edit can replace a pending request while save is waiting.
    while (current(frm, state)) {
      const pending = [...state.submissions];
      for (const entry of state.rows.values()) {
        if (find_row(frm, entry.row.name) === entry.row && entry.pending) pending.push(entry.pending);
      }
      if (!pending.length) break;
      await Promise.all(pending);
    }
    if (!current(frm, state)) {
      frappe.throw(__('The quotation changed while pricing was pending. Please save again.'));
    }
    const failed = [...state.rows.values()]
      .filter((entry) => find_row(frm, entry.row.name) === entry.row && entry.error)
      .map((entry) => entry.row.idx);
    if (failed.length) {
      frappe.throw(__('Resolve pricing preview errors in rows {0} before saving.', [failed.join(', ')]));
    }
    update_total(frm);
  }

  async function open_dialog(frm, cdt, cdn) {
    const state = get_state(frm);
    const opening = ++state.dialog_token;
    if (state.dialog) state.dialog.hide();
    const row = cdn ? find_row(frm, cdn) : null;
    if (cdn && !row) return;
    const entry = row ? row_state(state, row) : null;
    const row_token = entry ? entry.token : null;
    try {
      if (entry && entry.pending) await entry.pending;
      const products = await get_options(frm, state);
      if (!current(frm, state) || opening !== state.dialog_token ||
          (entry && !row_current(frm, state, entry, row_token))) return;
      if (entry && entry.reset) throw new Error(__('Select an enabled project or service before configuring this row.'));
      const product = row
        ? products.find((candidate) => candidate.project_type === row.project_type &&
          (!row.service || candidate.service === row.service))
        : products[0];
      if (!product) throw new Error(__('This product is unavailable. Select an enabled project or service.'));
      return build_dialog(frm, state, products, product, row, entry, row_token, opening);
    } catch (error) {
      if (current(frm, state) && opening === state.dialog_token) {
        frappe.msgprint({
          title: __('Project Configuration'), indicator: 'red',
          message: frappe.utils.escape_html(error.message || __('Could not load project configuration. Please try again.')),
        });
      }
    }
  }

  function build_dialog(frm, state, products, initial_product, row, entry, row_token, opening) {
    let product = initial_product;
    let token = 0;
    let ready = false;
    let submitting = false;
    const groups = [...new Set(products.map((candidate) => candidate.group))];
    let base = row ? { ...defaults(product), ...pick_item(row) } : defaults(product);
    const changed = () => { if (ready) return refresh_preview(); };
    const fields = [
      { fieldname: 'group', fieldtype: 'Select', label: __('Project Group'), options: groups,
        hidden: groups.length < 2, reqd: 1, onchange: () => change_product(true) },
      { fieldname: 'subtype', fieldtype: 'Select', label: __('Subtype'), onchange: () => change_product(false) },
      { fieldtype: 'Section Break' },
      { fieldname: 'quantity', fieldtype: 'Float', label: __('Quantity'), reqd: 1 },
      { fieldname: 'sample_type', fieldtype: 'Select', label: __('Sample Type') },
      { fieldname: 'species', fieldtype: 'Select', label: __('Species') },
      { fieldtype: 'Column Break' },
      { fieldname: 'million_reads_per_sample', fieldtype: 'Float', label: __('Million Reads per Sample') },
      { fieldname: 'read_depth', fieldtype: 'Float', label: __('Coverage (x)') },
      { fieldname: 'data_analysis', fieldtype: 'Select', label: __('Data Analysis') },
      { fieldtype: 'Section Break' },
      { fieldname: 'onsite_service', fieldtype: 'Check', label: __('On-site Service') },
      { fieldname: 'onsite_location', fieldtype: 'Select', label: __('On-site Location'),
        options: ['', 'Massachusetts', 'Outside Massachusetts'], depends_on: 'eval:doc.onsite_service' },
      { fieldname: 'onsite_address', fieldtype: 'Small Text', label: __('On-site Address'),
        depends_on: 'eval:doc.onsite_service' },
      { fieldname: 'confirmed_tbd_fee', fieldtype: 'Currency', label: __('TBD Fee'),
        depends_on: 'eval:doc.onsite_service && doc.onsite_location === "Outside Massachusetts"' },
      { fieldname: 'description', fieldtype: 'Small Text', label: __('Description') },
      { fieldtype: 'Section Break' },
      { fieldname: 'use_price_override', fieldtype: 'Check', label: __('Manual Unit Price') },
      { fieldname: 'override_unit_price', fieldtype: 'Currency', label: __('Override Unit Price'),
        depends_on: 'eval:doc.use_price_override' },
      { fieldtype: 'Column Break' },
      { fieldname: 'unit_price', fieldtype: 'Currency', label: __('Unit Price'), read_only: 1 },
      { fieldname: 'amount', fieldtype: 'Currency', label: __('Amount'), read_only: 1 },
      { fieldtype: 'Section Break' },
      { fieldname: 'rule_notes', fieldtype: 'Small Text', label: __('Rule Notes'), read_only: 1 },
    ];
    for (const field of fields) {
      if (field.fieldname && !field.read_only && !field.onchange) field.onchange = changed;
      // ControlData otherwise debounces input outside grids, including temporarily blank numbers.
      if (['Float', 'Currency'].includes(field.fieldtype) && !field.read_only) {
        field.on_make = (control) => control.$input.off('input');
      }
    }
    const dialog = new frappe.ui.Dialog({
      title: row ? __('Configure Project') : __('Add Project'), size: 'large', fields,
      primary_action_label: row ? __('Apply') : __('Add'),
      primary_action: () => submit(),
      onhide: () => { ready = false; token++; },
    });
    state.dialog = dialog;

    function live() {
      return ready && dialog.is_visible && current(frm, state) && opening === state.dialog_token &&
        (!entry || row_current(frm, state, entry, row_token));
    }

    function write_values(values) {
      for (const [field, value] of Object.entries(values)) {
        if (dialog.fields_dict[field]) dialog.fields_dict[field].set_input(value);
      }
      dialog.refresh_dependency();
    }

    function configure_fields(values) {
      const members = products.filter((candidate) => candidate.group === product.group);
      dialog.set_df_property('subtype', 'options', members.map((candidate) => candidate.subtype || candidate.project_type));
      dialog.set_df_property('subtype', 'hidden', members.length < 2);
      for (const [field, choices] of [
        ['sample_type', product.samples], ['species', product.species], ['data_analysis', product.analyses],
      ]) {
        // Keep an existing value visible; the server still validates it on preview.
        const options = [...choices];
        if (values[field] && !options.includes(values[field])) options.push(values[field]);
        dialog.set_df_property(field, 'options', options.length ? options : ['']);
        dialog.set_df_property(field, 'hidden', !options.length);
      }
      for (const [field, visible] of [
        ['million_reads_per_sample', product.reads], ['read_depth', product.depth],
        ['onsite_service', product.onsite],
      ]) dialog.set_df_property(field, 'hidden', !visible);
      write_values({ ...values, group: product.group, subtype: product.subtype || product.project_type,
        read_depth: product.depth ? String(values.read_depth ?? '').replace(/x$/i, '') : '' });
    }

    function change_product(group_changed) {
      if (!ready) return;
      const group = dialog.get_value('group');
      const members = products.filter((candidate) => candidate.group === group);
      const next = group_changed ? members[0] : members.find((candidate) =>
        (candidate.subtype || candidate.project_type) === dialog.get_value('subtype'));
      if (!next) return;
      const quantity = dialog.get_value('quantity');
      product = next;
      base = { ...defaults(product), quantity };
      configure_fields(base);
      return refresh_preview();
    }

    function get_item() {
      // get_values omits cleared fields; those must replace the original row's values too.
      const values = {};
      for (const field of fields) {
        if (field.fieldname && !field.read_only) {
          values[field.fieldname] = dialog.get_value(field.fieldname) ?? '';
        }
      }
      const item = { ...base, ...pick_item(values), project_type: product.project_type, service: product.service || '' };
      if (!Number.isFinite(Number(values.quantity)) || Number(values.quantity) <= 0) {
        throw new Error(__('Quantity must be greater than zero.'));
      }
      for (const [field, enabled, label] of [
        ['million_reads_per_sample', product.reads, __('Million Reads per Sample')],
        ['read_depth', product.depth, __('Coverage (x)')],
      ]) {
        if (!enabled) item[field] = field === 'read_depth' ? '' : 0;
        else if (!Number.isFinite(Number(values[field])) || Number(values[field]) <= 0) {
          throw new Error(__('{0} must be greater than zero.', [label]));
        }
      }
      if (product.depth) item.read_depth = String(values.read_depth) + 'x';
      if (!product.onsite || !item.onsite_service) {
        item.onsite_service = 0;
        item.onsite_location = '';
        item.onsite_address = '';
        item.confirmed_tbd_fee = 0;
      }
      if (item.use_price_override && (values.override_unit_price == null ||
          !Number.isFinite(Number(values.override_unit_price)) || Number(values.override_unit_price) < 0)) {
        throw new Error(__('Enter a non-negative manual unit price.'));
      }
      return item;
    }

    function show_error(error) {
      dialog.set_alert(frappe.utils.escape_html(error.message || __('Pricing preview failed. Please try again.')), 'danger');
      write_values({ unit_price: '', amount: '', rule_notes: '' });
    }

    async function refresh_preview() {
      const request = ++token;
      if (!live()) return;
      dialog.clear_alert();
      try {
        const item = get_item();
        const result = await preview(frm, item);
        if (!live() || request !== token || JSON.stringify(item) !== JSON.stringify(get_item())) return;
        // Never write defaults back into editable inputs during a preview.
        write_values({ unit_price: result.unit_price, amount: result.amount, rule_notes: result.rule_notes });
      } catch (error) {
        if (live() && request === token) show_error(error);
      }
    }

    function submit() {
      if (submitting) return;
      if (!live()) {
        dialog.set_alert(__('This quotation or row has changed. Close this dialog and configure it again.'), 'warning');
        return;
      }
      submitting = true;
      dialog.get_primary_btn().prop('disabled', true);
      const request = ++token;
      const task = (async () => {
        try {
          const item = get_item();
          const result = await preview(frm, item);
          if (!live() || request !== token || JSON.stringify(item) !== JSON.stringify(get_item())) return;
          if (row) {
            entry.token++;
            entry.error = null;
            entry.reset = null;
            apply_row(frm, row, result);
          } else {
            // Frappe starts a required table with one blank row; configure it instead of leaving an invalid placeholder.
            const placeholder = (frm.doc.items || []).find((candidate) =>
              Number(candidate.quantity || 1) === 1 && item_fields.every((field) =>
                field === 'quantity' || !candidate[field]));
            const added = placeholder || frm.add_child('items', result);
            const added_entry = row_state(state, added);
            added_entry.token++;
            added_entry.pending = null;
            added_entry.error = null;
            added_entry.reset = null;
            if (placeholder) apply_row(frm, added, result);
            frm.refresh_field('items');
            frm.dirty();
            update_total(frm);
            toggle_custom_project_description(frm);
          }
          dialog.hide();
        } catch (error) {
          if (live() && request === token) show_error(error);
        } finally {
          submitting = false;
          dialog.get_primary_btn().prop('disabled', false);
        }
      })();
      state.submissions.add(task);
      task.finally(() => state.submissions.delete(task));
      return task;
    }

    configure_fields(base);
    dialog.show();
    ready = true;
    refresh_preview();
    return dialog;
  }

  frappe.ngs_quote = { get_options, preview_row, open_dialog, validate };

  frappe.ui.form.on('NGS Quote', {
    refresh(frm) {
      get_state(frm);
      configure_grid(frm);
      frappe.ngs_item_details?.render(frm);
      toggle_custom_project_description(frm);
      if (Number(frm.doc.docstatus || 0) === 0) {
        frm.add_custom_button(__('Add Project'), () => open_dialog(frm));
      }
      frm.add_custom_button(__('\u2192 Customer'), () => {
        if (frm.doc.customer) frappe.set_route('Form', 'NGS Customer', frm.doc.customer);
      });
      if (!frm.is_new()) {
        frm.add_custom_button(__('Create Order'), () => {
          frappe.new_doc('NGS Order', {}, (doc) => {
            doc.customer = frm.doc.customer;
            doc.quote = frm.doc.name;
          });
        });
      }
    },
    customer(frm) {
      const previous = states.get(frm);
      const state = get_state(frm);
      if (previous && previous !== state && previous.doc === frm.doc) {
        for (const entry of previous.rows.values()) {
          if (find_row(frm, entry.row.name) === entry.row) {
            preview_row(frm, entry.row.doctype, entry.row.name, entry.reset);
          }
        }
      }
    },
    validate,
  });

  const handlers = {
    configure_project: open_dialog,
    rule_notes: (frm) => frappe.ngs_item_details?.render(frm),
    project_type: (frm, cdt, cdn) => {
      toggle_custom_project_description(frm);
      return preview_row(frm, cdt, cdn, 'project_type');
    },
    service: (frm, cdt, cdn) => preview_row(frm, cdt, cdn, 'service'),
    unit_price(frm, cdt, cdn) {
      const row = find_row(frm, cdn);
      if (!row) return;
      apply_row(frm, row, { use_price_override: 1, override_unit_price: row.unit_price });
      return preview_row(frm, cdt, cdn);
    },
    items_remove(frm, cdt, cdn) {
      const state = get_state(frm);
      state.rows.delete(cdn);
      update_total(frm);
      toggle_custom_project_description(frm);
    },
  };
  for (const field of [
    'quantity', 'description', 'million_reads_per_sample', 'read_depth', 'sample_type', 'species', 'data_analysis',
    'onsite_service', 'onsite_location', 'onsite_address', 'confirmed_tbd_fee', 'add_on_sequencing',
    'library_qc', 'tissue_dissociation', 'nuclei_extraction', 'use_price_override', 'override_unit_price',
  ]) handlers[field] = (frm, cdt, cdn) => preview_row(frm, cdt, cdn);
  frappe.ui.form.on('NGS Quote Item', handlers);
})();
