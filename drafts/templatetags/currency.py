from decimal import Decimal, InvalidOperation

from django import template


register = template.Library()


@register.filter
def ars(value):
    """Render an ARS amount without mutating its stored decimal value."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return value
    rendered = f"{amount:,.2f}"
    return "$ " + rendered.replace(",", "\u0000").replace(".", ",").replace("\u0000", ".")
