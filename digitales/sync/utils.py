from __future__ import unicode_literals
import json
import frappe
import requests
import datetime
from frappe import _
from frappe.utils import get_datetime, now, now_datetime
from requests_oauthlib import OAuth1 as OAuth

def date_minus_sec(date):
	return get_datetime(date) - datetime.timedelta(seconds=1)

def get_entity_and_page_count(max_date, entity_type="Item"):
	""" get total number of entities to sync and page count """

	entity_count = {
		"Item": "products_mcount",
		"Customer": "customer_mcount",
		"Sales Order": "orders_mcount"
	}
	page_count = {
		"Item": "product_pages_per_100_mcount",
		"Customer": "customer_pages_per_100_mcount",
		"Sales Order": "orders_pages_per_100_mcount"
	}

	url = "http://digitales.com.au/api/rest/mcount?start_date=%s"%max_date
	response = get_entities_from_magento(url, entity_type="Item")

	if response:
		entity_count = response.get(entity_count[entity_type])
		page_count = response.get(page_count[entity_type])

		if all([entity_type in ["Item", "Customer"], page_count >= 5]):
			page_count = 5
		elif all([entity_type == "Sales Order", page_count > 1]):
			page_count = 1

		return {
			"entity_count": entity_count,
			"page_count": page_count
		}
	else:
		return None

def get_entities_from_magento(url, entity_type=None):
	""" get Item, Customer, Sales Order from Magento """

	if not entity_type:
		return

	headers = {"Content-Type": "application/json", "Accept": "application/json"}
	oauth = GetOauthDetails()
	response = requests.get(url=url, headers=headers, auth=oauth)

	if response.status_code != 200:
		""" error while fetching item, create scheduler log"""
		msg = "Error While fetching %s(s)"%(entity_type)
		create_scheduler_exception(msg, "get_entity_from_magento", obj=json.dumps(response.json() or {}))
		return None
		
	elif not response.json():
		""" create log entity not available in magento """
		error = "empty response from magento, please contact administrator"
		create_scheduler_exception(msg, "get_entity_from_magento", obj=json.dumps(response.json() or {}))
		return None
	else:
		return response.json()

def log_sync_status(
	entity_type, count_response={},
	entities_to_sync=0, pages_to_sync=0,
	synced_entities={}, start=None,
	end=None):

	""" log Magento >> ERPNext entity sync status """

	log = frappe.new_doc("Sync Log")
	log.date = start or now_datetime()
	log.sync_end = end or now_datetime()
	log.sync_time = end - start
	log.entity_type = entity_type
	log.total_count = entities_to_sync
	log.pagewise_count = pages_to_sync - 1 if pages_to_sync else 0
	log.synced_failed = len([entity_id for entity_id, status in synced_entities.iteritems() if status.get("operation") == None])
	log.synced_count = len([entity_id for entity_id, status in synced_entities.iteritems() if status.get("operation") != None])
	log.count_api_response = json.dumps(count_response)
	log.synced_entities = json.dumps(synced_entities)
	log.sync_stat = ""
	log.save(ignore_permissions=True)

def log_sync_error(
		doctype, docname, response,
		error, method, missing_items=None,
		missing_customer=None, force_stop=False
	):

	import traceback
	name = frappe.db.get_value("Sync Error Log", { "sync_doctype": doctype, "sync_docname":docname }, "name")

	if name:
		log = frappe.get_doc("Sync Error Log", name)
	else:
		log = frappe.new_doc("Sync Error Log")
		log.sync_doctype = doctype
		log.sync_docname = "{}".format(docname)

	log.sync_attempts += 1
	log.is_synced = "Stopped" if log.sync_attempts >= 3 or force_stop else "No"

	err = log.append("errors", {})
	err.method = method
	err.error = str(error)
	err.obj_traceback = frappe.get_traceback()
	err.response = json.dumps(response)
	
	log.missing_items = json.dumps(missing_items) or ""
	log.missing_customer = missing_customer or ""
	log.magento_response = "<pre><code>%s</code></pre>"%(json.dumps(response, indent=2))
	log.save(ignore_permissions=True)

def GetOauthDetails():
	""" get auth object """
	try:
		oauth_details = frappe.db.get_value('API Configuration Page', None, '*', as_dict=1)
		oauth = OAuth(
			client_key = oauth_details.client_key,
			client_secret = oauth_details.client_secret,
			resource_owner_key = oauth_details.owner_key,
			resource_owner_secret=oauth_details.owner_secret
		)
		return oauth
	except Exception, e:
		create_scheduler_exception(e, 'GetOauthDetails', frappe.get_traceback())

#update configuration
def update_execution_date(document):
	now_plus_10 = datetime.datetime.now() + datetime.timedelta(minutes = 30)
	frappe.db.sql("""update `tabSingles` set value='%s' where doctype='API Configuration Page' and field='date'"""%(now_plus_10.strftime('%Y-%m-%d %H:%M:%S')))
	frappe.db.sql("""update `tabSingles` set value='%s' where doctype='API Configuration Page' and field='api_type'"""%(document))

def create_scheduler_exception(msg, method, obj=None):
	se = frappe.new_doc('Scheduler Log')
	se.method = method
	se.error = msg
	se.obj_traceback = cstr(obj)
	se.save(ignore_permissions=True)