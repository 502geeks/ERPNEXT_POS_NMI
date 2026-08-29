import json

import frappe
from frappe.utils import now_datetime

from custom_erp.nmi.client import NMIClient
from custom_erp.nmi.device import get_device


@frappe.whitelist()
def start_test_payment(pos_profile="Bridge", amount=1.00):

    amount = float(amount)

    if amount <= 0:
        frappe.throw("Payment amount must be greater than zero.")

    device = get_device(pos_profile=pos_profile)

    transaction = frappe.get_doc({
        "doctype": "NMI Payment Transaction",
        "device": device.name,
        "company": device.company,
        "pos_profile": device.pos_profile,
        "amount": amount,
        "currency": "USD",
        "status": "Created",
        "request_time": now_datetime(),
    })

    transaction.insert(ignore_permissions=True)

    client = NMIClient()

    try:
        result = client.start_sale(
            device_id=device.device_id,
            amount=amount,
            currency="USD",
            order_id=transaction.name,
        )

        request_id = result.get("id")

        if not request_id:
            frappe.throw(
                "NMI did not return a payment request ID."
            )

        transaction.payment_request_id = request_id
        transaction.status = "Sent To Terminal"
        transaction.sanitized_response = json.dumps(
            result,
            indent=2
        )

        transaction.save(ignore_permissions=True)

        return {
            "transaction": transaction.name,
            "request_id": request_id,
            "status": transaction.status,
        }

    except Exception as exc:

        transaction.status = "Error"
        transaction.response_message = str(exc)

        transaction.save(ignore_permissions=True)

        raise


# ---------------------------------------------------------
# CHECK / POLL NMI PAYMENT STATUS
# ---------------------------------------------------------

@frappe.whitelist()
def check_payment_status(transaction_name):

    transaction = frappe.get_doc(
        "NMI Payment Transaction",
        transaction_name,
    )

    if not transaction.payment_request_id:
        frappe.throw("Payment Request ID is missing.")

    device = frappe.get_doc(
        "NMI Device",
        transaction.device,
    )

    client = NMIClient()

    result = client.get_payment_status(
        device_id=device.device_id,
        request_id=transaction.payment_request_id,
    )

    transaction.sanitized_response = json.dumps(
        result,
        indent=2
    )

    status = result.get("status")

    if status in ("pending", "inFlight"):

        transaction.status = "In Flight"

    elif status in ("cancelledAtTerminal", "cancelled", "canceled"):
            transaction.status = "Cancelled"
            transaction.completed_time = now_datetime()

    elif status == "interactionComplete":

        nmi_txn = result.get("transaction") or {}

        transaction.nmi_transaction_id = str(
            nmi_txn.get("id") or ""
        )

        transaction.authorization_code = (
            nmi_txn.get("auth_code")
        )

        if nmi_txn.get("success") is True:
            transaction.status = "Approved"
        else:
            transaction.status = "Declined"

        transaction.completed_time = now_datetime()

    elif status:

        transaction.status = "Error"
    transaction.response_message = (
        f"Unhandled NMI status: {status}"
    )

    transaction.save(ignore_permissions=True)
    frappe.db.commit()
    return {
        "transaction": transaction.name,
        "status": transaction.status,
        "nmi_response": result,
    }

@frappe.whitelist()
def start_pos_payment(
    pos_profile,
    amount,
    customer=None,
    company=None,
    currency="USD"
):
    amount = frappe.utils.flt(amount)

    if amount <= 0:
        frappe.throw("Credit Card amount must be greater than zero.")

    device = get_device(pos_profile=pos_profile)

   
    transaction = frappe.get_doc({
        "doctype": "NMI Payment Transaction",
        "device": device.name,
        "customer": customer,
        "company": company or device.company,
        "pos_profile": pos_profile,
        "amount": amount,
        "currency": currency,
        "status": "Created",
        "request_time": now_datetime(),
        "erp_document_type": "POS Invoice"
    })

    transaction.insert(ignore_permissions=True)
    frappe.db.commit()

    client = NMIClient()

    try:
        result = client.start_sale(
            device_id=device.device_id,
            amount=amount,
            currency=currency,
            order_id=transaction.name
        )

        request_id = result.get("id")

        if not request_id:
            transaction.status = "Error"
            transaction.response_message = (
                "NMI did not return a payment request ID."
            )
            transaction.sanitized_response = json.dumps(
                result,
                indent=2
            )
            transaction.save(ignore_permissions=True)
            frappe.db.commit()

            frappe.throw(
                "NMI did not return a payment request ID."
            )

        transaction.payment_request_id = request_id
        transaction.status = "Sent To Terminal"
        transaction.sanitized_response = json.dumps(
            result,
            indent=2
        )

        transaction.save(ignore_permissions=True)
        frappe.db.commit()

        return {
            "transaction": transaction.name,
            "request_id": request_id,
            "status": transaction.status
        }

    except Exception as exc:
        transaction.status = "Error"
        transaction.response_message = str(exc)
        transaction.save(ignore_permissions=True)
        frappe.db.commit()
        raise


@frappe.whitelist()
def complete_erp_link(transaction_name, erp_document_type, erp_document_name):
    transaction = frappe.get_doc(
        "NMI Payment Transaction",
        transaction_name
    )

    if transaction.status != "Approved":
        frappe.throw(
            "Only approved NMI transactions can be linked to an ERP document."
        )

    transaction.erp_document_type = erp_document_type
    transaction.erp_document_name = erp_document_name
    transaction.status = "ERPNext Completed"

    transaction.save(ignore_permissions=True)
    frappe.db.commit()

    return {
        "transaction": transaction.name,
        "status": transaction.status,
        "erp_document_type": transaction.erp_document_type,
        "erp_document_name": transaction.erp_document_name
    }  

@frappe.whitelist()
def void_payment(transaction_name):
    txn = frappe.get_doc(
        "NMI Payment Transaction",
        transaction_name
    )

    # -------------------------------------------------
    # VALIDATION
    # -------------------------------------------------
    if not txn.nmi_transaction_id:
        frappe.throw(
            "NMI Transaction ID is missing."
        )

    void_status = txn.get("void_status") or "Not Requested"

    if void_status in (
        "Processing",
        "Approved"
    ):
        frappe.throw(
            "This payment already has a void request."
        )

    # Optional additional safety check
    if txn.status not in (
        "Approved",
        "ERPNext Completed"
    ):
        frappe.throw(
            "Only approved NMI payments can be voided."
        )

    # -------------------------------------------------
    # MARK VOID AS PROCESSING
    # -------------------------------------------------
    txn.set("void_status", "Processing")
    txn.set("void_request_time", now_datetime())
    txn.set("void_requested_by", frappe.session.user)

    txn.save(ignore_permissions=True)
    frappe.db.commit()

    client = NMIClient()

    try:
        # -------------------------------------------------
        # SEND VOID TO NMI
        # -------------------------------------------------
        result = client.void_transaction(
            txn.nmi_transaction_id
        )

        # -------------------------------------------------
        # STORE SANITIZED NMI RESPONSE
        # -------------------------------------------------
        txn.set(
            "void_response",
            json.dumps(
                result,
                indent=2
            )
        )

        # NMI Payment API normally returns:
        # transactionid
        # response
        # response_code
        # responsetext

        txn.set(
            "void_transaction_id",
            str(
                result.get("transactionid")
                or ""
            )
        )

        txn.set(
            "void_response_code",
            str(
                result.get("response_code")
                or ""
            )
        )

        txn.set(
            "void_response_message",
            str(
                result.get("responsetext")
                or ""
            )
        )

        # -------------------------------------------------
        # MAP NMI RESPONSE
        # -------------------------------------------------
        nmi_response = str(
            result.get("response")
            or ""
        )

        if nmi_response == "1":
            txn.set(
                "void_status",
                "Approved"
            )
        else:
            txn.set(
                "void_status",
                "Declined"
            )

        txn.set(
            "void_completed_time",
            now_datetime()
        )

        txn.save(ignore_permissions=True)
        frappe.db.commit()

        return {
            "transaction": txn.name,
            "original_nmi_transaction_id":
                txn.nmi_transaction_id,
            "void_status":
                txn.get("void_status"),
            "void_transaction_id":
                txn.get("void_transaction_id"),
            "response_code":
                txn.get("void_response_code"),
            "response_message":
                txn.get("void_response_message"),
            "nmi_response": result
        }

    except Exception as exc:
        txn.set(
            "void_status",
            "Error"
        )

        txn.set(
            "void_response_message",
            str(exc)
        )

        txn.set(
            "void_completed_time",
            now_datetime()
        )

        txn.save(ignore_permissions=True)
        frappe.db.commit()

        raise


@frappe.whitelist()
def refund_payment(transaction_name, amount):

    txn = frappe.get_doc(
        "NMI Payment Transaction",
        transaction_name
    )

    amount = frappe.utils.flt(amount)

    if not txn.nmi_transaction_id:
        frappe.throw("NMI Transaction ID is missing.")

    if txn.status not in (
        "Approved",
        "ERPNext Completed"
    ):
        frappe.throw(
            "Only approved NMI payments can be refunded."
        )

    if amount <= 0:
        frappe.throw(
            "Refund amount must be greater than zero."
        )

    if amount > frappe.utils.flt(txn.amount):
        frappe.throw(
            "Refund amount cannot exceed the original payment amount."
        )

    refund_status = (
        txn.get("refund_status")
        or "Not Requested"
    )

    if refund_status == "Processing":
        frappe.throw(
            "A refund is already processing for this payment."
        )

    txn.set("refund_status", "Processing")
    txn.set("refund_amount", amount)
    txn.set("refund_request_time", now_datetime())
    txn.set("refund_requested_by", frappe.session.user)

    txn.save(ignore_permissions=True)
    frappe.db.commit()

    client = NMIClient()

    try:
        result = client.refund_transaction(
            txn.nmi_transaction_id,
            amount
        )

        txn.set(
            "refund_response",
            json.dumps(result, indent=2)
        )

        txn.set(
            "refund_transaction_id",
            str(
                result.get("transactionid")
                or ""
            )
        )

        txn.set(
            "refund_response_code",
            str(
                result.get("response_code")
                or ""
            )
        )

        txn.set(
            "refund_response_message",
            str(
                result.get("responsetext")
                or ""
            )
        )

        if str(result.get("response") or "") == "1":
            txn.set("refund_status", "Approved")
        else:
            txn.set("refund_status", "Declined")

        txn.set(
            "refund_completed_time",
            now_datetime()
        )

        txn.save(ignore_permissions=True)
        frappe.db.commit()

        return {
            "transaction": txn.name,
            "refund_status":
                txn.get("refund_status"),
            "refund_amount":
                txn.get("refund_amount"),
            "refund_transaction_id":
                txn.get("refund_transaction_id"),
            "response_code":
                txn.get("refund_response_code"),
            "response_message":
                txn.get("refund_response_message"),
            "nmi_response": result
        }

    except Exception as exc:
        txn.set("refund_status", "Error")
        txn.set(
            "refund_response_message",
            str(exc)
        )
        txn.set(
            "refund_completed_time",
            now_datetime()
        )

        txn.save(ignore_permissions=True)
        frappe.db.commit()

        raise

def void_nmi_return(return_doc, original_invoice, nmi_txn):
    client = NMIClient()

    result = client.void_transaction(
        nmi_txn.nmi_transaction_id
    )

    return {
        "action": "VOID",
        "return_invoice": return_doc.name,
        "nmi_transaction_id": nmi_txn.nmi_transaction_id,
        "result": result,
    }

def refund_nmi_return(
    return_doc,
    original_invoice,
    nmi_txn,
    refund_amount
):
    client = NMIClient()

    result = client.refund_transaction(
        nmi_txn.nmi_transaction_id,
        refund_amount
    )

    return {
        "action": "REFUND",
        "return_invoice": return_doc.name,
        "nmi_transaction_id": nmi_txn.nmi_transaction_id,
        "refund_amount": refund_amount,
        "result": result,
    }