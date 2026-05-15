frappe.ui.form.on("NGS Pipeline Run", {
	setup(frm) {
		// filter the existing_attachment link by the selected project
		frm.set_query("existing_attachment", () => {
			if (!frm.doc.project) {
				return {};
			}
			return {
				filters: {
					attached_to_name: frm.doc.project,
					attached_to_doctype: "NGS Project",
				},
			};
		});
	},
	refresh(frm) {
		frm.add_custom_button(__("Validate"), async () => {
			const payload = await buildPayload(frm);
			console.log("Payload:", payload);
			const r = await frappe.call({
				method: "ngs_hub.api.pipeline.validate_run",
				args: payload,
				freeze: true,
			});
			frappe.msgprint(
				__("Validate result: {0}", [r.message.command]),
			);
		});
		frm.add_custom_button(__("Run"), async () => {
			const payload = await buildPayload(frm);
			console.log("Payload:", payload);
			const r = await frappe.call({
				method: "ngs_hub.api.pipeline.run",
				args: payload,
				freeze: true,
			});
			frappe.msgprint(
				__("Workflow queued: {0}", [r.message.workflow_id]),
			);
		});
		frm.refresh_field("existing_attachment");
		frm.refresh_field("upload_csv");
		frm.refresh_field("s3_input_path");
	},

	project(frm) {
		frm.set();
		frm.refresh_fields("existing_attachment");
	},

	source_type(frm) {
		frm.refresh();
	},
});

/**
 * Gather all the bits from frm.doc and return a WorkflowRequest‐shaped dict
 */
async function buildPayload(frm) {
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

	const pipeline_types = new Map([
		["RNAseq", "nextflow"],
		["rnaseq", "nextflow"],
		["cellranger", "cellranger"],
		["difference_analysis", "difference_analysis"],
	]);

	const params = {
		"--input": "",
		"--output": frm.doc.output_s3_path,
		"species_type": frm.doc.species_type,
	};

	// Choose source_type
	if (frm.doc.source_type === "Existing Attachment") {
		// existing_attachment is a Link to NGS Project Attached File or File
		const fileDocname = frm.doc.existing_attachment;
		params["--input"] = await frappe.call({
			method: "ngs_hub.api.pipeline.get_csv_text",
			args: { file_docname: fileDocname },
		}).then((r) => r.message);
	} else if (frm.doc.source_type === "Upload File") {
		// upload_csv is an Attach field, its value is the File docname too
		const fileDocname = frm.doc.upload_csv;
		params["--input"] = await frappe.call({
			method: "ngs_hub.api.pipeline.get_csv_text",
			args: { file_docname: fileDocname },
		}).then((r) => r.message);
	} else if (frm.doc.source_type === "S3 Path") {
		// user typed in the path themselves
		params["--input"] = frm.doc.s3_input_path;
	}

	// pipeline config
	const pipeline_config = {
		pipeline_type: pipeline_types.get(frm.doc.class_type),
		sample_id: frm.doc.project,
		params: params,
	};

	return {
		s3_credentials,
		s3_input_config,
		s3_output_config,
		pipeline_config,
	};
}
