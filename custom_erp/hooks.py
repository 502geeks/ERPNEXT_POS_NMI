page_js = {
    "point-of-sale": "public/js/nmi_pos.js"
}

app_name = "custom_erp"
app_title = "Custom ERP16"
app_publisher = "Suresh"
app_description = "Custom ERP16 for payments"
app_email = "suresh9753@gmail.com"
app_license = "mit"
required_apps = ["erpnext"]
# after_install = "custom_erp.setup.validate_nmi_setup"
# after_migrate = "custom_erp.setup.validate_nmi_setup"
after_sync = "custom_erp.setup.validate_nmi_setup"

doc_events = {
    "Sales Invoice": {
         "before_submit":
            "custom_erp.nmi.events.validate_sales_invoice_nmi_payment",
        "on_submit":
            "custom_erp.nmi.events.handle_sales_invoice_submit",
    }
}

fixtures = [
    {
        "dt": "Client Script",
        "filters": [
            ["name", "=", "NMI Payment Transaction Void"]
        ]
    },
      {
        "dt": "Custom Field",
        "filters": [
            [
                "name",
                "=",
                 "Sales Invoice-custom_nmi_payment_transaction",
            ]
        ],
    },
    {
    "dt": "Role",
    "filters": [
        ["name", "in", ["POS Payment User", "NMI Payment Supervisor"]]
        ]
    },
    {
        "dt": "Custom DocPerm",
        "filters": [
            ["parent", "=", "NMI Payment Transaction"],
            ["role", "in", ["POS Payment User", "NMI Payment Supervisor"]]
        ]
    },
]
