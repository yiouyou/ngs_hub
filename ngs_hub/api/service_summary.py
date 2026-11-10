"""Compact service specifications based only on recorded item facts."""

import math
import re


def _text(value):
	return " ".join(str(value or "").split())


def _number(value):
	try:
		number = float(value)
	except (TypeError, ValueError):
		return None
	return number if math.isfinite(number) else None


def _checked(value):
	return value in (True, 1, "1")


def recorded_coverage(item):
	depth = _text(item.get("read_depth"))
	if item.get("project_type") == "WGS":
		match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*x?", depth, re.IGNORECASE)
		return f"{float(match[1]):.15g}x" if match else depth
	reads = _number(item.get("million_reads_per_sample"))
	if not reads and depth.lower().endswith("m"):
		reads = _number(depth[:-1])
	if reads is not None and reads > 0:
		unit = "library" if item.get("project_type") == "Sequencing Only - 1M Reads" else "sample"
		return f"{reads:.15g}M/{unit}"
	return depth


def item_summary(item):
	project = _text(item.get("project_type"))
	title = project or _text(item.get("service")) or "Project"
	coverage = recorded_coverage(item)
	if project == "Sequencing Only - 1M Reads":
		title = "Sequencing Only"
	elif project.startswith("Sequencing Only - "):
		title = project.replace(" - ", " ")
	elif project.startswith("Microbial Genome Sequencing"):
		title = project.replace("Microbial Genome Sequencing", "Microbial Genome")
	if coverage:
		title = f"{title} {coverage}"
	elif project == "WGS":
		title += " (coverage not recorded)"
	elif project in {
		"Bulk RNAseq",
		"Shotgun Meta",
		"Sequencing Only - 1M Reads",
		"DNA Small Fragment Library Prep & Sequencing",
	} or project.startswith("10x"):
		title += " (reads not recorded)"
	quantity = _number(item.get("quantity"))
	parts = [title[:90], f"x{quantity:.15g}" if quantity is not None else "Qty not recorded"]
	species = _text(item.get("species"))
	sample = _text(item.get("sample_type"))
	if species:
		parts.append(species)
	if sample:
		parts.append(sample)
	analysis = _text(item.get("data_analysis"))
	if analysis and analysis != "No":
		parts.append(analysis)
	for field, label in (
		("library_qc", "Library QC"),
		("tissue_dissociation", "Tissue dissociation"),
		("nuclei_extraction", "Nuclei extraction"),
		("onsite_service", "On-site"),
	):
		if _checked(item.get(field)):
			parts.append(label)
	if project == "Custom Project" and item.get("description"):
		parts.append(_text(item.get("description"))[:40])
	return " / ".join(parts)


def build_project_summary(items, max_length=140):
	rows = [
		item_summary(item)
		for item in items
		if item.get("project_type") or item.get("service") or item.get("description")
	]
	if not rows:
		return ""
	visible = []
	for row in rows[:2]:
		remaining = len(rows) - len(visible) - 1
		suffix = f" | +{remaining} more" if remaining else ""
		candidate = " | ".join([*visible, row]) + suffix
		if len(candidate) <= max_length:
			visible.append(row)
		else:
			break
	if not visible:
		suffix = f" | +{len(rows) - 1} more" if len(rows) > 1 else ""
		budget = max_length - len(suffix)
		return (
			rows[0][: max(0, budget - 3)].rstrip() + "..." if budget >= 3 else rows[0][: max(0, budget)]
		) + suffix
	remaining = len(rows) - len(visible)
	return " | ".join(visible) + (f" | +{remaining} more" if remaining else "")
