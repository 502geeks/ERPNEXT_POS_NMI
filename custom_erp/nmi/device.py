import frappe


def get_device(pos_profile=None, device_name=None):
    filters = {"enabled": 1}

    if pos_profile:
        filters["pos_profile"] = pos_profile

    if device_name:
        filters["device_name"] = device_name

    devices = frappe.get_all(
        "NMI Device",
        filters=filters,
        fields=[
            "name",
            "device_name",
            "device_id",
            "company",
            "pos_profile",
            "register_name",
            "location",
            "last_status"
        ],
        limit=1
    )

    if not devices:
        frappe.throw("No enabled NMI Device was found.")

    return devices[0]