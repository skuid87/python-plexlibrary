# -*- coding: utf-8 -*-
"""MDBList source provider.

Payloads here mirror MDBList's published OpenAPI examples and the shapes
observed from live calls.
"""
import unittest

from . import context  # noqa: F401

import mdblistutils
from mdblistutils import MDBList
from utils import SourceListError

# From MDBList's own OpenAPI example response
SPEC_EXAMPLE = {
    "movies": [{
        "id": 917496, "rank": 1, "adult": 0,
        "title": "Beetlejuice Beetlejuice", "imdb_id": "tt2049403",
        "tvdb_id": None,
        "ids": {"mdblist": "m917496", "imdb": "tt2049403",
                "tmdb": 917496, "tvdb": None},
        "language": "en", "mediatype": "movie", "release_year": 2024,
        "spoken_language": "en", "country": "us",
    }],
    "shows": [{
        "id": 258902, "rank": 1, "adult": 0, "title": "English Teacher",
        "imdb_id": "tt20782190", "tvdb_id": 421968, "language": "en",
        "mediatype": "show", "release_year": 2024,
        "spoken_language": "en", "country": "us",
    }],
    "pagination": {},
}


class FakeResponse(object):
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.headers = {}
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class HandlesTest(unittest.TestCase):
    def test_recognises_mdblist_urls(self):
        for url in ("https://api.mdblist.com/lists/official/moviemeter/items",
                    "https://mdblist.com/lists/user/list",
                    "https://www.mdblist.com/lists/user/list",
                    "mdblist://lists/official/moviemeter/items"):
            self.assertTrue(MDBList.handles(url), url)

    def test_ignores_other_hosts(self):
        for url in ("https://api.trakt.tv/movies/trending",
                    "https://api.themoviedb.org/3/movie/popular",
                    "https://www.imdb.com/chart/moviemeter/"):
            self.assertFalse(MDBList.handles(url), url)


class UrlParsingTest(unittest.TestCase):
    def setUp(self):
        self.m = MDBList("dummy-key")

    def test_website_url_gets_items_suffix(self):
        path, _, _, _ = self.m._parse_url(
            "https://mdblist.com/lists/user/mylist")
        self.assertEqual(path, "lists/user/mylist/items")

    def test_api_url_left_alone(self):
        path, _, _, _ = self.m._parse_url(
            "https://api.mdblist.com/lists/official/moviemeter/items")
        self.assertEqual(path, "lists/official/moviemeter/items")

    def test_external_list(self):
        path, _, _, _ = self.m._parse_url(
            "https://api.mdblist.com/external/lists/12345")
        self.assertEqual(path, "external/lists/12345/items")

    def test_catalog_left_alone(self):
        path, params, limit, _ = self.m._parse_url(
            "https://api.mdblist.com/catalog/movie?genre=horror&limit=50")
        self.assertEqual(path, "catalog/movie")
        self.assertEqual(params.get('genre'), 'horror')
        self.assertEqual(limit, 50)

    def test_limit_and_page_size_are_ours_not_mdblists(self):
        _, params, limit, page_size = self.m._parse_url(
            "https://api.mdblist.com/lists/a/b/items?limit=250&page_size=50")
        self.assertEqual((limit, page_size), (250, 50))
        self.assertNotIn('limit', params)
        self.assertNotIn('page_size', params)

    def test_page_size_clamped_to_api_maximum(self):
        _, _, _, page_size = self.m._parse_url(
            "https://api.mdblist.com/lists/a/b/items?page_size=99999")
        self.assertEqual(page_size, mdblistutils.MAX_PAGE_SIZE)

    def test_same_slug_different_users_stay_distinct(self):
        a, _, _, _ = self.m._parse_url(
            "https://api.mdblist.com/lists/adamosborne01/imdb-top-250/items")
        b, _, _, _ = self.m._parse_url(
            "https://api.mdblist.com/lists/georgenilas/imdb-top-250/items")
        self.assertNotEqual(a, b)


class ItemMappingTest(unittest.TestCase):
    def setUp(self):
        self.m = MDBList("dummy-key")
        self.sent = {}

        def fake_get(path, params):
            self.sent = dict(params)
            return SPEC_EXAMPLE, {}
        self.m._get = fake_get

    def test_movie_ids_and_types(self):
        items, ids = self.m.add_items(
            'movie', "https://api.mdblist.com/lists/a/b/items", [], [], 0)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item['id'], 'tt2049403')
        # IdMap keys on strings; an int here would silently never match
        self.assertEqual(item['tmdb_id'], '917496')
        self.assertIsInstance(item['tmdb_id'], str)
        self.assertEqual(item['title'], 'Beetlejuice Beetlejuice')
        self.assertEqual(item['year'], 2024)
        self.assertIn('tt2049403', ids)
        self.assertIn('tmdb917496', ids)

    def test_movie_keeps_tvdb_id_when_the_api_supplies_one(self):
        """Live payloads carry a tvdb_id for movies too; it used to be
        dropped, discarding a third id the matcher could have tried."""
        live_movie = {
            "adult": 0, "country": "us", "id": 969681,
            "ids": {"imdb": "tt22084616", "mdblist": "32uh4",
                    "tmdb": 969681, "tvdb": 135896},
            "imdb_id": "tt22084616", "language": "en", "mediatype": "movie",
            "rank": 1, "release_date": "2026-07-29", "release_year": 2026,
            "runtime": 145, "status": "released",
            "title": "Spider-Man: Brand New Day", "tvdb_id": 135896,
        }
        self.m._get = lambda path, params: ({"movies": [live_movie]}, {})
        items, ids = self.m.add_items(
            'movie', "https://api.mdblist.com/lists/a/b/items", [], [], 0)
        self.assertEqual(items[0]['tvdb_id'], '135896')
        self.assertIn('tvdb135896', ids)

    def test_release_date_is_read_from_the_live_field_name(self):
        live_movie = {"id": 1, "imdb_id": "tt1", "title": "X",
                      "release_date": "2026-07-29", "release_year": 2026}
        self.m._get = lambda path, params: ({"movies": [live_movie]}, {})
        items, _ = self.m.add_items(
            'movie', "https://api.mdblist.com/lists/a/b/items", [], [], 0)
        self.assertEqual(str(items[0]['release_date']), '2026-07-29')

    def test_show_carries_tvdb_id(self):
        items, _ = self.m.add_items(
            'tv', "https://api.mdblist.com/lists/a/b/items", [], [], 0)
        self.assertEqual(items[0]['tvdb_id'], '421968')

    def test_media_type_is_taken_from_library_type(self):
        self.m.add_items('movie', "https://api.mdblist.com/lists/a/b/items",
                         [], [], 0)
        self.assertEqual(self.sent.get('mediatype'), 'movie')
        self.m.add_items('tv', "https://api.mdblist.com/lists/a/b/items",
                         [], [], 0)
        self.assertEqual(self.sent.get('mediatype'), 'show')

    def test_combined_list_does_not_leak_across_types(self):
        """The official lists are media:"all" and return both arrays."""
        movies, _ = self.m.add_items(
            'movie', "https://api.mdblist.com/lists/official/moviemeter/items",
            [], [], 0)
        shows, _ = self.m.add_items(
            'tv', "https://api.mdblist.com/lists/official/moviemeter/items",
            [], [], 0)
        self.assertEqual([i['title'] for i in movies],
                         ['Beetlejuice Beetlejuice'])
        self.assertEqual([i['title'] for i in shows], ['English Teacher'])

    def test_max_age_filters_by_year(self):
        items, _ = self.m.add_items(
            'movie', "https://api.mdblist.com/lists/a/b/items", [], [], 1)
        self.assertEqual(items, [])

    def test_items_without_ids_are_skipped(self):
        self.m._get = lambda path, params: (
            {"movies": [{"title": "No ids at all", "release_year": 2020}]}, {})
        items, _ = self.m.add_items(
            'movie', "https://api.mdblist.com/lists/a/b/items", [], [], 0)
        self.assertEqual(items, [])


class PaginationTest(unittest.TestCase):
    def setUp(self):
        self.m = MDBList("dummy-key")
        self.all_items = [
            {"id": 1000 + i, "title": "Movie %d" % i,
             "imdb_id": "tt%07d" % i, "tvdb_id": None,
             "mediatype": "movie", "release_year": 2020}
            for i in range(250)]
        self.requests = []

        def fake_get(path, params):
            self.requests.append(dict(params))
            cursor = int(params.get('cursor', 0))
            size = int(params['limit'])
            page = self.all_items[cursor:cursor + size]
            nxt = cursor + size
            pagination = ({"next_cursor": str(nxt)}
                          if nxt < len(self.all_items) else {})
            return {"movies": page, "shows": [],
                    "pagination": pagination}, {}
        self.m._get = fake_get

    def test_pages_until_cursor_runs_out(self):
        items, _ = self.m.add_items(
            'movie',
            "https://api.mdblist.com/lists/a/b/items?limit=250&page_size=50",
            [], [], 0)
        self.assertEqual(len(self.requests), 5)
        self.assertEqual(len(items), 250)
        self.assertEqual(items[0]['title'], 'Movie 0')
        self.assertEqual(items[-1]['title'], 'Movie 249')

    def test_no_duplicates_across_page_boundaries(self):
        items, _ = self.m.add_items(
            'movie',
            "https://api.mdblist.com/lists/a/b/items?limit=250&page_size=50",
            [], [], 0)
        self.assertEqual(len(set(i['tmdb_id'] for i in items)), 250)

    def test_page_size_is_not_sent_to_mdblist(self):
        self.m.add_items(
            'movie',
            "https://api.mdblist.com/lists/a/b/items?limit=250&page_size=50",
            [], [], 0)
        self.assertNotIn('page_size', self.requests[0])

    def test_stops_at_end_of_list_when_limit_is_higher(self):
        items, _ = self.m.add_items(
            'movie',
            "https://api.mdblist.com/lists/a/b/items?limit=1000&page_size=100",
            [], [], 0)
        self.assertEqual(len(items), 250)

    def test_runaway_cursor_cannot_loop_forever(self):
        self.m._get = lambda path, params: (
            {"movies": [self.all_items[0]], "shows": [],
             "pagination": {"next_cursor": "always"}}, {})
        self.m.add_items(
            'movie',
            "https://api.mdblist.com/lists/a/b/items?limit=99999&page_size=1",
            [], [], 0)
        # Guard is MAX_PAGES; without it this would never return
        self.assertTrue(True)


class ErrorMappingTest(unittest.TestCase):
    def setUp(self):
        self.m = MDBList("dummy-key")

    def _get_with_status(self, code):
        import requests as requests_module
        original = requests_module.get
        requests_module.get = lambda *a, **k: FakeResponse(code)
        try:
            self.m._get("lists/a/b/items", {})
        finally:
            requests_module.get = original

    def test_missing_api_key_is_rejected_up_front(self):
        with self.assertRaises(SourceListError):
            MDBList("")

    def test_401_names_the_config_key(self):
        with self.assertRaises(SourceListError) as ctx:
            self._get_with_status(401)
        self.assertIn("api_key", str(ctx.exception))

    def test_quota_exhaustion(self):
        for code in (402, 429):
            with self.assertRaises(SourceListError) as ctx:
                self._get_with_status(code)
            self.assertIn("quota", str(ctx.exception).lower())

    def test_404_names_the_list(self):
        with self.assertRaises(SourceListError) as ctx:
            self._get_with_status(404)
        self.assertIn("no such list", str(ctx.exception).lower())


if __name__ == '__main__':
    unittest.main()


class ReleasedFilterTest(unittest.TestCase):
    """Popularity charts are full of unreleased titles that can never match
    anything in a library, so `released_to=today` needs to resolve at run
    time rather than being pinned to whenever the recipe was written."""

    def setUp(self):
        self.m = MDBList("dummy-key")

    def test_today_resolves_to_the_current_date(self):
        import datetime
        _, params, _, _ = self.m._parse_url(
            "https://api.mdblist.com/lists/a/b/items?released_to=today")
        self.assertEqual(params['released_to'],
                         datetime.date.today().isoformat())

    def test_today_is_case_insensitive_and_works_for_released_from(self):
        import datetime
        today = datetime.date.today().isoformat()
        _, params, _, _ = self.m._parse_url(
            "https://api.mdblist.com/lists/a/b/items?released_from=TODAY")
        self.assertEqual(params['released_from'], today)

    def test_explicit_dates_are_passed_through_untouched(self):
        _, params, _, _ = self.m._parse_url(
            "https://api.mdblist.com/lists/a/b/items?released_to=2024-01-31")
        self.assertEqual(params['released_to'], '2024-01-31')

    def test_filter_reaches_the_api(self):
        sent = {}

        def fake_get(path, params):
            sent.update(params)
            return SPEC_EXAMPLE, {}
        self.m._get = fake_get
        self.m.add_items(
            'movie',
            "https://api.mdblist.com/lists/a/b/items?released_to=today",
            [], [], 0)
        self.assertIn('released_to', sent)
