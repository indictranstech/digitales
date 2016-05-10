import json
import frappe
import requests

from .products import create_or_update_item
from .customers import create_or_update_customer
from .orders import create_or_update_sales_order
from .utils import log_sync_error, GetOauthDetails, create_scheduler_exception

@frappe.whitelist()
def manually_sync_entity(entity_type, entity):
	func_map = {
		"Item": get_missing_items,
		"Sales Order": get_missing_orders,
		"Customer": get_missing_customers
	}
	
	func_map[entity_type](entity, manual_sync=True)

def get_missing_items(item=None, manual_sync=False):
	""" Get the missing items from magento """
	if all([item, manual_sync]):
		results = json.loads(item) if item else None
		if results: results = [results]
	
	items = get_entity_ids_to_sync(entity_type="Item", results=results)
	if not items:
		return

	url = "http://digitales.com.au/api/rest/products?filter[1][attribute]=sku&filter[1][eq]=%s"

	for item in items:
		response = get_entity_from_magento(url%(item), entity_type="Item", entity_id=item)
		if response:
			idx = response.keys()[0]
			create_or_update_item(response[idx])

	update_sync_status("Item", items)

def get_missing_customers(customer=None, manual_sync=False):
	""" Get the missing customer from magento """
	if all([customer, manual_sync]):
		results = json.loads(customer) if customer else None
		if results: results = [results]

	customers = get_entity_ids_to_sync(entity_type="Customer", results=results)
	if not customers:
		return

	url = "http://digitales.com.au/api/rest/customers?filter[1][attribute]=entity_id&filter[1][eq]=%s"

	for customer in customers:
		response = get_entity_from_magento(url%(customer), entity_type="Customer", entity_id=customer)
		if response:
			idx = response.keys()[0]
			create_or_update_customer(response[idx])

	update_sync_status("Customer", customers)

def get_missing_orders(order=None, manual_sync=False):
	""" Get the missing orders from magento """
	# orders = get_entity_ids_to_sync(entity_type="Sales Order") if not all(order, manual_sync) else [order]
	if all([order, manual_sync]):
		results = json.loads(order) if order else None
		if results: results = [results]

	orders = get_entity_ids_to_sync(entity_type="Sales Order", results=results)
	if not orders:
		return

	url = "http://digitales.com.au/api/rest/orders?filter[1][attribute]=entity_id&filter[1][eq]=%s"

	for order in orders:
		response = get_entity_from_magento(url%(order), entity_type="Sales Order", entity_id=order)
		if response:
			idx = response.keys()[0]
			create_or_update_sales_order(response[idx])

	update_sync_status("Sales Order", orders)

def get_entity_ids_to_sync(entity_type=None, results=None):
	""" get the list of entity ids from `Sync Error Log` to re-sync """
	entities = []
	if not entity_type:
		return

	if not results:
		filters = {
			"sync_doctype": entity_type,
			"is_synced": "No",
			"sync_attempts": ("<", 4)
		}
		fields = "sync_docname" if entity_type != "Sales Order" else ["sync_docname", "missing_items", "missing_customer"]		
		results = frappe.db.get_values("Sync Error Log", filters, fields, as_dict=True)

	if entity_type == "Sales Order":
		for order in results:
			entity_id = order.get("sync_docname")
			items = []
			customer = []
			sales_order = []

			if order.get("missing_items"):
				missing_items = [item for item in json.loads(order.get("missing_items")) or []]
				if missing_items: items = check_if_entity_already_synced("Item", missing_items)
			
			if not items and order.get("missing_customer"):
				customer = check_if_entity_already_synced("Customer", [order.get("missing_customer")])

			if not customer:
				sales_order = check_if_entity_already_synced("Sales Order", [order.get("sync_docname")])

			if sales_order: entities.append(entity_id)
	else:
		frappe.errprint(results)
		entities = list(set([result.get("sync_docname") for result in results]))
		if entities: entities = check_if_entity_already_synced(entity_type, entities)

	return entities

def update_sync_status(entity_type, entities):
	""" update the sync error log status to Yes if entity is synced """
	if not entities:
		return

	# get the list of synced entities from entities and update the status
	check_if_entity_already_synced(entity_type, entities)

def check_if_entity_already_synced(entity_type, entities):
	""" check if Items or Customer is already synced """
	field_map = { "Item": "name", "Sales Order": "entity_id", "Customer": "entity_id" }
	
	if not entities:
		return []

	# for item check name, for order check entity id, for customer check organization
	query = """ select {fieldname} from `tab{table}` where {fieldname} in ({names}) """.format(
				table=entity_type,
				fieldname=field_map.get(entity_type),
				names=",".join(["'%s'"%entity for entity in entities])
			)

	results = frappe.db.sql(query, as_dict=True)

	if not results:
		return entities
	
	synced_entities = list(set([result.get(field_map.get(entity_type)) for result in results]))
	
	if synced_entities:
		""" Update the Sync Status """
		query = """ update `tabSync Error Log` set is_synced='Yes' where sync_docname in ({names})
					and is_synced<>'Yes' """.format(
					table=entity_type,
					names=",".join(["'%s'"%entity for entity in synced_entities])
				)
		frappe.db.sql(query)

	entities = list(set(entities) - set(synced_entities))
	return entities

def get_entity_from_magento(url, entity_type=None, entity_id=None):
	""" get Item, Customer, Sales Order from Magento """

	if not all([entity_type, entity_id]):
		return

	headers = {"Content-Type": "application/json", "Accept": "application/json"}
	oauth = GetOauthDetails()
	response = requests.get(url=url, headers=headers, auth=oauth)

	if response.status_code != 200:
		""" error while fetching item, create scheduler log"""
		msg = "Error While fetching missing %s #%s"%(entity_type, entity_id)
		create_scheduler_exception(msg, "get_entity_from_magento", obj=json.dumps(response.json() or {}))
		return None
		
	elif not response.json():
		""" create log entity not available in magento """
		error = "can not find %s #%s in magento, please contact administrator"
		log_sync_error(
				entity_type, entity_id, response,
				error, "get_entity_from_magento",
				force_stop=True
			)
		return None
	else:
		return response.json()

def notifiy_stopped_entities_status():
	""" Notify User about stopped entities """

	filters = { "is_synced": "Stopped" }
	fields = ["name", "sync_doctype", "sync_docname", "sync_attempts"]
	entities = frappe.db.get_values("Sync Error Log", filters, fields, as_dict=True)
	if not entities:
		return

	recipients = frappe.get_hooks("error_report_email", app_name="erpnext")
	subject = "Magento >> ERP Sync Failed"
	template = "templates/emails/failed_synced.html"

	frappe.sendmail(recipients=recipients, subject=subject,
			message=frappe.get_template(template).render({"entities": entities}))

	# update notification sent status
	query = """ update `tabSync Error Log` set notification_sent='Yes' where
				name in ({names}) """.format(
					names=",".join(["'%s'"%entity.get("name") for entity in entities])
				)
	frappe.db.sql(query)