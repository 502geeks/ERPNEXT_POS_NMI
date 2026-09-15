import frappe
from frappe import _
from frappe.utils import flt
from custom_erp.nmi.api import (
    void_payment,
    refund_payment,
)



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

    frappe.logger().info(
    "NMI submit payments: %s",
    [
        {
            "mode": p.mode_of_payment,
            "amount": p.amount
        }
        for p in (doc.get("payments") or [])
    ]
    )

    credit_card_amount = 0

    for payment in doc.get("payments") or []:
        if payment.mode_of_payment == "Credit Card":
            credit_card_amount += flt(payment.amount)

    credit_card_amount = flt(
        credit_card_amount,
        2
    )

    if credit_card_amount <= 0:
        frappe.throw(
            _("No Credit Card payment amount was found on the invoice.")
        )

    if abs(
        flt(txn.amount, 2) -
        credit_card_amount
    ) > 0.01:
        frappe.throw(
            _(
                "NMI approved amount {0} does not "
                "match invoice Credit Card amount {1}."
            ).format(
                txn.amount,
                credit_card_amount
            )
        )

    # ---------------------------------------------
    # LINK FINAL ERP DOCUMENT
    # ---------------------------------------------
    txn.erp_document_type = doc.doctype
    txn.erp_document_name = doc.name
    txn.status = "ERPNext Completed"

    txn.save(ignore_permissions=True)

def handle_sales_invoice_submit(doc, method=None):
    # ---------------------------------------------------------
    # Normal Sales Invoice
    # ---------------------------------------------------------
    if not doc.is_return:
        link_nmi_payment(doc, method)
        return

    # ---------------------------------------------------------
    # Return Sales Invoice
    # ---------------------------------------------------------
    if not doc.return_against:
        return

    original_invoice = frappe.get_doc(
        "Sales Invoice",
        doc.return_against
    )

    # Check original invoice payment method
    is_card_payment = any(
        p.mode_of_payment == "Credit Card"
        for p in original_invoice.payments
    )

    nmi_payment_transaction = original_invoice.get(
        "custom_nmi_payment_transaction"
    )

    # Not an NMI card transaction
    if not is_card_payment or not nmi_payment_transaction:
        frappe.logger("nmi").info(
            f"NMI RETURN SKIPPED | "
            f"Return={doc.name} | "
            f"Original={original_invoice.name} | "
            f"Card={is_card_payment} | "
            f"NMI Link={nmi_payment_transaction}"
        )
        return

    nmi_txn = frappe.get_doc(
        "NMI Payment Transaction",
        nmi_payment_transaction
    )

    if nmi_txn.status != "ERPNext Completed":
        frappe.throw(
            "The original NMI payment is not in ERPNext Completed status. "
            "Return processing cannot continue."
        )

    # ---------------------------------------------------------
    # Current return amount
    # ---------------------------------------------------------
    current_return_amount = abs(doc.grand_total)

    current_nmi_return_amount = sum(
    abs(p.amount or 0)
    for p in doc.payments
    if p.mode_of_payment == "Credit Card"
    )

    
    original_nmi_amount = abs(
    frappe.utils.flt(nmi_txn.amount)
    )

    if current_nmi_return_amount <= 0:
        frappe.throw(
            "No Credit Card return amount was found for this NMI transaction."
        )

    if current_nmi_return_amount > original_nmi_amount + 0.01:
        frappe.throw(
            "NMI return amount cannot exceed the original NMI payment amount."
        )


    is_full_nmi_return = abs(
        original_nmi_amount - current_nmi_return_amount
    ) < 0.01

    if current_return_amount <= 0:
        frappe.throw(
            "Return amount must be greater than zero."
        )
    # ---------------------------------------------------------
    # Previous submitted ERPNext returns
    # Exclude the current return
    # ---------------------------------------------------------
    previous_returns = frappe.get_all(
        "Sales Invoice",
        filters={
            "return_against": original_invoice.name,
            "is_return": 1,
            "docstatus": 1,
            "name": ["!=", doc.name],
        },
        fields=["name", "grand_total"]
    )

    previous_return_amount = sum(
        abs(row.grand_total or 0)
        for row in previous_returns
    )

    total_return_amount = (
        previous_return_amount + current_return_amount
    )
    
    original_amount = abs(original_invoice.grand_total)

    if total_return_amount > original_amount + 0.01:
        frappe.throw(
            "Total return amount cannot exceed the original invoice amount."
        )

    # Allow a small rounding tolerance
    is_full_return = abs(
        original_amount - total_return_amount
    ) < 0.01
   

    # ---------------------------------------------------------
    # Decide VOID vs REFUND
    # ---------------------------------------------------------

    has_previous_nmi_refund = (
    nmi_txn.refund_status == "Approved"
    and (nmi_txn.refund_amount or 0) > 0
    )

    if has_previous_nmi_refund:
        frappe.throw(
            "A partial NMI refund has already been processed for this "
            "transaction. Additional automatic NMI refunds are not allowed."
    )

    elif not is_full_nmi_return:
        action = "REFUND"
        reason = "Partial NMI card return"

    else:
        action = "VOID"
        reason = "Full NMI card return - attempt NMI void"

    
    if action == "VOID":
        result = void_payment(nmi_txn.name)

        if result.get("void_status") != "Approved":
            frappe.throw(
                "NMI void was not approved. "
                "The ERPNext return cannot be completed."
            )

    elif action == "REFUND":
        result = refund_payment(
            nmi_txn.name,
            current_nmi_return_amount
        )

        if result.get("refund_status") != "Approved":
            frappe.throw(
                "NMI refund was not approved. "
                "The ERPNext return cannot be completed."
            )

    return {
        "return_invoice": doc.name,
        "original_invoice": original_invoice.name,
        "nmi_payment_transaction": nmi_txn.name,
        "nmi_transaction_id": nmi_txn.nmi_transaction_id,
        "action": action,
        "reason": reason,
        "amount": current_return_amount,
        "nmi_result": result,
    }

    
