# -*- coding: utf-8 -*-
"""IMDb chart scraping, which can now only fail.

IMDb serves scripted requests an AWS WAF challenge rather than the chart.
What matters is that this fails loudly: the old code handed the error page
to lxml, matched nothing, and returned an empty list, which the cleanup
phase would read as "every item is stale".
"""
import unittest

from . import context  # noqa: F401

import imdbutils
from utils import SourceListError

WAF_CHALLENGE = (
    b"<!DOCTYPE html><html><head><script>"
    b"window.awsWafCookieDomainList = ['imdb.com'];</script>"
    b"<script src='https://x.token.awswaf.com/challenge.js'></script>"
    b"</head><body></body></html>")


class FakeResponse(object):
    def __init__(self, status_code, content=b''):
        self.status_code = status_code
        self.content = content


class FailLoudlyTest(unittest.TestCase):
    def setUp(self):
        self.imdb = imdbutils.IMDb(None)
        import requests
        self.requests = requests
        self.original = requests.get

    def tearDown(self):
        self.requests.get = self.original

    def _respond(self, status_code, content=b''):
        self.requests.get = lambda *a, **k: FakeResponse(status_code, content)

    def test_403_raises(self):
        self._respond(403, b'<html><head><title>403</title></head></html>')
        with self.assertRaises(SourceListError) as ctx:
            self.imdb.add_items('movie', 'https://www.imdb.com/chart/top/',
                                [], [], 0)
        self.assertIn("403", str(ctx.exception))

    def test_bot_challenge_is_recognised(self):
        self._respond(202, WAF_CHALLENGE)
        with self.assertRaises(SourceListError) as ctx:
            self.imdb.add_items('movie',
                                'https://www.imdb.com/chart/moviemeter/',
                                [], [], 0)
        self.assertIn("bot-protection challenge", str(ctx.exception))

    def test_unparseable_page_raises_rather_than_returning_nothing(self):
        self._respond(200, b'<html><body><p>Some other page</p></body></html>')
        with self.assertRaises(SourceListError):
            self.imdb.add_items('movie', 'https://www.imdb.com/chart/top/',
                                [], [], 0)

    def test_error_points_at_working_alternatives(self):
        self._respond(403)
        with self.assertRaises(SourceListError) as ctx:
            self.imdb.add_items('movie', 'https://www.imdb.com/chart/top/',
                                [], [], 0)
        message = str(ctx.exception)
        self.assertIn("mdblist.com", message)
        self.assertIn("themoviedb.org", message)


if __name__ == '__main__':
    unittest.main()
