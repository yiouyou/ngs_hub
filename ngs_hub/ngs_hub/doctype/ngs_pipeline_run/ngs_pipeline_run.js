frappe.ui.form.on("NGS Pipeline Run", {
	setup(frm) {
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
				{ saveResult: true },
			);
		});
		frm.refresh_field("existing_attachment");
		frm.refresh_field("upload_csv");
		frm.refresh_field("s3_input_path");
	},

	project(frm) {
		frm.set_query();
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
	console.log("Current Form:", frm.doc);
	const s3_credentials = {
		role_arn: frm.doc.role_arn,
		access_key_id: frm.doc.access_key_id,
		secret_access_key: frm.doc.secret_access_key,
		session_token: frm.doc.session_token,
	};

	const s3_input_config = (frm.doc.s3_input_paths || []).map((p) => ({
		bucket: p.bucket_name,
		region: p.bucket_region,
		prefix: p.prefix,
		save_dir_name: p.save_to_folder,
	}));

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
		"species_type": frm.doc.species_type?.toLowerCase(),
	};

	if (frm.doc.source_type === "Existing Attachment") {
		const fileDocname = frm.doc.existing_attachment;
		params["--input"] = await frappe.call({
			method: "ngs_hub.api.pipeline.get_csv_text",
			args: { file_docname: fileDocname },
		}).then((r) => r.message);
	} else if (frm.doc.source_type === "Upload File") {
		const fileDocname = frm.doc.upload_csv;
		params["--input"] = await frappe.call({
			method: "ngs_hub.api.pipeline.get_csv_text",
			args: { file_docname: fileDocname },
		}).then((r) => r.message);
	} else if (frm.doc.source_type === "S3 Path") {
		params["--input"] = frm.doc.s3_input_path;
	}

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

async function callPipelineApi(frm, method, title, opts = {}) {
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

	let result;
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
		result = r && r.message;
	} catch (e) {
		console.error(`${title} failed:`, e);
		return;
	} finally {
		if (frappe.dom && typeof frappe.dom.unfreeze === "function") {
			try { frappe.dom.unfreeze(); } catch (_) { /* ignore */ }
		}
	}

	if (opts.saveResult && result && result.workflow_id && frm.doc.project) {
		try {
			const docName = await frappe.call({
				method: "ngs_hub.api.pipeline.save_workflow_result",
				args: {
					project: frm.doc.project,
					workflow_id: result.workflow_id,
					pipeline_name: result.pipeline_name || payload.pipeline_config.pipeline_type,
					status: result.status || "pending",
					created_at: result.created_at || frappe.datetime.now_datetime(),
				},
			}).then((r) => r.message);
			result._saved_doc = docName;
		} catch (e) {
			console.error("Failed to save workflow result:", e);
		}
	}

	showApiResult(title, result);
}

function showApiResult(title, message) {
	const data = message || {};
	const esc = (s) => frappe.utils.escape_html(String(s));
	const parts = [];
	if (data.status) parts.push(`<p><b>Status:</b> ${esc(data.status)}</p>`);
	if (data.message) parts.push(`<p><b>Message:</b> ${esc(data.message)}</p>`);
	if (data.command) {
		parts.push(`<p><b>Command:</b></p><pre>${esc(data.command)}</pre>`);
	}
	if (data.workflow_id) {
		const link = data._saved_doc
			? `<a href="/app/ngs-workflow-result/${encodeURIComponent(data._saved_doc)}">${esc(data.workflow_id)}</a>`
			: esc(data.workflow_id);
		parts.push(`<p><b>Workflow ID:</b> ${link}</p>`);
	}
	if (Array.isArray(data.s3_files_ls) && data.s3_files_ls.length) {
		parts.push(
			`<p><b>S3 Files:</b></p><pre>${esc(JSON.stringify(data.s3_files_ls, null, 2))
			}</pre>`,
		);
	}
	if (!parts.length) {
		parts.push(`<pre>${esc(JSON.stringify(data, null, 2))}</pre>`);
	}

	const status = String(data.status || "").toLowerCase();
	const indicator = status.includes("error") || status.includes("fail")
		? "red"
		: "green";

	frappe.msgprint({
		title,
		message: parts.join(""),
		indicator,
	});
}
