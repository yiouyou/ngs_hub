"""Seed missing DNA small-fragment and sequencing tier catalog entries."""

from ngs_hub.install import create_ngs_service_catalog, ensure_erp_service_rate_precision


def execute():
	create_ngs_service_catalog()
	ensure_erp_service_rate_precision()
