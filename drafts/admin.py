from django.contrib import admin
from .models import MercadoLibreConnection, ProductDraft, ProductImage, PublicationAttempt


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0


@admin.register(ProductDraft)
class ProductDraftAdmin(admin.ModelAdmin):
    list_display = ("title", "price", "currency", "status", "updated_at")
    list_filter = ("status", "condition")
    search_fields = ("title", "category_id")
    inlines = [ProductImageInline]


@admin.register(MercadoLibreConnection)
class MercadoLibreConnectionAdmin(admin.ModelAdmin):
    list_display = ("seller_id", "nickname", "token_expires_at", "updated_at")
    readonly_fields = ("seller_id", "nickname", "token_expires_at", "scopes", "created_at", "updated_at")
    exclude = ("access_token_encrypted", "refresh_token_encrypted")


@admin.register(PublicationAttempt)
class PublicationAttemptAdmin(admin.ModelAdmin):
    list_display = ("draft", "seller_id", "flow", "status", "item_id", "created_at")
    list_filter = ("flow", "status")
    readonly_fields = ("draft", "seller_id", "flow", "listing_type_id", "channels", "request_key", "payload_snapshot", "picture_ids", "item_id", "user_product_id", "permalink", "safe_error", "created_at", "updated_at")
