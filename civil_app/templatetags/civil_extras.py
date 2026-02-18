from django import template

register = template.Library()

@register.filter
def get_item(value, key):
    try:
        if isinstance(value, dict):
            return value.get(key)
        return None
    except Exception:
        return None
