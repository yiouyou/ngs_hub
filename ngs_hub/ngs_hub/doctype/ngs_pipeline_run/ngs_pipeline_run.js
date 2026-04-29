frappe.ui.form.on("NGS Pipeline Run", {
	setup(frm) {
		// any client‐side setup (e.g. hide fields until CSV or S3)
	},
	refresh(frm) {
		frm.add_custom_button(__("Validate"), () => {
			frappe.call({
				method: "ngs_hub.api.pipeline.validate_run",
				args: { doc: frm.doc },
				freeze: true,
				callback: (r) => {
					if (!r.exc) {
						frappe.msgprint(
							__("Validate result: {0}", [r.message.command]),
						);
					}
				},
			});
		});
		frm.add_custom_button(__("Run"), () => {
			frappe.call({
				method: "ngs_hub.api.pipeline.run",
				args: { doc: frm.doc },
				freeze: true,
				callback: (r) => {
					if (!r.exc) {
						frappe.msgprint(
							__("Workflow queued: {0}", [r.message.workflow_id]),
						);
					}
				},
			});
		});
	},
});
