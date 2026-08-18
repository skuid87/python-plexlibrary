# -*- coding: utf-8 -*-
"""IMDb charts via the GraphQL endpoint.

Payloads mirror live responses. Nothing here touches the network.
"""
import unittest

from . import context  # noqa: F401

import imdbutils
from imdbutils import IMDbChart
from utils import SourceListError

TOP_MOVIES = {"data": {"chartTitles": {"total": 250, "edges": [
    {"node": {"id": "tt0111161",
              "titleText": {"text": "The Shawshank Redemption"},
              "releaseYear": {"year": 1994}}},
    {"node": {"id": "tt0068646", "titleText": {"text": "The Godfather"},
              "releaseYear": {"year": 1972}}},
]}}}

BOX_OFFICE = {"data": {"boxOfficeWeekendChart": {"entries": [
    {"title": {"id": "tt22084616",
               "titleText": {"text": "Spider-Man: Brand New Day"},
               "releaseYear": {"year": 2026}}},
]}}}


class FakeResponse(object):
    def __init__(self, status_code=200, payload=None, bad_json=False):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._payload


class PatchedPost(object):
    """Swap requests.post for the duration of a test."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def __enter__(self):
        import requests
        self._original = requests.post
        self._requests = requests

        def fake_post(url, headers=None, json=None, timeout=None):
            self.calls.append({'url': url, 'headers': headers or {},
                               'json': json or {}})
            return self.response
        requests.post = fake_post
        return self

    def __exit__(self, *exc):
        self._requests.post = self._original


class HandlesTest(unittest.TestCase):
    def test_recognises_chart_urls(self):
        for url in ("imdb://chart/top_movies", "imdb://top_movies",
                    "https://www.imdb.com/chart/top/",
                    "https://imdb.com/chart/moviemeter/"):
            self.assertTrue(IMDbChart.handles(url), url)

    def test_ignores_non_chart_imdb_urls(self):
        """Those still belong to the scraper, which fails loudly."""
        self.assertFalse(IMDbChart.handles("https://www.imdb.com/list/ls123/"))
        self.assertFalse(IMDbChart.handles("https://www.imdb.com/title/tt1/"))

    def test_ignores_other_sources(self):
        self.assertFalse(IMDbChart.handles("https://api.mdblist.com/lists/a/b"))
        self.assertFalse(IMDbChart.handles("https://api.trakt.tv/movies/trending"))


class ChartNameTest(unittest.TestCase):
    def test_shorthand_and_website_urls_resolve_to_the_same_chart(self):
        for url in ("imdb://chart/top_movies", "imdb://top_movies",
                    "https://www.imdb.com/chart/top/",
                    "https://www.imdb.com/chart/top"):
            self.assertEqual(IMDbChart._chart_name(url), 'top_movies')

    def test_website_aliases(self):
        cases = {"https://www.imdb.com/chart/moviemeter/": 'popular_movies',
                 "https://www.imdb.com/chart/toptv/": 'top_shows',
                 "https://www.imdb.com/chart/tvmeter/": 'popular_shows',
                 "https://www.imdb.com/chart/boxoffice/": 'box_office',
                 "https://www.imdb.com/chart/bottom/": 'lowest_rated'}
        for url, expected in cases.items():
            self.assertEqual(IMDbChart._chart_name(url), expected, url)

    def test_unknown_chart_lists_the_valid_ones(self):
        with self.assertRaises(SourceListError) as ctx:
            IMDbChart._chart_name("imdb://chart/nonsense")
        self.assertIn("top_movies", str(ctx.exception))

    def test_no_chart_named(self):
        with self.assertRaises(SourceListError):
            IMDbChart._chart_name("imdb://chart/")


class QueryTest(unittest.TestCase):
    def test_sends_the_client_header_the_endpoint_requires(self):
        with PatchedPost(FakeResponse(200, TOP_MOVIES)) as patched:
            IMDbChart(None).add_items('movie', "imdb://chart/top_movies",
                                      [], [], 0)
        headers = patched.calls[0]['headers']
        self.assertEqual(headers.get('x-imdb-client-name'), 'imdb-web-next')

    def test_limit_is_capped_at_the_charts_own_size(self):
        with PatchedPost(FakeResponse(200, TOP_MOVIES)) as patched:
            IMDbChart(None).add_items(
                'movie', "imdb://chart/top_movies?limit=99999", [], [], 0)
        self.assertIn("first: 250", patched.calls[0]['json']['query'])

    def test_box_office_uses_its_own_query(self):
        with PatchedPost(FakeResponse(200, BOX_OFFICE)) as patched:
            items, _ = IMDbChart(None).add_items(
                'movie', "imdb://chart/box_office", [], [], 0)
        self.assertIn("boxOfficeWeekendChart", patched.calls[0]['json']['query'])
        self.assertEqual(items[0]['id'], 'tt22084616')


class ItemMappingTest(unittest.TestCase):
    def test_ids_titles_and_years(self):
        with PatchedPost(FakeResponse(200, TOP_MOVIES)):
            items, ids = IMDbChart(None).add_items(
                'movie', "imdb://chart/top_movies", [], [], 0)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['id'], 'tt0111161')
        self.assertEqual(items[0]['title'], 'The Shawshank Redemption')
        self.assertEqual(items[0]['year'], 1994)
        self.assertIn('tt0111161', ids)

    def test_chart_order_is_preserved(self):
        """The whole point of a ranked chart; sort titles depend on it."""
        with PatchedPost(FakeResponse(200, TOP_MOVIES)):
            items, _ = IMDbChart(None).add_items(
                'movie', "imdb://chart/top_movies", [], [], 0)
        self.assertEqual([i['title'] for i in items],
                         ['The Shawshank Redemption', 'The Godfather'])

    def test_works_without_tmdb_configured(self):
        with PatchedPost(FakeResponse(200, TOP_MOVIES)):
            items, _ = IMDbChart(None).add_items(
                'movie', "imdb://chart/top_movies", [], [], 0)
        self.assertIsNone(items[0]['tmdb_id'])

    def test_resolves_tmdb_ids_when_available(self):
        class FakeTMDb(object):
            def get_tmdb_from_imdb(self, imdb_id, library_type):
                return {'id': 278}

            def get_external_ids(self, tmdb_id, library_type):
                return {'imdb_id': None, 'tvdb_id': None}

        with PatchedPost(FakeResponse(200, TOP_MOVIES)):
            items, ids = IMDbChart(FakeTMDb()).add_items(
                'movie', "imdb://chart/top_movies", [], [], 0)
        self.assertEqual(items[0]['tmdb_id'], '278')
        self.assertIn('tmdb278', ids)

    def test_max_age_filters_by_year(self):
        with PatchedPost(FakeResponse(200, TOP_MOVIES)):
            items, _ = IMDbChart(None).add_items(
                'movie', "imdb://chart/top_movies", [], [], 5)
        self.assertEqual(items, [])

    def test_already_seen_ids_are_not_duplicated(self):
        with PatchedPost(FakeResponse(200, TOP_MOVIES)):
            items, _ = IMDbChart(None).add_items(
                'movie', "imdb://chart/top_movies", [], ['tt0111161'], 0)
        self.assertEqual([i['id'] for i in items], ['tt0068646'])


class MediaTypeTest(unittest.TestCase):
    def test_movie_chart_in_a_tv_recipe_is_rejected(self):
        with self.assertRaises(SourceListError) as ctx:
            IMDbChart(None).add_items('tv', "imdb://chart/top_movies",
                                      [], [], 0)
        message = str(ctx.exception)
        self.assertIn("holds movies", message)
        self.assertIn("top_shows", message)  # suggests the tv charts

    def test_tv_chart_in_a_movie_recipe_is_rejected(self):
        with self.assertRaises(SourceListError):
            IMDbChart(None).add_items('movie', "imdb://chart/top_shows",
                                      [], [], 0)


class FailureTest(unittest.TestCase):
    """The endpoint is undocumented, so breakage must be obvious."""

    def test_http_error(self):
        with PatchedPost(FakeResponse(403)):
            with self.assertRaises(SourceListError) as ctx:
                IMDbChart(None).add_items('movie', "imdb://chart/top_movies",
                                          [], [], 0)
        self.assertIn("403", str(ctx.exception))

    def test_non_json_response(self):
        with PatchedPost(FakeResponse(200, bad_json=True)):
            with self.assertRaises(SourceListError):
                IMDbChart(None).add_items('movie', "imdb://chart/top_movies",
                                          [], [], 0)

    def test_graphql_errors_are_surfaced(self):
        payload = {"errors": [{"message": "Unknown chart type"}]}
        with PatchedPost(FakeResponse(200, payload)):
            with self.assertRaises(SourceListError) as ctx:
                IMDbChart(None).add_items('movie', "imdb://chart/top_movies",
                                          [], [], 0)
        self.assertIn("Unknown chart type", str(ctx.exception))

    def test_empty_chart_raises_rather_than_returning_nothing(self):
        empty = {"data": {"chartTitles": {"total": 0, "edges": []}}}
        with PatchedPost(FakeResponse(200, empty)):
            with self.assertRaises(SourceListError):
                IMDbChart(None).add_items('movie', "imdb://chart/top_movies",
                                          [], [], 0)


if __name__ == '__main__':
    unittest.main()
