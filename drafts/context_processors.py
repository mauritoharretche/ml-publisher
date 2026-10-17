from .services.connections import active_connection


def mercado_libre_connection(request):
    """Expose only connection presence/metadata to templates, never credentials."""
    return {"connection": active_connection()}
