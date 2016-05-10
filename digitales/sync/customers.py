import frappe
from .utils import log_sync_error, get_entities_from_magento

def create_or_update_customer(entity):
	""" create or update customer """
	try:
		organisation = entity.get('organisation').replace("'","")
		organisation = "%s(C)"%organisation if is_supplier_or_customer_group(organisation) else organisation
		name = frappe.db.get_value('Customer', organisation)
		if not name:
			customer = frappe.new_doc("Customer")
			customer.customer_name = organisation
		else:
			customer = frappe.get_doc("Customer", name)

		customer.entity_id = entity.get('entity_id')
		customer.customer_type = 'Company'
		if entity.get('group'):
			if entity.get('group').strip() == 'General':
				customer.customer_group = 'All Customer Groups'
			elif frappe.db.get_value('Customer Group', entity.get('group').strip()):
				customer.customer_group = entity.get('group').strip() or 'All Customer Groups'
			elif frappe.db.get_value('Customer', entity.get('group').strip()):
				customer.customer_group = 'All Customer Groups'
			else:
				customer.customer_group = create_customer_group(entity.get('group').strip())
		customer.territory = 'Australia'
		customer.customer_status = 'Existing'
		customer.modified_date = entity.get('updated_at')
		customer.save(ignore_permissions=True)
		if "(C)" in customer.customer_name:
			frappe.db.set_value("Cusomer", customer.name, "customer_name", organisation.replace("(C)", ""))

		create_or_update_contact(customer, entity)
		get_addresses(entity.get('entity_id'))

		# return status
		return {
			entity.get("entity_id"): {
				"operation": "Customer Created" if not name else "Customer Updated",
				"name": customer.name,
				"modified_date": entity.get("updated_at")
			}
		}
	except Exception, e:
		docname = entity.get('entity_id')
		response = entity
		log_sync_error("Customer", docname, response, e, "create_new_customer")

def is_supplier_or_customer_group(organisation):
	if frappe.db.get_value('Supplier', organisation) or frappe.db.get_value('Customer Group', organisation):
		return True
	else:
		return False

def create_customer_group(customer_group):
	group = frappe.new_doc('Customer Group')
	group.customer_group_name = customer_group
	group.parent_customer_group = 'All Customer Groups'
	group.is_group = 'No'
	group.save(ignore_permissions=True)

	return group.name or 'All Customer Group'

def create_or_update_contact(customer, entity):
	""" create or update the customer Contact """
	name = frappe.db.get_value('Contact', { 'entity_id': entity.get('entity_id') })
	if not name:
		contact = frappe.new_doc('Contact')
	else:
		contact = frappe.get_doc("Contact", name)

	if not entity.get('firstname'):
		return
	
	contact.first_name = entity.get('firstname')
	contact.last_name = entity.get('lastname')
	contact.customer = customer.name
	contact.customer_name = customer.customer_name
	contact.entity_id = entity.get('entity_id')
	contact.email_id = entity.get('email')
	contact.save(ignore_permissions=True)

def get_addresses(entity_id):
	customer = frappe.db.get_value('Contact', { 'entity_id': entity_id }, 'customer')
	if customer:
		url = "http://digitales.com.au/api/rest/customers/%s/addresses"%entity_id
		response = get_entities_from_magento(url, entity_type="Address")
		if not response:
			return

		for address in response:
			create_or_update_address(address, customer)

def create_or_update_address(address, customer):
	""" create or update the address """
	name = frappe.db.get_value('Address', { 'entity_id': address.get('entity_id') })
	if not name:
		addr = frappe.new_doc('Address')
		addr.address_title = "{} {} {}".format(
			address.get("firstname"),
			address.get("lastname"),
			address.get("entity_id")
		)
	else:
		addr = frappe.get_doc("Address", name)

	addr.address_type = get_address_type(address).get('type')
	addr.entity_id = address.get('entity_id')
	addr.address_line1 = address.get('street')[0]
	addr.address_line2 = address.get('street')[1] if len(address.get('street')) > 1 else ""
	addr.city = address.get('city')
	addr.country = frappe.db.get_value('Country', { 'code': address.get('country_id') })
	addr.state = address.get('region')
	addr.pincode = address.get('postcode')
	addr.phone = address.get('telephone') or '00000'
	addr.fax = address.get('fax')
	addr.customer = customer
	addr.customer_name = address.get('firstname')+' '+address.get('lastname')
	addr.is_primary_address = get_address_type(address).get('is_primary_address')
	addr.is_shipping_address = get_address_type(address).get('is_shipping_address')

	addr.save(ignore_permissions=True)

def get_address_type(address):
	if address.get('is_default_billing'):
		return { "type":"Billing", "is_primary_address":1, "is_shipping_address":0 }
	elif address.get('is_default_shipping'):
		return { "type":"Shipping", "is_primary_address":0, "is_shipping_address":1 }
	else:
		return { "type":"Other", "is_primary_address":0, "is_shipping_address":0 }