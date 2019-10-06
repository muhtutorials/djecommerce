import random
import string

from django.shortcuts import render, get_object_or_404, redirect
from django.views.generic import ListView, DetailView, View
from django.utils import timezone
from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.conf import settings
import stripe

from .models import Item, OrderItem, Order, Address, Payment, Coupon, Refund, UserProfile
from .forms import CheckoutForm, CouponForm, RequestRefundForm, PaymentForm


stripe.api_key = settings.STRIPE_SECRET_KEY


def generate_ref_code():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=20))


def is_valid_form(fields):
    valid = True
    for field in fields:
        if field == '':
            valid = False
    return valid


class HomeView(ListView):
    model = Item
    paginate_by = 10
    template_name = 'home.html'


# since it's a class-based view we should use a mixin instead of a function decorator
class OrderSummaryView(LoginRequiredMixin, View):
    def get(self, *args, **kwargs):
        try:
            order = Order.objects.get(user=self.request.user, ordered=False)
            return render(self.request, 'order_summary.html', {'order': order})
        except ObjectDoesNotExist:
            messages.warning(self.request, 'You do not have an active order')
            return redirect('/')


class CheckoutView(View):
    def get(self, *args, **kwargs):
        try:
            form = CheckoutForm()
            order = Order.objects.get(user=self.request.user, ordered=False)
            context = {
                'form': form,
                'coupon_form': CouponForm(),
                'order': order,
                'DISPLAY_COUPON_FORM': True  # not implemented
            }

            shipping_address_qs = Address.objects.filter(user=self.request.user, address_type='S', default=True)
            if shipping_address_qs.exists():
                context.update({'default_shipping_address': shipping_address_qs[0]})

            billing_address_qs = Address.objects.filter(user=self.request.user, address_type='B', default=True)
            if billing_address_qs.exists():
                context.update({'default_billing_address': billing_address_qs[0]})

        except ObjectDoesNotExist:
            messages.info(self.request, 'You do not have an active order')
            return redirect('core:checkout')

        return render(self.request, 'checkout.html', context)

    def post(self, *args, **kwargs):
        form = CheckoutForm(self.request.POST or None)
        print(form.is_valid())
        try:
            order = Order.objects.get(user=self.request.user, ordered=False)
            if form.is_valid():
                use_default_shipping = form.cleaned_data.get('use_default_shipping')
                if use_default_shipping:
                    shipping_address_qs = Address.objects.filter(user=self.request.user, address_type='S', default=True)
                    if shipping_address_qs.exists():
                        shipping_address = shipping_address_qs[0]
                        order.shipping_address = shipping_address
                        order.save()
                    else:
                        messages.info(self.request, 'No default shipping address was available')
                        return redirect('core:checkout')
                else:
                    shipping_address_1 = form.cleaned_data.get('shipping_address')
                    shipping_address_2 = form.cleaned_data.get('shipping_address_2')
                    shipping_country = form.cleaned_data.get('shipping_country')
                    shipping_zip = form.cleaned_data.get('shipping_zip')

                    # check if forms are filled out
                    if is_valid_form([shipping_address_1, shipping_address_1, shipping_zip]):
                        shipping_address = Address.objects.create(
                            user=self.request.user,
                            street_address=shipping_address_1,
                            apartment_address=shipping_address_2,
                            country=shipping_country,
                            zip=shipping_zip,
                            address_type='S'
                        )

                        order.shipping_address = shipping_address
                        order.save()

                        set_default_shipping = form.cleaned_data.get('set_default_shipping')
                        if set_default_shipping:
                            shipping_address.default = True
                            shipping_address.save()
                    else:
                        messages.info(self.request, 'Please fill in the required shipping address fields')

                use_default_billing = form.cleaned_data.get('use_default_billing')
                same_billing_address = form.cleaned_data.get('same_billing_address')
                if same_billing_address:
                    billing_address = shipping_address
                    billing_address.pk = None  # cloning of shipping address
                    billing_address.save()
                    billing_address.address_type = 'B'
                    billing_address.save()
                    order.billing_address = billing_address
                    order.save()
                elif use_default_billing:
                    billing_address_qs = Address.objects.filter(user=self.request.user, address_type='B', default=True)
                    if billing_address_qs.exists():
                        billing_address = billing_address_qs[0]
                        order.billing_address = billing_address
                        order.save()
                    else:
                        messages.info(self.request, 'No default billing address was available')
                        return redirect('core:checkout')
                else:
                    billing_address_1 = form.cleaned_data.get('billing_address')
                    billing_address_2 = form.cleaned_data.get('billing_address_2')
                    billing_country = form.cleaned_data.get('billing_country')
                    billing_zip = form.cleaned_data.get('billing_zip')

                    # check if forms are filled out
                    if is_valid_form([billing_address_1, billing_address_1, billing_zip]):
                        billing_address = Address.objects.create(
                            user=self.request.user,
                            street_address=billing_address_1,
                            apartment_address=billing_address_2,
                            country=billing_country,
                            zip=billing_zip,
                            address_type='B'
                        )

                        order.billing_address = billing_address
                        order.save()

                        set_default_billing = form.cleaned_data.get('set_default_billing')
                        if set_default_billing:
                            billing_address.default = True
                            billing_address.save()
                    else:
                        messages.info(self.request, 'Please fill in the required billing address fields')

                payment_option = form.cleaned_data.get('payment_option')  # not implemented yet

                if payment_option == 'S':
                    return redirect('core:payment', payment_option='stripe')
                elif payment_option == 'P':
                    return redirect('core:payment', payment_option='paypal')
                else:
                    messages.warning(self.request, 'Invalid payment option selected')
            return redirect('core:checkout')
        except ObjectDoesNotExist:
            messages.warning(self.request, 'You do not have an active order')
            return redirect('/')


class PaymentView(View):
    def get(self, *args, **kwargs):
        order = Order.objects.get(user=self.request.user, ordered=False)
        if order.billing_address:
            context = {
                'order': order,
                'DISPLAY_COUPON_FORM': False
            }
            user_profile = self.request.user.userprofile
            if user_profile.one_click_purchasing:
                # fetch the users card list
                cards = stripe.Customer.list_sources(
                    user_profile.stripe_customer_id,
                    limit=3,
                    object='card'
                )
                card_list = cards['data']
                if len(card_list) > 0:
                    # update the context with the default card
                    context.update({'card': card_list[0]})
            return render(self.request, 'payment.html', context)
        else:
            messages.warning(self.request, 'You have not added a billing address')
            return redirect('core:checkout')

    def post(self, *args, **kwargs):
        order = get_object_or_404(Order, user=self.request.user, ordered=False)
        user_profile = get_object_or_404(UserProfile, user=self.request.user)
        form = PaymentForm(self.request.POST)
        if form.is_valid():
            # doesn't work on test visa card with the number 4242 4242 4242 4242
            # token = self.request.POST.get('stripeToken')
            # https://stripe.com/docs/testing#cards
            token = 'tok_visa'
            save = form.cleaned_data.get('save')
            use_default = form.cleaned_data.get('use_default')

            if save:
                if user_profile.stripe_customer_id != '' and user_profile.stripe_customer_id is not None:
                    customer = stripe.Customer.retrieve(
                        user_profile.stripe_customer_id)
                    customer.sources.create(source=token)

                else:
                    customer = stripe.Customer.create(
                        email=self.request.user.email,
                    )
                    customer.sources.create(source=token)
                    user_profile.stripe_customer_id = customer['id']
                    user_profile.one_click_purchasing = True
                    user_profile.save()

            amount = int(order.get_total() * 100)

            try:

                if use_default or save:
                    # create a charge (https://stripe.com/docs/api/charges/create?lang=python)
                    # charge the customer because we cannot charge the token more than once
                    charge = stripe.Charge.create(
                        amount=amount,  # cents
                        currency="usd",
                        customer=user_profile.stripe_customer_id
                    )
                else:
                    # charge once off on the token
                    charge = stripe.Charge.create(
                        amount=amount,  # cents
                        currency="usd",
                        source=token  # obtained with Stripe.js
                    )

                # create the payment
                payment = Payment()
                payment.stripe_charge_id = charge['id']
                payment.user = self.request.user
                payment.amount = order.get_total()
                payment.save()

                # assign the payment to the order

                order_items = order.items.all()
                order_items.update(ordered=True)
                for item in order_items:
                    item.save()

                order.ordered = True
                order.payment = payment
                order.ref_code = generate_ref_code()
                order.save()

                messages.success(self.request, "Your order was successful!")
                return redirect('/')

            # stripe error handling (https://stripe.com/docs/api/errors/handling?lang=python)
            except stripe.error.CardError as e:
                # Since it's a decline, stripe.error.CardError will be caught
                body = e.json_body
                err = body.get('error', {})
                messages.warning(self.request, f"{err.get('message')}")
                return redirect('/')

            except stripe.error.RateLimitError as e:
                # Too many requests made to the API too quickly
                messages.warning(self.request, 'Too many requests made to the API too quickly')
                return redirect('/')

            except stripe.error.InvalidRequestError as e:
                # Invalid parameters were supplied to Stripe's API
                messages.warning(self.request, "Invalid parameters were supplied to Stripe's API")
                return redirect('/')

            except stripe.error.AuthenticationError as e:
                # Authentication with Stripe's API failed
                # (maybe you changed API keys recently)
                messages.warning(self.request, "Authentication with Stripe's API failed")
                return redirect('/')

            except stripe.error.APIConnectionError as e:
                # Network communication with Stripe failed
                messages.warning(self.request, "Network communication with Stripe failed")
                return redirect('/')

            except stripe.error.StripeError as e:
                # Display a very generic error to the user, and maybe send
                # yourself an email
                messages.warning(self.request, 'Something went wrong. You were not charged. Please try again')
                return redirect('/')

            except Exception as e:
                # send an email to myself because there's something wrong with my code
                messages.warning(self.request, 'Serious error occurred. We have been notified')
                return redirect('/')


class ItemDetailView(DetailView):
    model = Item
    template_name = 'product.html'


@login_required
def add_to_cart(request, slug):
    item = get_object_or_404(Item, slug=slug)
    # ordered is a field which we set so we don't get an item that's already been purchased
    order_item, created = OrderItem.objects.get_or_create(user=request.user, item=item, ordered=False)
    order_qs = Order.objects.filter(user=request.user, ordered=False)  # ordered is a field
    if order_qs.exists():
        order = order_qs[0]
        # check if the ordered item is in the order
        if order.items.filter(item__slug=slug).exists():
            order_item.quantity += 1
            order_item.save()
            messages.info(request, 'This item quantity was updated')
            return redirect('core:order-summary')
        else:
            order.items.add(order_item)
            messages.info(request, 'This item was added to your cart')
            return redirect('core:order-summary')
    else:
        ordered_date = timezone.now()
        order = Order.objects.create(user=request.user, ordered_date=ordered_date)
        order.items.add(order_item)
        messages.info(request, 'This item was added to your cart')
        return redirect('core:order-summary')


# set quantity to zero
@login_required
def remove_from_cart(request, slug):
    item = get_object_or_404(Item, slug=slug)
    order_qs = Order.objects.filter(user=request.user, ordered=False)  # ordered is a field
    if order_qs.exists():
        order = order_qs[0]
        # check if the ordered item is in the order
        if order.items.filter(item__slug=slug).exists():
            order_item = OrderItem.objects.filter(user=request.user, item=item, ordered=False)[0]
            order_item.delete()
            messages.info(request, 'This item was removed from your cart')
            return redirect('core:order-summary')
        else:
            messages.info(request, 'This item was not in your cart')
            return redirect('core:product', slug=slug)
    else:
        messages.info(request, 'You do not have an active order')
        return redirect('core:product', slug=slug)


# decrease quantity
@login_required
def remove_single_item_from_cart(request, slug):
    item = get_object_or_404(Item, slug=slug)
    order_qs = Order.objects.filter(user=request.user, ordered=False)  # ordered is a field
    if order_qs.exists():
        order = order_qs[0]
        # check if the ordered item is in the order
        if order.items.filter(item__slug=slug).exists():
            order_item = OrderItem.objects.filter(user=request.user, item=item, ordered=False)[0]
            if order_item.quantity > 1:
                order_item.quantity -= 1
                order_item.save()
            else:
                order.items.remove(order_item)
            messages.info(request, 'This item quantity was updated')
            return redirect('core:order-summary')
        else:
            messages.info(request, 'This item was not in your cart')
            return redirect('core:product', slug=slug)
    else:
        messages.info(request, 'You do not have an active order')
        return redirect('core:product', slug=slug)


def get_coupon(request, code):
    try:
        coupon = Coupon.objects.get(code=code)
        return coupon
    except ObjectDoesNotExist:
        messages.info(request, 'This coupon does not exist')
        return redirect('core:checkout')


class AddCoupon(View):
    def post(self, *args, **kwargs):
        form = CouponForm(self.request.POST)
        if form.is_valid():
            try:
                code = form.cleaned_data.get('code')
                order = Order.objects.get(user=self.request.user, ordered=False)
                order.coupon = get_coupon(self.request, code)
                order.save()
                messages.success(self.request, 'Successfully added coupon')
                return redirect('core:checkout')

            except ObjectDoesNotExist:
                messages.info(self.request, 'You do not have an active order')
                return redirect('core:checkout')


class RequestRefundView(View):
    def get(self, *args, **kwargs):
        form = RequestRefundForm()
        return render(self.request, 'request_refund.html', {'form': form})

    def post(self, *args, **kwargs):
        form = RequestRefundForm(self.request.POST)
        if form.is_valid():
            ref_code = form.cleaned_data.get('ref_code')
            message = form.cleaned_data.get('message')
            email = form.cleaned_data.get('email')
            try:
                order = Order.objects.get(ref_code=ref_code)
                order.refund_requested = True
                order.save()

                refund = Refund()
                refund.order = order
                refund.reason = message
                refund.email = email
                refund.save()
                messages.info(self.request, 'Your request was received')
                return redirect('core:request-refund')

            except ObjectDoesNotExist:
                messages.info(self.request, 'This order does not exist')
                return redirect('core:request-refund')
