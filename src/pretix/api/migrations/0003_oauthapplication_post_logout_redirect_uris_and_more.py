# Generated by Django 4.2.13 on 2024-07-09 08:13

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('pretixapi', '0002_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='oauthapplication',
            name='post_logout_redirect_uris',
            field=models.TextField(default=''),
        ),
    ]