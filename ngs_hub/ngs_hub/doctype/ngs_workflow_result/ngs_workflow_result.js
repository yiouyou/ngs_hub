frappe.ui.form.on("NGS Workflow Result", {
	refresh(frm) {
		if (frm.doc.workflow_id && !frm.doc.__islocal) {
			frm.add_custom_button(__("Refresh Status"), () => fetchAndSync(frm));
		}
	},
});

async function fetchAndSync(frm) {
	frappe.show_alert({ message: __("Fetching live status…"), indicator: "blue" });
	let data;
	try {
		const r = await frappe.call({
			method: "ngs_hub.api.pipeline.get_workflow_result",
			args: { workflow_id: frm.doc.workflow_id },
		});
		data = r.message;
	} catch (e) {
		console.error("Failed to fetch workflow status:", e);
		frappe.show_alert({ message: __("Could not reach pipeline API"), indicator: "red" });
		return;
	}

	if (!data) return;

	const updates = {};
	const fields = ["status", "completed_at", "output_path", "error_message"];
	for (const f of fields) {
		const incoming = data[f] ?? null;
		const current = frm.doc[f] ?? null;
		if (incoming !== current) updates[f] = incoming;
	}

	if (!Object.keys(updates).length) {
		frappe.show_alert({ message: __("Status is up to date"), indicator: "green" });
		return;
	}

	await frappe.call({
		method: "frappe.client.set_value",
		args: { doctype: "NGS Workflow Result", name: frm.doc.name, fieldname: updates },
	});
	for (const [k, v] of Object.entries(updates)) {
		frm.set_value(k, v);
	}
	frappe.show_alert({ message: __("Status updated"), indicator: "green" });
}
