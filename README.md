### Custom ERP16

Custom ERP16 for payments.

## Compatibility

This version of `custom_erp` is designed for:

- Frappe Framework: version 16
- ERPNext: version 16
- Python: 3.14 or later
- NMI integration scope: Customer Present / card-present terminal payments

ERPNext is a required application and is declared through `required_apps` in `hooks.py`.

The application is not currently certified for Frappe/ERPNext version 17 or later.

## Legacy NMI Transaction Environment

Older NMI Payment Transaction records may have a blank `gateway_environment`.

The application does not automatically infer or populate the environment for these transactions. A blank environment must be manually verified against the appropriate NMI merchant/gateway records before reconciliation.

Transactions with a blank environment cannot be voided or refunded until reconciliation is completed.

After verification, an Administrator can reconcile the transaction from the Bench console:

```python
from custom_erp.nmi.api import reconcile_legacy_transaction_environment

reconcile_legacy_transaction_environment(
    "<NMI_PAYMENT_TRANSACTION_NAME>",
    "Production",  # or "Sandbox"
)

frappe.db.commit()
```

Never assign an environment based only on the current NMI Settings value.

## Installation

You can install this app using the Bench CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch customerp16
bench --site $SITE_NAME install-app custom_erp
bench --site $SITE_NAME migrate
```

## License

MIT