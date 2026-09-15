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