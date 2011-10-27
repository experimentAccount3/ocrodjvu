# encoding=UTF-8

# Copyright © 2010, 2011 Jakub Wilk <jwilk@jwilk.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 dated June, 1991.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.

from __future__ import division

import contextlib
import functools
import re
import shlex

try:
    from lxml import etree
except ImportError:
    from xml.etree import cElementTree as etree

from . import common
from .. import errors
from .. import image_io
from .. import ipc
from .. import text_zones
from .. import unicode_support
from .. import utils

const = text_zones.const

_default_language = 'eng'
_version_re = re.compile(r'\bgocr ([0-9]+).([0-9]+)\b')

class ExtractSettings(object):

    def __init__(self, rotation=0, details=text_zones.TEXT_DETAILS_WORD, uax29=None, page_size=None, **kwargs):
        self.rotation = rotation
        self.details = details
        if uax29 is not None:
            icu = unicode_support.get_icu()
            if uax29 is True:
                uax29 = icu.Locale()
            else:
                uax29 = icu.Locale(uax29)
        self.uax29 = uax29
        self.page_size = page_size

def scan(stream, settings):
    word_break_iterator = functools.partial(unicode_support.word_break_iterator, locale=settings.uax29)
    stack = [[], [], []]
    for _, element in stream:
        if element.tag in ('barcode', 'img'):
            continue
        if element.tag == 'page':
            if len(stack) != 1:
                raise errors.MalformedOcrOutput('<page> at unexpected depth')
            children = stack.pop()
            bbox = text_zones.BBox(*((0, 0) + settings.page_size))
            zone = text_zones.Zone(const.TEXT_ZONE_PAGE, bbox, children)
            zone.rotate(settings.rotation)
            return zone
        elif element.tag == 'block':
            if len(stack) != 2:
                raise errors.MalformedOcrOutput('<page> at unexpected depth')
            children = stack.pop()
            if len(children) == 0:
                raise errors.MalformedOcrOutput('<block> has no children')
            bbox = text_zones.BBox()
            for child in children:
                bbox.update(child.bbox)
            zone = text_zones.Zone(const.TEXT_ZONE_REGION, bbox, children)
            stack[-1] += [zone]
        elif element.tag == 'line':
            if len(stack) != 3:
                raise errors.MalformedOcrOutput('<line> at unexpected depth')
            children = stack.pop()
            if len(children) == 0:
                raise errors.MalformedOcrOutput('<line> has no children')
            bbox = text_zones.BBox()
            for child in children:
                bbox.update(child.bbox)
            children = text_zones.group_words(children, settings.details, word_break_iterator)
            zone = text_zones.Zone(const.TEXT_ZONE_LINE, bbox, children)
            stack[-1] += [zone]
        elif element.tag in ('box', 'space'):
            if len(stack) > 3:
                raise errors.MalformedOcrOutput('<%s> at unexpected depth' % element.tag)
            while len(stack) < 3:
                stack += [[]]
            if element.tag == 'space':
                text = ' '
            else:
                text = element.get('value')
            x, y, w, h = (int(element.get(x)) for x in ('x', 'y', 'dx', 'dy'))
            bbox = text_zones.BBox(x, y, x + w, y + h)
            zone = text_zones.Zone(const.TEXT_ZONE_CHARACTER, bbox, [text])
            stack[-1] += [zone]
        else:
            raise errors.MalformedOcrOutput('unexpected <%s>' % element.tag.encode('ASCII', 'unicode-escape'))
    raise errors.MalformedOcrOutput('<page> not found')

class Engine(common.Engine):

    name = 'gocr'
    image_format = image_io.PNM
    output_format = 'gocr.xml'

    executable = utils.property('gocr')
    extra_args = utils.property([], shlex.split)

    def __init__(self, *args, **kwargs):
        assert args == ()
        common.Engine.__init__(self, *args, **kwargs)
        self._check_version()

    def _check_version(self):
        try:
            gocr = ipc.Subprocess([self.executable],
                stdout=ipc.PIPE,
                stderr=ipc.PIPE,
            )
        except OSError:
            raise errors.EngineNotFound(Engine.name)
        try:
            line = gocr.stderr.read()
            m = _version_re.search(line)
            if not m:
                raise errors.EngineNotFound(Engine.name)
            version = tuple(map(int, m.groups()))
            if version >= (0, 40):
                return
            else:
                raise errors.EngineNotFound(Engine.name)
        finally:
            gocr.wait()

    @classmethod
    def get_default_language(cls):
        return _default_language

    def has_language(self, language):
        return language == _default_language

    def list_languages(self):
        yield _default_language

    @contextlib.contextmanager
    def recognize(self, image, language, details=None, uax29=None):
        worker = ipc.Subprocess(
            [self.executable, '-i', image.name, '-f', 'XML'] + self.extra_args,
            stdout=ipc.PIPE,
        )
        try:
            yield worker.stdout
        finally:
            worker.wait()

    def extract_text(self, stream, **kwargs):
        settings = ExtractSettings(**kwargs)
        stream = etree.iterparse(stream)
        scan_result = scan(stream, settings)
        return [scan_result.sexpr]

# vim:ts=4 sw=4 et
