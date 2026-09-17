import requests
import frappe

class NMIAmbiguousPaymentError(Exception):
    """The NMI request may have reached the gateway, but the result is unknown."""
    pass

class NMIClient:
    def __init__(self):
        settings = frappe.get_single("NMI Settings")

        if not settings.enabled:
            frappe.throw("NMI integration is disabled.")

        self.api_key = settings.get_password("api_key")
        self.environment = settings.environment
        self.timeout = settings.timeout_seconds or 300
        self.poll_interval = settings.poll_interval_seconds or 2

        if self.environment == "Production":
            self.base_url = "https://secure.nmi.com"
            self.transaction_url = "https://secure.nmi.com/api/transact.php"
        else:
            self.base_url = "https://sandbox.nmi.com"
            self.transaction_url = "https://sandbox.nmi.com/api/transact.php"

    def _headers(self):
        return {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def start_sale(self, device_id, amount, currency="USD", order_id=None):
        url = (
            f"{self.base_url}/api/v5/devices/"
            f"{device_id}/payment-requests/sale"
        )

        payload = {
            "amount": f"{float(amount):.2f}",
            "currency": currency,
        }

        if order_id:
            payload["order_details"] = {
                "id": order_id,
                "order_description": "ERPNext POS Sale"
            }

        try:
            response = requests.post(
                url,
                headers=self._headers(),
                json=payload,
                timeout=30,
            )

            response.raise_for_status()
            return response.json()

        except requests.Timeout as exc:
            raise NMIAmbiguousPaymentError(
                "NMI payment request timed out after submission. "
                "The payment status is unknown. Do not retry this payment "
                "until the existing transaction is reconciled."
            ) from exc

        except requests.RequestException as exc:
            raise NMIAmbiguousPaymentError(
                "NMI payment request failed due to a network error. "
                "The payment status may be unknown. Do not retry this payment "
                "until the existing transaction is reconciled."
            ) from exc

    def get_payment_status(self, device_id, request_id):
        url = (
            f"{self.base_url}/api/v5/devices/"
            f"{device_id}/payment-requests/{request_id}"
        )

        response = requests.get(
            url,
            headers=self._headers(),
            timeout=30,
        )

        response.raise_for_status()
        return response.json()

    def void_transaction(self, transaction_id):
       
        payload = {
            "security_key": self.api_key,
            "type": "void",
            "transactionid": transaction_id,
        }


        try:
            response = requests.post(
                self.transaction_url,
                data=payload,
                timeout=30,
            )

            response.raise_for_status()
        except (requests.Timeout, requests.ConnectionError) as exec:
            raise NMIAmbiguousPaymentError("NMI void result is unknown due to a network failure. "
        "Do not retry the void until the transaction is reconciled.") from exec
        
        from urllib.parse import parse_qs

        parsed = parse_qs(response.text)

        return {
            key: values[0] if values else ""
            for key, values in parsed.items()
        }


    def refund_transaction(self, transaction_id, amount):
        from urllib.parse import parse_qs

        

        payload = {
            "security_key": self.api_key,
            "type": "refund",
            "transactionid": transaction_id,
            "amount": f"{float(amount):.2f}",
        }

        try:
            response = requests.post(
                self.transaction_url,
                data=payload,
                timeout=30,
            )

            response.raise_for_status()
        except(requests.Timeout, requests.ConnectionError) as exec:
            raise NMIAmbiguousPaymentError("NMI refund result is unknown due to a network failure. "
        "Do not retry the refund until the transaction is reconciled.") from exec

        parsed = parse_qs(response.text)

        return {
            key: values[0] if values else ""
            for key, values in parsed.items()
        }