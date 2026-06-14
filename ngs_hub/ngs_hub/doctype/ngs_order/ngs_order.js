// Copyright (c) 2026, sz and contributors
// For license information, please see license.txt

frappe.ui.form.on("NGS Order", {
	refresh(frm) {
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
