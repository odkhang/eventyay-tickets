# Generated by Django 4.2.16 on 2024-10-11 07:54

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pretixbase", "0004_create_customer_table"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrganizerBillingModel",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False
                    ),
                ),
                ("primary_contact_name", models.CharField(max_length=255)),
                ("primary_contact_email", models.EmailField(max_length=255)),
                ("company_or_organization_name", models.CharField(max_length=255)),
                ("address_line_1", models.CharField(max_length=255)),
                ("address_line_2", models.CharField(max_length=255)),
                ("city", models.CharField(max_length=255)),
                ("zip_code", models.CharField(max_length=255)),
                ("country", models.CharField(max_length=255)),
                ("preferred_language", models.CharField(max_length=255)),
                ("tax_id", models.CharField(max_length=255)),
                ("payment_information", models.TextField()),
                (
                    "organizer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="billing",
                        to="pretixbase.organizer",
                    ),
                ),
            ],
        ),
    ]
