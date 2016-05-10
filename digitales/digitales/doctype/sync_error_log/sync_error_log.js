frappe.ui.form.on("Sync Error Log", "refresh", function(frm) {
	if(frm.doc.is_synced != "Yes") {
		frm.add_custom_button("Manual Sync", function() {
			// manually sync entity
			entity = {}
			if(frm.doc.sync_doctype == "Sales Order"){
				entity.missing_items = frm.doc.missing_items;
				entity.missing_customer = frm.doc.missing_customer;
			}
			entity.sync_docname = frm.doc.sync_docname;

			return frappe.call({
				method: "digitales.sync.sync_missing_entities.manually_sync_entity",
				args: {
					"entity_type": frm.doc.sync_doctype,
					entity: entity
				},
				callback: function(r){
					frm.reload_doc()
				}
			})
		});
	}
});