from __future__ import unicode_literals

import base64
import calendar
import datetime
import re
import sys
import unicodedata
from binascii import Error as BinasciiError
from email.utils import formatdate

from django.core.exceptions import SuspiciousOperation
from django.utils import six
from django.utils.datastructures import MultiValueDict
from django.utils.encoding import force_bytes, force_str, force_text
from django.utils.functional import keep_lazy_text
from django.utils.six.moves.urllib.parse import (
    quote, quote_plus, unquote, unquote_plus, urlencode as original_urlencode,
    urlparse,
)

ETAG_MATCH = re.compile(r'(?:W/)?"((?:\\.|[^"])*)"')

MONTHS = 'jan feb mar apr may jun jul aug sep oct nov dec'.split()
__D = r'(?P<day>\d{2})'
__D2 = r'(?P<day>[ \d]\d)'
__M = r'(?P<mon>\w{3})'
__Y = r'(?P<year>\d{4})'
__Y2 = r'(?P<year>\d{2})'
__T = r'(?P<hour>\d{2}):(?P<min>\d{2}):(?P<sec>\d{2})'
RFC1123_DATE = re.compile(r'^\w{3}, %s %s %s %s GMT$' % (__D, __M, __Y, __T))
RFC850_DATE = re.compile(r'^\w{6,9}, %s-%s-%s %s GMT$' % (__D, __M, __Y2, __T))
ASCTIME_DATE = re.compile(r'^\w{3} %s %s %s %s$' % (__M, __D2, __T, __Y))

RFC3986_GENDELIMS = str(":/?#[]@")
RFC3986_SUBDELIMS = str("!$&'()*+,;=")

PROTOCOL_TO_PORT = {
    'http': 80,
    'https': 443,
}


@keep_lazy_text
def urlquote(url, safe='/'):
    """
    A version of Python's urllib.quote() function that can operate on unicode
    strings. The url is first UTF-8 encoded before quoting. The returned string
    can safely be used as part of an argument to a subsequent iri_to_uri() call
    without double-quoting occurring.
    """
    return force_text(quote(force_str(url), force_str(safe)))


@keep_lazy_text
def urlquote_plus(url, safe=''):
    """
    A version of Python's urllib.quote_plus() function that can operate on
    unicode strings. The url is first UTF-8 encoded before quoting. The
    returned string can safely be used as part of an argument to a subsequent
    iri_to_uri() call without double-quoting occurring.
    """
    return force_text(quote_plus(force_str(url), force_str(safe)))


@keep_lazy_text
def urlunquote(quoted_url):
    """
    A wrapper for Python's urllib.unquote() function that can operate on
    the result of django.utils.http.urlquote().
    """
    return force_text(unquote(force_str(quoted_url)))


@keep_lazy_text
def urlunquote_plus(quoted_url):
    """
    A wrapper for Python's urllib.unquote_plus() function that can operate on
    the result of django.utils.http.urlquote_plus().
    """
    return force_text(unquote_plus(force_str(quoted_url)))


def urlencode(query, doseq=0):
    """
    A version of Python's urllib.urlencode() function that can operate on
    unicode strings. The parameters are first cast to UTF-8 encoded strings and
    then encoded as per normal.
    """
    if isinstance(query, MultiValueDict):
        query = query.lists()
    elif hasattr(query, 'items'):
        query = query.items()
    return original_urlencode(
        [(force_str(k),
         [force_str(i) for i in v] if isinstance(v, (list, tuple)) else force_str(v))
            for k, v in query],
        doseq)


def cookie_date(epoch_seconds=None):
    """
    Formats the time to ensure compatibility with Netscape's cookie standard.

    Accepts a floating point number expressed in seconds since the epoch, in
    UTC - such as that outputted by time.time(). If set to None, defaults to
    the current time.

    Outputs a string in the format 'Wdy, DD-Mon-YYYY HH:MM:SS GMT'.
    """
    rfcdate = formatdate(epoch_seconds)
    return '%s-%s-%s GMT' % (rfcdate[:7], rfcdate[8:11], rfcdate[12:25])


def http_date(epoch_seconds=None):
    """
    Formats the time to match the RFC1123 date format as specified by HTTP
    RFC2616 section 3.3.1.

    Accepts a floating point number expressed in seconds since the epoch, in
    UTC - such as that outputted by time.time(). If set to None, defaults to
    the current time.

    Outputs a string in the format 'Wdy, DD Mon YYYY HH:MM:SS GMT'.
    """
    return formatdate(epoch_seconds, usegmt=True)


def parse_http_date(date):
    """
    Parses a date format as specified by HTTP RFC2616 section 3.3.1.

    The three formats allowed by the RFC are accepted, even if only the first
    one is still in widespread use.

    Returns an integer expressed in seconds since the epoch, in UTC.
    """
    # emails.Util.parsedate does the job for RFC1123 dates; unfortunately
    # RFC2616 makes it mandatory to support RFC850 dates too. So we roll
    # our own RFC-compliant parsing.
    for regex in RFC1123_DATE, RFC850_DATE, ASCTIME_DATE:
        m = regex.match(date)
        if m is not None:
            break
    else:
        raise ValueError("%r is not in a valid HTTP date format" % date)
    try:
        year = int(m.group('year'))
        if year < 100:
            if year < 70:
                year += 2000
            else:
                year += 1900
        month = MONTHS.index(m.group('mon').lower()) + 1
        day = int(m.group('day'))
        hour = int(m.group('hour'))
        min = int(m.group('min'))
        sec = int(m.group('sec'))
        result = datetime.datetime(year, month, day, hour, min, sec)
        return calendar.timegm(result.utctimetuple())
    except Exception:
        six.reraise(ValueError, ValueError("%r is not a valid date" % date), sys.exc_info()[2])


def parse_http_date_safe(date):
    """
    Same as parse_http_date, but returns None if the input is invalid.
    """
    try:
        return parse_http_date(date)
    except Exception:
        pass


# Base 36 functions: useful for generating compact URLs

def base36_to_int(s):
    """
    Converts a base 36 string to an ``int``. Raises ``ValueError` if the
    input won't fit into an int.
    """
    # To prevent overconsumption of server resources, reject any
    # base36 string that is long than 13 base36 digits (13 digits
    # is sufficient to base36-encode any 64-bit integer)
    if len(s) > 13:
        raise ValueError("Base36 input too large")
    value = int(s, 36)
    # ... then do a final check that the value will fit into an int to avoid
    # returning a long (#15067). The long type was removed in Python 3.
    if six.PY2 and value > sys.maxint:
        raise ValueError("Base36 input too large")
    return value


def int_to_base36(i):
    """
    Converts an integer to a base36 string
    """
    char_set = '0123456789abcdefghijklmnopqrstuvwxyz'
    if i < 0:
        raise ValueError("Negative base36 conversion input.")
    if six.PY2:
        if not isinstance(i, six.integer_types):
            raise TypeError("Non-integer base36 conversion input.")
        if i > sys.maxint:
            raise ValueError("Base36 conversion input too large.")
    if i < 36:
        return char_set[i]
    b36 = ''
    while i != 0:
        i, n = divmod(i, 36)
        b36 = char_set[n] + b36
    return b36


def urlsafe_base64_encode(s):
    """
    Encodes a bytestring in base64 for use in URLs, stripping any trailing
    equal signs.
    """
    return base64.urlsafe_b64encode(s).rstrip(b'\n=')


def urlsafe_base64_decode(s):
    """
    Decodes a base64 encoded string, adding back any trailing equal signs that
    might have been stripped.
    """
    s = force_bytes(s)
    try:
        return base64.urlsafe_b64decode(s.ljust(len(s) + len(s) % 4, b'='))
    except (LookupError, BinasciiError) as e:
        raise ValueError(e)


def parse_etags(etag_str):
    """
    Parses a string with one or several etags passed in If-None-Match and
    If-Match headers by the rules in RFC 2616. Returns a list of etags
    without surrounding double quotes (") and unescaped from \<CHAR>.
    """
    etags = ETAG_MATCH.findall(etag_str)
    if not etags:
        # etag_str has wrong format, treat it as an opaque string then
        return [etag_str]
    etags = [e.encode('ascii').decode('unicode_escape') for e in etags]
    return etags


def quote_etag(etag):
    """
    Wraps a string in double quotes escaping contents as necessary.
    """
    return '"%s"' % etag.replace('\\', '\\\\').replace('"', '\\"')


def unquote_etag(etag):
    """
    Unquote an ETag string; i.e. revert quote_etag().
    """
    return etag.strip('"').replace('\\"', '"').replace('\\\\', '\\') if etag else etag


def is_same_domain(host, pattern):
    """
    Return ``True`` if the host is either an exact match or a match
    to the wildcard pattern.

    Any pattern beginning with a period matches a domain and all of its
    subdomains. (e.g. ``.example.com`` matches ``example.com`` and
    ``foo.example.com``). Anything else is an exact string match.
    """
    if not pattern:
        return False

    pattern = pattern.lower()
    return (
        pattern[0] == '.' and (host.endswith(pattern) or host == pattern[1:]) or
        pattern == host
    )


def is_safe_url(url, host=None):
    """
    Return ``True`` if the url is a safe redirection (i.e. it doesn't point to
    a different host and uses a safe scheme).

    Always returns ``False`` on an empty url.
    """
    if url is not None:
        url = url.strip()
    if not url:
        return False
    # Chrome treats \ completely as /
    url = url.replace('\\', '/')
    # Chrome considers any URL with more than two slashes to be absolute, but
    # urlparse is not so flexible. Treat any url with three slashes as unsafe.
    if url.startswith('///'):
        return False
    url_info = urlparse(url)
    # Forbid URLs like http:///example.com - with a scheme, but without a hostname.
    # In that URL, example.com is not the hostname but, a path component. However,
    # Chrome will still consider example.com to be the hostname, so we must not
    # allow this syntax.
    if not url_info.netloc and url_info.scheme:
        return False
    # Forbid URLs that start with control characters. Some browsers (like
    # Chrome) ignore quite a few control characters at the start of a
    # URL and might consider the URL as scheme relative.
    if unicodedata.category(url[0])[0] == 'C':
        return False
    return ((not url_info.netloc or url_info.netloc == host) and
            (not url_info.scheme or url_info.scheme in ['http', 'https']))


# Functions lifted from their respective modules in order to implement
# several checks which were not possible with the stdlib, namely limiting
# the number of fields that will be parsed.
#
# Copyright (C) 2013 Python Software Foundation, see license.python.txt for
# details.
def limited_parse_qsl_py2(qs, keep_blank_values=0, strict_parsing=0, fields_limit=None):
    """
    Parse a query given as a string argument and return a list.

    Lifted from urlparse with an additional "fields_limit" argument.

    Arguments:

    qs: percent-encoded query string to be parsed

    keep_blank_values: flag indicating whether blank values in
        percent-encoded queries should be treated as blank strings.  A
        true value indicates that blanks should be retained as blank
        strings.  The default false value indicates that blank values
        are to be ignored and treated as if they were  not included.

    strict_parsing: flag indicating what to do with parsing errors. If
        false (the default), errors are silently ignored. If true,
        errors raise a ValueError exception.

    fields_limit: maximum number of fields parsed, or an exception
        is raised. None means no limit and is the default.
    """
    fields_limit = fields_limit or 0
    pairs = re.split('[&;]', qs, fields_limit)
    if fields_limit > 0 and len(pairs) > fields_limit:
        raise SuspiciousOperation('Too many fields')
    r = []
    for name_value in pairs:
        if not name_value and not strict_parsing:
            continue
        nv = name_value.split(b'=', 1)
        if len(nv) != 2:
            if strict_parsing:
                raise ValueError("bad query field: %r" % (name_value,))
            # Handle case of a control-name with no equal sign
            if keep_blank_values:
                nv.append(b'')
            else:
                continue
        if len(nv[1]) or keep_blank_values:
            name = unquote(nv[0].replace(b'+', b' '))
            value = unquote(nv[1].replace(b'+', b' '))
            r.append((name, value))

    return r


_implicit_encoding = 'ascii'
_implicit_errors = 'strict'


def _noop(obj):
    return obj


def _encode_result(obj, encoding=_implicit_encoding, errors=_implicit_errors):
    return obj.encode(encoding, errors)


def _decode_args(args, encoding=_implicit_encoding, errors=_implicit_errors):
    return tuple(x.decode(encoding, errors) if x else '' for x in args)


def _coerce_args(*args):
    # Invokes decode if necessary to create str args
    # and returns the coerced inputs along with
    # an appropriate result coercion function
    #   - noop for str inputs
    #   - encoding function otherwise
    str_input = isinstance(args[0], str)
    for arg in args[1:]:
        # We special-case the empty string to support the
        # "scheme=''" default argument to some functions
        if arg and isinstance(arg, str) != str_input:
            raise TypeError("Cannot mix str and non-str arguments")
    if str_input:
        return args + (_noop,)
    return _decode_args(args) + (_encode_result,)


def limited_parse_qsl_py3(qs, keep_blank_values=False, strict_parsing=False,
               encoding='utf-8', errors='replace', fields_limit=None):
    """
    Parse a query given as a string argument and return a list.

    Lifted from urlparse with an additional "fields_limit" argument.

    Arguments:

    qs: percent-encoded query string to be parsed

    keep_blank_values: flag indicating whether blank values in
        percent-encoded queries should be treated as blank strings.  A
        true value indicates that blanks should be retained as blank
        strings.  The default false value indicates that blank values
        are to be ignored and treated as if they were  not included.

    strict_parsing: flag indicating what to do with parsing errors. If
        false (the default), errors are silently ignored. If true,
        errors raise a ValueError exception.

    encoding and errors: specify how to decode percent-encoded sequences
        into Unicode characters, as accepted by the bytes.decode() method.

    fields_limit: maximum number of fields parsed or an exception
        is raised. None means no limit and is the default.
    """
    fields_limit = fields_limit or 0
    qs, _coerce_result = _coerce_args(qs)
    pairs = re.split('[&;]', qs, fields_limit)
    if fields_limit > 0 and len(pairs) > fields_limit:
        raise SuspiciousOperation('Too many fields')
    r = []
    for name_value in pairs:
        if not name_value and not strict_parsing:
            continue
        nv = name_value.split('=', 1)
        if len(nv) != 2:
            if strict_parsing:
                raise ValueError("bad query field: %r" % (name_value,))
            # Handle case of a control-name with no equal sign
            if keep_blank_values:
                nv.append('')
            else:
                continue
        if len(nv[1]) or keep_blank_values:
            name = nv[0].replace('+', ' ')
            name = unquote(name, encoding=encoding, errors=errors)
            name = _coerce_result(name)
            value = nv[1].replace('+', ' ')
            value = unquote(value, encoding=encoding, errors=errors)
            value = _coerce_result(value)
            r.append((name, value))
    return r
