import frappe
from frappe import _
from frappe.utils import flt


def link_nmi_payment(doc, method=None):
    nmi_payment_transaction = doc.get(
        "custom_nmi_payment_transaction"
    )

    if not nmi_payment_transaction:
        return

    txn = frappe.get_doc(
        "NMI Payment Transaction",
        nmi_payment_transaction
    )

    # ---------------------------------------------
    # NMI MUST HAVE APPROVED THE TRANSACTION
    # ---------------------------------------------
    if txn.status != "Approved":
        frappe.throw(
            _(
                "NMI payment {0} is not approved."
            ).format(txn.name)
        )

    # ---------------------------------------------
    # VERIFY AMOUNT
    # ---------------------------------------------
    if abs(
        flt(txn.amount) -
        flt(doc.grand_total)
    ) > 0.01:
        frappe.throw(
            _(
                "NMI approved amount {0} does not "
                "match invoice grand total {1}."
            ).format(
                txn.amount,
                doc.grand_total
            )
        )

    # ---------------------------------------------
    # LINK FINAL ERP DOCUMENT
    # ---------------------------------------------
    txn.erp_document_type = doc.doctype
    txn.erp_document_name = doc.name
    txn.status = "ERPNext Completed"

    txn.save(ignore_permissions=True)