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

### License

mit
