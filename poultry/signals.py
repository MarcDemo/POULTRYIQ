from django.db.models.signals import post_save
from django.dispatch import receiver

from .bird_ledger import record_batch_stocking
from .models import PoultryBatch


@receiver(post_save, sender=PoultryBatch)
def create_stocking_movement_for_new_batch(sender, instance, created, **kwargs):
    if created and not instance.origin_batch_id:
        record_batch_stocking(instance, operator=instance.created_by)
