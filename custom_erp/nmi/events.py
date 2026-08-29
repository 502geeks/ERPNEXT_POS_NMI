import frappe
from frappe import _
from frappe.utils import flt
from custom_erp.nmi.api import (
    void_nmi_return,
    refund_nmi_return,
)
from custom_erp.nmi.api import void_payment, refund_payment

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

    # ---------------------------------------------------------
    # Current return amount
    # ---------------------------------------------------------
    current_return_amount = abs(doc.grand_total)

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

    # Allow a small rounding tolerance
    is_full_return = abs(
        original_amount - total_return_amount
    ) < 0.01

    has_previous_return = len(previous_returns) > 0

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

    elif not is_full_return:
        action = "REFUND"
        reason = "Partial return"

    elif nmi_txn.is_settled:
        action = "REFUND"
        reason = "NMI transaction is settled"

    else:
        action = "VOID"
        reason = "Full return and NMI transaction is not settled"


    
    if action == "VOID":
        result = void_payment(nmi_txn.name)

    elif action == "REFUND":
        result = refund_payment(
            nmi_txn.name,
            current_return_amount
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

    


# def handle_sales_invoice_submit(doc, method=None):
#     # Normal Sales Invoice
#     if not doc.is_return:
#         link_nmi_payment(doc, method)
#         return

#     # Return Sales Invoice
#     if not doc.return_against:
#         return

#     original_invoice = frappe.get_doc(
#         "Sales Invoice",
#         doc.return_against
#     )

#     nmi_payment_transaction = original_invoice.get(
#         "custom_nmi_payment_transaction"
#     )

#     if not nmi_payment_transaction:
#         frappe.logger("nmi").info(
#             f"Return {doc.name}: Original invoice "
#             f"{original_invoice.name} has no NMI linkage."
#         )
#         return

#     nmi_txn = frappe.get_doc(
#         "NMI Payment Transaction",
#         nmi_payment_transaction
#     )

#     frappe.logger("nmi").info(
#         f"""
#         NMI RETURN DETECTED
#         Return Invoice: {doc.name}
#         Original Invoice: {original_invoice.name}
#         NMI Payment Transaction: {nmi_txn.name}
#         NMI Transaction ID: {nmi_txn.nmi_transaction_id}
#         Is Settled: {nmi_txn.is_settled}
#         Settlement Time: {nmi_txn.settlement_time}
#         Return Grand Total: {doc.grand_total}
#         """
#             )