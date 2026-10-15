import json
import re
from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import Image
from .models import ProductDraft

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class ProductDraftForm(forms.ModelForm):
    attributes_json = forms.CharField(label="Atributos adicionales (JSON)", required=False, widget=forms.Textarea(attrs={"rows": 4, "placeholder": '{"COLOR": "Rojo"}'}))

    class Meta:
        model = ProductDraft
        fields = ["title", "price", "description", "category_id", "condition", "stock", "status"]
        widgets = {"description": forms.Textarea(attrs={"rows": 5}), "price": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"})}

    def __init__(self, *args, attribute_definitions=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.attribute_definitions = attribute_definitions or []
        if self.instance and self.instance.pk and self.instance.additional_attributes:
            self.initial["attributes_json"] = json.dumps(self.instance.additional_attributes, ensure_ascii=False, indent=2)
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
            options = {"label": definition.get("name", attribute_id), "required": required, "help_text": "Requerido para esta categoría." if required else ""}
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
        if commit:
            instance.save()
        return instance


class ImageUploadForm(forms.Form):
    images = forms.FileField(required=False, widget=MultipleFileInput(attrs={"accept": ".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"}))

    @staticmethod
    def validate_upload(upload):
        if upload.size > settings.MAX_UPLOAD_SIZE:
            raise ValidationError(f"{upload.name}: supera el tamaño máximo permitido.")
        if upload.content_type not in ALLOWED_IMAGE_TYPES:
            raise ValidationError(f"{upload.name}: formato no permitido.")
        try:
            image = Image.open(upload)
            image.verify()
            upload.seek(0)
        except Exception as error:
            raise ValidationError(f"{upload.name}: archivo de imagen inválido.") from error


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
