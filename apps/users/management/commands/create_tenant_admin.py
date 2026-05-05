from getpass import getpass

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from apps.tenants.models import Tenant
from apps.users.models import User


class Command(BaseCommand):
    help = "Create or update a tenant and its tenant_admin user."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--business-name", required=True)
        parser.add_argument("--password")
        parser.add_argument("--slug")
        parser.add_argument("--trade-name", default="")
        parser.add_argument("--nit", default="")
        parser.add_argument("--first-name", default="")
        parser.add_argument("--last-name", default="")

    @transaction.atomic
    def handle(self, *args, **options):
        email = User.objects.normalize_email(options["email"])
        password = options.get("password") or getpass("Password: ")
        if len(password) < 8:
            raise CommandError("Password must be at least 8 characters.")

        business_name = options["business_name"].strip()
        slug = options.get("slug") or slugify(options["trade_name"] or business_name)[:70]
        if not slug:
            raise CommandError("Tenant slug could not be generated.")

        tenant, tenant_created = Tenant.objects.update_or_create(
            slug=slug,
            defaults={
                "business_name": business_name,
                "trade_name": options["trade_name"],
                "nit": options["nit"],
                "email": email,
                "status": "active",
                "is_active": True,
            },
        )

        user, user_created = User.objects.update_or_create(
            email=email,
            defaults={
                "tenant": tenant,
                "first_name": options["first_name"],
                "last_name": options["last_name"],
                "role": User.Role.TENANT_ADMIN,
                "is_active": True,
                "is_staff": False,
            },
        )
        user.set_password(password)
        user.save(update_fields=["password", "updated_at"])

        tenant_status = "created" if tenant_created else "updated"
        user_status = "created" if user_created else "updated"
        self.stdout.write(self.style.SUCCESS(f"Tenant {tenant_status}: {tenant.slug}"))
        self.stdout.write(self.style.SUCCESS(f"Tenant admin {user_status}: {user.email}"))
