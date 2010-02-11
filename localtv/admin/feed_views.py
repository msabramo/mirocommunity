# Copyright 2009 - Participatory Culture Foundation
# 
# This file is part of Miro Community.
# 
# Miro Community is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or (at your
# option) any later version.
# 
# Miro Community is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
# 
# You should have received a copy of the GNU Affero General Public License
# along with Miro Community.  If not, see <http://www.gnu.org/licenses/>.

import datetime
import hashlib
import re

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.urlresolvers import reverse
from django.http import (HttpResponse, HttpResponseBadRequest,
                         HttpResponseRedirect)
from django.shortcuts import get_object_or_404, render_to_response
from django.template import RequestContext

from localtv.decorators import get_sitelocation, require_site_admin, \
    referrer_redirect
from localtv import models, util
from localtv.admin import forms

from vidscraper import bulk_import

VIDEO_SERVICE_TITLES = (
    re.compile(r'Uploads by (.+)'),
    re.compile(r"Vimeo / (.+)'s? uploaded videos"),
    re.compile(r'Vimeo / (.+)')
    )

@require_site_admin
@get_sitelocation
def add_feed(request, sitelocation=None):
    if request.method == 'GET':
        def gen():
            yield render_to_response('localtv/admin/feed_wait.html',
                                     {
                    'message': 'Checking out this URL',
                    'feed_url': request.GET.get('feed_url')},
                                     context_instance=RequestContext(request))
            yield add_feed_response(request, sitelocation)
        return util.HttpMixedReplaceResponse(request, gen())
    else:
        return add_feed_response(request, sitelocation)


def add_feed_response(request, sitelocation=None):
    add_form = forms.AddFeedForm(request.GET)

    if not add_form.is_valid():
        return HttpResponseBadRequest(
            add_form['feed_url'].errors.as_text())

    feed_url = add_form.cleaned_data['feed_url']
    parsed_feed = request.session['parsed_feed'] = \
        add_form.cleaned_data['parsed_feed']

    title = parsed_feed.feed.title
    for regexp in VIDEO_SERVICE_TITLES:
        match = regexp.match(title)
        if match:
            title = match.group(1)
            break

    defaults = {
        'name': title,
        'feed_url': feed_url,
        'webpage': parsed_feed.feed.get('link', ''),
        'description': parsed_feed.feed.get('summary', ''),
        'when_submitted': datetime.datetime.now(),
        'last_updated': datetime.datetime.now(),
        'status': models.FEED_STATUS_ACTIVE,
        'user': request.user,
        'etag': '',
        'auto_approve': bool(request.POST.get('auto_approve', False))}

    video_count = request.session['video_count'] = bulk_import.video_count(
        feed_url, parsed_feed)

    if request.method == 'POST':
        if 'cancel' in request.POST:
            # clean up the session
            del request.session['parsed_feed']
            del request.session['video_count']

            return HttpResponseRedirect(reverse('localtv_admin_manage_page'))

        form = forms.SourceForm(request.POST, instance=models.Feed(**defaults))
        if form.is_valid():
            feed, created = models.Feed.objects.get_or_create(
                feed_url=defaults['feed_url'],
                site=sitelocation.site,
                defaults=defaults)

            if not created:
                for key, value in defaults.items():
                    setattr(feed, key, value)

            for key, value in form.cleaned_data.items():
                setattr(feed, key, value)

            if feed.video_service():
                user, created = User.objects.get_or_create(
                    username=feed.name[:30],
                    defaults={'email': ''})
                if created:
                    user.set_unusable_password()
                    models.Profile.objects.create(
                        user=user,
                        website=defaults['webpage'])
                    user.save()
                feed.auto_authors.add(user)
            feed.save()

            return HttpResponseRedirect(reverse('localtv_admin_feed_add_done',
                                                args=[feed.pk]))

    else:
        form = forms.SourceForm(instance=models.Feed(**defaults))
    return render_to_response('localtv/admin/add_feed.html',
                              {'form': form,
                               'video_count': video_count},
                              context_instance=RequestContext(request))


@require_site_admin
@get_sitelocation
def add_feed_done(request, feed_id, sitelocation):
    feed = get_object_or_404(
        models.Feed,
        pk=feed_id)

    def gen():
        context = {
            'message': 'Importing %i videos from' % (
                request.session['video_count'],),
            'feed_url': request.session['parsed_feed']['feed']['link']}
        yield render_to_response('localtv/admin/feed_wait.html',
                                 context,
                                 context_instance=RequestContext(request))

        cache_key = 'localtv-admin-feed-import:%s' % feed.feed_url
        if len(cache_key) >= 250:
            cache_key = 'localtv-admin-feed-import-hash:%s' % (
                hashlib.sha1(feed.feed_url).hexdigest(),)

        parsed_feed = cache.get(cache_key)
        if not parsed_feed:
            parsed_feed = bulk_import.bulk_import(
                feed.feed_url, request.session['parsed_feed'])
            cache.set(cache_key, parsed_feed)

        for last in feed._update_items_generator(parsed_feed=parsed_feed):
            if last['index'] + 1 != last['total']: # last video
                context['message'] = 'Imported %i of %i videos from' % (
                    last['index'] + 1, last['total'])
                yield render_to_response(
                    'localtv/admin/feed_wait.html',
                    context,
                    context_instance=RequestContext(request))

        # clean up the session
        del request.session['parsed_feed']
        del request.session['video_count']

        yield render_to_response('localtv/admin/feed_done.html',
                                 {'feed': feed},
                                 context_instance=RequestContext(request))

    return util.HttpMixedReplaceResponse(request, gen())


@referrer_redirect
@require_site_admin
@get_sitelocation
def feed_auto_approve(request, feed_id, sitelocation=None):
    feed = get_object_or_404(
        models.Feed,
        id=feed_id,
        site=sitelocation.site)

    feed.auto_approve = not request.GET.get('disable')
    feed.save()

    return HttpResponse('SUCCESS')
