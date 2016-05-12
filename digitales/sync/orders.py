import frappe
from .utils import log_sync_error
from frappe.utils import add_days, nowdate

def create_or_update_sales_order(entity):
	""" create new Sales Order """
	missing_items = []
	missing_customer = None
	try:
		name = frappe.db.get_value("Sales Order", { "entity_id": entity.get("entity_id") })
		customer = frappe.db.get_value('Contact', {'entity_id': entity.get('customer_id')}, 'customer')
		if name:
			raise Exception("#{} Order updated after sync".format(entity.get("increment_id")))
		if not entity.get("order_items"):
			raise Exception("#{} Order has no Items".format(entity.get("increment_id")))
		if not customer:
			missing_customer = entity.get('customer_id')
			raise Exception("{} customer is not yet syned".format(customer))

		missing_items = get_missing_items(entity.get("order_items"), entity.get("increment_id"))
		if not missing_items:
			so = frappe.new_doc("Sales Order")
			so.customer = customer
			so.entity_id = entity.get('entity_id')
			so.modified_date = entity.get('updated_at')
			so.delivery_date = add_days(nowdate(), 6)
			so.grand_total_export = entity.get('grand_total')
			so.order_number_details = entity.get('increment_id')
			so.po_no = entity.get('po_number')

			# If Order type is general then set SO order type as Standard Order
			if entity.get('order_type') == "General" or entity.get('order_type') == None:
				so.new_order_type="Standard Order"
			else:
				so.new_order_type = entity.get('order_type')

			so.set('sales_order_details', [])
			for item in entity.get('order_items'):
				soi = so.append('sales_order_details', {})
				soi.item_code = item.get("sku")
				soi.release_date_of_item = frappe.db.get("Item", item.get("sku"), "product_release_date") or ""
				soi.qty = item.get('qty_ordered') or 0
				soi.rate = item.get('price')
				soi.artist = frappe.db.get_value("Item", item.get("sku"), "artist") or ""

				# # set shipping and billing address
			set_sales_order_address(entity.get('addresses'), so)
			so.save(ignore_permissions=True)

			return {
				entity.get("entity_id"): {
					"operation": "Order Created" if not name else "Order Updated",
					"name": so.name,
					"modified_date": entity.get("updated_at")
				}
			}
	except Exception, e:
		docname = entity.get('entity_id')
		response = entity
		log_sync_error(
			"Sales Order", docname, response,
			e, "create_or_update_sales_order",
			missing_customer=missing_customer,
			missing_items=missing_items
		)

def get_missing_items(items, increment_id):
	""" check if all the items from orders are synced in ERP """
	missing_items = []
	for item in items:
		if not frappe.db.get_value('Item',item.get('sku')):
			error = Exception("%s Item from order #%s is missing or not synced"%(
						item.get('sku'),
						content[key].get("increment_id") or ""
					))
			log_sync_error("Item", i.get('sku'), content, error, "check_item_presence")
			missing_items.append(item.get('sku'))
	return missing_items

def set_sales_order_address(address_details, order):
	# Check if Address is available if it is then set addr id in SO else set None
	address_type_mapper = {'billing': 'Billing', 'shipping': 'Shipping'}
	if not address_details:
		return
	for address in address_details:
		addr_filter = { 'entity_id': address.get('customer_address_id') }
		cust_address = frappe.db.get_value('Address', addr_filter)
		if cust_address and address.get('address_type') == "billing":
			# Check the address type if billing the set to billing addr likewise for shipping
			order.customer_address = frappe.db.get_value('Address',{ 'entity_id':cust_address })
		elif cust_address and address.get('address_type') == "shipping":
			order.shipping_address_name = frappe.db.get_value('Address', { 'entity_id':cust_address })