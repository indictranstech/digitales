import frappe
from frappe.utils import now, get_datetime, now_datetime
from .utils import (date_minus_sec, get_entity_and_page_count, get_entities_from_magento,
	log_sync_status, update_execution_date, create_scheduler_exception) 
from digitales.sync import (create_or_update_item, create_or_update_customer, create_or_update_sales_order)

def sync_entity_from_magento():
	""" check and decide which entity to sync """
	conf = frappe.get_doc("API Configuration Page", "API Configuration Page")
	if get_datetime(now()) > get_datetime(conf.date):
		get_and_sync_entities(api_type=conf.api_type)

def get_and_sync_entities(api_type="Product"):
	""" get and sync the Item, Customer, Sales Order """
	count = {}
	sync_stat = {}
	page_count = 0
	total_entities_to_sync = 0
	start = end = now_datetime()
	entity_map = { "Product": "Item", "Customer": "Customer", "Order": "Sales Order" }
	next_sync_type = { "Product": "Customer", "Customer": "Order", "Order": "Product" }
	entity_url = { "Item": "products", "Customer": "customers", "Sales Order": "orders" } 
	sync_methods = {
		"Item": create_or_update_item,
		"Customer": create_or_update_customer,
		"Sales Order": create_or_update_sales_order
	}
	entity_type = entity_map[api_type]

	try:
		query = "select max(modified_date) as max_date from `tab{}`".format(entity_type)
		max_date = frappe.db.sql(query, as_dict=True)
		max_date = "1991-09-07 05:43:13" if not max_date else max_date[0].get("max_date")
		count = get_entity_and_page_count(max_date, entity_type=entity_type)
		if not count:
			return

		total_entities_to_sync = count.get("entity_count")
		page_count = count.get("page_count") + 1 if count.get("page_count") else 0
		print total_entities_to_sync, page_count

		url = "http://digitales.com.au/api/rest/{}?filter[1][attribute]=updated_at".format(entity_url[entity_type])
		url += "&filter[1][gt]={}&page={}&limit=100&order=updated_at&dir=asc"
		for idx in xrange(1, page_count):
			response = get_entities_from_magento(url.format(date_minus_sec(max_date), idx), entity_type=entity_type)
			if not response:
				continue
			sync_stat.update({ entity_id: { "modified_date": entity.get("updated_at") } for entity_id, entity in response.iteritems() })
			for entity_id, entity in response.iteritems():
				status = sync_methods[entity_type](entity)
				if status: sync_stat.update(status)
				frappe.db.commit()
	except Exception, e:
		create_scheduler_exception(e , sync_methods[entity_type].func_name, frappe.get_traceback())
		print frappe.get_traceback()
	finally:
		end = now_datetime()
		log_sync_status(
			entity_type, count_response=count, entities_to_sync=total_entities_to_sync, 
			pages_to_sync=page_count, synced_entities=sync_stat, start=start,
			end=end
		)
		update_execution_date(next_sync_type[api_type])