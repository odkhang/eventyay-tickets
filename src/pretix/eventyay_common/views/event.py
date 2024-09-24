from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import Prefetch, Min, Max, F
from django.db.models.functions import Greatest, Coalesce
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from django.views.generic import ListView
from pytz import timezone

from pretix.base.forms import SafeSessionWizardView
from pretix.base.i18n import language
from pretix.base.models import Event, EventMetaValue, Quota, Organizer
from pretix.base.services import tickets
from pretix.base.services.quotas import QuotaAvailability
from pretix.control.forms.event import EventWizardFoundationForm, EventWizardBasicsForm, EventUpdateForm
from pretix.control.forms.filter import EventFilterForm
from pretix.control.permissions import EventPermissionRequiredMixin
from pretix.control.views import PaginationMixin, UpdateView
from pretix.control.views.event import DecoupleMixin, EventSettingsViewMixin
from pretix.control.views.item import MetaDataEditorMixin
from pretix.eventyay_common.forms.event import EventCommonSettingsForm
from pretix.eventyay_common.tasks import send_event_webhook


class EventList(PaginationMixin, ListView):
    model = Event
    context_object_name = 'events'
    template_name = 'eventyay_common/events/index.html'

    def get_queryset(self):
        query_set = self.request.user.get_events_with_any_permission(self.request).prefetch_related(
            'organizer', '_settings_objects', 'organizer___settings_objects', 'organizer__meta_properties',
            Prefetch(
                'meta_values',
                EventMetaValue.objects.select_related('property'),
                to_attr='meta_values_cached'
            )
        ).order_by('-date_from')

        query_set = query_set.annotate(
            min_from=Min('subevents__date_from'),
            max_from=Max('subevents__date_from'),
            max_to=Max('subevents__date_to'),
            max_fromto=Greatest(Max('subevents__date_to'), Max('subevents__date_from'))
        ).annotate(
            order_from=Coalesce('min_from', 'date_from'),
            order_to=Coalesce('max_fromto', 'max_to', 'max_from', 'date_to', 'date_from'),
        )

        query_set = query_set.prefetch_related(
            Prefetch('quotas',
                     queryset=Quota.objects.filter(subevent__isnull=True).annotate(s=Coalesce(F('size'), 0)).order_by(
                         '-s'),
                     to_attr='first_quotas')
        )

        if self.filter_form.is_valid():
            query_set = self.filter_form.filter_qs(query_set)
        return query_set

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['filter_form'] = self.filter_form

        quotas = []
        for s in ctx['events']:
            s.first_quotas = s.first_quotas[:4]
            quotas += list(s.first_quotas)

        qa = QuotaAvailability(early_out=False)
        for q in quotas:
            qa.queue(q)
        qa.compute()

        for q in quotas:
            q.cached_avail = qa.results[q]
            q.cached_availability_paid_orders = qa.count_paid_orders.get(q, 0)
            if q.size is not None:
                q.percent_paid = min(
                    100,
                    round(q.cached_availability_paid_orders / q.size * 100) if q.size > 0 else 100
                )
        return ctx

    @cached_property
    def filter_form(self):
        return EventFilterForm(data=self.request.GET, request=self.request)


class EventCreateView(SafeSessionWizardView):
    form_list = [
        ('foundation', EventWizardFoundationForm),
        ('basics', EventWizardBasicsForm),
    ]
    templates = {
        'foundation': 'eventyay_common/events/create_foundation.html',
        'basics': 'eventyay_common/events/create_basics.html',
    }
    condition_dict = {}

    def get_form_initial(self, step):
        initial_form = super().get_form_initial(step)
        request_user = self.request.user
        request_get = self.request.GET

        if step == 'foundation' and 'organizer' in request_get:
            try:
                queryset = Organizer.objects.all()
                if not request_user.has_active_staff_session(self.request.session.session_key):
                    queryset = queryset.filter(
                        id__in=request_user.teams.filter(can_create_events=True).values_list('organizer', flat=True)
                    )
                initial_form['organizer'] = queryset.get(slug=request_get.get('organizer'))
            except Organizer.DoesNotExist:
                pass

        return initial_form

    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, form, **kwargs):
        context = super().get_context_data(form, **kwargs)
        context['create_for'] = self.storage.extra_data.get('create_for', 'all')
        context['has_organizer'] = self.request.user.teams.filter(can_create_events=True).exists()
        if self.steps.current == 'basics':
            context['organizer'] = self.get_cleaned_data_for_step('foundation').get('organizer')
        return context

    def render(self, form=None, **kwargs):
        if self.steps.current == 'basics' and 'create_for' in self.request.POST:
            self.storage.extra_data['create_for'] = self.request.POST.get('create_for')
        if self.steps.current != 'foundation':
            form_data = self.get_cleaned_data_for_step('foundation')
            if form_data is None:
                return self.render_goto_step('foundation')

        return super().render(form, **kwargs)

    def get_form_kwargs(self, step=None):
        kwargs = {
            'user': self.request.user,
            'session': self.request.session,
        }
        if step != 'foundation':
            form_data = self.get_cleaned_data_for_step('foundation')
            if form_data is None:
                form_data = {
                    'organizer': Organizer(slug='_nonexisting'),
                    'has_subevents': False,
                    'locales': ['en']
                }
            kwargs.update(form_data)
        return kwargs

    def get_template_names(self):
        return [self.templates[self.steps.current]]

    def done(self, form_list, form_dict, **kwargs):
        foundation_data = self.get_cleaned_data_for_step('foundation')
        basics_data = self.get_cleaned_data_for_step('basics')

        create_for = self.storage.extra_data.get('create_for')

        if create_for == "talk":
            event_dict = {
                'organiser_slug': foundation_data.get('organizer').slug if foundation_data.get('organizer') else None,
                'name': basics_data.get('name').data if basics_data.get('name') else None,
                'slug': basics_data.get('slug'),
                'is_public': False,
                'date_from': str(basics_data.get('date_from')),
                'date_to': str(basics_data.get('date_to')),
                'timezone': str(basics_data.get('timezone')),
                'locale': basics_data.get('locale'),
                'locales': foundation_data.get('locales'),
            }
            send_event_webhook.delay(user_id=self.request.user.id, event=event_dict, action='create')

        else:
            with transaction.atomic(), language(basics_data['locale']):
                event = form_dict['basics'].instance
                event.organizer = foundation_data['organizer']
                event.plugins = settings.PRETIX_PLUGINS_DEFAULT
                event.has_subevents = foundation_data['has_subevents']
                event.testmode = True
                form_dict['basics'].save()

                event.checkin_lists.create(
                    name=_('Default'),
                    all_products=True
                )
                event.set_defaults()
                event.settings.set('timezone', basics_data['timezone'])
                event.settings.set('locale', basics_data['locale'])
                event.settings.set('locales', foundation_data['locales'])
                if create_for == 'all':
                    event_dict = {
                        'organiser_slug': event.organizer.slug,
                        'name': event.name.data,
                        'slug': event.slug,
                        'is_public': event.live,
                        'date_from': str(event.date_from),
                        'date_to': str(event.date_to),
                        'timezone': str(basics_data.get('timezone')),
                        'locale': event.settings.locale,
                        'locales': event.settings.locales,
                    }
                    send_event_webhook.delay(user_id=self.request.user.id, event=event_dict, action='create')
                event.settings.set('create_for', create_for)
        return redirect(reverse('eventyay_common:events') + '?congratulations=1')


class EventUpdate(DecoupleMixin, EventSettingsViewMixin, EventPermissionRequiredMixin, MetaDataEditorMixin, UpdateView):
    model = Event
    form_class = EventUpdateForm
    template_name = 'eventyay_common/event/settings.html'
    permission = 'can_change_event_settings'

    @cached_property
    def object(self) -> Event:
        return self.request.event

    def get_object(self, queryset=None) -> Event:
        return self.object

    @cached_property
    def sform(self):
        return EventCommonSettingsForm(
            obj=self.object,
            prefix='settings',
            data=self.request.POST if self.request.method == 'POST' else None,
            files=self.request.FILES if self.request.method == 'POST' else None,
        )

    def get_context_data(self, *args, **kwargs) -> dict:
        context = super().get_context_data(*args, **kwargs)
        context['sform'] = self.sform
        talk_host = settings.TALK_HOSTNAME
        context['talk_edit_url'] = talk_host + '/orga/event/' + self.object.slug + '/settings'
        return context

    @transaction.atomic
    def form_valid(self, form):
        self._save_decoupled(self.sform)
        self.sform.save()

        tickets.invalidate_cache.apply_async(kwargs={'event': self.request.event.pk})
        messages.success(self.request, _('Your changes have been saved.'))
        return super().form_valid(form)

    def get_success_url(self) -> str:
        return reverse('eventyay_common:event.update', kwargs={
            'organizer': self.object.organizer.slug,
            'event': self.object.slug,
        })

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if self.request.user.has_active_staff_session(self.request.session.session_key):
            kwargs['change_slug'] = True
            kwargs['domain'] = True
        return kwargs

    def post(self, request, *args, **kwargs):
        form = self.get_form()
        form.instance.sales_channels = ['web']
        if form.is_valid() and self.sform.is_valid():
            zone = timezone(self.sform.cleaned_data['timezone'])
            event = form.instance
            event.date_from = self.reset_timezone(zone, event.date_from)
            event.date_to = self.reset_timezone(zone, event.date_to)
            if event.settings.create_for and event.settings.create_for == 'all':
                event_dict = {
                    'organiser_slug': event.organizer.slug,
                    'name': event.name.data,
                    'slug': event.slug,
                    'date_from': str(event.date_from),
                    'date_to': str(event.date_to),
                    'timezone': str(event.settings.timezone),
                    'locale': event.settings.locale,
                    'locales': event.settings.locales,
                }
                send_event_webhook.delay(user_id=self.request.user.id, event=event_dict, action='update')
            return self.form_valid(form)
        else:
            messages.error(self.request, _('We could not save your changes. See below for details.'))
            return self.form_invalid(form)

    @staticmethod
    def reset_timezone(tz, dt):
        return tz.localize(dt.replace(tzinfo=None)) if dt is not None else None