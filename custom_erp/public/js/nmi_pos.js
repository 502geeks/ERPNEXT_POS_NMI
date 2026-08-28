console.log("CUSTOM ERP: NMI POS integration loaded from nmi_pos.js");

(() => {
    "use strict";

    const NMI_MODE_OF_PAYMENT = "Credit Card";

    let nmi_processing = false;
    let allow_erpnext_submit = false;

    function is_pos_page() {
        return frappe.get_route_str() === "point-of-sale";
    }

    function get_pos() {
        return window.cur_pos;
    }

    function get_credit_card_payment(frm) {
        return (frm.doc.payments || []).find(
            p =>
                p.mode_of_payment === NMI_MODE_OF_PAYMENT &&
                flt(p.amount) > 0
        );
    }

    function sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    async function start_nmi_payment(frm, card_payment) {
        const response = await frappe.call({
            method: "custom_erp.nmi.api.start_pos_payment",
            args: {
                pos_profile: frm.doc.pos_profile,
                amount: card_payment.amount,
                customer: frm.doc.customer,
                company: frm.doc.company,
                currency: frm.doc.currency || "USD"
            }
        });

        return response.message;
    }

    async function wait_for_nmi_result(transaction_name) {
        const max_attempts = 150;

        for (let attempt = 0; attempt < max_attempts; attempt++) {
            await sleep(2000);

            const response = await frappe.call({
                method: "custom_erp.nmi.api.check_payment_status",
                args: {
                    transaction_name: transaction_name
                }
            });

            const data = response.message;

            if (!data) {
                throw new Error(
                    "Empty response from NMI status API."
                );
            }

            console.log(
                "NMI payment status:",
                data.status
            );

            if (data.status === "Approved") {
                return data;
            }

            if (
                [
                    "Declined",
                    "Cancelled",
                    "Timed Out",
                    "Error"
                ].includes(data.status)
            ) {
                throw new Error(
                    `NMI payment ${data.status}.`
                );
            }
        }

        throw new Error("NMI payment timed out.");
    }

    async function process_nmi_payment(
        frm,
        card_payment
    ) {
        if (nmi_processing) {
            frappe.show_alert({
                message: __(
                    "NMI payment is already processing."
                ),
                indicator: "orange"
            });

            return null;
        }

        nmi_processing = true;

        frappe.dom.freeze(
            __("Waiting for card payment on NMI terminal...")
        );

        try {
            // ---------------------------------------------
            // 1. CREATE NMI PAYMENT REQUEST
            // ---------------------------------------------
            const payment = await start_nmi_payment(
                frm,
                card_payment
            );

            if (!payment?.transaction) {
                throw new Error(
                    "NMI transaction was not created."
                );
            }

            console.log(
                "NMI Payment Transaction:",
                payment.transaction
            );

            frappe.show_alert({
                message: __(
                    "Payment sent to NMI terminal."
                ),
                indicator: "blue"
            });

            // ---------------------------------------------
            // 2. WAIT FOR TERMINAL RESPONSE
            // ---------------------------------------------
            const result = await wait_for_nmi_result(
                payment.transaction
            );

            if (result.status !== "Approved") {
                throw new Error(
                    `Unexpected NMI status: ${result.status}`
                );
            }

            frappe.show_alert({
                message: __("NMI payment approved."),
                indicator: "green"
            });

            // IMPORTANT:
            // Return the complete result instead of true.
            // We need result.transaction later.
            return result;

        } catch (error) {
            console.error(
                "NMI POS Payment Error:",
                error
            );

            frappe.msgprint({
                title: __("Card Payment Failed"),
                indicator: "red",
                message:
                    error.message ||
                    __("Unable to process NMI payment.")
            });

            return null;

        } finally {
            nmi_processing = false;
            frappe.dom.unfreeze();
        }
    }

    document.addEventListener(
        "click",
        async function(event) {
            if (!is_pos_page()) {
                return;
            }

            const submit_button =
                event.target.closest(
                    ".submit-order-btn"
                );

            if (!submit_button) {
                return;
            }

            /*
             * ERPNext is being allowed through after
             * successful NMI authorization.
             */
            if (allow_erpnext_submit) {
                allow_erpnext_submit = false;
                return;
            }

            const pos = get_pos();

            if (!pos?.frm?.doc) {
                return;
            }

            const frm = pos.frm;

            const credit_card_payment =
                get_credit_card_payment(frm);

            /*
             * No Credit Card amount:
             * leave ERPNext's normal Cash / Check /
             * Wire Transfer behavior untouched.
             */
            if (!credit_card_payment) {
                return;
            }

            /*
             * Stop ERPNext invoice submission until
             * NMI authorization succeeds.
             */
            event.preventDefault();
            event.stopPropagation();
            event.stopImmediatePropagation();

            // ---------------------------------------------
            // 3. PROCESS NMI PAYMENT
            // ---------------------------------------------
            const nmi_result =
                await process_nmi_payment(
                    frm,
                    credit_card_payment
                );

            if (
                !nmi_result ||
                nmi_result.status !== "Approved"
            ) {
                return;
            }

            /*
             * Store the approved NMI Payment Transaction
             * on the Sales Invoice BEFORE ERPNext submits.
             */
            if (
                frm.fields_dict
                    ?.custom_nmi_payment_transaction
            ) {
                await frm.set_value(
                    "custom_nmi_payment_transaction",
                    nmi_result.transaction
                );

                console.log(
                    "Linked NMI transaction:",
                    nmi_result.transaction
                );
            } else {
                console.warn(
                    "Custom field custom_nmi_payment_transaction " +
                    "was not found on Sales Invoice."
                );
            }

            /*
             * NMI approved.
             * Permit exactly one pass through ERPNext's
             * original Complete Order handler.
             */
            allow_erpnext_submit = true;

            submit_button.click();
        },
        true
    );
})();