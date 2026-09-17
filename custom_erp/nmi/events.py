import frappe
from frappe import _
from frappe.utils import flt
from custom_erp.nmi.api import (
    void_payment,
    refund_payment,
    _validate_transaction_environment,
)



def validate_sales_invoice_nmi_payment(doc, method=None):
    # Returns are handled separately by the existing return logic.
    if doc.is_return:
        return

    credit_card_amount = sum(
        flt(payment.amount or 0)
        for payment in (doc.get("payments") or [])
        if payment.mode_of_payment == "Credit Card"
    )

    credit_card_amount = flt(credit_card_amount, 2)

    # Invoice does not use the NMI-configured payment method.
    if credit_card_amount <= 0:
        return

    nmi_payment_transaction = doc.get(
        "custom_nmi_payment_transaction"
    )

    if not nmi_payment_transaction:
        frappe.throw(
            _(
                "This invoice contains a Credit Card payment of {0}, "
                "but no NMI Payment Transaction is linked. "
                "Complete the NMI payment before submitting the invoice."
            ).format(credit_card_amount)
        )

    txn = frappe.get_doc(
        "NMI Payment Transaction",
        nmi_payment_transaction
    )

    # Prevent an NMI approval from being reused by another invoice.
    if txn.erp_document_name and txn.erp_document_name != doc.name:
        frappe.throw(
            _(
                "NMI Payment Transaction {0} is already linked "
                "to {1} {2} and cannot be reused for invoice {3}."
            ).format(
                txn.name,
                txn.erp_document_type or "ERP document",
                txn.erp_document_name,
                doc.name,
            )
        )

    if txn.erp_document_type and txn.erp_document_type != doc.doctype:
        frappe.throw(
            _(
                "NMI Payment Transaction {0} belongs to document type {1} "
                "and cannot be used for {2}."
            ).format(
                txn.name,
                txn.erp_document_type,
                doc.doctype,
            )
        )

    if txn.gateway_status != "Approved":
        frappe.throw(
            _(
                "NMI Payment Transaction {0} does not have "
                "a verified gateway approval."
            ).format(txn.name)
        )
    void_status = txn.get("void_status") or "Not Requested"
    refund_status = txn.get("refund_status") or "Not Requested"

    if void_status in ("Processing", "Approved", "Unknown"):
        frappe.throw(
            _(
                "NMI Payment Transaction {0} cannot be used because "
                "its void status is {1}."
            ).format(
                txn.name,
                void_status,
            )
        )

    if refund_status in ("Processing", "Approved", "Unknown"):
        frappe.throw(
            _(
                "NMI Payment Transaction {0} cannot be used because "
                "its refund status is {1}."
            ).format(
                txn.name,
                refund_status,
            )
        )
    approved_amount = flt(txn.authorized_amount, 2)

    if approved_amount <= 0:
        frappe.throw(
            _(
                "NMI Payment Transaction {0} does not contain "
                "a valid gateway authorized amount."
            ).format(txn.name)
        )

    if abs(approved_amount - credit_card_amount) > 0.01:
        frappe.throw(
            _(
                "NMI approved amount {0} does not match "
                "invoice Credit Card amount {1}."
            ).format(
                approved_amount,
                credit_card_amount,
            )
        )
    if txn.company != doc.company:
        frappe.throw(
            _(
                "NMI Payment Transaction {0} belongs to company {1}, "
                "but this invoice belongs to company {2}."
            ).format(
                txn.name,
                txn.company,
                doc.company,
            )
        )

    if txn.currency != doc.currency:
        frappe.throw(
            _(
                "NMI Payment Transaction {0} uses currency {1}, "
                "but this invoice uses currency {2}."
            ).format(
                txn.name,
                txn.currency,
                doc.currency,
            )
        )
    if txn.customer != doc.customer:
        frappe.throw(
            _(
                "NMI Payment Transaction {0} belongs to customer {1}, "
                "but this invoice belongs to customer {2}."
            ).format(
                txn.name,
                txn.customer,
                doc.customer,
            )
        )
    if doc.is_pos:
        if not doc.pos_profile:
            frappe.throw(
                _("POS Profile is required for an NMI POS payment.")
            )

        if txn.pos_profile != doc.pos_profile:
            frappe.throw(
                _(
                    "NMI Payment Transaction {0} belongs to POS Profile {1}, "
                    "but this invoice uses POS Profile {2}."
                ).format(
                    txn.name,
                    txn.pos_profile,
                    doc.pos_profile,
                )
            )
    if not txn.device:
        frappe.throw(
            _("NMI Payment Transaction {0} has no terminal assigned.").format(
                txn.name
            )
        )

    device = frappe.get_doc("NMI Device", txn.device)

    if not device.enabled:
        frappe.throw(
            _("The NMI terminal {0} is disabled.").format(
                device.device_name
            )
        )

    if device.company != doc.company:
        frappe.throw(
            _(
                "NMI terminal {0} belongs to company {1}, "
                "but this invoice belongs to company {2}."
            ).format(
                device.device_name,
                device.company,
                doc.company,
            )
        )

    if doc.is_pos and device.pos_profile != doc.pos_profile:
        frappe.throw(
            _(
                "NMI terminal {0} belongs to POS Profile {1}, "
                "but this invoice uses POS Profile {2}."
            ).format(
                device.device_name,
                device.pos_profile,
                doc.pos_profile,
            )
        )
    # Fail closed if the approved transaction belongs to a
    # different or unknown NMI environment.
    _validate_transaction_environment(txn)

def link_nmi_payment(doc, method=None):
    nmi_payment_transaction = doc.get(
        "custom_nmi_payment_transaction"
    )

    if not nmi_payment_transaction:
        return

    # Revalidate all NMI authorization and invoice bindings
    # immediately before final ERPNext completion.
    validate_sales_invoice_nmi_payment(doc, method)
    txn = frappe.get_doc(
        "NMI Payment Transaction",
        nmi_payment_transaction
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

    
