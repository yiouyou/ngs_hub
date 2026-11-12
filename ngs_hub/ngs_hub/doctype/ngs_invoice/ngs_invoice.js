frappe.ui.form.on("NGS Invoice", {
	refresh(frm) {
		if (frm.is_new()) return;
		if (frm.doc.pdf_file) {
			frm.add_custom_button(__("Download PDF"), () => {
				window.open(
					"/api/method/ngs_hub.api.invoicing.download_invoice?invoice_name=" +
						encodeURIComponent(frm.doc.name),
					"_blank",
					"noopener"
				);
			});
		}
		if (frm.doc.status !== "Issued" || !frappe.model.can_write("NGS Invoice")) return;
		frm.add_custom_button(__("Resend Invoice"), () => {
			frappe.confirm(
				__("Queue this invoice again to {0}?", [
					frappe.utils.escape_html(frm.doc.invoice_email_address),
				]),
				() => frappe.call({
					method: "ngs_hub.api.invoicing.resend_invoice",
					args: { invoice_name: frm.doc.name },
					freeze: true,
					callback: () => frappe.show_alert({ message: __("Invoice email queued"), indicator: "green" }),
				})
			);
		});
		frm.add_custom_button(__("Void Invoice"), () => {
			frappe.prompt(
				{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 },
				({ reason }) => frappe.confirm(
					__("Void this invoice? Its number and original PDF will be retained. Pending emails will be stopped; previously sent copies remain with recipients. Do not retry or force-send its cancelled Email Queue entries."),
					() => frappe.call({
						method: "ngs_hub.api.invoicing.void_invoice",
						args: { invoice_name: frm.doc.name, reason },
						freeze: true,
						callback: () => frm.reload_doc(),
					})
				),
				__("Void Invoice"),
				__("Continue")
			);
		});
	},
});
