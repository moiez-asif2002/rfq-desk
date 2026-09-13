"""Deterministic quote email. Prices never come from the model — only from the catalog or a human."""

from datetime import date, timedelta
from decimal import Decimal

from app.models import Rfq

QUOTE_VALID_DAYS = 30


def quote_total(rfq: Rfq) -> Decimal:
    return sum(((line.quantity or 0) * (line.unit_price or 0) for line in rfq.lines), Decimal("0"))


def quote_number(rfq: Rfq) -> str:
    return f"Q-{rfq.created_at:%Y%m}-{rfq.id:05d}"


def render_quote_email(rfq: Rfq) -> str:
    greeting = rfq.customer_name.split()[0] if rfq.customer_name else "there"
    rows = []
    for line in rfq.lines:
        product = line.product
        qty = line.quantity or Decimal("0")
        price = line.unit_price or Decimal("0")
        if product and product.stock_qty >= qty:
            availability = "in stock"
        else:
            availability = f"{product.lead_time_days if product else '?'} day lead time"
        rows.append(
            f"{line.line_no:>2}. {product.sku if product else '-':<16} {qty.normalize():>8} "
            f"x ${price:>9,.2f} = ${qty * price:>11,.2f}   ({availability})\n"
            f"    {product.name if product else line.raw_description}"
        )
    valid_until = date.today() + timedelta(days=QUOTE_VALID_DAYS)
    rep = rfq.assigned_rep
    signature = f"{rep.name}\n{rep.email}" if rep else "Sales Team"

    return (
        f"Hi {greeting},\n\n"
        f"Thanks for your request. Please find our quote {quote_number(rfq)} below.\n\n"
        + "\n".join(rows)
        + f"\n\n    Total (excl. tax & freight): ${quote_total(rfq):,.2f}\n\n"
        + (f"Requested delivery: {rfq.requested_delivery_date}\n" if rfq.requested_delivery_date else "")
        + (f"Ship to: {rfq.ship_to}\n" if rfq.ship_to else "")
        + f"Quote valid until {valid_until:%B %d, %Y}.\n\n"
        "Reply to this email to confirm and we'll issue a sales order.\n\n"
        f"Best regards,\n{signature}\n"
    )
