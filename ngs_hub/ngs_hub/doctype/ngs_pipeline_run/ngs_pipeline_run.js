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
		frm.add_custom_button(__("Validate"), () => {
			callPipelineApi(
				frm,
				"ngs_hub.api.pipeline.validate_run",
				__("Validate Result"),
			);
		});
		frm.add_custom_button(__("Run"), () => {
			callPipelineApi(
				frm,
				"ngs_hub.api.pipeline.run",
				__("Run Result"),
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
		"--outdir": frm.doc.output_s3_path,
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

async function callPipelineApi(frm, method, title) {
	let payload;
	try {
		payload = await buildPayload(frm);
	} catch (e) {
		console.error("Failed to build payload:", e);
		frappe.msgprint({
			title: __("Error"),
			message: __("Failed to build request payload: {0}", [
				e && e.message ? e.message : String(e),
			]),
			indicator: "red",
		});
		return;
	}

	console.log("Payload:", payload);

	try {
		const r = await frappe.call({
			method,
			args: payload,
			headers: {
				"Authorization": "Bearer " + frappe.get_cookie("token"),
			},
			freeze: true,
			xhrFields: { withCredentials: true },
		});
		showApiResult(title, r && r.message);
	} catch (e) {
		// frappe.call already surfaces the server-side error dialog from
		// frappe.throw; swallow here so the rejection is not unhandled.
		console.error(`${title} failed:`, e);
	} finally {
		// Defensive unfreeze: on error paths frappe.call has been observed
		// to leave the freeze overlay up, which made the custom buttons
		// un-clickable until reload.
		if (frappe.dom && typeof frappe.dom.unfreeze === "function") {
			try { frappe.dom.unfreeze(); } catch (_) { /* ignore */ }
		}
	}
}

function showApiResult(title, message) {
	const data = message || {};
	const esc = (s) => frappe.utils.escape_html(String(s));
	const parts = [];
	if (data.status) parts.push(`<p><b>Status:</b> ${esc(data.status)}</p>`);
	if (data.message) parts.push(`<p><b>Message:</b> ${esc(data.message)}</p>`);
	if (data.command) parts.push(`<p><b>Command:</b></p><pre>${esc(data.command)}</pre>`);
	if (data.workflow_id) parts.push(`<p><b>Workflow ID:</b> ${esc(data.workflow_id)}</p>`);
	if (Array.isArray(data.s3_files_ls) && data.s3_files_ls.length) {
		parts.push(
			`<p><b>S3 Files:</b></p><pre>${esc(JSON.stringify(data.s3_files_ls, null, 2))}</pre>`,
		);
	}
	if (!parts.length) {
		parts.push(`<pre>${esc(JSON.stringify(data, null, 2))}</pre>`);
	}

	const status = String(data.status || "").toLowerCase();
	const indicator = status.includes("error") || status.includes("fail") ? "red" : "green";

	frappe.msgprint({
		title,
		message: parts.join(""),
		indicator,
	});
}
