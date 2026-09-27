from django import template

from poultry.units import format_eggs_as_trays


register = template.Library()


@register.filter
def egg_trays(value):
    return format_eggs_as_trays(value)
