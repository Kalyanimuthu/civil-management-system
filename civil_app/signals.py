from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Site, Department, DefaultRate


DEFAULT_DEPARTMENTS = [
    "Civil", "Electrical", "Carpenter",
    "Plumbing", "Painting", "Tiles", "Misc"
]

# 🔥 Create default departments ONCE (global)
@receiver(post_save, sender=Site)
def create_departments(sender, instance, created, **kwargs):
    if created:
        for dept in DEFAULT_DEPARTMENTS:
            Department.objects.get_or_create(name=dept)


# 🔥 Create default payment for each department
@receiver(post_save, sender=Department)
def create_default_rate(sender, instance, created, **kwargs):
    if created and instance.name != "Civil":
        DefaultRate.objects.get_or_create(
            department=instance,
            defaults={
                "full_day_rate": 0,
                "is_locked": False
            }
        )
