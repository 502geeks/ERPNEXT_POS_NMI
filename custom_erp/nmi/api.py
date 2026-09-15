import json

import frappe
from frappe.utils import now_datetime

from custom_erp.nmi.client import (
    NMIClient,
    NMIAmbiguousPaymentError,
)
from custom_erp.nmi.device import get_device


from frappe import _


def _require_authenticated_user():
    """Reject Guest access to payment APIs."""
    if frappe.session.user == "Guest":
        frappe.throw(
            _("Authentication is required to perform payment operations."),
            frappe.PermissionError,
        )


def _require_payment_permission(permission_type, transaction=None):
    """
    Enforce NMI Payment Transaction custom permission types.

    If transaction is supplied, also enforce document-level Read access.
    """
    _require_authenticated_user()

    if not frappe.has_permission(
        "NMI Payment Transaction",
        ptype=permission_type,
        user=frappe.session.user,
    ):
        frappe.throw(
            _("You are not permitted to perform this payment operation."),
            frappe.PermissionError,
        )

    if transaction and not frappe.has_permission(
        "NMI Payment Transaction",
        ptype="read",
        doc=transaction,
        user=frappe.session.user,
    ):
        frappe.throw(
            _("You do not have access to this payment transaction."),
            frappe.PermissionError,
        )



@frappe.whitelist()
def start_test_payment(pos_profile="Bridge", amount=1.00):

    _require_payment_permission("process_payment")
    amount = float(amount)

    if amount <= 0:
        frappe.throw("Payment amount must be greater than zero.")

    device = get_device(pos_profile=pos_profile)
    client = NMIClient()

    if client.environment != "Sandbox":
        frappe.throw(
            "Test payments are disabled in Production."
        )

    transaction = frappe.get_doc({
        "doctype": "NMI Payment Transaction",
        "device": device.name,
        "company": device.company,
        "pos_profile": device.pos_profile,
        "amount": amount,
        "currency": "USD",
        "status": "Created",
        "gateway_environment": client.environment,
        "request_time": now_datetime(),
    })

    transaction.insert(ignore_permissions=True)

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
            _sanitize_nmi_response(result),
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

    _require_payment_permission(
        "check_payment_status",
        transaction
    )

    if transaction.status == "ERPNext Completed":
        return {
            "transaction": transaction.name,
            "status": transaction.status,
            "erp_document_type": transaction.erp_document_type,
            "erp_document_name": transaction.erp_document_name,
            "already_completed": True,
        }

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
        _sanitize_nmi_response(result),
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
        "nmi_response": _sanitize_nmi_response(result),
    }

@frappe.whitelist()
def start_pos_payment(
    pos_profile,
    amount,
    customer=None,
    company=None,
    currency="USD",
    erp_document_type=None,
    erp_document_name=None,
    payment_allocations=None
):
    _require_payment_permission("process_payment")
    _validate_required_nmi_fields()

    # -------------------------------------------------
    # VALIDATE ERP DOCUMENT
    # -------------------------------------------------
    if not erp_document_type or not erp_document_name:
        frappe.throw(
            "ERP document is required before starting NMI payment."
        )

    if erp_document_type not in (
        "Sales Invoice",
        "POS Invoice"
    ):
        frappe.throw(
            "Unsupported ERP document type for NMI payment."
        )

    erp_doc = frappe.get_doc(
        erp_document_type,
        erp_document_name
    )

    # -------------------------------------------------
    # DUPLICATE PAYMENT PROTECTION
    # -------------------------------------------------
    existing_transaction = _get_existing_pos_payment(
        erp_document_type,
        erp_document_name
    )

    if existing_transaction:
        return _handle_existing_pos_payment(
        existing_transaction
        )

    # -------------------------------------------------
    # VALIDATE PAYMENT ALLOCATIONS
    # -------------------------------------------------
    if not payment_allocations:
        frappe.throw(
            "Payment allocations are required."
        )

    if isinstance(payment_allocations, str):
        try:
            payment_allocations = json.loads(
                payment_allocations
            )
        except Exception:
            frappe.throw(
                "Invalid payment allocations."
            )

    if not isinstance(payment_allocations, list):
        frappe.throw(
            "Payment allocations must be a list."
        )

    allocation_total = 0
    credit_card_amount = 0

    for payment in payment_allocations:

        if not isinstance(payment, dict):
            frappe.throw(
                "Invalid payment allocation entry."
            )

        mode_of_payment = payment.get(
            "mode_of_payment"
        )

        payment_amount = frappe.utils.flt(
            payment.get("amount"),
            2
        )

        if payment_amount < 0:
            frappe.throw(
                "Payment allocation cannot be negative."
            )

        allocation_total += payment_amount

        if mode_of_payment == "Credit Card":
            credit_card_amount += payment_amount

    allocation_total = frappe.utils.flt(
        allocation_total,
        2
    )

    credit_card_amount = frappe.utils.flt(
        credit_card_amount,
        2
    )

    # -------------------------------------------------
    # VERIFY AGAINST ERP GRAND TOTAL
    # -------------------------------------------------
    grand_total = frappe.utils.flt(
        abs(erp_doc.grand_total),
        2
    )

    if allocation_total != grand_total:
        frappe.throw(
            "Payment allocation mismatch. "
            f"ERPNext Grand Total is {grand_total}, "
            f"but payment allocations total {allocation_total}."
        )

    if credit_card_amount <= 0:
        frappe.throw(
            "No Credit Card payment amount was found."
        )

    # -------------------------------------------------
    # VERIFY REQUESTED NMI AMOUNT
    # -------------------------------------------------
    requested_amount = frappe.utils.flt(
        amount,
        2
    )

    if requested_amount != credit_card_amount:
        frappe.throw(
            "Payment amount mismatch. "
            f"Credit Card allocation is {credit_card_amount}, "
            f"but the requested NMI amount is {requested_amount}."
        )

    # Authoritative amount sent to NMI
    amount = credit_card_amount

    # -------------------------------------------------
    # DEVICE / CLIENT
    # -------------------------------------------------
    device = get_device(
        pos_profile=pos_profile
    )

    client = NMIClient()

    # -------------------------------------------------
    # CREATE NMI PAYMENT TRANSACTION
    # -------------------------------------------------
    transaction = frappe.get_doc({
        "doctype": "NMI Payment Transaction",
        "device": device.name,
        "customer": customer,
        "company": company or device.company,
        "pos_profile": pos_profile,
        "amount": amount,
        "currency": currency,
        "status": "Created",
        "gateway_environment": client.environment,
        "request_time": now_datetime(),
        "erp_document_type": erp_document_type,
        "erp_document_name": erp_document_name,
    })

    transaction.insert(
        ignore_permissions=True
    )

    frappe.db.commit()

    try:
        # -------------------------------------------------
        # SEND PAYMENT TO NMI
        # -------------------------------------------------
        result = client.start_sale(
            device_id=device.device_id,
            amount=amount,
            currency=currency,
            order_id=transaction.name
        )

        request_id = result.get("id")

        if not request_id:
            transaction.status = "Unknown"

            transaction.response_message = (
                "NMI returned a response without a payment request ID. "
                "The gateway payment state cannot be safely determined."
            )

            transaction.sanitized_response = json.dumps(
                _sanitize_nmi_response(result),
                indent=2
            )

            transaction.save(
                ignore_permissions=True
            )

            frappe.db.commit()

            raise NMIAmbiguousPaymentError(
                "NMI returned a response without a payment request ID. "
                "Do not retry this payment until the existing "
                "transaction is reconciled."
            )

        transaction.payment_request_id = (
            request_id
        )

        transaction.status = (
            "Sent To Terminal"
        )

        transaction.sanitized_response = (
            json.dumps(
                _sanitize_nmi_response(result),
                indent=2
            )
        )

        transaction.save(
            ignore_permissions=True
        )

        frappe.db.commit()

        return {
            "transaction": transaction.name,
            "request_id": request_id,
            "status": transaction.status
        }

    except NMIAmbiguousPaymentError as exc:
        # The request may have reached NMI.
        # Never allow an automatic retry.
        transaction.status = "Unknown"
        transaction.response_message = str(exc)

        transaction.save(
            ignore_permissions=True
        )
        frappe.db.commit()

        raise

    except Exception as exc:
        transaction.status = "Error"
        transaction.response_message = str(exc)

        transaction.save(
            ignore_permissions=True
        )
        frappe.db.commit()

        raise

@frappe.whitelist()
def complete_erp_link(
    transaction_name,
    erp_document_type,
    erp_document_name
):
    transaction = frappe.get_doc(
        "NMI Payment Transaction",
        transaction_name
    )

    _require_payment_permission(
        "link_payment",
        transaction
    )

    if transaction.status != "Approved":
        frappe.throw(
            "Only approved NMI transactions can be linked to an ERP document."
        )

    if erp_document_type not in (
        "POS Invoice",
        "Sales Invoice"
    ):
        frappe.throw(
            "Unsupported ERP document type for NMI payment."
        )

    erp_doc = frappe.get_doc(
        erp_document_type,
        erp_document_name
    )

    if not erp_doc.meta.has_field(
        "custom_nmi_payment_transaction"
    ):
        frappe.throw(
            f"Required NMI field is missing from {erp_document_type}."
        )

    erp_doc.db_set(
        "custom_nmi_payment_transaction",
        transaction.name,
        update_modified=False
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

    _require_payment_permission(
        "void_payment",
        txn
    )
    # -------------------------------------------------
    # VALIDATION
    # -------------------------------------------------
    if not txn.nmi_transaction_id:
        frappe.throw(
            "NMI Transaction ID is missing."
        )

    refund_status = (
    txn.get("refund_status")
    or "Not Requested"
    )

    if refund_status in (
    "Processing",
    "Approved",
    "Unknown"
    ):
        frappe.throw(
            "This NMI transaction already has a refund that is "
            "processing, approved, or requires reconciliation. "
            "The transaction cannot be voided."
        )

    void_status = txn.get("void_status") or "Not Requested"

    if void_status in (
        "Processing",
        "Approved",
        "Unknown"
        ):
        frappe.throw(
            "This payment already has a void request "
            "that is processing, approved, or requires reconciliation."
        )

    # Optional additional safety check
    if txn.status not in (
        "Approved",
        "ERPNext Completed"
    ):
        frappe.throw(
            "Only approved NMI payments can be voided."
        )
    client = _validate_transaction_environment(txn)
    
    # -------------------------------------------------
    # MARK VOID AS PROCESSING
    # -------------------------------------------------
    txn.set("void_status", "Processing")
    txn.set("void_request_time", now_datetime())
    txn.set("void_requested_by", frappe.session.user)

    txn.save(ignore_permissions=True)
    frappe.db.commit()


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
                _sanitize_nmi_response(result),
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
            "nmi_response": _sanitize_nmi_response(result)
        }
    except NMIAmbiguousPaymentError as exc:
        txn.set("void_status", "Unknown")
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

    _require_payment_permission(
        "refund_payment",
        txn
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

    client = _validate_transaction_environment(txn)
        
    if amount <= 0:
        frappe.throw(
            "Refund amount must be greater than zero."
        )

    if amount > frappe.utils.flt(txn.amount):
        frappe.throw(
            "Refund amount cannot exceed the original payment amount."
        )

    void_status = (
    txn.get("void_status")
    or "Not Requested"
    )
    if void_status in (
    "Processing",
    "Approved",
    "Unknown"
    ):
        frappe.throw(
            "This NMI transaction already has a void that is "
            "processing, approved, or requires reconciliation. "
            "The transaction cannot be refunded."
        )

    refund_status = (
        txn.get("refund_status")
        or "Not Requested"
    )

    if refund_status in("Processing","Unknown","Approved"
    ):
        frappe.throw(
            "A refund has already been processed, is processing, "
            "or requires reconciliation. "
            "Additional refunds are not allowed."
         )

    txn.set("refund_status", "Processing")
    txn.set("refund_amount", amount)
    txn.set("refund_request_time", now_datetime())
    txn.set("refund_requested_by", frappe.session.user)

    txn.save(ignore_permissions=True)
    frappe.db.commit()

   

    try:
        result = client.refund_transaction(
            txn.nmi_transaction_id,
            amount
        )

        txn.set(
            "refund_response",
            json.dumps(_sanitize_nmi_response(result), indent=2)
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
            "nmi_response": _sanitize_nmi_response(result)
        }

    except NMIAmbiguousPaymentError as exc:
        txn.set("refund_status", "Unknown")
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
    client = _validate_transaction_environment(nmi_txn)

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
    client = _validate_transaction_environment(nmi_txn)

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

def _validate_required_nmi_fields():
    meta = frappe.get_meta("Sales Invoice")

    if not meta.has_field("custom_nmi_payment_transaction"):
        frappe.throw(
            "Required NMI field 'custom_nmi_payment_transaction' "
            "is missing from Sales Invoice. "
            "Run bench migrate before processing payments."
        )

def _validate_transaction_environment(txn):
    client = NMIClient()

    if not txn.gateway_environment:
        frappe.throw(
            "Gateway Environment is missing for this transaction."
        )

    if txn.gateway_environment != client.environment:
        frappe.throw(
            "NMI environment mismatch. "
            f"This transaction was created in {txn.gateway_environment}, "
            f"but NMI Settings is currently {client.environment}."
        )

    return client

def _get_existing_pos_payment(
    erp_document_type,
    erp_document_name
):
    blocking_statuses = [
        "Created",
        "Sent To Terminal",
        "In Flight",
        "Approved",
        "Unknown",
        "ERPNext Completed",
    ]

    return frappe.db.get_value(
        "NMI Payment Transaction",
        {
            "erp_document_type": erp_document_type,
            "erp_document_name": erp_document_name,
            "status": ["in", blocking_statuses],
        },
        ["name", "status", "nmi_transaction_id"],
        as_dict=True,
    )


def _handle_existing_pos_payment(transaction):
    if transaction.status in (
        "Sent To Terminal",
        "In Flight",
        "Approved",
    ):
        return {
            "transaction": transaction.name,
            "status": transaction.status,
            "existing_payment": True,
            "resume_polling": transaction.status in (
                "Sent To Terminal",
                "In Flight",
            ),
        }

    if transaction.status == "ERPNext Completed":
        frappe.throw(
            "This invoice has already been paid through NMI. "
            f"Transaction: {transaction.name}. "
            "A second card payment is not allowed."
        )

    frappe.throw(
        "An existing NMI payment requires reconciliation. "
        f"Transaction: {transaction.name}, "
        f"Status: {transaction.status}. "
        "Do not retry the card payment."
    )

def _sanitize_nmi_response(data):
    if not isinstance(data, dict):
        return {}

    sensitive_keys = {
        "security_key",
        "ccnumber",
        "ccexp",
        "cvv",
        "cvv2",
        "card_number",
        "cardnumber",
        "account_number",
        "accountnumber",
        "routing_number",
        "routingnumber",
    }

    return {
        key: "***REDACTED***"
        if str(key).lower() in sensitive_keys
        else value
        for key, value in data.items()
    }