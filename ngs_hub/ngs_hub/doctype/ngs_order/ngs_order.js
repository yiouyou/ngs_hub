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
			add_review_buttons(frm);
		}
	},
});

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
