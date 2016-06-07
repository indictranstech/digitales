from __future__ import unicode_literals
import frappe
from frappe.widgets.reportview import get_match_cond
from frappe.utils import add_days, cint, cstr, date_diff, rounded, flt, getdate, nowdate, \
	get_first_day, get_last_day,money_in_words, now, nowtime
from frappe import _
from frappe.model.db_query import DatabaseQuery
from requests_oauthlib import OAuth1 as OAuth
from datetime import datetime
from time import sleep
import requests
import json
import datetime
import time
import itertools

def create_purchase_order(doc,method):
	check_duplicate_item_code(doc)
	check_ispurchase_item(doc,method)
	return "Done"

def check_duplicate_item_code(doc):
	duplicate_item = []
	s = [duplicate_item.append(d.item_code) for d in doc.get('sales_order_details')]
	for item_code in duplicate_item:
		if duplicate_item.count(item_code) > 1:
			frappe.throw(_("Item code {0} is added twise, merge the qty against same Item Code").format(item_code))

def check_ispurchase_item(doc,method):
	for d in doc.get('sales_order_details'):
		if frappe.db.get_value("Item",{'is_purchase_item':'Yes','name':d.item_code},'name'):
			supplier_validate(d.item_code)
			if frappe.db.get_value("Item",{'is_stock_item':'Yes','name':d.item_code},'name'):
				Stock_Availability(doc,d)
				assign_extra_qty_to_other(d)
			else:
				create_purchase_order_record(doc,d, d.qty)

def Stock_Availability(so_doc, child_args):
	bin_details = frappe.db.get_value('Bin', {'item_code': child_args.item_code, 'warehouse': child_args.warehouse}, '*', as_dict=1)
	if bin_details:
		assign_qty = get_assigned_qty(child_args.item_code, child_args.warehouse) #get assigned qty
		bin_qty = flt(bin_details.actual_qty) - flt(assign_qty) # To calculate the available qty
		if flt(bin_qty) > 0.0 and flt(bin_qty) >= flt(child_args.qty):
			sal = create_stock_assignment_document(child_args, so_doc.name, child_args.qty)
			# Stock in Hand
			make_history_of_assignment(sal, so_doc.transaction_date, "Stock In Hand", "", child_args.qty)
			child_args.assigned_qty = child_args.qty
		else:
			assigned_qty = bin_qty
			po_qty = (flt(child_args.qty) - flt(assigned_qty)) if flt(assigned_qty) > 0.0 else flt(child_args.qty)
			if flt(assigned_qty) > 0.0:
				sal = create_stock_assignment_document(child_args, so_doc.name, assigned_qty)
				# Stock in Hand
				make_history_of_assignment(sal, so_doc.transaction_date, "Stock In Hand", "", assigned_qty)
				child_args.assigned_qty = assigned_qty
			if flt(po_qty) > 0.0:
				new_po_qty = get_po_qty(child_args.item_code, child_args.warehouse) + flt(bin_details.actual_qty) - flt(bin_details.reserved_qty) # if negative then make po
				if new_po_qty < 0:
					create_purchase_order_record(so_doc, child_args, flt(new_po_qty * -1))

def get_assigned_qty(item_code, warehouse):
	assign_qty = frappe.db.sql(''' select sum(ifnull(assigned_qty,0)) - sum(ifnull(delivered_qty,0)) from `tabSales Order Item` where item_code = "%s"
		and warehouse ="%s" and docstatus=1 and ifnull(qty,0) >= ifnull(assigned_qty,0)'''%(item_code, warehouse), as_list = 1)
	if assign_qty:
		return assign_qty[0][0] or 0.0
	return 0.0

def assign_extra_qty_to_other(data):
	assign_qty = get_assigned_qty(data.item_code, data.warehouse)
	bin_details = frappe.db.get_value('Bin', {'item_code': data.item_code, 'warehouse': data.warehouse}, '*', as_dict=1)
	qty = flt(bin_details.actual_qty) - flt(assign_qty)
	if cint(qty) > 0:
		sales_order = get_item_SODetails(data.item_code)
		if sales_order:
			create_StockAssignment_AgainstSTopSOItem(data, sales_order, qty)

def get_po_qty(item_code, warehouse=None):
	cond = 'poi.warehouse ="%s"'%(warehouse) if warehouse else '1=1'
	qty = frappe.db.sql(''' select sum(ifnull(poi.qty,0)-ifnull(poi.received_qty,0)) from `tabPurchase Order Item` poi, `tabPurchase Order` po
		where poi.parent = po.name and po.status <> 'Stopped' and ifnull(poi.stop_status, 'No') <> 'Yes' and poi.docstatus <> 2 and poi.item_code = "%s" and %s'''%(item_code, cond), as_list=1)
	qty = flt(qty[0][0]) if qty else 0.0
	return qty

def supplier_validate(item_code):
	if not frappe.db.get_value('Item', item_code, 'default_supplier'):
		frappe.throw(_("Default supplier is not defined against the item code {0}").format(item_code))

def create_purchase_order_record(doc,d,qty):
	supplier=frappe.db.sql('''select default_supplier from `tabItem` where
								name="%s"'''%d.item_code,as_list=1)
	if supplier:
		purchase_order=frappe.db.sql('''select name from `tabPurchase Order` where supplier="%s"
										 and docstatus=0'''%supplier[0][0],as_list=1)
		if purchase_order:
			purchase_order_item=frappe.db.sql('''select item_code from `tabPurchase Order Item`
													 where parent="%s" and item_code="%s"'''
													 %(purchase_order[0][0],d.item_code),as_list=1)
			if purchase_order_item:
				for item in purchase_order_item:
					if item[0]==d.item_code:
						purchase_order_qty=frappe.db.sql('''select qty,rate from `tabPurchase Order Item`
													 where parent="%s" and item_code="%s"'''
													 %(purchase_order[0][0],d.item_code),as_list=1)

						qty_new= qty + flt(purchase_order_qty[0][0])
						update_qty(doc,d,item[0],purchase_order[0][0],qty_new,purchase_order_qty[0][1])
					else:
						child_entry=update_child_entry(doc,d,purchase_order[0][0],qty)
			else:
				child_entry=update_child_entry(doc,d,purchase_order[0][0],qty)
			purchase_order = purchase_order[0][0]
		else:
			purchase_order = create_new_po(doc,d,supplier[0][0],qty)

		if purchase_order:
			qty = qty if flt(qty) < flt(d.qty) else flt(d.qty)
			frappe.db.sql(''' update `tabSales Order Item` set po_data = "%s", po_qty="%s" where name = "%s"'''%(purchase_order, qty, d.name))
	else:
		frappe.throw("Supplier must be specify for items in Item Master Form.")

def create_stock_assignment_document(args, sales_order, assigned_qty):
	sa = frappe.new_doc('Stock Assignment Log')
	sa.item_name = args.item_name
	sa.sales_order = sales_order
	sa.ordered_qty = frappe.db.get_value('Sales Order Item', {'item_code': args.item_code, 'parent': sales_order}, 'qty')
	sa.assign_qty = assigned_qty
	sa.purchase_receipt_no = args.parent if args.doctype == 'Purchase Receipt Item' else ''
	sa.item_code = args.item_code
	sa.media  = frappe.db.get_value("Item",args.item_code,'item_group')
	sa.customer_name = frappe.db.get_value('Sales Order',sa.sales_order,'customer_name')

	sa.save(ignore_permissions=True)
	return sa.name

def delete_stock_assignment(doc, method):
	st_error=stop_error(doc)
	if(st_error=='true'):
		frappe.throw(_("You can not cancel this sales order"))
	else:
		stl = frappe.db.sql(""" select * from `tabStock Assignment Log` where sales_order = '%s'"""%(doc.name), as_dict=1)
		if stl:
			for data in stl:
				delete_stl(data)
		reduce_po(doc)
		update_stock_assigned_qty_to_zero(doc)

def stop_error(doc):
	st_error=''
	for data in doc.get('sales_order_details'):
		if(data.stop_status=="Yes"):
			st_error='true'
			break
	return st_error

def delete_stl(args):
	frappe.db.sql(''' delete from `tabDocument Stock Assignment` where parent="%s"	'''%(args.name))
	frappe.db.sql(''' delete from `tabStock Assignment Log` where name="%s"	'''%(args.name))

def update_stock_assigned_qty_to_zero(doc):
	for data in doc.get('sales_order_details'):
		frappe.db.sql(''' update `tabSales Order Item` set po_data = (select true from dual where 1=2), po_qty=0.0, assigned_qty=0.0 where name = "%s"	'''%(data.name))
		data.assigned_qty = 0.0
		data.po_data = ''
		data.po_qty = 0.0

def reduce_po(doc):
	for data in doc.get('sales_order_details'):
		# get draft order(s)
		po_qty = data.qty - data.delivered_qty or 0
		query = '''
					select distinct poi.parent, poi.qty from `tabPurchase Order Item` poi, `tabPurchase Order` po
					where poi.parent=po.name and po.docstatus=0 and po.status="Draft" and poi.item_code='%s'
					and poi.warehouse='%s'
				'''%(data.item_code, data.warehouse)
		results = frappe.db.sql(query)
		order = [result[0] for result in results] if results else None
		if order and len(order)==1:
			po_details = frappe.db.get_value('Purchase Order Item', {
				'parent': order[0],
				'item_code': data.item_code,
				'docstatus': 0
				}, '*', as_dict=1)

			update_child_table(po_details, data)
			update_parent_table(po_details)
		elif order and len(order) > 1:
			frappe.throw("Multiple Draft PO (%s) present for Item %s"%(",".join(order), data.item_code))

def update_child_table(po_details, data):
	if po_details:
		# po_qty = flt(data.po_qty) or flt(data.qty)
		po_qty = data.qty - data.delivered_qty or 0
		qty = flt(po_details.qty) - po_qty
		if flt(qty) >= 1.0:
			frappe.db.sql(""" update `tabPurchase Order Item` set qty = '%s' where name ='%s'"""%(qty, po_details.name))
		elif flt(qty) <= 0.0:
			delete_document('Purchase Order Item', po_details.name)

def update_parent_table(po_details):
	if po_details:
		count = frappe.db.sql(''' select ifnull(count(*),0) from `tabPurchase Order Item` where parent = "%s"	'''%(po_details.parent), as_list=1)
		if count:
			if count[0][0] == 0:
				obj = frappe.get_doc('Purchase Order', po_details.parent)
				obj.delete()

def stock_assignment(doc,method):
	for pr_details in doc.get('purchase_receipt_details'):
		if frappe.db.get_value("Item",{'is_stock_item':'Yes','name':pr_details.item_code},'name'):
			qty = flt(pr_details.qty)
			sales_order = get_SODetails(pr_details.item_code, pr_details.warehouse)
			if sales_order:
				check_stock_assignment(qty, sales_order, pr_details)

# So list which has stock not assign or partially assign
def get_SODetails(item_code, warehouse):
	return frappe.db.sql('''select s.parent as parent,ifnull(s.qty,0)-ifnull(s.assigned_qty,0) AS qty,
				s.assigned_qty as assigned_qty from `tabSales Order Item` s inner join `tabSales Order` so
				on s.parent=so.name where s.item_code="%s" and so.docstatus=1 and ifnull(s.stop_status, 'No') <> 'Yes' and
				ifnull(s.qty,0)>ifnull(s.assigned_qty,0) and so.status!='Stopped' and s.warehouse="%s"
				order by so.priority,so.creation'''%(item_code, warehouse),as_dict=1)

def check_stock_assignment(qty, sales_order, pr_details):
	for so_details in sales_order:
		if flt(qty) > 0.0 and flt(so_details.qty)>0:
			stock_assigned_qty = so_details.qty if flt(qty) >= flt(so_details.qty) else flt(qty)
			qty = (flt(qty) - flt(so_details.qty)) if flt(qty) >= flt(so_details.qty) else 0.0
			create_stock_assignment(stock_assigned_qty , so_details, pr_details)

def create_stock_assignment(stock_assigned_qty, sales_order_data, pr_details):
	sal = frappe.db.get_value('Stock Assignment Log', {'sales_order': sales_order_data.parent, 'item_code': pr_details.item_code},'*', as_dict=1)
	stock_assigned_qty = sales_order_data.qty if sales_order_data.qty < stock_assigned_qty else stock_assigned_qty
	if sal:
		sal_name = update_stock_assigned_qty(sal, stock_assigned_qty, pr_details)
	else:
		sal_name = create_stock_assignment_document(pr_details, sales_order_data.parent, stock_assigned_qty)
		make_history_of_assignment(sal_name,nowdate(),"Purchase Receipt", pr_details.parent, stock_assigned_qty)

def update_stock_assigned_qty(stock_assignment_details, assigned_qty, pr_details):
	doc_qty = get_document_STOCK_qty(stock_assignment_details.name)
	if cint(doc_qty) == cint(stock_assignment_details.assign_qty):
		assign_qty = cint(stock_assignment_details.assign_qty) + cint(assigned_qty)
	else:
		assign_qty = cint(doc_qty) + cint(assigned_qty)
	obj = frappe.get_doc('Stock Assignment Log', stock_assignment_details.name)
	obj.assign_qty = assign_qty
	update_doc_SAL(obj, pr_details, assigned_qty)
	obj.save(ignore_permissions=True)
	return stock_assignment_details.name

def update_doc_SAL(obj, pr_details, assigned_qty):
	sal = obj.append('document_stock_assignment', {})
	sal.document_no = pr_details.parent
	sal.qty = assigned_qty
	sal.created_date = nowdate()
	sal.document_type = 'Purchase Receipt'
	return True

def get_document_STOCK_qty(name):
	sum_qty = 0.0
	qty = frappe.db.sql(''' select ifnull(sum(qty),0) from `tabDocument Stock Assignment`
		where parent = "%s"'''%(name), as_list=1)
	if qty:
		sum_qty = qty[0][0]
	return sum_qty

def make_history_of_assignment(sal, date, doc_type, pr_name, qty):
	sal= frappe.get_doc('Stock Assignment Log', sal)
	sal_child = sal.append('document_stock_assignment', {})
	sal_child.created_date = nowdate();
	sal_child.document_type = doc_type
	sal_child.document_no = pr_name
	sal_child.qty = qty
	sal.save(ignore_permissions=True)

def stock_cancellation(doc,method):
	cancel_all_child_table(doc)
	cancel_parent_table(doc)
	# check_assigned_qty(doc)

def check_assigned_qty(doc):
	for data in doc.get('purchase_receipt_details'):
		bin_details = frappe.db.get_value('Bin', {'item_code': data.item_code, 'warehouse': data.warehouse}, '*', as_dict=1)
		if bin_details:
			assign_qty = get_assigned_qty(data.item_code, data.warehouse) #get assigned qty
			actual_qty = flt(bin_details.actual_qty)
			if flt(assign_qty) > flt(actual_qty):
				frappe.throw(_("Not allowed to cancel, the available stock of item {0} is assigned to the sales order").format(data.item_code))

def cancel_all_child_table(doc):
	sal_details = frappe.db.sql(''' select * from `tabDocument Stock Assignment`
		where document_no = "%s"'''%(doc.name), as_dict=1)
	if sal_details:
		for sal in sal_details:
			obj = frappe.get_doc('Stock Assignment Log', sal.parent)
			obj.assign_qty = flt(obj.assign_qty) - flt(sal.qty)
			to_remove_obj = []
			for d in obj.get('document_stock_assignment'):
				if d.name == sal.name:
					to_remove_obj.append(d)
			[obj.remove(d) for d in to_remove_obj]
			obj.save(ignore_permissions=True)

def cancel_parent_table(doc):
	sal_data = frappe.db.sql(''' select * from `tabStock Assignment Log` where assign_qty = 0''', as_dict=1)
	if sal_data:
		for data in sal_data:
			obj = frappe.get_doc('Stock Assignment Log', data.name)
			obj.delete()

def delete_document(table, name):
	frappe.db.sql(''' delete from `tab%s` where name = "%s" '''%(table, name))

def update_so_assign_qty(args):
	so_details = frappe.db.get_value('Sales Order Item', {'parent': args.sales_order, 'item_code': args.item_code}, 'assigned_qty') or 0.0
	qty = (flt(so_details) - flt(args.qty)) if flt(so_details) >= flt(args.qty) else 0.0
	frappe.db.sql(''' update `tabSales Order Item` set assigned_qty = "%s" where parent = "%s" and
		item_code = "%s" '''%(qty, args.sales_order, args.item_code))

def create_new_po(doc,d,supplier,qty):
	po = frappe.new_doc('Purchase Order')
	po.supplier= supplier
	po.currency = frappe.db.get_value('Supplier', supplier, 'default_currency')
	e = po.append('po_details', {})
	e.item_code=d.item_code
	e.item_name=d.item_name
	e.description=d.description
	e.qty= qty
	e.uom=d.stock_uom
	e.conversion_factor=1
	e.rate=d.rate
	e.amount=d.amount
	e.base_rate=d.rate
	e.base_amount=d.amount
	e.warehouse=d.warehouse
	e.schedule_date=nowdate()
	e.product_release_date = d.release_date_of_item
	po.save(ignore_permissions=True)
	return po.name

def update_child_entry(doc,d,purchase_order,qty):
	doc1 = frappe.get_doc("Purchase Order", purchase_order)
	poi = doc1.append('po_details', {})
	poi.item_code=d.item_code
	poi.item_name=d.item_name
	poi.description=d.description
	poi.qty=qty
	poi.uom=d.stock_uom
	poi.conversion_factor=1
	poi.rate=d.rate
	poi.amount=d.amount
	poi.base_rate=d.rate
	poi.base_amount=d.amount
	poi.warehouse=d.warehouse
	poi.schedule_date=nowdate()
	poi.product_release_date = d.release_date_of_item
	doc1.save(ignore_permissions=True)

def update_qty(doc,d,item,purchase_order,qty,rate):
	amount=rate*qty
	frappe.db.sql("""update `tabPurchase Order Item` set qty='%s', amount='%s'
						where parent='%s' and item_code='%s'"""
							%(qty,amount,purchase_order,item))

def update_so_details(doc,d,item,purchase_order):
	doc2 = frappe.get_doc("Purchase Order", purchase_order)
	so = doc2.append('so_item_detail', {})
	so.item_code=item
	so.qty=d.qty
	so.sales_order_name=doc.name
	doc2.save(ignore_permissions=True)

def update_stock_assignment_log_on_submit(doc,method):
	for d in doc.get('delivery_note_details'):
		sales_order_name=frappe.db.sql("""select s.against_sales_order from
										`tabDelivery Note Item` s inner join `tabDelivery Note` so
											on s.parent=so.name where s.item_code='%s'
												and so.docstatus=1 and s.parent='%s'
													order by so.creation"""
														%(d.item_code,doc.name),as_list=1)
		if sales_order_name:
			delivery_note_name=frappe.db.sql(""" select delivery_note  from `tabStock Assignment Log` where
							sales_order='%s' and item_code='%s' and delivery_note is not null"""%(sales_order_name[0][0],d.item_code))

			if not delivery_note_name:
				frappe.db.sql("""update `tabStock Assignment Log`
								set delivered_qty='%s', delivery_note='%s'
									where sales_order='%s' and item_code='%s'"""
										%(d.qty,doc.name,sales_order_name[0][0],d.item_code))
			else:
				delivery_note = doc.name
				delivery_note_details=frappe.db.sql("""select delivered_qty from `tabStock Assignment Log`
												where sales_order='%s' and item_code='%s'"""%(sales_order_name[0][0],d.item_code))
				if delivery_note_details:
					qty=cint(delivery_note_details[0][0])+d.qty
					frappe.db.sql("""update `tabStock Assignment Log`
								set delivered_qty='%s', delivery_note='%s'
									where sales_order='%s' and item_code='%s'"""
										%(qty,delivery_note,sales_order_name[0][0],d.item_code))

def update_delivery_note(doc,method):
	for d in doc.get('delivery_note_details'):
		if(d.stop_status=="Yes"):
			frappe.throw(_("Item code {0} is stopped please delete it.").format(d.item_code))

def update_sales_invoice(doc,method):
	for d in doc.get('entries'):
		if(d.stop_status=="Yes"):
			frappe.throw(_("Item code {0} is stopped please delete it.").format(d.item_code))


def update_stock_assignment_log_on_cancel(doc,method):
	update_delivery_note(doc, method)
	for d in doc.get('delivery_note_details'):
		name=frappe.db.sql(""" select name,delivered_qty from `tabStock Assignment Log` where
							sales_order='%s' and item_code='%s'"""%(d.against_sales_order,d.item_code))
		if name:
			delivery_note=frappe.db.sql("""select delivery_note from `tabStock Assignment Log` where
									name='%s'"""%name[0][0])
			delivery_note_name=cstr(delivery_note[0][0]).split(", ")
			if d.parent in delivery_note_name:
				delivery_note_name.remove(d.parent)
				qty=cint(name[0][1])-d.qty
				if name:
					frappe.db.sql("""update `tabStock Assignment Log`
								set delivered_qty='%s',delivery_note='%s' where item_code='%s'"""%(qty,','.join(delivery_note_name),d.item_code))

def validate_qty_on_submit(doc,method):
	qty_count = 0
	for d in doc.get('delivery_note_details'):
		qty_count += d.qty
		if not d.assigned_qty>=d.qty:
			frappe.throw("Delivered Quantity must be less than or equal to assigned_qty for item_code='"+d.item_code+"'")
	doc.total_qty = qty_count

def check_uom_conversion(item):
	stock_uom=frappe.db.sql(""" select stock_uom from `tabItem` where name='%s'"""%item,as_list=1)
	if stock_uom:
		uom_details= frappe.db.sql("""select ifnull(count(idx),0) from `tabUOM Conversion Detail` where uom='%s' and parent='%s'
		"""%(stock_uom[0][0],item),as_list=1)
		if uom_details:
			if uom_details[0][0]!=1:
				return False
			else:
				return True
	else:
		return False

@frappe.whitelist()
def upload():
	if not frappe.has_permission("Attendance", "create"):
		raise frappe.PermissionError

	from frappe.utils.csvutils import read_csv_content_from_uploaded_file
	from frappe.modules import scrub

	rows = read_csv_content_from_uploaded_file()
	rows = filter(lambda x: x and any(x), rows)
	if not rows:
		msg = [_("Please select a csv file")]
		return {"messages": msg, "error": msg}
	columns = [scrub(f) for f in rows[4]]
	columns[0] = "name"
	columns[3] = "att_date"
	ret = []
	error = False

	from frappe.utils.csvutils import check_record, import_doc
	attendance_dict = attendance_rowdata = {}
	for i, row in enumerate(rows[5:]):
		if not row: continue
		row_idx = i + 5
		if row[1]:
			data = row[1]
			attendance_rowdata.setdefault(data, row)
		if data in attendance_dict:
			attendance_dict[data].append([row[8], row[9]])
		else:
			attendance_dict.setdefault(data, [[row[8], row[9]]])
	if attendance_dict and attendance_rowdata:
		for r in attendance_rowdata:
			pass
	if error:
		frappe.db.rollback()
	return {"messages": ret, "error": error}

# added by pitambar
@frappe.whitelist()
def assign_stopQty_toOther(doc,item_list):
	import json
	stopping_items=item_list
	self = frappe.get_doc('Sales Order', doc)
	for data in self.get('sales_order_details'):
		if data.item_code in (stopping_items) and data.stop_status!="Yes":			# check item code in selected stopping item
			# get the draft po
			po_qty = data.qty - data.delivered_qty or 0
			query = '''
						select distinct poi.parent, poi.qty from `tabPurchase Order Item` poi, `tabPurchase Order` po
						where poi.parent=po.name and po.docstatus=0 and po.status="Draft" and poi.item_code='%s'
						and poi.warehouse='%s'
					'''%(data.item_code, data.warehouse)
			results = frappe.db.sql(query)
			order = [result[0] for result in results] if results else None
			if order and len(order)==1:
				reduce_po_item(order[0], data.item_code, po_qty)
			elif order and len(order) > 1:
				frappe.throw("Multiple Draft PO (%s) present for Item %s"%(",".join(order), data.item_code))
				# what if multiple PO are in draft

			update_so_item_status(data.item_code,data.parent)
			if flt(data.qty) > flt(data.delivered_qty):
				update_bin_qty(data.item_code,data.qty,data.delivered_qty,data.warehouse)
				qty = flt(data.assigned_qty) - flt(data.delivered_qty)
				if flt(data.assigned_qty) > 0.0:
					update_sal(data.item_code, data.parent, flt(data.delivered_qty), qty)
					sales_order = get_item_SODetails(data.item_code)
					if sales_order:
						create_StockAssignment_AgainstSTopSOItem(data, sales_order, qty, reduce_po=False)
	return "Done"

def create_StockAssignment_AgainstSTopSOItem(data, sales_order, qty, reduce_po=True):
	for so_data in sales_order:
		if flt(so_data.qty) > 0.0 and qty > 0.0:
			stock_assigned_qty = so_data.qty if flt(qty) >= flt(so_data.qty) else flt(qty)
			qty = (flt(qty) - flt(so_data.qty)) if flt(qty) >= flt(so_data.qty) else 0.0
			if flt(stock_assigned_qty) > 0.0:
				sal = frappe.db.get_value('Stock Assignment Log', {'sales_order': so_data.parent, 'item_code':data.item_code}, 'name')
				if not sal:
					sal = create_stock_assignment_document_item(data, so_data.parent, so_data.qty, stock_assigned_qty)
				else:
					sal= frappe.get_doc('Stock Assignment Log', sal)
					sal.assign_qty = cint(sal.assign_qty) + cint(stock_assigned_qty)
				make_history_of_assignment_item(sal, nowdate(), "Stock In Hand", "", stock_assigned_qty)
				sal.save(ignore_permissions=True)
				update_or_reducePoQty(so_data.parent, data.item_code, reduce_po=reduce_po)

def update_or_reducePoQty(sales_order, item_code, reduce_po=True):
	obj = frappe.get_doc('Sales Order', sales_order)
	for data in obj.get('sales_order_details'):
		if data.item_code == item_code:
			assign_qty = flt(data.qty) - flt(data.assigned_qty)
			if flt(data.po_qty) > flt(assign_qty):
				po_qty = flt(assign_qty)
			else:
				po_qty = 0.0
			frappe.db.sql(""" update `tabSales Order Item` set
				po_qty = '%s' where name ='%s'"""%(po_qty, data.name))
			if reduce_po: reduce_po_item(data.po_data, data.item_code, data.assigned_qty)

def reduce_po_item(purchase_order,item, assign_qty):
	po_details = frappe.db.get_value('Purchase Order Item', {
						'parent': purchase_order,
						'item_code': item,
						'docstatus': 0
					}, '*', as_dict=1)
	if po_details:
		update_child_table_item(po_details, assign_qty)
		update_parent_table_item(po_details)

def update_child_table_item(po_details, po_qty):
	qty = flt(po_details.qty) - po_qty
	if flt(qty) >= 1.0:
		frappe.db.sql(""" update `tabPurchase Order Item` set qty = '%s' where name ='%s'"""%(qty, po_details.name))
	elif flt(qty) <= 0.0:
		delete_document('Purchase Order Item', po_details.name)

def update_parent_table_item(po_details):
	count = frappe.db.sql(''' select ifnull(count(*),0) from `tabPurchase Order Item` where parent = "%s"	'''%(po_details.parent), as_list=1)
	if count:
		if count[0][0] == 0:
			obj = frappe.get_doc('Purchase Order', po_details.parent)
			obj.delete()

def create_stock_assignment_document_item(args, sales_order, qty, assigned_qty):
	sa = frappe.new_doc('Stock Assignment Log')
	sa.item_name = args.item_name
	sa.sales_order = sales_order
	sa.ordered_qty = qty
	sa.assign_qty = assigned_qty
	sa.purchase_receipt_no = args.parent if args.doctype == 'Purchase Receipt Item' else ''
	sa.item_code = args.item_code
	sa.media  = frappe.db.get_value("Item",args.item_code,'item_group')
	sa.customer_name = frappe.db.get_value('Sales Order',sa.sales_order,'customer_name')
	return sa

def make_history_of_assignment_item(sal, date, doc_type, pr_name, qty):
	sal_child = sal.append('document_stock_assignment', {})
	sal_child.created_date = nowdate();
	sal_child.document_type = doc_type
	sal_child.document_no = pr_name
	sal_child.qty = qty

def get_item_SODetails(item_c):
	return frappe.db.sql('''select s.parent as parent,ifnull(s.qty,0)-ifnull(s.assigned_qty,0) AS qty,
				s.assigned_qty as assigned_qty from `tabSales Order Item` s inner join `tabSales Order` so
				on s.parent=so.name where s.item_code="%s" and so.docstatus=1 and ifnull(s.stop_status, 'No') <> 'Yes' and
				ifnull(s.qty,0)>ifnull(s.assigned_qty,0) and so.status!='Stopped' order by so.priority,so.creation'''%(item_c),as_dict=1)

def update_bin_qty(item_code,qty,delivered_qty,warehouse):
	obj=frappe.get_doc("Bin",{"item_code":item_code,"warehouse":warehouse})
	obj.reserved_qty=flt(obj.reserved_qty)-(flt(qty) - flt(delivered_qty))
	obj.save(ignore_permissions=True)

def update_so_item_status(item_code,parent):
	frappe.db.sql(''' update `tabSales Order Item` set stop_status = "Yes" where item_code = "%s" and parent="%s"'''%(item_code,parent))
	frappe.db.sql(''' update `tabDelivery Note Item` set stop_status = "Yes" where item_code = "%s" and against_sales_order="%s" and docstatus<>2'''%(item_code,parent))
	frappe.db.sql(''' update `tabSales Invoice Item` set stop_status = "Yes" where item_code = "%s" and sales_order="%s" and docstatus<>2'''%(item_code,parent))

def create_StockAssignment_AgainstSTopSO(data, sales_order, qty):
	for so_data in sales_order:
		if flt(so_data.qty) > 0.0 and qty > 0.0:
			stock_assigned_qty = so_data.qty if flt(qty) >= flt(so_data.qty) else flt(qty)
			qty = (flt(qty) - flt(so_data.qty)) if flt(qty) >= flt(so_data.qty) else 0.0
			if flt(stock_assigned_qty) > 0.0:
				sal = frappe.db.get_value('Stock Assignment Log', {'sales_order': so_data.parent, 'item_code':data.item_code}, 'name')
				if not sal:
					sal = create_stock_assignment_document(data, so_data.parent, stock_assigned_qty)
				make_history_of_assignment(sal, nowdate(), "Stock In Hand", "", stock_assigned_qty)

def update_sal(item_code, sales_order, delivered_qty, assigned_qty):
	sal = frappe.db.get_value('Stock Assignment Log', {'item_code': item_code, 'sales_order': sales_order}, '*', as_dict=1)
	if sal:
		obj = frappe.get_doc('Stock Assignment Log', sal.name)
		if flt(assigned_qty) > 0.0 and delivered_qty > 0.0:
			obj.assign_qty = delivered_qty
			to_remove_obj = []
			for d in obj.get('document_stock_assignment'):
				if flt(assigned_qty) > 0:
					assigned_qty = flt(assigned_qty) - flt(d.qty)
					if flt(assigned_qty) <= 0.0:
						d.qty = assigned_qty * -1
					else:
						to_remove_obj.append(d)
			[obj.remove(d) for d in to_remove_obj]
			obj.save(ignore_permissions=True)
		else:
			frappe.db.sql(''' update `tabSales Order Item` set assigned_qty=0 where item_code ="%s"
				and parent ="%s"'''%(item_code, sales_order))
			obj.delete()

def make_csv():
	import csv
	present_list = []
	with open('/home/indictrance/Desktop/finaltryitem2.csv', 'rb') as f:
		reader = csv.reader(f)
		for item in reader:
			item_name=frappe.db.get_value('Item', item[0], 'name')
			if item_name:
				present_list.append([item_name])

def validate_sales_invoice(doc, method):
	set_terms_and_condition(doc)
	set_sales_order_details(doc)
	set_contract_details(doc)

def set_terms_and_condition(si_obj):
	si_obj.tc_name = 'Net 30' if not si_obj.tc_name else si_obj.tc_name
	if si_obj.tc_name:
		si_obj.terms = frappe.db.get_value('Terms and Conditions', si_obj.tc_name, 'terms')

def set_sales_order_details(doc):
	if doc.entries and doc.entries[0].sales_order:
		so = frappe.get_doc("Sales Order", doc.entries[0].sales_order)

		doc.po_no = so.po_no if not doc.po_no else doc.po_no
		doc.new_order_type = so.new_order_type if not doc.new_order_type else doc.new_order_type
		budget = so.budget if not doc.budget else doc.budget

def set_contract_details(doc):
	from erpnext.selling.doctype.customer.customer import get_contract_details

	contract_details = get_contract_details(doc.customer)
	doc.contract_number = contract_details.get("contract_no") if not doc.contract_number else doc.contract_number
	doc.tender_group = contract_details.get("tender_group") if not doc.tender_group else doc.tender_group

@frappe.whitelist()
def get_artist(item_code):
	return frappe.db.get_value('Item', {'name':item_code}, 'artist') or ''

def set_artist(doc, method):
	for i in doc.item_details:
		art = frappe.db.get_value('Item', {'name':i.item_code}, 'artist') or ''
		i.artist=art
		
def fetch_barcode_supplier(doc, method):
	for item in doc.sales_order_details:
		item.barcode = frappe.db.get_value("Item", item.item_code, "barcode")
		item.default_supplier = frappe.db.get_value("Item", item.item_code, "default_supplier")

# def check_APItime():
# 	time = frappe.db.sql("""select value from `tabSingles` where doctype='API Configuration Page' and field in ('date','api_type')""",as_list=1)
# 	if time:
# 		dates= list(itertools.chain.from_iterable(time))
# 		api_date = datetime.datetime.strptime(dates[1], '%Y-%m-%d %H:%M:%S')
# 		if datetime.datetime.now() > api_date and dates[0] =='Product':
# 			GetItem()
# 			get_missing_items()
# 		elif datetime.datetime.now() > api_date and dates[0]=='Customer':
# 			GetCustomer()
# 			get_missing_customers()
# 		elif datetime.datetime.now() > api_date and dates[0]=='Order':
# 			GetOrders()
# 			get_missing_orders()

# def get_Data_count(max_date, document_key, headers, oauth_data):
# 	r = requests.get(url='http://digitales.com.au/api/rest/mcount?start_date='+cstr(max_date)+'', headers=headers, auth=oauth_data)
# 	total_page_count = json.loads(r.content)
# 	if total_page_count.get(document_key) > 0:
# 		return total_page_count.get(document_key)
# 	return 0

# def GetItem():
# 	update_execution_date('Customer')
# 	h = {'Content-Type': 'application/json', 'Accept': 'application/json'}
# 	oauth = GetOauthDetails()
# 	max_item_date = '1991-09-07 05:43:13'
# 	max_date = frappe.db.sql(""" select max(modified_date) as max_date from `tabItem` """,as_list=1)
# 	if max_date[0][0]!=None:
# 		max_item_date = max_date[0][0]
# 	max_item_date = max_item_date.split('.')[0] if '.' in max_item_date else max_item_date
# 	max_item_date = (datetime.datetime.strptime(max_item_date, '%Y-%m-%d %H:%M:%S') - datetime.timedelta(seconds=1)).strftime('%Y-%m-%d %H:%M:%S')
# 	status = get_SyncItemsCount(max_item_date, h, oauth)	

# def get_SyncItemsCount(max_date, header, oauth_data):
# 	count = get_Data_count(max_date, 'product_pages_per_100_mcount', header, oauth_data)
# 	original_count = count
# 	count = 15 if count > 15 else count
# 	pagewise_count = {}
# 	if count > 0:
# 		for index in range(1, count+1):
# 			products_count = get_products_from_magento(index, max_date,header, oauth_data)
# 			pagewise_count[index] = products_count
# 		make_sync_log(json.dumps(pagewise_count), original_count, count, max_date)

# def get_products_from_magento(page, max_date, header, oauth_data):
# 	products_count = 0
# 	if page:
# 		r = requests.get(url='http://digitales.com.au/api/rest/products?filter[1][attribute]=updated_at&filter[1][gt]=%s&page=%s&limit=100&order=updated_at&dir=asc'%(max_date, page), headers=header, auth=oauth_data)
# 		product_data = json.loads(r.content)
# 		products_count = len(product_data)
# 		if products_count > 0:
# 			for index in product_data:
# 				name = frappe.db.get_value('Item', product_data[index].get('sku'), 'name')
# 				if name:
# 					update_item(name, index, product_data)
# 					check_item_price(name,index,product_data)
# 				else:
# 					create_item(index, product_data)
# 	return products_count

# def make_sync_log(pagewise_count, original_count, synced_count, max_date):
# 	slog = frappe.new_doc('Sync Log')
# 	slog.pagewise_count = pagewise_count
# 	slog.total_count = original_count
# 	slog.synced_count = synced_count
# 	slog.date = max_date
# 	slog.save(ignore_permissions=True)

# def create_item(i,content):
# 	try:
# 		item = frappe.new_doc('Item')
# 		item.item_code = content[i].get('sku')
# 		create_new_product(item,i,content)
# 		item.save(ignore_permissions=True)
# 		check_item_price(item.name,i,content)
# 	except Exception, e:
# 		docname = content[i].get('sku')
# 		response = content
# 		log_sync_error("Item", docname, response, e, "create_item")

# def update_item(name,i,content):
# 	try:
# 		item = frappe.get_doc("Item", name)
# 		create_new_product(item,i,content)
# 		item.save(ignore_permissions=True)
# 	except Exception, e:
# 		docname = content[i].get('sku')
# 		response = content
# 		log_sync_error("Item", docname, response, e, "update_item")

# def check_item_price(name,i,content):
# 	if content[i].get('price'):
# 		price_list=get_price_list()
# 		if price_list:
# 			item_price_list_name=frappe.db.get_value('Item Price',{'item_code':name,'price_list':price_list},'name')
# 			if item_price_list_name:
# 				update_price_list(item_price_list_name,i,content,price_list)
# 			else:
# 				create_price_list(name,i,content,price_list)

# def get_price_list():
# 		price_list=frappe.db.sql("""select value from `tabSingles` where doctype='Configuration Page'
# 					and field='price_list'""",as_list=1)
# 		if price_list:
# 			return price_list[0][0]
# 		else:
# 			frappe.msgprint("Please specify default price list in Configuration Page",raise_exception=1)

# def update_price_list(price_list_name,i,content,price_list):
# 	try:
# 		item_price = frappe.get_doc("Item Price", price_list_name)
# 		create_new_item_price(item_price,i,content,price_list)
# 		item_price.save(ignore_permissions=True)
# 	except Exception, e:
# 		docname = content[i].get('sku')
# 		response = content
# 		log_sync_error("Item", docname, response, e, "update_price_list")

# def create_price_list(item,i,content,price_list):
# 	try:
# 		item_price=frappe.new_doc("Item Price")
# 		create_new_item_price(item_price,i,content,price_list)
# 		item_price.save(ignore_permissions=True)
# 	except Exception, e:
# 		docname = content[i].get('sku')
# 		response = content
# 		log_sync_error("Item", docname, response, e, "update_price_list")

# def create_new_item_price(item_price,i,content,price_list):
# 	item_price.price_list=price_list
# 	item_price.item_code=content[i].get('sku')
# 	item_price.price_list_rate=content[i].get('price')
# 	return True

# def create_new_product(item,i,content):
# 	item.event_id=i
# 	item.artist = content[i].get('artist')
# 	item.item_name=content[i].get('name') or content[i].get('sku')
# 	item.item_group = media_type(content[i].get('media'))
# 	item.description = 'Desc: ' + content[i].get('short_description') if content[i].get('short_description') else content[i].get('sku')
# 	warehouse=get_own_warehouse()
# 	item.default_warehouse=warehouse
# 	if content[i].get('barcode') and not frappe.db.get_value('Item', {'barcode':content[i].get('barcode')}, 'name'):
# 		item.barcode = content[i].get('barcode')
# 	item.modified_date = content[i].get('updated_at')
# 	item.distributor = content[i].get('distributor')
# 	item.product_release_date = content[i].get('release_date')
# 	item.default_supplier = get_supplier(content[i].get('distributor'))
# 	item.expense_account, item.income_account = default_ExpenxeIncomeAccount(item.item_group)
	# return True

# def media_type(itemgroup):
# 	media = 'Products'
# 	if itemgroup:
# 		media = itemgroup
# 		media = media.split(',')[0]
# 		if not frappe.db.get_value('Item Group', media, 'name'):
# 			create_new_itemgroup(media)
# 	return media

# def default_ExpenxeIncomeAccount(media):
# 	# Check item group and assign the default expence and income account
# 	expense_account, income_account = '', ''
# 	if media in ['DVD', 'CD', 'BLURAY', 'Graphic Novel', 'CDROM', 'Audio Book', 'Manga',
# 				'Online Resource', 'Blu-Ray', 'PC Games', 'Hardcover', 'Playstation 3',
# 				'Xbox 360', 'Xbox One', 'Playstation 4', 'Nintendo Wii U', '2CD and DVD',
# 				'Graphics', '3D', 'UV', 'BLURAY, 3D', 'Nintendo 3DS', 'Nintendo Wii', 'DVD, UV',
# 				'BLURAY, DVD', 'BLURAY, DVD, UV', 'Playstation Vita', 'Paperback']:
# 		expense_account = "5-1100 Cost of Goods Sold : COGS Stock"
# 		income_account = "4-1100 Product Sales"

# 	return expense_account, income_account

# def get_supplier(supplier):
# 	temp = ''
# 	if supplier:
# 		if frappe.db.get_value('Customer', supplier, 'name'):
# 			supplier = supplier + '(s)'
# 			temp = supplier
# 		name = supplier if frappe.db.get_value('Supplier', supplier, 'name') else create_supplier(supplier)
# 		if temp:
# 			update_supplier(supplier)
# 		return name
# 	return ''

# def update_supplier(supplier):
# 	try:
# 		obj = frappe.get_doc('Supplier', supplier)
# 		obj.supplier_name = supplier.replace('(s)', '')
# 		obj.save(ignore_permissions=True)
# 	except Exception,e:
# 		create_scheduler_exception(e , 'method name update_supplier: ' ,supplier)
# 	return True

# def create_supplier(supplier):
# 	try:
# 		sl = frappe.new_doc('Supplier')
# 		sl.supplier_name = supplier
# 		sl.supplier_type = 'Stock supplier' if frappe.db.get_value('Supplier Type', 'Stock supplier', 'name') else create_supplier_type()
# 		sl.save(ignore_permissions=True)
# 	except Exception, e:
# 		create_scheduler_exception(e , 'method name create_supplier: ' , supplier)
# 	return sl.name

# def create_supplier_type():
# 	try:
# 		st = frappe.new_doc('Supplier Type')
# 		st.supplier_type = 'Stock supplier'
# 		st.save(ignore_permissions=True)
# 	except Exception, e:
# 		create_scheduler_exception(e, 'method name create_supplier_type: ' , 'supplier type')
# 	return st.name

# def create_new_itemgroup(item_group):
# 	try:
# 		itemgroup=frappe.new_doc('Item Group')
# 		itemgroup.parent_item_group='All Item Groups'
# 		itemgroup.item_group_name=item_group
# 		itemgroup.is_group='No'
# 		itemgroup.save()
# 	except Exception, e:
# 		create_scheduler_exception(e , 'method name create_new_itemgroup: ' , item_group)
# 	return itemgroup.name or 'Products'

# def get_own_warehouse():
# 	warehouse=frappe.db.sql("""select value from `tabSingles` where doctype='Configuration Page'
# 				and field='own_warehouse'""",as_list=1)
# 	if warehouse:
# 		return warehouse[0][0]
# 	else:
# 		frappe.msgprint("Please specify default own warehouse in Configuration Page",raise_exception=1)

# def GetOauthDetails():
# 	try:
# 		oauth_details = frappe.db.get_value('API Configuration Page', None, '*', as_dict=1)
# 		oauth = OAuth(
# 			client_key = oauth_details.client_key,
# 			client_secret = oauth_details.client_secret,
# 			resource_owner_key = oauth_details.owner_key,
# 			resource_owner_secret=oauth_details.owner_secret
# 		)
# 		return oauth
# 	except Exception, e:
# 		create_scheduler_exception(e, 'GetOauthDetails', frappe.get_traceback())

# #update configuration
# def update_execution_date(document):
# 	now_plus_10 = datetime.datetime.now() + datetime.timedelta(minutes = 30)
# 	frappe.db.sql("""update `tabSingles` set value='%s' where doctype='API Configuration Page' and field='date'"""%(now_plus_10.strftime('%Y-%m-%d %H:%M:%S')))
# 	frappe.db.sql("""update `tabSingles` set value='%s' where doctype='API Configuration Page' and field='api_type'"""%(document))

# def GetCustomer():
# 	update_execution_date('Order')
# 	h = {'Content-Type': 'application/json', 'Accept': 'application/json'}
# 	oauth = GetOauthDetails()
# 	max_customer_date = '1988-09-07 05:43:13'
# 	max_date = frappe.db.sql(""" select max(modified_date) as max_date from `tabCustomer` """,as_list=1)
# 	if max_date[0][0]!=None:
# 		max_customer_date = max_date[0][0]
# 	max_customer_date = max_customer_date.split('.')[0] if '.' in max_customer_date else max_customer_date
# 	max_customer_date = (datetime.datetime.strptime(max_customer_date, '%Y-%m-%d %H:%M:%S') - datetime.timedelta(seconds=1)).strftime('%Y-%m-%d %H:%M:%S')
# 	status=get_SyncCustomerCount(max_customer_date, h, oauth)

# def get_SyncCustomerCount(max_date, header, oauth_data):
# 	count = get_Data_count(max_date, 'customer_pages_per_100_mcount', header, oauth_data)
# 	count = 25 if count > 30 else count
# 	if count > 0:
# 		for index in range(1, count+1):
# 			get_customers_from_magento(index, max_date,header, oauth_data, 'missed')

# def get_customers_from_magento(page, max_date, header, oauth_data,type_of_data=None):
# 	try:
# 		if page:
# 			r = requests.get(url='http://digitales.com.au/api/rest/customers?filter[1][attribute]=updated_at&filter[1][gt]=%s&page=%s&limit=100&order=updated_at&dir=asc'%(max_date, page), headers=header, auth=oauth_data)
# 			customer_data = json.loads(r.content)
# 			if len(customer_data) > 0:
# 				for index in customer_data:
# 					name = frappe.db.get_value('Customer', customer_data[index].get('organisation').replace("'",""), 'name')
# 					if name:
# 						update_customer(name, index, customer_data)
# 						GetAddress(customer_data[index].get('entity_id'))
# 					else:
# 						create_customer(index, customer_data)
# 						GetAddress(customer_data[index].get('entity_id'))
# 	except Exception, e:
# 		create_scheduler_exception(e , 'Method name get_customers_from_magento' , customer_data[index].get('organisation'))

# def create_customer(i,content):
# 	temp_customer = ''
# 	customer = frappe.new_doc('Customer')
# 	if frappe.db.get_value('Supplier',content[i].get('organisation').replace("'",""),'name') or frappe.db.get_value('Customer Group',content[i].get('organisation').replace("'",""),'name'):
# 		temp_customer= customer.customer_name = cstr(content[i].get('organisation')).replace("'","") + '(C)'
# 	else:
# 		customer.customer_name=cstr(content[i].get('organisation')).replace("'","")
# 	if not frappe.db.get_value('Customer', customer.customer_name, 'name'):
# 		create_new_customer(customer,i,content)
# 	create_contact(customer,i,content)
# 	if temp_customer:
# 		update_customer_name(temp_customer)

# def update_customer_name(customer_name):
# 	try:
# 		customer = frappe.get_doc("Customer", customer_name)
# 		customer.customer_name= customer_name.replace("(C)","")
# 		customer.save(ignore_permissions=True)
# 	except Exception, e:
# 		create_scheduler_exception(e , 'Method name update_customer_name: ' , customer_name)

# def update_customer(customer,i ,content):
# 	customer = frappe.get_doc("Customer", customer)
# 	create_new_customer(customer,i,content)
# 	contact=frappe.db.sql("""select name from `tabContact` where entity_id='%s'"""%content[i].get('entity_id'),as_list=1)
# 	if contact:
# 		update_contact(customer,i,content,contact[0][0])
# 	else:
# 		create_contact(customer,i,content)

# def update_contact(customer,i,content,contact):
# 	contact = frappe.get_doc("Contact", contact)
# 	create_customer_contact(customer,i,content,contact)

# def create_contact(customer,i,content):
# 	contact=frappe.new_doc('Contact')
# 	if not frappe.db.get_value('Contact', {'entity_id':content[i].get('entity_id')}, 'name'):
# 		create_customer_contact(customer,i,content,contact)

# def create_new_customer(customer,i,content):
# 	import itertools
# 	try:
# 		customer.entity_id = content[i].get('entity_id')
# 		customer.customer_type = 'Company'
# 		if content[i].get('group'):
# 			if content[i].get('group').strip() == 'General':
# 				customer.customer_group= 'All Customer Groups'
# 			elif frappe.db.get_value('Customer Group', content[i].get('group').strip(), 'name'):
# 				customer.customer_group=content[i].get('group').strip() or 'All Customer Groups'
# 			elif frappe.db.get_value('Customer', content[i].get('group').strip(), 'name'):
# 				customer.customer_group = 'All Customer Groups'
# 			else:
# 				customer_group=create_customer_group(content[i].get('group').strip())
# 				customer.customer_group=customer_group
# 		customer.territory = 'Australia'
# 		customer.customer_status = 'Existing'
# 		customer.modified_date=content[i].get('updated_at')
# 		customer.save(ignore_permissions=True)
# 	except Exception, e:
# 		docname = content[i].get('entity_id')
# 		response = content
# 		log_sync_error("Customer", docname, response, e, "create_new_customer")

# def create_customer_contact(customer,i,content,contact):
# 	try:
# 		if content[i].get('firstname'):
# 			contact.first_name=content[i].get('firstname')
# 			contact.last_name=content[i].get('lastname')
# 			contact.customer= customer.customer_name
# 			contact.customer_name= frappe.db.get_value('Customer', contact.customer, 'customer_name')
# 			contact.entity_id = content[i].get('entity_id')
# 			contact.email_id=content[i].get('email')
# 			contact.save(ignore_permissions=True)
# 	except Exception, e:
# 		docname = content[i].get('entity_id')
# 		response = content
# 		log_sync_error("Customer", docname, response, e, "create_customer_contact")

# def create_new_contact(customer,i,content):
# 	try:
# 		contact=frappe.new_doc('Contact')
# 		if content[i].get('firstname'):
# 			contact.first_name=content[i].get('firstname')
# 			contact.last_name=content[i].get('lastname')
# 			contact.customer= customer
# 			contact.customer_name=customer
# 			contact.entity_id = content[i].get('entity_id')
# 			contact.email_id=content[i].get('email')
# 			contact.save(ignore_permissions=True)
# 	except Exception, e:
# 		create_scheduler_exception(e , 'Method name create_new_contact: ', content[i])

# def create_customer_group(i):
# 	try:
# 		cg=frappe.new_doc('Customer Group')
# 		cg.customer_group_name = i
# 		cg.parent_customer_group='All Customer Groups'
# 		cg.is_group='No'
# 		cg.save(ignore_permissions=True)
# 	except Exception, e:
# 		create_scheduler_exception(e , 'Method name create_customer_group: ', i)
# 	return cg.name or 'All Customer Group'

# def sync_existing_customers_address():
# 	offset = frappe.db.get_value('API Configuration Page', None, 'offset_limit')
# 	if offset:
# 		customer_data = frappe.db.sql(''' Select entity_id from tabContact order by creation limit %s, 100'''%(offset), as_dict=1)
# 		frappe.db.sql(''' update `tabSingles` set value = "%s" where doctype = "API Configuration Page" and field="offset_limit"'''%(cint(offset)+100))
# 		if customer_data:
# 			for data in customer_data:
# 				GetAddress(data.entity_id)

# def GetAddress(entity_id):
# 	try:
# 		h = {'Content-Type': 'application/json', 'Accept': 'application/json'}
# 		oauth = GetOauthDetails()
# 		customer=frappe.db.get_value('Contact',{'entity_id':entity_id},'customer')
# 		if customer:
# 			r = requests.get(url='http://digitales.com.au/api/rest/customers/%s/addresses'%(entity_id), headers=h, auth=oauth)
# 			cust_address_data=json.loads(r.content)
# 			if cust_address_data:
# 				for data in cust_address_data:
# 					address_entity_id = data.get('entity_id')
# 					address_name = frappe.db.get_value('Address', {'entity_id': address_entity_id}, 'name')
# 					if not address_name:
# 						create_new_address(data, customer)
# 					else:
# 						update_customer_address(data, address_name, customer)
# 	except Exception, e:
# 		create_scheduler_exception(e, 'Get Address', entity_id)

# def create_scheduler_exception(msg, method, obj=None):
# 	se = frappe.new_doc('Scheduler Log')
# 	se.method = method
# 	se.error = msg
# 	se.obj_traceback = cstr(obj)
# 	se.save(ignore_permissions=True)

# def create_new_address(data, customer):
# 	try:
# 		obj = frappe.new_doc('Address')
# 		obj.address_title = cstr(data.get('firstname'))+' '+cstr(data.get('lastname')) +' '+cstr(data.get('entity_id'))
# 		customer_address(data, obj, customer)
# 		obj.save(ignore_permissions=True)
# 	except Exception, e:
# 		create_scheduler_exception(e, 'create_new_address', customer)

# def update_customer_address(data, address_name, customer):
# 	try:
# 		obj = frappe.get_doc('Address', address_name)
# 		customer_address(data, obj, customer)
# 		obj.save(ignore_permissions=True)
# 	except Exception, e:
# 		create_scheduler_exception(e ,'Method name update_customer_address: ', customer)

# def customer_address(data, obj, customer):
# 	obj.address_type = get_address_type(data).get('type') # Address Type is Billing or is Shipping??
# 	obj.entity_id = cstr(data.get('entity_id'))
# 	obj.address_line1 = cstr(data.get('street')[0])
# 	obj.address_line2 = cstr(data.get('street')[1]) if len(data.get('street')) > 1 else ""
# 	obj.city = cstr(data.get('city'))
# 	obj.country = frappe.db.get_value('Country', {'code': data.get('country_id')}, 'name')
# 	obj.state = cstr(data.get('region'))
# 	obj.pincode = cstr(data.get('postcode'))
# 	obj.phone = cstr(data.get('telephone')) or '00000'
# 	obj.fax = cstr(data.get('fax'))
# 	obj.customer = customer
# 	obj.customer_name = cstr(data.get('firstname'))+' '+cstr(data.get('lastname'))
# 	obj.is_primary_address = get_address_type(data).get('is_primary_address')
# 	obj.is_shipping_address = get_address_type(data).get('is_shipping_address')

# def get_address_type(content):
# 	if content.get('is_default_billing'):
# 		return {"type":"Billing", "is_primary_address":1, "is_shipping_address":0}
# 	elif content.get('is_default_shipping'):
# 		return {"type":"Shipping", "is_primary_address":0, "is_shipping_address":1}
# 	else:
# 		return {"type":"Other", "is_primary_address":0, "is_shipping_address":0}

# def GetOrders():
# 	update_execution_date('Product')
# 	h = {'Content-Type': 'application/json', 'Accept': 'application/json'}
# 	oauth = GetOauthDetails()
# 	max_order_date = '2001-09-07 05:43:13'
# 	max_date = frappe.db.sql(""" select max(modified_date) as max_date from `tabSales Order` """,as_list=1)
# 	if max_date[0][0]!=None:
# 		max_order_date = max_date[0][0]
# 	max_order_date = max_order_date.split('.')[0] if '.' in max_order_date else max_order_date
# 	max_order_date = (datetime.datetime.strptime(max_order_date, '%Y-%m-%d %H:%M:%S') - datetime.timedelta(seconds=1)).strftime('%Y-%m-%d %H:%M:%S')
# 	status=get_SyncOrdersCount(max_order_date, h, oauth)

# def get_SyncOrdersCount(max_date, header, oauth_data):
# 	count = get_Data_count(max_date, 'orders_pages_per_100_mcount', header, oauth_data)
# 	count = 25 if count > 30 else count
# 	if count > 0:
# 		for index in range(1, count+1):
# 			get_orders_from_magento(index, max_date,header, oauth_data, 'missed')

# addr_details = {}

# def get_orders_from_magento(page, max_date, header, oauth_data,type_of_data=None):
# 	if page:
# 		r = requests.get(url='http://digitales.com.au/api/rest/orders?filter[1][attribute]=updated_at&filter[1][gt]=%s&page=%s&limit=100&order=updated_at&dir=asc'%(max_date, page), headers=header, auth=oauth_data)
# 		order_data = json.loads(r.content)
# 		if len(order_data) > 0:
# 			for index in order_data:
# 				try:
# 					customer = frappe.db.get_value('Contact', {'entity_id': order_data[index].get('customer_id')}, 'customer')
# 					if customer:
# 						order = frappe.db.get_value('Sales Order', {'entity_id': order_data[index].get('entity_id')}, 'name')
# 						if not order:
# 							create_order(index,order_data,customer)
# 					else:
# 						docname = order_data[index].get('entity_id')
# 						response = order_data
# 						log_sync_error("Sales Order", docname, response, e, "get_orders_from_magento", missing_customer=order_data[index].get('customer_id'))
# 						# frappe.throw(_('Customer with id {0} not found in erpnext').format(order_data[index].get('customer_id')))
# 				except Exception, e:
# 					create_scheduler_exception(e, 'get_orders_from_magento', index)
# 	return True

# def create_or_update_customer_address(address_details, customer):
# 	address_type_mapper = {'billing': 'Billing', 'shipping': 'Shipping'}
# 	if address_details:
# 		for address in address_details:
# 			address_type = address_type_mapper.get(address.get('address_type'))
# 			if not frappe.db.get_value('Address',{'address_title': address.get('firstname') +' '+address.get('lastname') +' '+address.get('street'), 'address_type': address_type},'name'):
# 				create_address_forCustomer(address, customer, address_type)
# 			else:
# 				cust_address=frappe.db.get_value('Address',{'address_title': address.get('firstname') +' '+address.get('lastname') +' '+address.get('street'), 'address_type': address_type},'name')
# 				update_address_forCustomer(cust_address,address,customer,address_type)

# def create_address_forCustomer(address_details, customer, address_type):
# 	try:
# 		cad = frappe.new_doc('Address')
# 		cad.address_title = address_details.get('firstname')+' '+address_details.get('lastname') +' '+address_details.get('street')
# 		cad.address_type = address_type
# 		cad.address_line1 = address_details.get('street')
# 		cad.city = address_details.get('city')
# 		cad.state = address_details.get('region')
# 		cad.pincode = address_details.get('postcode')
# 		cad.phone = address_details.get('telephone') or '00000'
# 		cad.customer = customer
# 		cad.save(ignore_permissions=True)
# 	except Exception, e:
# 		create_scheduler_exception(e , 'Method name create_address_forCustomer: ' , customer)

# def update_address_forCustomer(cust_address,address_details, customer, address_type):
# 	try:
# 		cad = frappe.get_doc('Address',cust_address)
# 		cad.address_type = address_type
# 		cad.address_line1 = address_details.get('street')
# 		cad.city = address_details.get('city')
# 		cad.state = address_details.get('region')
# 		cad.pincode = address_details.get('postcode')
# 		cad.phone = address_details.get('telephone') or '00000'
# 		cad.customer = customer
# 		cad.save(ignore_permissions=True)
# 	except Exception, e:
# 		create_scheduler_exception(e , 'Method name update_address_forCustomer: ', customer)

# def update_order(order,i,content,customer):
# 	try:
# 		order = frappe.get_doc("Sales Order", order)
# 		create_new_order(order,i,content,customer)
# 		order.save(ignore_permissions=True)
# 		print order.name
# 	except Exception, e:
# 		docname = content[i].get('entity_id')
# 		response = content
# 		log_sync_error("Sales Order", docname, response, e, "update_order")

# def create_order(i,content,customer):
# 	missing_items = []
# 	try:
# 		if content[i].get('order_items'):
# 			missing_items = check_item_presence(i,content)
# 			if not missing_items:
# 				order = frappe.new_doc('Sales Order')
# 				create_new_order(order,i,content,customer)
# 				order.save(ignore_permissions=True)
# 			else:
# 				frappe.throw(_("Few of the Items in Order #%s are missing or not yet synced"%(content[i].get("increment_id"))))
# 	except Exception, e:
# 		docname = content[i].get('entity_id')
# 		response = content
# 		log_sync_error("Sales Order", docname, response, e, "create_order", missing_items=missing_items)

# def create_new_order(order,index,content,customer):
# 	from datetime import date
# 	from dateutil.relativedelta import relativedelta
# 	delivery_date = date.today() + relativedelta(days=+6)
# 	order.customer=customer
# 	order.entity_id=content[index].get('entity_id')
# 	order.modified_date=content[index].get('updated_at')
# 	order.delivery_date=delivery_date
# 	order.grand_total_export=content[index].get('grand_total')
# 	order.order_number_details = content[index].get('increment_id')
# 	order.po_no=content[index].get('po_number')

# 	# If Order type is general then set SO order type as Standard Order
# 	if content[index].get('order_type') == "General" or content[index].get('order_type') == None:
# 		order.new_order_type="Standard Order"
# 	else:
# 		order.new_order_type=content[index].get('order_type')
# 	for i in content[index].get('order_items'):
#  		create_child_item(i,order)

#  	# # set shipping and billing address
#  	set_sales_order_address(content[index].get('addresses'),order)

# def set_sales_order_address(address_details, order):
# 	# Check if Address is available if it is then set addr id in SO else set None
# 	address_type_mapper = {'billing': 'Billing', 'shipping': 'Shipping'}
# 	if address_details:
# 		for address in address_details:
# 			addr_filter = {'entity_id': cstr(address.get('customer_address_id'))}
# 			cust_address = frappe.db.get_value('Address',addr_filter,'name')
# 			if cust_address:
# 				# Check the address type if billing the set to billing addr likewise for shipping
# 				if cstr(address.get('address_type')) == "billing":
# 					order.customer_address = frappe.db.get_value('Address',{'entity_id':cust_address},'name')
# 				if cstr(address.get('address_type')) == "shipping":
# 					order.shipping_address_name = frappe.db.get_value('Address',{'entity_id':cust_address},'name')

# def check_item_presence(key,content):
# 	missing_items = []
# 	for i in content[key].get('order_items'):
# 		if not frappe.db.get_value('Item',i.get('sku'),'name'):
# 			error = Exception("%s Item from order #%s is missing or not synced"%(
# 						i.get('sku'),
# 						content[key].get("increment_id") or ""
# 					))
# 			log_sync_error("Item", i.get('sku'), content, error, "check_item_presence")
# 			missing_items.append(i.get('sku'))
# 	return missing_items

# def create_child_item(i,order):
# 	oi = order.append('sales_order_details', {})
# 	oi.item_code=i['sku']
# 	if i['sku']:
# 		item_release_date=frappe.db.sql("""select product_release_date from `tabItem`
# 								where name='%s'"""%i['sku'],as_list=1)
# 		if item_release_date:
# 			oi.release_date_of_item=item_release_date[0][0]
# 	oi.qty=i['qty_ordered']
# 	oi.rate=i['price']
# 	art = frappe.db.get_value("Item", i['sku'],"artist")
# 	if art:
# 		oi.artist = art
# 	# oi.amount=i['row_total_incl_tax']
# 	return True