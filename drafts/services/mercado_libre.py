from dataclasses import dataclass
from typing import Protocol
from drafts.models import ProductDraft


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: list[str]


class MercadoLibreGateway(Protocol):
    def validate(self, draft: ProductDraft) -> ValidationResult: ...
    def publish(self, draft: ProductDraft) -> dict: ...


class DisabledMercadoLibreGateway:
    """Placeholder seguro: no autentica ni crea publicaciones."""
    def validate(self, draft: ProductDraft) -> ValidationResult:
        return ValidationResult(valid=False, errors=["La integración con Mercado Libre aún no está configurada."])

    def publish(self, draft: ProductDraft) -> dict:
        raise NotImplementedError("La publicación está fuera del alcance de esta versión.")
