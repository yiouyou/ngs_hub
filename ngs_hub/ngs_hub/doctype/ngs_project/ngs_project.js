frappe.ui.form.on("NGS Project", {
	refresh(frm) {
		frm.add_custom_button(__("→ Customer"), () => {
			if (frm.doc.customer) {
				frappe.set_route("Form", "NGS Customer", frm.doc.customer);
			} else {
				frappe.msgprint(__("No associated Customer found."));
			}
		});

		if (frm.doc.class_type === "RNAseq") {
			frm.add_custom_button(__("Sequencing"), () => {
				frappe.new_doc("NGS Pipeline Run", {}, (doc) => {
					doc.project = frm.doc.name;
					doc.customer = frm.doc.customer;
					doc.class_type = frm.doc.class_type;
					doc.project_id = frm.doc.project_id;
				});
			});
		}

		frm.add_custom_button(__("+ Sample Transfer"), () => {
			frappe.new_doc("NGS Sample Transfer", {}, (doc) => {
				doc.customer = frm.doc.customer;
				doc.class_type = frm.doc.class_type;
				doc.project = frm.doc.project_id;
				doc.save();
			});
		});

		if (frm.doc.name && !frm.doc.__islocal) {
			renderWorkflowRuns(frm);
		}
	},
});

function renderWorkflowRuns(frm) {
	frappe.call({
		method: "frappe.client.get_list",
		args: {
			doctype: "NGS Workflow Result",
			filters: { project: frm.doc.name },
			fields: ["name", "workflow_id", "pipeline_name", "status", "created_at"],
			order_by: "created_at desc",
			limit: 50,
		},
		callback(r) {
			const rows = r.message || [];
			let html;

			if (!rows.length) {
				html = "<p class='text-muted' style='margin-top:8px'>No workflow runs yet.</p>";
			} else {
				const statusColor = {
					pending: "orange",
					running: "blue",
					completed: "green",
					failed: "red",
					terminated: "grey",
				};
				const badge = (s) => {
					const c = statusColor[s] || "grey";
					return `<span class="indicator-pill ${c}" style="font-size:11px">${frappe.utils.escape_html(s)}</span>`;
				};
				const esc = frappe.utils.escape_html;

				html = `<table class="table table-bordered table-sm" style="margin-top:8px;font-size:13px">
					<thead style="background:#f5f5f5">
						<tr>
							<th>Workflow ID</th>
							<th>Pipeline</th>
							<th>Status</th>
							<th>Started</th>
						</tr>
					</thead>
					<tbody>`;

				for (const row of rows) {
					const shortId = row.workflow_id
						? (row.workflow_id.length > 28 ? row.workflow_id.substring(0, 28) + "…" : row.workflow_id)
						: row.name;
					const started = row.created_at
						? frappe.datetime.str_to_user(row.created_at)
						: "";
					html += `<tr style="cursor:pointer"
						onclick="frappe.set_route('Form','NGS Workflow Result','${encodeURIComponent(row.name)}')">
						<td><a>${esc(shortId)}</a></td>
						<td>${esc(row.pipeline_name || "")}</td>
						<td>${badge(row.status || "")}</td>
						<td>${esc(started)}</td>
					</tr>`;
				}
				html += "</tbody></table>";
			}

			frm.set_df_property("workflow_runs_html", "options", html);
			frm.refresh_field("workflow_runs_html");
		},
	});
}
