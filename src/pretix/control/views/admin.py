from django.utils.functional import cached_property
from django.views.generic import ListView, TemplateView, DetailView

from pretix.base.models import Organizer
from pretix.base.models.billing import BillingInvoice
from pretix.control.forms.filter import OrganizerFilterForm
from pretix.control.permissions import AdministratorPermissionRequiredMixin
from pretix.control.views import PaginationMixin


class AdminDashboard(AdministratorPermissionRequiredMixin, TemplateView):
    template_name = 'pretixcontrol/admin/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return context


class OrganizerList(PaginationMixin, ListView):
    model = Organizer
    context_object_name = 'organizers'
    template_name = 'pretixcontrol/admin/organizers.html'

    def get_queryset(self):
        qs = Organizer.objects.all()
        if self.filter_form.is_valid():
            qs = self.filter_form.filter_qs(qs)
        if self.request.user.has_active_staff_session(self.request.session.session_key):
            return qs
        else:
            return qs.filter(pk__in=self.request.user.teams.values_list('organizer', flat=True))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['filter_form'] = self.filter_form
        return ctx

    @cached_property
    def filter_form(self):
        return OrganizerFilterForm(data=self.request.GET, request=self.request)


class InvoiceList(PaginationMixin, ListView):
    model = BillingInvoice
    template_name = 'pretixcontrol/admin/invoices.html'
    context_object_name = 'invoices'

    def get_queryset(self):
        return BillingInvoice.objects.select_related('event', 'organizer')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return context


class InvoiceDetail(DetailView):
    model = BillingInvoice
    template_name = 'pretixcontrol/admin/invoice_detail.html'
    context_object_name = 'invoice'
    pk_url_kwarg = 'id'

    def get_queryset(self):
        return BillingInvoice.objects.select_related(
            'event',
            'organizer'
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        invoice = self.get_object()

        context.update({
            'invoice_details': {
                'id': invoice.id,
                'organizer_name': invoice.organizer.name,
                'event_slug': invoice.event.slug,
                'amount': invoice.amount,
                'currency': invoice.currency,
                'ticket_fee': invoice.ticket_fee,
                'monthly_bill': invoice.monthly_bill,
                'status': invoice.status,
                'created_at': invoice.created_at,
            }
        })
        return context
