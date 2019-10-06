# Development server won’t automatically restart
# After adding the templatetags module,
# you will need to restart your server before you can use the tags or filters in templates.
from django import template
from core.models import Order


register = template.Library()


# register is the variable from above
@register.filter
def cart_item_count(user):
    if user.is_authenticated:
        qs = Order.objects.filter(user=user, ordered=False)
        if qs.exists():
            return qs[0].items.count()
    return 0
