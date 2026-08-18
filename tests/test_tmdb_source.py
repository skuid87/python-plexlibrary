# -*- coding: utf-8 -*-
"""TMDb source provider."""
import unittest

from . import context  # noqa: F401

from tmdbutils import TMDbSource
from utils import SourceListError


class FakeTMDb(object):
    """Stands in for the TMDb client; records what was asked for."""

    def __init__(self, pages=2):
        self.calls = []
        self.pages = pages

    def get_json(self, path, params=None):
        params = dict(params or {})
        self.calls.append((path, params))
        page = params.get('page', 1)
        if path == 'trending/movie/week':
            if page > self.pages:
                return {'results': [], 'total_pages': self.pages}
            return {'page': page, 'total_pages': self.pages, 'results': [
                {'id': 100 + page, 'title': 'Movie %d' % page,
                 'release_date': '2025-06-0%d' % page}]}
        if path == 'trending/tv/week':
            return {'page': 1, 'total_pages': 1, 'results': [
                {'id': 500, 'name': 'Show', 'first_air_date': '2021-03-04'}]}
        return None

    def get_external_ids(self, tmdb_id, library_type):
        if library_type == 'tv':
            return {'imdb_id': 'tt%07d' % tmdb_id, 'tvdb_id': 900 + tmdb_id}
        return {'imdb_id': 'tt%07d' % tmdb_id, 'tvdb_id': None}


class HandlesTest(unittest.TestCase):
    def test_recognises_tmdb_urls(self):
        for url in ("https://api.themoviedb.org/3/trending/movie/week",
                    "https://www.themoviedb.org/list/8261445",
                    "tmdb://movie/top_rated"):
            self.assertTrue(TMDbSource.handles(url), url)

    def test_ignores_other_hosts(self):
        for url in ("https://api.mdblist.com/lists/a/b/items",
                    "https://api.trakt.tv/movies/trending"):
            self.assertFalse(TMDbSource.handles(url), url)


class UrlParsingTest(unittest.TestCase):
    def setUp(self):
        self.source = TMDbSource(FakeTMDb())

    def test_strips_api_version_prefix(self):
        path, _, _ = self.source._parse_url(
            "https://api.themoviedb.org/3/discover/movie")
        self.assertEqual(path, 'discover/movie')

    def test_passes_arbitrary_filters_through(self):
        _, params, _ = self.source._parse_url(
            "https://api.themoviedb.org/3/discover/movie"
            "?sort_by=vote_average.desc&vote_count.gte=500")
        self.assertEqual(params.get('sort_by'), 'vote_average.desc')
        self.assertEqual(params.get('vote_count.gte'), '500')

    def test_limit_is_ours_not_tmdbs(self):
        _, params, limit = self.source._parse_url(
            "https://api.themoviedb.org/3/movie/popular?limit=40")
        self.assertEqual(limit, 40)
        self.assertNotIn('limit', params)

    def test_shorthand_scheme(self):
        path, _, _ = self.source._parse_url("tmdb://movie/top_rated")
        self.assertEqual(path, 'movie/top_rated')

    def test_unusable_url_raises(self):
        with self.assertRaises(SourceListError):
            self.source._parse_url("tmdb://")


class ItemMappingTest(unittest.TestCase):
    def setUp(self):
        self.tmdb = FakeTMDb()
        self.source = TMDbSource(self.tmdb)

    def test_pages_up_to_limit(self):
        items, _ = self.source.add_items(
            'movie',
            "https://api.themoviedb.org/3/trending/movie/week?limit=10",
            [], [], 0)
        self.assertEqual(len(items), 2)

    def test_resolves_imdb_id_and_stringifies_tmdb_id(self):
        items, _ = self.source.add_items(
            'movie',
            "https://api.themoviedb.org/3/trending/movie/week?limit=10",
            [], [], 0)
        self.assertEqual(items[0]['id'], 'tt0000101')
        self.assertEqual(items[0]['tmdb_id'], '101')
        self.assertIsInstance(items[0]['tmdb_id'], str)

    def test_keeps_release_date_for_the_missing_items_report(self):
        items, _ = self.source.add_items(
            'movie',
            "https://api.themoviedb.org/3/trending/movie/week?limit=10",
            [], [], 0)
        self.assertIn('release_date', items[0])

    def test_tv_uses_name_and_first_air_date_and_gets_tvdb_id(self):
        items, _ = self.source.add_items(
            'tv', "https://api.themoviedb.org/3/trending/tv/week", [], [], 0)
        self.assertEqual(items[0]['title'], 'Show')
        self.assertEqual(items[0]['year'], 2021)
        self.assertEqual(items[0]['tvdb_id'], '1400')

    def test_dead_endpoint_raises_rather_than_returning_nothing(self):
        with self.assertRaises(SourceListError):
            self.source.add_items(
                'movie', "https://api.themoviedb.org/3/no/such/endpoint",
                [], [], 0)


if __name__ == '__main__':
    unittest.main()
