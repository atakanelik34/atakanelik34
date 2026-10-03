"""Built-in document-type templates.

Starting points a tenant can copy into its own taxonomy (as a draft to review
and publish). They are ordinary `SchemaDefinition`s — nothing about them is
special-cased by the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from idp.domain.taxonomy import SchemaDefinition


@dataclass(frozen=True, slots=True)
class Template:
    key: str
    name: str
    description: str
    definition: SchemaDefinition


def _f(name: str, type_: str, **kw: Any) -> dict[str, Any]:
    return {"name": name, "type": type_, **kw}


_AMOUNT = {"normalization": {"decimal_separator": "auto"}}

INVOICE = {
    "classification": {
        "keywords": [
            "invoice",
            "rechnung",
            "facture",
            "fatura",
            "factura",
            "fattura",
            "factuur",
            "invoice number",
            "invoice no",
            "rechnungsnummer",
            "amount due",
            "tax invoice",
        ],
        "negative_keywords": ["credit note", "gutschrift", "purchase order", "delivery note"],
        "first_page_markers": ["invoice number", "invoice no", "rechnungsnummer", "invoice date"],
        "min_score": 0.6,
    },
    "fields": [
        _f(
            "vendor_name",
            "string",
            required=True,
            aliases=["from", "seller", "supplier", "lieferant"],
            extraction_hints={"position": "any", "fallback": "first_line"},
        ),
        _f(
            "vendor_tax_number",
            "tax_number",
            aliases=["vat id", "vat no", "ust-idnr", "tax id", "vergi no"],
        ),
        _f(
            "invoice_number",
            "string",
            required=True,
            confidence_threshold=0.85,
            aliases=[
                "invoice number",
                "invoice no",
                "invoice #",
                "rechnungsnummer",
                "rechnung nr",
                "fatura no",
            ],
            extraction_hints={
                "patterns": [
                    r"\b(?:invoice|rechnung)\s*(?:no\.?|number|nr\.?|#)\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/]{2,30})"
                ]
            },
        ),
        _f(
            "invoice_date",
            "date",
            required=True,
            aliases=["invoice date", "date", "rechnungsdatum", "datum", "fatura tarihi"],
        ),
        _f(
            "due_date",
            "date",
            aliases=["due date", "payment due", "fällig am", "zahlbar bis", "son ödeme"],
        ),
        _f(
            "purchase_order_number",
            "string",
            aliases=["po number", "purchase order", "bestellnummer", "order no"],
        ),
        _f("currency", "currency", aliases=["currency", "währung"]),
        _f(
            "subtotal",
            "decimal",
            aliases=["subtotal", "net amount", "net total", "nettobetrag", "zwischensumme"],
            **_AMOUNT,
        ),
        _f("tax", "decimal", aliases=["tax", "vat", "mwst", "ust", "kdv", "sales tax"], **_AMOUNT),
        _f(
            "total",
            "decimal",
            required=True,
            confidence_threshold=0.85,
            aliases=[
                "total",
                "total due",
                "amount due",
                "grand total",
                "gesamtbetrag",
                "rechnungsbetrag",
                "toplam",
            ],
            validation_rules=[{"type": "min", "params": {"value": 0}}],
            **_AMOUNT,
        ),
        _f("iban", "iban", aliases=["iban"], validation_rules=[{"type": "iban"}]),
        _f(
            "lines",
            "array",
            item=_f(
                "line",
                "object",
                children=[
                    _f(
                        "description",
                        "string",
                        aliases=["description", "item", "beschreibung", "artikel"],
                    ),
                    _f(
                        "quantity",
                        "decimal",
                        aliases=["qty", "quantity", "menge", "anzahl"],
                        **_AMOUNT,
                    ),
                    _f(
                        "unit_price",
                        "decimal",
                        aliases=["unit price", "price", "einzelpreis", "preis"],
                        **_AMOUNT,
                    ),
                    _f(
                        "tax_rate",
                        "decimal",
                        aliases=["vat %", "tax rate", "mwst %", "ust %"],
                        **_AMOUNT,
                    ),
                    _f(
                        "total",
                        "decimal",
                        aliases=["amount", "total", "line total", "betrag", "gesamt"],
                        **_AMOUNT,
                    ),
                ],
            ),
        ),
    ],
    "rules": [
        {
            "type": "sum",
            "params": {"target": "total", "terms": ["subtotal", "tax"], "tolerance": "0.01"},
            "severity": "REQUIRES_HUMAN",
            "message": "Total must equal subtotal + tax",
        },
        {
            "type": "compare",
            "params": {"left": "invoice_date", "op": "<=", "right": "due_date"},
            "severity": "WARNING",
            "message": "Invoice date should not be after the due date",
        },
        {
            "type": "line_items_sum",
            "params": {
                "lines": "lines",
                "line_total": "total",
                "target": "subtotal",
                "tolerance": "0.05",
            },
            "severity": "WARNING",
            "message": "Line items should add up to the subtotal",
        },
    ],
}

RECEIPT = {
    "classification": {
        "keywords": [
            "receipt",
            "quittung",
            "kassenbon",
            "beleg",
            "fiş",
            "thank you for your purchase",
            "cash",
            "change",
            "card payment",
        ],
        "negative_keywords": ["invoice number", "purchase order"],
        "min_score": 0.6,
    },
    "fields": [
        _f(
            "merchant_name",
            "string",
            required=True,
            extraction_hints={"position": "any", "fallback": "first_line"},
        ),
        _f("receipt_date", "date", required=True, aliases=["date", "datum", "tarih"]),
        _f(
            "total",
            "decimal",
            required=True,
            aliases=["total", "summe", "gesamt", "toplam"],
            **_AMOUNT,
        ),
        _f("tax", "decimal", aliases=["vat", "tax", "mwst", "kdv"], **_AMOUNT),
        _f("currency", "currency", aliases=["currency"]),
        _f("payment_method", "string", aliases=["payment", "paid by", "zahlart"]),
    ],
    "rules": [],
}

PURCHASE_ORDER = {
    "classification": {
        "keywords": [
            "purchase order",
            "bestellung",
            "po number",
            "order number",
            "satın alma siparişi",
            "ship to",
            "bill to",
            "order date",
        ],
        "negative_keywords": ["invoice number", "delivery note"],
        "first_page_markers": ["purchase order number", "po number"],
        "min_score": 0.6,
    },
    "fields": [
        _f(
            "po_number",
            "string",
            required=True,
            aliases=["po number", "order number", "bestellnummer", "po no"],
        ),
        _f("order_date", "date", required=True, aliases=["order date", "date", "bestelldatum"]),
        _f("buyer_name", "string", aliases=["bill to", "buyer", "besteller"]),
        _f("supplier_name", "string", aliases=["supplier", "vendor", "lieferant"]),
        _f("currency", "currency", aliases=["currency"]),
        _f("total", "decimal", aliases=["total", "order total", "gesamt"], **_AMOUNT),
        _f(
            "lines",
            "array",
            item=_f(
                "line",
                "object",
                children=[
                    _f("description", "string", aliases=["description", "item", "artikel"]),
                    _f("quantity", "decimal", aliases=["qty", "quantity", "menge"], **_AMOUNT),
                    _f(
                        "unit_price", "decimal", aliases=["unit price", "price", "preis"], **_AMOUNT
                    ),
                    _f("total", "decimal", aliases=["amount", "total", "betrag"], **_AMOUNT),
                ],
            ),
        ),
    ],
    "rules": [],
}

DELIVERY_NOTE = {
    "classification": {
        "keywords": [
            "delivery note",
            "lieferschein",
            "packing slip",
            "irsaliye",
            "bon de livraison",
            "delivered",
            "shipment",
            "consignee",
        ],
        "negative_keywords": ["invoice number", "amount due"],
        "first_page_markers": ["delivery note number", "delivery note no", "lieferscheinnummer"],
        "min_score": 0.6,
    },
    "fields": [
        _f(
            "delivery_note_number",
            "string",
            required=True,
            aliases=[
                "delivery note no",
                "delivery note number",
                "lieferscheinnummer",
                "lieferschein nr",
            ],
        ),
        _f("delivery_date", "date", aliases=["delivery date", "date", "lieferdatum"]),
        _f("order_reference", "string", aliases=["order no", "po number", "bestellnummer"]),
        _f(
            "lines",
            "array",
            item=_f(
                "line",
                "object",
                children=[
                    _f("description", "string", aliases=["description", "item", "artikel"]),
                    _f("quantity", "decimal", aliases=["qty", "quantity", "menge"], **_AMOUNT),
                ],
            ),
        ),
    ],
    "rules": [],
}

BANK_STATEMENT = {
    "classification": {
        "keywords": [
            "bank statement",
            "kontoauszug",
            "account statement",
            "hesap özeti",
            "opening balance",
            "closing balance",
            "statement period",
            "iban",
        ],
        "first_page_markers": ["statement period", "opening balance"],
        "min_score": 0.6,
    },
    "fields": [
        _f("account_holder", "string", aliases=["account holder", "kontoinhaber"]),
        _f("iban", "iban", required=True, aliases=["iban"], validation_rules=[{"type": "iban"}]),
        _f("statement_date", "date", aliases=["statement date", "date", "datum"]),
        _f("opening_balance", "decimal", aliases=["opening balance", "anfangssaldo"], **_AMOUNT),
        _f(
            "closing_balance",
            "decimal",
            required=True,
            aliases=["closing balance", "endsaldo"],
            **_AMOUNT,
        ),
        _f("currency", "currency", aliases=["currency", "währung"]),
    ],
    "rules": [],
}

CONTRACT = {
    "classification": {
        "keywords": [
            "agreement",
            "contract",
            "vertrag",
            "sözleşme",
            "contrat",
            "hereinafter",
            "party",
            "parties",
            "term",
            "governing law",
            "signature",
        ],
        "first_page_markers": [
            "is made between",
            "is entered into",
            "made and entered into",
            "wird geschlossen zwischen",
        ],
        "min_score": 0.6,
    },
    "fields": [
        _f("title", "string", extraction_hints={"position": "any", "fallback": "first_line"}),
        _f("effective_date", "date", aliases=["effective date", "dated", "commencement date"]),
        _f("party_a", "string", aliases=["between", "party a"]),
        _f("party_b", "string", aliases=["and", "party b"]),
        _f("governing_law", "string", aliases=["governing law"]),
    ],
    "rules": [],
}

TEMPLATES: dict[str, Template] = {
    t.key: t
    for t in (
        Template(
            "invoice",
            "Invoice",
            "Supplier invoice with line items, totals and payment details",
            SchemaDefinition.model_validate(INVOICE),
        ),
        Template(
            "receipt", "Receipt", "Point-of-sale receipt", SchemaDefinition.model_validate(RECEIPT)
        ),
        Template(
            "purchase_order",
            "Purchase order",
            "Buyer purchase order with line items",
            SchemaDefinition.model_validate(PURCHASE_ORDER),
        ),
        Template(
            "delivery_note",
            "Delivery note",
            "Delivery / packing note",
            SchemaDefinition.model_validate(DELIVERY_NOTE),
        ),
        Template(
            "bank_statement",
            "Bank statement",
            "Account statement with balances",
            SchemaDefinition.model_validate(BANK_STATEMENT),
        ),
        Template(
            "contract",
            "Contract",
            "Commercial agreement (metadata only)",
            SchemaDefinition.model_validate(CONTRACT),
        ),
    )
}
