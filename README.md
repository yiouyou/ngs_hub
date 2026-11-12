### NGS Hub

NGS Hub

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app ngs_hub
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/ngs_hub
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### Service Pricing

- DNA Small Fragment Library Prep & Sequencing bills USD45 per library plus all requested reads at USD1.50/M. It has no included-read allowance or separate Library QC fee.
- New Sequencing Only per-read quotes/orders pool `quantity * million_reads_per_sample` across their sequencing lines. Marginal bands are first 200M at USD1.90/M, next 400M at USD1.25/M, next 600M at USD0.54/M, then USD1.00/M above 1200M. Library QC is USD18 per library, outside the bands. Fixed 10B/25B lanes do not enter this pool.
- Each band is rounded to cents and allocated proportionally by requested reads. Row amounts sum exactly to the pooled charge plus QC. Explicit administrator unit-price overrides remain visible adjustments, and their reads still contribute to the pool.
- Saved documents retain their captured tier rates. Pre-migration quotes/orders retain legacy flat-rate pricing. Changing an order's quantities recalculates that order, not its source quote.
- Catalog seeding is insert-only by service code; migration does not overwrite existing prices or enabled flags. Seed data lives under `ngs_hub/fixtures/catalog/`, outside Frappe's overwrite-based root fixture import.
- The migration sets Quotation Item and Sales Order Item `rate`, `net_rate`, `base_rate`, and `base_net_rate` precision to at least nine decimal places through standard Property Setters. This applies to those ERP field types site-wide, preserves actual quantities and fractional allocated rates, and leaves currency amount precision unchanged.

### Order Adjustments and Invoices

- NGS Customer is the authoritative customer profile. Normal draft/submitted profile changes synchronize its linked ERP Customer/Contact and optional Frappe CRM Organization; there is no reverse writeback.
- Saved order quantities and manual prices change through **Actions > Adjust Order**, with a required reason and before/after audit. A processing fee is an explicit priced row. Final-total allocation preserves those processing fees and reconciles the remaining service rows to cents. The original quote remains unchanged.
- Existing ERP draft dates and payment schedules are preserved when an NGS document changes. Financial adjustments require a draft linked ERP Sales Order; submitted accounting documents are not silently rewritten.
- ERP integration links are server-managed. Changing a customer's organization does not reassign historical ERP quotations/orders; newly created documents use the current customer mapping.
- After migration, a System Manager must configure **NGS Billing Settings**. Bank details remain private site data, not source fixtures. Set each customer's **Billing Category** (Commercial: 30 days, Academic: 45 days), invoice email and optional Billing Address. A blank billing address falls back to Address.
- **Invoices > Generate Invoice** creates one active `INV-YYYY-#####` PDF snapshot per order and queues its attachment to the invoice email only. It does not create or post an ERP Sales Invoice. An outgoing Email Account and running mail workers are required for delivery; queued does not mean sent.
- Download, explicit resend and reasoned void actions are available on NGS Invoice. An issued invoice locks order financial facts and PO until voided; voiding retains the number and original PDF. Generating again after voiding assigns a new number. Send-time guards prevent retrying void invoice emails, including framework forced sends.
- Archived invoice File records cannot be made public, detached, deleted or renamed. PDF bytes are stored without text decoding. Concurrent invoice and email actions use locking reads; duplicate generation returns the same archived invoice without queuing another email.
- Credit accounting and online sample registration/LIMS orchestration are not included in this change. Uploaded registration files remain the current submission mechanism.

### License

mit
