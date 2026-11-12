// Copyright (c) 2026, sz and contributors
// For license information, please see license.txt

frappe.ui.form.on("NGS Order", {
	async validate(frm) {
		const state = frm.__ngs_sequencing_preview;
		while (state?.pending) await Promise.race([state.pending, state.change]);
		if (state?.error) frappe.throw(__("Resolve the order pricing preview error before saving."));
	},
	refresh(frm) {
		frappe.ngs_item_details?.render(frm);
		frm.add_custom_button(__("Customer"), () => {
			if (frm.doc.customer) frappe.set_route("Form", "NGS Customer", frm.doc.customer);
		}, __("Open"));
		frm.add_custom_button(__("Quote"), () => {
			if (frm.doc.quote) frappe.set_route("Form", "NGS Quote", frm.doc.quote);
		}, __("Open"));
		if (!frm.is_new()) {
			const grid = frm.fields_dict?.items?.grid;
			if (grid?.update_docfield_property) {
				for (const field of ["quote_item", "service", "project_type", "sample_type", "description", "quantity",
					"read_depth", "data_analysis", "onsite_service", "onsite_location", "onsite_address",
					"tissue_dissociation", "nuclei_extraction", "library_qc"]) {
					grid.update_docfield_property(field, "read_only", 1);
				}
			}
			add_review_buttons(frm);
			add_order_billing_buttons(frm);
		}
	},
});

function add_order_billing_buttons(frm) {
	const roles = frappe.user_roles || [];
	if (!roles.some(role => ["NGS Internal Staff", "System Manager"].includes(role))) return;
	if (frm.doc.docstatus === 0 && frm.doc.status !== "Cancelled") {
		frm.add_custom_button(__("Adjust Order"), () => show_order_adjustment(frm), __("Actions"));
	}
	if (frm.doc.docstatus !== 2 && frm.doc.status !== "Cancelled") {
		frm.add_custom_button(__("Generate Invoice"), async () => {
			if (!saved_order_required(frm) || frm.__ngs_invoice_busy) return;
			frm.__ngs_invoice_busy = true;
			try {
				const response = await frappe.call({
					method: "ngs_hub.api.invoicing.generate_invoice",
					type: "POST", args: { order_name: frm.doc.name }, freeze: true,
					freeze_message: __("Generating invoice..."),
				});
				if (response.message?.name) {
					frappe.show_alert({ message: response.message.existing
						? __("Existing invoice opened. No duplicate email queued.")
						: __("Invoice generated. Email queued."), indicator: "green" });
					frappe.set_route("Form", "NGS Invoice", response.message.name);
				}
			} finally {
				frm.__ngs_invoice_busy = false;
			}
		}, __("Invoices"));
	}
	frm.add_custom_button(__("View Invoices"), () => {
		frappe.route_options = { order: frm.doc.name };
		frappe.set_route("List", "NGS Invoice");
	}, __("Invoices"));
}

function saved_order_required(frm) {
	if (frm.is_dirty?.()) {
		frappe.msgprint(__("Save order changes first."));
		return false;
	}
	return true;
}

function show_order_adjustment(frm) {
	if (!saved_order_required(frm)) return;
	const dialog = new frappe.ui.Dialog({
		title: __("Adjust Order"), size: "extra-large",
		fields: [
			{ fieldname: "items", fieldtype: "Table", label: __("Order Items"),
				cannot_add_rows: true, cannot_delete_rows: true, in_place_edit: true,
				data: (frm.doc.items || []).map(row => ({ name: row.name, row_name: row.name,
					product: row.description || row.project_type, quantity: row.quantity,
					remove: 0,
					use_price_override: row.use_price_override || 0,
					override_unit_price: row.use_price_override ? row.override_unit_price || 0 : row.unit_price })),
				fields: [
					{ fieldname: "row_name", fieldtype: "Data", hidden: true },
					{ fieldname: "product", fieldtype: "Data", label: __("Product"), read_only: true, in_list_view: 1 },
					{ fieldname: "quantity", fieldtype: "Float", label: __("Quantity"), precision: 9, in_list_view: 1, reqd: 1 },
					{ fieldname: "remove", fieldtype: "Check", label: __("Remove"), in_list_view: 1 },
					{ fieldname: "use_price_override", fieldtype: "Check", label: __("Manual Price"), in_list_view: 1 },
					{ fieldname: "override_unit_price", fieldtype: "Currency", label: __("Manual Unit Price"), precision: 9, in_list_view: 1 },
				] },
			{ fieldname: "processing_fee", fieldtype: "Currency", label: __("Sample Processing Fee"), default: 0 },
			{ fieldname: "processing_description", fieldtype: "Small Text", label: __("Processing Description") },
			{ fieldname: "set_final_total", fieldtype: "Check", label: __("Set Final Total") },
			{ fieldname: "final_total", fieldtype: "Currency", label: __("Final Total"),
				depends_on: "eval:doc.set_final_total", mandatory_depends_on: "eval:doc.set_final_total" },
			{ fieldname: "reason", fieldtype: "Small Text", label: __("Adjustment Reason"), reqd: 1 },
		],
		primary_action_label: __("Apply Adjustment"),
		async primary_action(values) {
			if (dialog.__ngs_busy) return;
			dialog.__ngs_busy = true;
			dialog.get_primary_btn().prop("disabled", true);
			try {
				const args = {
					order_name: frm.doc.name, reason: values.reason,
					items: JSON.stringify((values.items || []).map(row => ({ name: row.row_name,
						quantity: row.remove ? 0 : row.quantity, use_price_override: row.use_price_override || 0,
						override_unit_price: row.use_price_override ? row.override_unit_price || 0 : 0 }))),
					processing_fee: values.processing_fee || 0,
					processing_description: values.processing_description || "",
				};
				if (values.set_final_total) args.final_total = values.final_total;
				await frappe.call({ method: "ngs_hub.api.order_adjustments.adjust_order", type: "POST",
					args, freeze: true, freeze_message: __("Updating order...") });
				dialog.hide();
				await frm.reload_doc();
			} finally {
				dialog.__ngs_busy = false;
				dialog.get_primary_btn().prop("disabled", false);
			}
		},
	});
	dialog.show();
	dialog.fields_dict?.items?.grid?.wrapper.find(
		".grid-add-row, .grid-add-multiple-rows, .grid-upload, .grid-download, .grid-duplicate-rows, .grid-remove-rows, .grid-remove-all-rows"
	).remove();
}

frappe.ui.form.on("NGS Order Item", {
	items_remove(frm) {
		frappe.ngs_item_details?.render(frm);
		return preview_order_sequencing(frm);
	},
	quantity(frm, cdt, cdn) {
		return update_order_item_amount(frm, cdt, cdn);
	},
	...Object.fromEntries([
		"project_type", "service", "sample_type", "species", "read_depth", "million_reads_per_sample",
		"data_analysis", "description", "notes", "onsite_service", "onsite_location", "onsite_address",
		"library_qc", "tissue_dissociation", "nuclei_extraction", "add_on_sequencing",
		"confirmed_tbd_fee", "use_price_override", "override_unit_price", "unit_price", "amount",
	].map((field) => [field, (frm) => frappe.ngs_item_details?.render(frm)])),
});

function update_order_item_amount(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	const state = frm.doc.sequencing_pricing_version && !frm.is_new?.() ? order_pricing_state(frm) : null;
	if (state) state.error = null;
	const task = frappe.model.set_value(cdt, cdn, "amount", flt(row.quantity) * flt(row.unit_price)).then(() => {
		frm.refresh_field("items");
		frappe.ngs_item_details?.render(frm);
		return preview_order_sequencing(frm);
	});
	if (state) {
		state.pending = task;
		wake_order_pricing(state);
		task.then(() => {
			if (state.pending === task) state.pending = null;
			wake_order_pricing(state);
		}, error => {
			if (state.pending === task) { state.pending = null; state.error = error; }
			wake_order_pricing(state);
		});
	}
	return task;
}

function wake_order_pricing(state) {
	state.wake?.();
	state.change = new Promise(resolve => { state.wake = resolve; });
}

function order_pricing_state(frm) {
	let state = frm.__ngs_sequencing_preview;
	if (!state || state.doc !== frm.doc) {
		state = frm.__ngs_sequencing_preview = { doc: frm.doc, token: 0, pending: null, error: null };
		wake_order_pricing(state);
	}
	return state;
}

function preview_order_sequencing(frm) {
	if (!frm.doc.sequencing_pricing_version || frm.is_new?.()) return Promise.resolve();
	const state = order_pricing_state(frm);
	const token = ++state.token;
	state.error = null;
	const quantities = () => (frm.doc.items || []).map(row => ({ name: row.name, quantity: row.quantity }));
	const inputs = JSON.stringify(quantities());
	if (!(frm.doc.items || []).some(row => row.project_type === "Sequencing Only - 1M Reads")) {
		state.pending = null;
		wake_order_pricing(state);
		return Promise.resolve();
	}
	const current = () => state.doc === frm.doc && token === state.token && inputs === JSON.stringify(quantities());
	const task = (async () => {
		try {
			const response = await frappe.call({
				method: "ngs_hub.api.desk_order.preview_order_sequencing",
				args: { order_name: frm.doc.name, items: inputs }, silent: true,
			});
			if (!current()) return;
			const rows = response.message?.items;
			if (!Array.isArray(rows) || rows.length !== frm.doc.items.length || new Set(rows.map(row => row.name)).size !== rows.length ||
				!rows.every(row => {
					const item = row && frm.doc.items.find(item => item.name === row.name);
					return item && Number.isFinite(row.unit_price) && row.unit_price >= 0 &&
						Number.isFinite(row.amount) && row.amount >= 0 &&
						Math.abs(row.unit_price * Number(item.quantity) - row.amount) <= 0.005 + 1e-9 &&
						(item.project_type !== "Sequencing Only - 1M Reads" ||
							typeof row.pricing_details === "string" && row.pricing_details.length > 0);
				})) {
				throw new Error(__("Invalid order pricing preview."));
			}
			for (const values of rows) {
				const row = frm.doc.items.find(item => item.name === values.name);
				for (const field of ["unit_price", "amount", "pricing_details", "notes"]) row[field] = values[field];
			}
			frm.refresh_field("items");
			frappe.ngs_item_details?.render(frm);
		} catch (error) {
			if (!current()) return;
			state.error = error;
			frappe.show_alert({ message: __("Order pricing preview failed. Edit the quantity to retry."), indicator: "red" });
		} finally {
			if (state.token === token) state.pending = null;
			wake_order_pricing(state);
		}
	})();
	state.pending = task;
	wake_order_pricing(state);
	return task;
}

function add_review_buttons(frm) {
	if (["Draft", "Submitted"].includes(frm.doc.status)) {
		frm.add_custom_button(__("Start Review"), () => {
			call_order_review(frm, "start_review");
		}, __("Review"));
	}
	if (["Submitted", "In Review"].includes(frm.doc.status)) {
		frm.add_custom_button(__("Accept Order"), () => {
			call_order_review(frm, "accept_order");
		}, __("Review"));
	}
	if (frm.doc.status === "Accepted") {
		frm.add_custom_button(__("Submit ERPNext Sales Order"), () => {
			frappe.confirm(
				__("Submit the linked ERPNext Sales Order? This makes the Sales Order official."),
				() => call_order_review(frm, "submit_erpnext_sales_order")
			);
		}, __("Review"));
	}
}

function call_order_review(frm, method) {
	frappe.call({
		method: `ngs_hub.api.order_review.${method}`,
		args: {
			order_name: frm.doc.name,
		},
		freeze: true,
		callback: () => {
			frm.reload_doc();
		},
	});
}
