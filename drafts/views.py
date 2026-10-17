import base64
import hashlib
import secrets
from django.contrib import messages
from django.conf import settings
from django.db import transaction
from django.db.models import Count, Max, Prefetch, Q
from django.http import HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView
from .forms import CommercialPublicationConfirmationForm, ImageUploadForm, ProductDraftForm, TestPublicationForm
from .models import ProductDraft, ProductImage
from .services.connections import access_token, active_connection, save_tokens
from .services.exceptions import MercadoLibreAuthorizationError, MercadoLibreError
from .services.mercado_libre_client import MercadoLibreMetadataService, MercadoLibreOAuthService
from .services.oauth_transactions import consume_transaction, create_transaction
from .services.preflight import IntegrationDiagnosticService, PublicationPreflightService
from .services.publishing import LiveMercadoLibrePublicationClient, PublicationBlocked, PublicationOptions, PublicationService
from .services.commercial_publishing import CommercialPublicationService, LiveCommercialPublicationClient, draft_fingerprint
from .services.enrichment import EnrichmentError, OpenAIVisionProvider, ProductEnrichmentService
from .models import ProductEnrichment


class ImageHandlingMixin:
    metadata_error = None

    def get_category_metadata(self):
        category_id = self.request.POST.get("category_id") if self.request.method == "POST" else getattr(self.get_object(), "category_id", "") if hasattr(self, "get_object") and self.kwargs.get("pk") else ""
        if not category_id:
            return [], [], [], [], []
        try:
            connection = active_connection()
            if not connection:
                return [], [], [], [], []
            token = access_token(connection, allow_refresh=False)
            metadata = MercadoLibreMetadataService()
            definitions = metadata.category_attributes(category_id, token)
            listing_types = (metadata.available_listing_types(connection.seller_id, category_id, token).get("available") or [])
            sale_terms = metadata.category_sale_terms(category_id, token)
            seller_shipping = metadata.seller_shipping_preferences(connection.seller_id, token)
            category_shipping = metadata.category_shipping_preferences(category_id, token)
            seller_modes = set(seller_shipping.get("modes") or []) if isinstance(seller_shipping, dict) else set()
            category_modes = {entry.get("mode") for entry in (category_shipping.get("logistics") or []) if entry.get("mode")} if isinstance(category_shipping, dict) else set()
            draft = self.get_object() if hasattr(self, "get_object") and self.kwargs.get("pk") else None
            price = draft.price if draft else self.request.POST.get("price", "")
            currency = draft.currency if draft else "ARS"
            estimates = metadata.listing_prices(site_id=settings.MERCADO_LIBRE_SITE_ID, price=price, currency_id=currency, category_id=category_id, access_token=token) if price else []
            available_ids = {entry.get("id") for entry in listing_types}
            estimates = [entry for entry in estimates if entry.get("listing_type_id") in available_ids] if isinstance(estimates, list) else []
            return definitions, listing_types, sale_terms if isinstance(sale_terms, list) else [], sorted(seller_modes & category_modes), estimates
        except MercadoLibreError as error:
            # Sin conexión se conservan los borradores preexistentes y sus campos libres.
            if active_connection():
                self.metadata_error = str(error)
            return [], [], [], [], []

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        definitions, listing_types, sale_terms, shipping_options, cost_estimates = self.get_category_metadata()
        kwargs.update(attribute_definitions=definitions, listing_types=listing_types, sale_term_definitions=sale_terms, shipping_options=shipping_options, cost_estimates=cost_estimates)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("image_form", ImageUploadForm())
        context.setdefault("metadata_error", self.metadata_error)
        context.setdefault("upload_max_mb", min(settings.MAX_UPLOAD_SIZE, 10 * 1024 * 1024) // (1024 * 1024))
        context.setdefault("cost_estimates", getattr(context.get("form"), "cost_estimates", []))
        context.setdefault("sale_term_definitions", getattr(context.get("form"), "sale_term_definitions", []))
        context.setdefault("shipping_options", getattr(context.get("form"), "shipping_options", []))
        return context

    def form_valid(self, form):
        if self.metadata_error:
            form.add_error(None, self.metadata_error)
            return self.form_invalid(form)
        image_form = ImageUploadForm(self.request.POST, self.request.FILES)
        uploads = self.request.FILES.getlist("images")
        errors = []
        for upload in uploads:
            try:
                image_form.validate_upload(upload)
            except Exception as error:
                errors.extend(error.messages)
        if errors:
            form.add_error(None, errors)
            return self.form_invalid(form)
        with transaction.atomic():
            self.object = form.save()
            for image_id in self.request.POST.getlist("delete_image"):
                ProductImage.objects.filter(pk=image_id, draft=self.object).delete()
            position = (self.object.images.aggregate(max_position=Max("position"))["max_position"] or -1) + 1
            for upload in uploads:
                ProductImage.objects.create(draft=self.object, image=upload, position=position, original_filename=upload.name[:255], content_type=upload.content_type or "", size=upload.size)
                position += 1
        messages.success(self.request, "Borrador guardado correctamente.")
        return HttpResponseRedirect(self.get_success_url())


class DraftListView(ListView):
    model = ProductDraft
    template_name = "drafts/draft_list.html"
    context_object_name = "drafts"

    def get_queryset(self):
        queryset = ProductDraft.objects.prefetch_related(
            Prefetch("images", queryset=ProductImage.objects.order_by("position", "id"), to_attr="prefetched_images")
        ).annotate(image_count=Count("images"))
        self.query = self.request.GET.get("q", "").strip()
        self.status_filter = self.request.GET.get("status", "").strip()
        if self.query:
            queryset = queryset.filter(Q(title__icontains=self.query) | Q(category_id__icontains=self.query))
        if self.status_filter in ProductDraft.Status.values:
            queryset = queryset.filter(status=self.status_filter)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_drafts = ProductDraft.objects.all()
        context.update({
            "draft_count": all_drafts.count(),
            "needs_attention_count": all_drafts.filter(Q(status=ProductDraft.Status.VALIDATION_ERROR) | Q(category_id="") | Q(listing_type_id="")).count(),
            "recent_drafts": all_drafts.order_by("-updated_at")[:4],
            "query": self.query,
            "status_filter": self.status_filter,
            "status_choices": ProductDraft.Status.choices,
        })
        return context


class DraftCreateView(ImageHandlingMixin, CreateView):
    model = ProductDraft
    form_class = ProductDraftForm
    template_name = "drafts/draft_form.html"

    def get_success_url(self):
        return reverse("drafts:detail", kwargs={"pk": self.object.pk})


class DraftUpdateView(ImageHandlingMixin, UpdateView):
    model = ProductDraft
    form_class = ProductDraftForm
    template_name = "drafts/draft_form.html"

    def get_success_url(self):
        return reverse("drafts:detail", kwargs={"pk": self.object.pk})


class DraftDetailView(DetailView):
    model = ProductDraft
    template_name = "drafts/draft_detail.html"


class EnrichmentView(DetailView):
    model = ProductDraft
    template_name = "drafts/enrichment.html"

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "enrichment": self.object.enrichments.first(), "ai_configured": OpenAIVisionProvider().configured(), "max_images": settings.OPENAI_MAX_ENRICHMENT_IMAGES}


@require_POST
def run_enrichment(request, pk):
    draft = ProductDraft.objects.get(pk=pk)
    if request.POST.get("consent") != "yes":
        messages.error(request, "Debés confirmar que las imágenes seleccionadas se enviarán al proveedor de IA configurado.")
        return redirect("drafts:enrichment", pk=pk)
    try:
        if not OpenAIVisionProvider().configured():
            raise EnrichmentError("La IA no está configurada. Definí OPENAI_API_KEY y OPENAI_MODEL en el entorno local.")
        connection = active_connection()
        if not connection:
            raise EnrichmentError("No hay conexión de Mercado Libre para validar atributos de categoría.")
        token = access_token(connection, allow_refresh=False)
        definitions = MercadoLibreMetadataService().category_attributes(draft.category_id, token)
        ProductEnrichmentService().run(draft, attribute_definitions=definitions)
        messages.success(request, "Análisis completado. Revisá cada sugerencia antes de aplicarla.")
    except (EnrichmentError, MercadoLibreError) as error:
        messages.error(request, str(error))
    return redirect("drafts:enrichment", pk=pk)


@require_POST
def apply_enrichment(request, pk, enrichment_pk):
    draft = ProductDraft.objects.get(pk=pk)
    enrichment = ProductEnrichment.objects.get(pk=enrichment_pk, draft=draft)
    try:
        _, applied = ProductEnrichmentService().apply(enrichment, request.POST.getlist("suggestion"))
        messages.success(request, f"Se aplicaron {len(applied)} sugerencia(s) sin sobrescribir datos existentes.")
    except EnrichmentError as error:
        messages.error(request, str(error))
    return redirect("drafts:enrichment", pk=pk)


class DraftPreflightView(DetailView):
    model = ProductDraft
    template_name = "drafts/preflight.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["report"] = PublicationPreflightService().run(self.object)
        return context


class CommercialPublicationConfirmView(DetailView):
    model = ProductDraft
    template_name = "drafts/commercial_publication_confirm.html"

    def get_context_data(self, **kwargs):
        report = PublicationPreflightService().run(self.object)
        return {**super().get_context_data(**kwargs), "report": report, "form": CommercialPublicationConfirmationForm(initial={"draft_fingerprint": draft_fingerprint(self.object)}), "production_writes_enabled": settings.MERCADO_LIBRE_ENABLE_PRODUCTION_WRITES}


@require_POST
def publish_commercial_draft(request, pk):
    draft = ProductDraft.objects.get(pk=pk)
    form = CommercialPublicationConfirmationForm(request.POST)
    if not form.is_valid():
        report = PublicationPreflightService().run(draft)
        return render(request, "drafts/commercial_publication_confirm.html", {"object": draft, "report": report, "form": form, "production_writes_enabled": settings.MERCADO_LIBRE_ENABLE_PRODUCTION_WRITES}, status=400)
    try:
        attempt = CommercialPublicationService(LiveCommercialPublicationClient()).publish_initial(draft, confirmation_fingerprint=form.cleaned_data["draft_fingerprint"])
    except PublicationBlocked as error:
        messages.error(request, str(error))
        return redirect("drafts:commercial_publish_confirm", pk=draft.pk)
    messages.success(request, f"Publicación comercial creada: {attempt.item_id}.")
    return redirect("drafts:detail", pk=draft.pk)


class TestPublicationConfirmView(DetailView):
    model = ProductDraft
    template_name = "drafts/test_publication_confirm.html"

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "form": TestPublicationForm(), "report": PublicationPreflightService().run(self.object)}


@require_POST
def publish_test_draft(request, pk):
    draft = ProductDraft.objects.get(pk=pk)
    form = TestPublicationForm(request.POST)
    if not form.is_valid():
        return render(request, "drafts/test_publication_confirm.html", {"object": draft, "form": form, "report": PublicationPreflightService().run(draft)}, status=400)
    try:
        attempt = PublicationService(LiveMercadoLibrePublicationClient()).publish(draft, PublicationOptions(form.cleaned_data["listing_type_id"], form.cleaned_data["channels_json"]), confirmed=form.cleaned_data["confirmed"])
    except PublicationBlocked as error:
        messages.error(request, str(error))
        return redirect("drafts:test_publish_confirm", pk=draft.pk)
    messages.success(request, f"Publicación de prueba creada: {attempt.item_id}.")
    return redirect("drafts:detail", pk=draft.pk)


def _pkce_pair():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class ConnectionStatusView(TemplateView):
    template_name = "drafts/connection_status.html"

    def get_context_data(self, **kwargs):
        oauth = MercadoLibreOAuthService()
        return {
            **super().get_context_data(**kwargs),
            "connection": active_connection(),
            "oauth_configured": oauth.connection_is_configured(),
            "oauth_missing_configuration": oauth.missing_connection_configuration(),
        }


class IntegrationDiagnosticView(TemplateView):
    template_name = "drafts/integration_diagnostic.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        title = self.request.GET.get("title", "").strip()
        category_id = self.request.GET.get("category_id", "").strip().upper()
        context.update({"checks": IntegrationDiagnosticService().run(title=title, category_id=category_id), "title": title, "category_id": category_id})
        return context


def start_connection(request):
    oauth = MercadoLibreOAuthService()
    if not oauth.connection_is_configured():
        messages.error(request, "OAuth no está configurado. Completá: " + ", ".join(oauth.missing_connection_configuration()) + ".")
        return redirect("drafts:connection")
    state = secrets.token_urlsafe(32)
    verifier, challenge = _pkce_pair()
    try:
        authorization_url = oauth.authorization_url(state, challenge)
        create_transaction(state, verifier)
        return redirect(authorization_url)
    except MercadoLibreError as error:
        messages.error(request, str(error))
        return redirect("drafts:connection")


def oauth_callback(request):
    received_state = request.GET.get("state", "")
    code = request.GET.get("code")
    error = request.GET.get("error")
    try:
        verifier = consume_transaction(received_state)
    except MercadoLibreError:
        return HttpResponseBadRequest("La respuesta de autorización no es válida o expiró.")
    if error or not code:
        messages.error(request, "Mercado Libre no autorizó la conexión. Podés intentarlo nuevamente.")
        return redirect("drafts:connection")
    try:
        oauth = MercadoLibreOAuthService()
        token_data = oauth.exchange_code(code, verifier)
        save_tokens(token_data, oauth.seller_profile(token_data.access_token))
    except MercadoLibreError as exception:
        messages.error(request, str(exception))
        return redirect("drafts:connection")
    messages.success(request, "Cuenta de Mercado Libre conectada.")
    return redirect("drafts:connection")


@require_POST
def disconnect(request):
    connection = active_connection()
    if connection:
        connection.delete()
    messages.success(request, "La cuenta fue desconectada y las credenciales locales eliminadas.")
    return redirect("drafts:connection")


@require_GET
def category_suggestions(request):
    title = request.GET.get("title", "").strip()
    if len(title) < 3:
        return render(request, "drafts/partials/category_suggestions.html", {"error": "Ingresá al menos 3 caracteres para buscar categorías."})
    try:
        suggestions = MercadoLibreMetadataService().predict_categories(title, access_token(allow_refresh=False))
        return render(request, "drafts/partials/category_suggestions.html", {"suggestions": suggestions})
    except MercadoLibreError as error:
        return render(request, "drafts/partials/category_suggestions.html", {"error": str(error)})


@require_GET
def category_attributes(request):
    category_id = request.GET.get("category_id", "").strip().upper()
    try:
        connection = active_connection()
        token = access_token(connection, allow_refresh=False)
        metadata = MercadoLibreMetadataService()
        definitions = metadata.category_attributes(category_id, token)
        listing_types = (metadata.available_listing_types(connection.seller_id, category_id, token).get("available") or [])
        form = ProductDraftForm(attribute_definitions=definitions, listing_types=listing_types, initial={"category_id": category_id})
        return render(request, "drafts/partials/category_attributes.html", {"form": form})
    except MercadoLibreError as error:
        return render(request, "drafts/partials/category_attributes.html", {"error": str(error)})
