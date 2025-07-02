app_name = "ngs_hub"
app_title = "NGS Hub"
app_publisher = "sz"
app_description = "NGS Hub"
app_email = "zhuosong@gmail.com"
app_license = "mit"


fixtures = [
	{"dt": "Role", "filters": [["name", "in", ["NGS Internal Staff", "NGS External Customer"]]]},
	{"dt": "Workspace", "filters": [["name", "in", ["NGS Hub", "QC FAIL"]]]},
	{
		"dt": "Server Script",
		"filters": [
			[
				"name",
				"in",
				[
					"Send Email on SingleCell LIB QC PASS",
					"Send Email on RNAseq LIB QC PASS",
					"Send Email on Meta LIB QC PASS",
				],
			]
		],
	},
	{
		"dt": "UOM",
		"filters": [
			["name", "in", ["uL", "mL", "L", "Prep", "Reaction", "Piece", "Pack", "Box", "Gb", "Hour"]]
		],
	},
	{"dt": "UOM Category", "filters": [["name", "in", ["Volume", "Quantity", "Data Volume"]]]},
	{"dt": "UOM Conversion Factor", "filters": [["from_uom", "in", ["mL", "uL", "L"]]]},
	{"dt": "Warehouse", "filters": [["name", "in", ["NGS Lab - ATH"]]]},
	{
		"dt": "Item Group",
		"filters": [
			[
				"name",
				"in",
				[
					"NGS Supplies",
					"Kits",
					"Reagents",
					"NGS Consumables",
					"NGS Services",
					"External Packages",
					"SingleCell",
					"RNAseq",
					"Meta",
					"Customized Data Analysis",
					"Internal Workflow",
					"SingleCell Wetlab",
					"SingleCell Bioinfo",
					"RNAseq Wetlab",
					"RNAseq Bioinfo",
					"Meta Wetlab",
					"Meta Bioinfo",
					"Sequencing Outsource",
				],
			]
		],
	},
	{"dt": "Item", "filters": [["item_code", "in", ["BUFFER-ATL-100ML", "QIA-DNA-KIT-96", "TUBE-1.5ML"]]]},
	# {"dt": "Role Profile", "filters": [["name", "in", ["NGS Customer"]]]},
	# {"dt": "Module Profile", "filters": [["name", "in", ["NGS Customer"]]]},
]


# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
add_to_apps_screen = [
	{
		"name": "ngs_hub",
		"logo": "/assets/ngs_hub/logo.png",
		"title": "NGS Hub",
		"route": "/app/ngs-hub",
		# "has_permission": "ngs_hub.api.permission.has_app_permission",
	}
]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/ngs_hub/css/ngs_hub.css"
# app_include_js = "/assets/ngs_hub/js/ngs_hub.js"

# include js, css files in header of web template
# web_include_css = "/assets/ngs_hub/css/ngs_hub.css"
# web_include_js = "/assets/ngs_hub/js/ngs_hub.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "ngs_hub/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "ngs_hub/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "ngs_hub.utils.jinja_methods",
# 	"filters": "ngs_hub.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "ngs_hub.install.before_install"
# after_install = "ngs_hub.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "ngs_hub.uninstall.before_uninstall"
# after_uninstall = "ngs_hub.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "ngs_hub.utils.before_app_install"
# after_app_install = "ngs_hub.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "ngs_hub.utils.before_app_uninstall"
# after_app_uninstall = "ngs_hub.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "ngs_hub.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"ngs_hub.tasks.all"
# 	],
# 	"daily": [
# 		"ngs_hub.tasks.daily"
# 	],
# 	"hourly": [
# 		"ngs_hub.tasks.hourly"
# 	],
# 	"weekly": [
# 		"ngs_hub.tasks.weekly"
# 	],
# 	"monthly": [
# 		"ngs_hub.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "ngs_hub.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "ngs_hub.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "ngs_hub.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["ngs_hub.utils.before_request"]
# after_request = ["ngs_hub.utils.after_request"]

# Job Events
# ----------
# before_job = ["ngs_hub.utils.before_job"]
# after_job = ["ngs_hub.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"ngs_hub.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }
