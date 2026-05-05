from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from apps.users.models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    model = User
    list_display = ("email", "tenant", "role", "is_active", "is_staff", "created_at")
    list_filter = ("role", "is_active", "is_staff", "is_superuser", "tenant")
    search_fields = ("email", "first_name", "last_name", "tenant__business_name", "tenant__slug")
    ordering = ("email",)
    readonly_fields = ("id", "last_login", "created_at", "updated_at", "deleted_at")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Tenant y rol", {"fields": ("tenant", "role")}),
        ("Datos personales", {"fields": ("first_name", "last_name")}),
        ("Permisos Django", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Auditoria", {"fields": ("id", "last_login", "created_at", "updated_at", "deleted_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2", "tenant", "role", "is_active", "is_staff"),
            },
        ),
    )
