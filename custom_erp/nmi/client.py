import requests
import frappe


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
        else:
            self.base_url = "https://sandbox.nmi.com"

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

        response = requests.post(
            url,
            headers=self._headers(),
            json=payload,
            timeout=30,
        )

        response.raise_for_status()
        return response.json()

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
        url = "https://secure.nmi.com/api/transact.php"

        payload = {
            "security_key": self.api_key,
            "type": "void",
            "transactionid": transaction_id,
        }

        response = requests.post(
            url,
            data=payload,
            timeout=30,
        )

        response.raise_for_status()

        from urllib.parse import parse_qs

        parsed = parse_qs(response.text)

        return {
            key: values[0] if values else ""
            for key, values in parsed.items()
        }


    def refund_transaction(self, transaction_id, amount):
        from urllib.parse import parse_qs

        url = "https://secure.nmi.com/api/transact.php"

        payload = {
            "security_key": self.api_key,
            "type": "refund",
            "transactionid": transaction_id,
            "amount": f"{float(amount):.2f}",
        }

        response = requests.post(
            url,
            data=payload,
            timeout=30,
        )

        response.raise_for_status()

        parsed = parse_qs(response.text)

        return {
            key: values[0] if values else ""
            for key, values in parsed.items()
        }