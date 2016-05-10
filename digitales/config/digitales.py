from frappe import _

def get_data():
	return [
		{
			"label": _("Documents"),
			"icon": "icon-star",
			"items": [
				{
					"type": "doctype",
					"name": "Customer Contract Form",
					"description": _("Customer contract details.")
				},
				{
					"type": "doctype",
					"name": "Stock Assignment Log",
					"description": _("Stock assignment details.")
				},
				{
					"type": "doctype",
					"name": "API Configuration Page",
					"description": _("API timing and API type details.")
				},
				{
					"type": "doctype",
					"name": "Configuration Page",
					"description": _("Warehouse Configuration Page.")
				},
				{
					"type": "doctype",
					"name": "Process",
					"label": _("Shelf Ready Charge"),
					"description": _("Shelf Ready Charge Details.")
				}
			]
		},
		{
			"label": _("Setup"),
			"icon": "icon-cog",
			"items": [
				{
					"type": "doctype",
					"icon": "icon-table",
					"name": "Sync Log",
					"label": _("Sync Log"),
					"description": _("Magento > ERPNext Synchronization Logs")
				},
				{
					"type": "doctype",
					"name": "Sync Error Log",
					"label": _("Sync Error Log"),
					"description": _("Magento > ERPNext Synchronization Error Logs")
				}
			]
		},
		{
			"label": _("Reports"),
			"icon": "icon-table",
			"items": [
				{
					"type": "report",
					"is_query_report": True,
					"name": "Stock Assignment Log",
					"doctype": "Stock Assignment Log"
				},
				{
					"type": "report",
					"is_query_report": True,
					"name": "Item Back Order Report",
					"label": "Item Back Order Report",
					"doctype": "Delivery Note"
				},				
			]
		},

	]
