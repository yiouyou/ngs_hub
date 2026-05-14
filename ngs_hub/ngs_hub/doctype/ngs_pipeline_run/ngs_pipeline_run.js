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
			const payload = buildPayload(frm);
			console.log("Run payload:", payload);
			// frappe.call({
			// 	method: "ngs_hub.api.pipeline.validate_run",
			// 	args: { request_data: payload },
			// 	freeze: true,
			// 	callback: (r) => {
			// 		if (!r.exc) {
			// 			frappe.msgprint(
			// 				__("Validate result: {0}", [r.message.command]),
			// 			);
			// 		}
			// 	},
			// });
		});
		frm.add_custom_button(__("Run"), () => {
			const payload = buildPayload(frm);
			console.log("Run payload:", payload);
			// frappe.call({
			// 	method: "ngs_hub.api.pipeline.run",
			// 	args: { request_data: payload },
			// 	freeze: true,
			// 	callback: (r) => {
			// 		if (!r.exc) {
			// 			frappe.msgprint(
			// 				__("Workflow queued: {0}", [r.message.workflow_id]),
			// 			);
			// 		}
			// 	},
			// });
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

/**
 * Gather all the bits from frm.doc and return a WorkflowRequest‐shaped dict
 */
function buildPayload(frm) {
	// example: grab S3 creds off the form
	console.log("Current Form:", frm.doc);
	const s3_credentials = {
		role_arn: frm.doc.role_arn,
		access_key_id: frm.doc.access_key_id,
		secret_access_key: frm.doc.secret_access_key,
		session_token: frm.doc.session_token,
	};

	// build input buckets (you may have one or many)
	const s3_input_config = (frm.doc.s3_input_paths || []).map((p) => ({
		bucket: p.bucket_name,
		region: p.bucket_region,
		prefix: p.prefix,
		save_dir_name: p.save_to_folder,
	}));

	// assume a single output bucket section in the form
	const s3_output_config = {
		bucket: frm.doc.output_bucket_name,
		region: frm.doc.output_bucket_region,
	};

	// pipeline config
	const pipeline_config = {
		pipeline_type: frm.doc.pipeline_type,
		sample_id: frm.doc.sample_id,
		params: JSON.parse(frm.doc.params_json || "{}"),
		time_limit_hours: frm.doc.time_limit_hours,
	};

	return {
		s3_credentials,
		s3_input_config,
		s3_output_config,
		pipeline_config,
	};
}
