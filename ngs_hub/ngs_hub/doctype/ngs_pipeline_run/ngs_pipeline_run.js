frappe.ui.form.on("NGS Pipeline Run", {
	setup(frm) {
		// filter the existing_attachment link by the selected project
		frm.set_query("existing_attachment", () => {
			if (!frm.doc.project) return {};
			return { filters: { parent: frm.doc.project } };
		});
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
		frm.refresh_field("existing_attachment");
		frm.refresh_field("upload_csv");
		frm.refresh_field("s3_input_path");
	},

	project(frm) {
		frm.refresh();
	},

	source_type(frm) {
		frm.refresh();
	},
});
