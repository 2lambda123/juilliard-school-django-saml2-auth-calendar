#!/usr/bin/env python
# -*- coding:utf-8 -*-

import re
import logging
from saml2 import (
    BINDING_HTTP_POST,
    BINDING_HTTP_REDIRECT,
    entity,
)
from saml2.client import Saml2Client
from saml2.config import Config as Saml2Config

from django import get_version
from pkg_resources import parse_version
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, logout
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.template import TemplateDoesNotExist
from django.http import HttpResponseRedirect
from django.utils.http import is_safe_url


try:
    import urllib2 as _urllib
except:
    import urllib.request as _urllib
    import urllib.error
    import urllib.parse


User = get_user_model()
logger = logging.getLogger(__name__)


def get_current_domain(r):
    if 'ASSERTION_URL' in settings.SAML2_AUTH:
        return settings.SAML2_AUTH['ASSERTION_URL']
    return '{scheme}://{host}'.format(
        scheme='https' if r.is_secure() else 'http',
        host=r.get_host(),
    )


def get_reverse(objs):
    '''In order to support different django version, I have to do this '''
    if parse_version(get_version()) >= parse_version('2.0'):
        from django.urls import reverse
    else:
        from django.core.urlresolvers import reverse
    if objs.__class__.__name__ not in ['list', 'tuple']:
        objs = [objs]

    for obj in objs:
        try:
            return reverse(obj)
        except:
            pass
    raise Exception('We got a URL reverse issue: %s. This is a known issue but please still submit a ticket at https://github.com/fangli/django-saml2-auth/issues/new' % str(objs))


def _get_saml_client(domain):
    acs_url = domain + get_reverse([acs, 'acs', 'django_saml2_auth:acs'])

    saml_settings = {
        'metadata': {
            'remote': [
                {
                    "url": settings.SAML2_AUTH['METADATA_AUTO_CONF_URL'],
                },
            ],
        },
        'service': {
            'sp': {
                'endpoints': {
                    'assertion_consumer_service': [
                        (acs_url, BINDING_HTTP_REDIRECT),
                        (acs_url, BINDING_HTTP_POST)
                    ],
                },
                'allow_unsolicited': True,
                'authn_requests_signed': False,
                'logout_requests_signed': True,
                'want_assertions_signed': True,
                'want_response_signed': False,
            },
        },
    }

    if 'ENTITY_ID' in settings.SAML2_AUTH:
        saml_settings['entityid'] = settings.SAML2_AUTH['ENTITY_ID']

    if 'NAME_ID_FORMAT' in settings.SAML2_AUTH:
        saml_settings['service']['sp']['name_id_format'] = settings.SAML2_AUTH['NAME_ID_FORMAT']

    spConfig = Saml2Config()
    spConfig.load(saml_settings)
    spConfig.allow_unknown_attributes = True
    saml_client = Saml2Client(config=spConfig)
    return saml_client


@login_required
def welcome(r):
    try:
        return render(r, 'django_saml2_auth/welcome.html', {'user': r.user})
    except TemplateDoesNotExist:
        return HttpResponseRedirect(settings.SAML2_AUTH.get('DEFAULT_NEXT_URL', get_reverse('admin:index')))


def denied(r):
    return render(r, 'django_saml2_auth/denied.html')


@csrf_exempt
def acs(request):
    saml_client = _get_saml_client(get_current_domain(request))
    resp = request.POST.get('SAMLResponse', None)
    next_url = request.session.get('login_next_url', settings.SAML2_AUTH.get('DEFAULT_NEXT_URL', get_reverse('admin:index')))

    if not resp:
        logger.error('No SAMLResponse')
        return HttpResponseRedirect(get_reverse([denied, 'denied', 'django_saml2_auth:denied']))

    authn_response = saml_client.parse_authn_request_response(
        resp, entity.BINDING_HTTP_POST)
    if authn_response is None:
        logger.error('No authn response after parsing request')
        return HttpResponseRedirect(get_reverse([denied, 'denied', 'django_saml2_auth:denied']))

    try:
        regex = '<saml2:NameID.*>(.*)<\/saml2:NameID>'
        nameid = re.findall(regex, str(authn_response))
        email = nameid[0]
        username = email.split('@')[0].lower()
    except IndexError:
        logger.error('Could not find user in response. NameID: {}'.format(nameid))
        return HttpResponseRedirect(get_reverse([denied, 'denied', 'django_saml2_auth:denied']))
    
    try:        
        target_user = User.objects.filter(is_active=True).get(username__iexact=username)
    except User.DoesNotExist:
        target_user = User.objects.create_user(username, email)
        target_user.is_active = True
        target_user.is_staff = True
        target_user.save()    

    request.session.flush()
    target_user.backend = 'django.contrib.auth.backends.ModelBackend'
    login(request, target_user)

    return HttpResponseRedirect(next_url)


def signin(r):
    try:
        import urlparse as _urlparse
        from urllib import unquote
    except:
        import urllib.parse as _urlparse
        from urllib.parse import unquote
    next_url = r.GET.get('next', settings.SAML2_AUTH.get('DEFAULT_NEXT_URL', get_reverse('admin:index')))

    try:
        if 'next=' in unquote(next_url):
            next_url = _urlparse.parse_qs(_urlparse.urlparse(unquote(next_url)).query)['next'][0]
    except:
        next_url = r.GET.get('next', settings.SAML2_AUTH.get('DEFAULT_NEXT_URL', get_reverse('admin:index')))

    # Only permit signin requests where the next_url is a safe URL
    if not is_safe_url(next_url, None):
        logger.error('Next URL is not safe url in Okta login.')
        return HttpResponseRedirect(get_reverse([denied, 'denied', 'django_saml2_auth:denied']))

    r.session['login_next_url'] = next_url

    saml_client = _get_saml_client(get_current_domain(r))
    _, info = saml_client.prepare_for_authenticate()

    redirect_url = None

    for key, value in info['headers']:
        if key == 'Location':
            redirect_url = value
            break

    return HttpResponseRedirect(redirect_url)


def signout(r):
    logout(r)
    return render(r, 'django_saml2_auth/signout.html')
