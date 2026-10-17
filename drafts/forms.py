import json
import re
from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from .models import ProductDraft
from .services.image_validation import validate_image_file

class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class ProductDraftForm(forms.ModelForm):
    attributes_json = forms.CharField(label="Atributos adicionales (JSON)", required=False, widget=forms.Textarea(attrs={"rows": 4, "placeholder": '{"COLOR": "Rojo"}'}))
    sale_terms_json = forms.CharField(label="Términos de venta (JSON)", required=False, widget=forms.Textarea(attrs={"rows": 3, "placeholder": '[{"id": "WARRANTY_TYPE", "value_id": "..."}]'}))

    label_or_company = forms.CharField(label="Sello o compañía de la edición", required=False, max_length=160)
    catalog_number = forms.CharField(label="Número de catálogo", required=False, max_length=100)
    physical_edition = forms.CharField(label="Edición física", required=False, max_length=160)
    manufacturing_country = forms.CharField(label="País de fabricación", required=False, max_length=100)
    channels_json = forms.CharField(label="Canales de venta confirmados (JSON)", required=False, widget=forms.Textarea(attrs={"rows": 2, "placeholder": '["marketplace"]'}))

    class Meta:
        model = ProductDraft
        fields = ["title", "family_name", "domain_id", "price", "description", "category_id", "listing_type_id", "condition", "stock", "edition_and_authenticity_verified", "commercialization_authorization_confirmed", "status"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
            "price": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
            "domain_id": forms.TextInput(attrs={"readonly": "readonly", "placeholder": "Se completa al elegir una sugerencia"}),
        }

    def __init__(self, *args, attribute_definitions=None, listing_types=None, sale_term_definitions=None, shipping_options=None, cost_estimates=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.attribute_definitions = attribute_definitions or []
        self.listing_types = listing_types or []
        self.sale_term_definitions = sale_term_definitions or []
        self.shipping_options = shipping_options or []
        self.cost_estimates = cost_estimates or []
        self.fields["title"].label = "Título de referencia"
        self.fields["family_name"].help_text = "Sugerido desde el título; podés editarlo. No se enviará como title."
        self.fields["domain_id"].help_text = "Se obtiene del predictor de categorías de Mercado Libre."
        self.fields["condition"].label = "Condición de referencia"
        self.fields["listing_type_id"] = forms.ChoiceField(
            label="Tipo de publicación",
            required=False,
            choices=[("", "Elegí una categoría para consultar opciones")] + [(item.get("id", ""), item.get("name", item.get("id", ""))) for item in self.listing_types if item.get("id")],
            help_text="Seleccioná explícitamente una opción devuelta para este seller y categoría.",
        )
        details = getattr(self.instance, "commercial_details", {}) or {}
        for name in ("label_or_company", "catalog_number", "physical_edition", "manufacturing_country"):
            self.initial.setdefault(name, details.get(name, ""))
        self.initial.setdefault("channels_json", json.dumps(details.get("channels", []), ensure_ascii=False))
        self.fields["shipping_mode"] = forms.ChoiceField(
            label="Modo de envío confirmado", required=False,
            choices=[("", "Pendiente de confirmar")] + [(item, item) for item in self.shipping_options],
            initial=details.get("shipping_mode", ""),
            help_text="Opciones coincidentes entre la cuenta y la categoría, obtenidas por GET.",
        )
        self.fields["sale_terms_json"].help_text = "Usá únicamente IDs y valores devueltos para esta categoría. Dejalo vacío si no aplica."
        if self.instance and self.instance.pk and self.instance.additional_attributes:
            self.initial["attributes_json"] = json.dumps(self.instance.additional_attributes, ensure_ascii=False, indent=2)
        if self.instance and self.instance.pk and self.instance.sale_terms:
            self.initial["sale_terms_json"] = json.dumps(self.instance.sale_terms, ensure_ascii=False, indent=2)
        if not self.initial.get("family_name") and not getattr(self.instance, "family_name", ""):
            title = self.data.get("title") if self.is_bound else self.initial.get("title", getattr(self.instance, "title", ""))
            if title:
                self.initial["family_name"] = title
        attributes = self.data if self.is_bound else self.initial.get("attributes_json", self.instance.additional_attributes if self.instance else {})
        if isinstance(attributes, str):
            try:
                attributes = json.loads(attributes or "{}")
            except json.JSONDecodeError:
                attributes = {}
        for definition in self.attribute_definitions:
            attribute_id = definition.get("id")
            if not attribute_id:
                continue
            name = f"ml_attribute_{attribute_id}"
            tags = definition.get("tags") or {}
            condition = self.data.get("condition") if self.is_bound else self.initial.get("condition", getattr(self.instance, "condition", ""))
            required = bool(tags.get("required") or (tags.get("new_required") and condition == ProductDraft.Condition.NEW))
            choices = [("", "Seleccioná una opción")]
            choices += [(str(value.get("id", value.get("name", ""))), value.get("name", "")) for value in definition.get("values", [])]
            hierarchy = definition.get("hierarchy", "")
            details = ["Requerido para esta categoría." if required else ""]
            if hierarchy:
                details.append(f"Jerarquía: {hierarchy}.")
            if tags.get("conditional_required"):
                details.append("Requisito condicional; confirmalo antes de publicar.")
            options = {"label": definition.get("name", attribute_id), "required": required, "help_text": " ".join(item for item in details if item)}
            value = attributes.get(attribute_id, {}) if isinstance(attributes, dict) else {}
            initial = value.get("value_id") or value.get("value_name") if isinstance(value, dict) else value
            if definition.get("value_type") == "list" and len(choices) > 1:
                self.fields[name] = forms.ChoiceField(choices=choices, initial=initial, **options)
            else:
                self.fields[name] = forms.CharField(initial=initial, max_length=definition.get("value_max_length") or None, widget=forms.TextInput(attrs={"inputmode": "decimal" if definition.get("value_type") in ("number", "number_unit") else "text"}), **options)

    def clean_title(self):
        title = self.cleaned_data["title"].strip()
        if len(title) < 3:
            raise ValidationError("El título debe tener al menos 3 caracteres.")
        return title

    def clean_category_id(self):
        category_id = self.cleaned_data["category_id"].strip().upper()
        if category_id and not re.fullmatch(r"MLA\d+", category_id):
            raise ValidationError("Usá un ID de categoría argentino válido, por ejemplo MLA1234.")
        return category_id

    def clean_domain_id(self):
        domain_id = self.cleaned_data["domain_id"].strip().upper()
        if domain_id and not re.fullmatch(r"MLA-[A-Z0-9_]+", domain_id):
            raise ValidationError("El dominio debe provenir del predictor de Mercado Libre.")
        return domain_id

    def clean_status(self):
        status = self.cleaned_data["status"]
        if status == ProductDraft.Status.PUBLISHED:
            raise ValidationError("El estado Publicado sólo puede asignarse tras una publicación confirmada.")
        return status

    def clean_attributes_json(self):
        value = self.cleaned_data["attributes_json"].strip()
        if not value:
            return {}
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValidationError("Ingresá un objeto JSON válido.") from error
        if not isinstance(parsed, dict):
            raise ValidationError("Los atributos deben ser un objeto JSON.")
        return parsed

    def clean_sale_terms_json(self):
        value = self.cleaned_data["sale_terms_json"].strip()
        if not value:
            return []
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValidationError("Ingresá un array JSON válido de términos de venta.") from error
        if not isinstance(parsed, list) or not all(isinstance(item, dict) and item.get("id") for item in parsed):
            raise ValidationError("Los términos de venta deben ser un array de objetos con id.")
        allowed = {item.get("id") for item in self.sale_term_definitions if item.get("id")}
        unknown = {item["id"] for item in parsed} - allowed
        if allowed and unknown:
            raise ValidationError("Incluí sólo términos de venta devueltos por Mercado Libre para esta categoría.")
        return parsed

    def clean_channels_json(self):
        value = self.cleaned_data["channels_json"].strip()
        if not value:
            return []
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValidationError("Ingresá un array JSON válido de canales.") from error
        if not isinstance(parsed, list) or not all(isinstance(item, str) and item for item in parsed):
            raise ValidationError("Los canales deben ser un array de textos no vacíos.")
        return parsed

    def save(self, commit=True):
        instance = super().save(commit=False)
        attributes = self.cleaned_data["attributes_json"]
        for definition in self.attribute_definitions:
            attribute_id = definition.get("id")
            field_name = f"ml_attribute_{attribute_id}"
            if not attribute_id or field_name not in self.cleaned_data:
                continue
            value = self.cleaned_data[field_name]
            if not value:
                attributes.pop(attribute_id, None)
                continue
            option = next((item for item in definition.get("values", []) if str(item.get("id")) == str(value)), None)
            attributes[attribute_id] = {"value_id": option.get("id") if option else None, "value_name": option.get("name") if option else value}
        instance.additional_attributes = attributes
        instance.sale_terms = self.cleaned_data["sale_terms_json"]
        instance.commercial_details = {
            "label_or_company": self.cleaned_data["label_or_company"].strip(),
            "catalog_number": self.cleaned_data["catalog_number"].strip(),
            "physical_edition": self.cleaned_data["physical_edition"].strip(),
            "manufacturing_country": self.cleaned_data["manufacturing_country"].strip(),
            "channels": self.cleaned_data["channels_json"],
            "shipping_mode": self.cleaned_data["shipping_mode"],
        }
        if commit:
            instance.save()
        return instance


class ImageUploadForm(forms.Form):
    images = forms.FileField(required=False, widget=MultipleFileInput(attrs={"accept": ".jpg,.jpeg,.png,image/jpeg,image/png"}))

    @staticmethod
    def validate_upload(upload):
        return validate_image_file(upload, declared_content_type=upload.content_type or "", declared_size=upload.size)


class TestPublicationForm(forms.Form):
    listing_type_id = forms.ChoiceField(label="Tipo de publicación", choices=[("free", "Gratuita (free)")])
    channels_json = forms.CharField(label="Canales (JSON)", initial='["marketplace"]')
    confirmed = forms.BooleanField(label="Confirmo que es un único producto de prueba y autorizo iniciar el proceso.", required=True)

    def clean_listing_type_id(self):
        value = self.cleaned_data["listing_type_id"].strip()
        if value != "free":
            raise ValidationError("El único tipo permitido para este ensayo es free.")
        return value

    def clean_channels_json(self):
        try:
            channels = json.loads(self.cleaned_data["channels_json"])
        except json.JSONDecodeError as error:
            raise ValidationError("Ingresá un array JSON válido de canales.") from error
        if not isinstance(channels, list) or not channels or not all(isinstance(item, str) and item for item in channels):
            raise ValidationError("Los canales deben ser un array no vacío de textos.")
        return channels


class CommercialPublicationConfirmationForm(forms.Form):
    draft_fingerprint = forms.CharField(widget=forms.HiddenInput())
    confirmed = forms.BooleanField(label="Confirmo que revisé este borrador y autorizo iniciar la publicación comercial.", required=True)
