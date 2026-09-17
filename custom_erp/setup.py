import frappe


REQUIRED_FIELDS = [
    {
        "doctype": "Sales Invoice",
        "fieldname": "custom_nmi_payment_transaction",
    }
]


def validate_nmi_setup():
    missing = []

    for field in REQUIRED_FIELDS:
        meta = frappe.get_meta(field["doctype"])

        if not meta.has_field(field["fieldname"]):
            missing.append(
                f'{field["doctype"]}.{field["fieldname"]}'
            )

    if missing:
        frappe.throw(
            "NMI setup is incomplete. Missing required fields: "
            + ", ".join(missing)
        )