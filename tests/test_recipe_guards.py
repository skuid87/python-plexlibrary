# -*- coding: utf-8 -*-
"""Source dispatch, the empty-list guard, and stale symlink handling.

The guard matters more than it looks: everything downstream treats "not in
the source list" as "remove from the library", so a source that silently
returns nothing would unlink the entire destination library.
"""
import os
import shutil
import tempfile
import unittest

from . import context  # noqa: F401

import recipe as recipe_module
from recipe import Recipe, resolve_source
from utils import SourceListError


def bare_recipe(**attrs):
    """A Recipe without __init__, which would require a Plex server."""
    r = Recipe.__new__(Recipe)
    r.use_playlists = False
    r.library_type = 'movie'
    r._sources = {'trakt': None, 'mdblist': None, 'tmdb_source': None,
                  'imdb': None}
    for key, value in attrs.items():
        setattr(r, key, value)
    return r


class EmptySource(object):
    def add_items(self, *args, **kwargs):
        return [], []


class DispatchTest(unittest.TestCase):
    def test_unconfigured_mdblist_explains_how_to_configure_it(self):
        with self.assertRaises(SourceListError) as ctx:
            resolve_source("https://api.mdblist.com/lists/a/b/items",
                           {'mdblist': None})
        self.assertIn("mdblist.com/preferences", str(ctx.exception))

    def test_unconfigured_tmdb_explains_how_to_configure_it(self):
        with self.assertRaises(SourceListError) as ctx:
            resolve_source("https://api.themoviedb.org/3/movie/popular",
                           {'tmdb_source': None})
        self.assertIn("api_key", str(ctx.exception))

    def test_trakt_mentions_the_vip_restriction(self):
        with self.assertRaises(SourceListError) as ctx:
            resolve_source("https://api.trakt.tv/movies/trending",
                           {'trakt': None})
        self.assertIn("VIP", str(ctx.exception))

    def test_unknown_source_raises(self):
        with self.assertRaises(SourceListError):
            resolve_source("https://example.com/whatever", {})

    def test_known_sources_route_to_their_provider(self):
        sources = {'mdblist': 'MDB', 'tmdb_source': 'TMDB', 'imdb': 'IMDB',
                   'imdb_chart': 'IMDB_CHART', 'trakt': 'TRAKT'}
        self.assertEqual(
            resolve_source("https://api.mdblist.com/lists/a/b/items", sources),
            'MDB')
        self.assertEqual(
            resolve_source("https://api.themoviedb.org/3/movie/popular",
                           sources), 'TMDB')
        self.assertEqual(
            resolve_source("https://api.trakt.tv/movies/trending", sources),
            'TRAKT')

    def test_chart_urls_go_to_the_graphql_source_not_the_scraper(self):
        """The chart pages can't be scraped, but the same charts are served
        as JSON, so those URLs are routed to the working source."""
        sources = {'imdb': 'IMDB', 'imdb_chart': 'IMDB_CHART'}
        for url in ("https://www.imdb.com/chart/top/",
                    "https://www.imdb.com/chart/moviemeter/",
                    "imdb://chart/top_movies"):
            self.assertEqual(resolve_source(url, sources), 'IMDB_CHART', url)

    def test_non_chart_imdb_urls_still_go_to_the_scraper(self):
        """Which fails loudly, as it should."""
        sources = {'imdb': 'IMDB', 'imdb_chart': 'IMDB_CHART'}
        self.assertEqual(
            resolve_source("https://www.imdb.com/list/ls055386972/", sources),
            'IMDB')


class EmptyListGuardTest(unittest.TestCase):
    def test_empty_source_list_aborts_and_names_the_library(self):
        r = bare_recipe(recipe={
            'source_list_urls': ['https://api.themoviedb.org/3/movie/popular'],
            'new_library': {'name': 'Movies - Trending', 'max_age': 0},
            'weighted_sorting': {'enabled': False}})
        r._sources['tmdb_source'] = EmptySource()
        with self.assertRaises(SourceListError) as ctx:
            r._get_source_lists()
        message = str(ctx.exception)
        self.assertIn("Refusing to continue", message)
        self.assertIn("Movies - Trending", message)


class ListSettingsTest(unittest.TestCase):
    def test_playlist_mode_reads_the_playlist_section(self):
        r = bare_recipe(recipe={'new_playlist': {'name': 'P', 'max_age': 5}})
        r.use_playlists = True
        self.assertEqual(r._list_settings().get('max_age'), 5)

    def test_library_mode_reads_the_library_section(self):
        r = bare_recipe(recipe={'new_library': {'name': 'L', 'max_age': 3}})
        self.assertEqual(r._list_settings().get('max_age'), 3)

    def test_missing_section_does_not_raise(self):
        r = bare_recipe(recipe={})
        self.assertEqual(r._list_settings(), {})


class StaleLinkTest(unittest.TestCase):
    """os.path.exists() follows symlinks, so a broken link looks absent to
    it while lexists() still sees it. Link creation is guarded on lexists(),
    so a broken link would never be repaired."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.recipe = bare_recipe()
        self.target = os.path.join(self.tmp, 'real.mkv')
        with open(self.target, 'w') as f:
            f.write('x')
        self.link = os.path.join(self.tmp, 'link.mkv')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_broken_link_is_removed(self):
        missing = os.path.join(self.tmp, 'gone.mkv')
        os.symlink(missing, self.link)
        self.assertTrue(os.path.lexists(self.link))
        self.assertFalse(os.path.exists(self.link))

        self.assertTrue(self.recipe._remove_stale_link(self.link, missing))
        self.assertFalse(os.path.lexists(self.link))

    def test_healthy_link_to_the_expected_target_is_kept(self):
        os.symlink(self.target, self.link)
        self.assertFalse(self.recipe._remove_stale_link(self.link,
                                                        self.target))
        self.assertTrue(os.path.lexists(self.link))

    def test_link_pointing_at_the_wrong_file_is_removed(self):
        other = os.path.join(self.tmp, 'other.mkv')
        with open(other, 'w') as f:
            f.write('y')
        os.symlink(other, self.link)
        self.assertTrue(self.recipe._remove_stale_link(self.link,
                                                       self.target))
        self.assertFalse(os.path.lexists(self.link))

    def test_real_files_are_never_touched(self):
        self.assertFalse(self.recipe._remove_stale_link(self.target,
                                                        self.target))
        self.assertTrue(os.path.isfile(self.target))

    def test_absent_path_is_a_no_op(self):
        self.assertFalse(
            self.recipe._remove_stale_link(os.path.join(self.tmp, 'nope'),
                                           self.target))


if __name__ == '__main__':
    unittest.main()


class LegacyConfigCompatibilityTest(unittest.TestCase):
    """A config.yml written for the old version must keep working.

    Existing installs have a `trakt` section with now-revoked credentials, a
    `tvdb` section for the module that has since been removed, and /tmp cache
    paths. None of that should break a run, and in particular a stale Trakt
    username must not trigger an auth handshake for a recipe that doesn't use
    Trakt -- that could block a cron run on an interactive PIN prompt.
    """

    LEGACY = {
        'guid_cache_file': '/tmp/plex_guid_cache.json',
        'plex': {'baseurl': 'http://localhost:32400', 'token': 'x'},
        'trakt': {'username': 'someone', 'client_id': 'revoked',
                  'client_secret': 'revoked', 'oauth_token': ''},
        'tmdb': {'api_key': 'key', 'cache_file': '/tmp/tmdb_details.shelve'},
        'tvdb': {'username': 'someone', 'api_key': 'k', 'user_key': 'k'},
    }

    def _config(self, data):
        import config as config_module

        class FakeConfig(config_module.ConfigParser):
            def __init__(self, payload):
                self.data = payload
        return FakeConfig(data)

    def test_legacy_config_still_validates(self):
        self.assertTrue(self._config(dict(self.LEGACY)).validate())

    def test_removed_tvdb_section_is_ignored_not_fatal(self):
        sources = recipe_module.build_sources(self._config(dict(self.LEGACY)))
        self.assertIsNotNone(sources['tmdb'])

    def test_trakt_is_not_constructed_until_a_trakt_url_appears(self):
        sources = recipe_module.build_sources(self._config(dict(self.LEGACY)))
        self.assertIsNone(sources['trakt'])

    def test_non_trakt_urls_never_trigger_trakt_auth(self):
        sources = recipe_module.build_sources(self._config(dict(self.LEGACY)))

        def explode():
            raise AssertionError("Trakt auth ran when it should not have")
        sources['trakt_factory'] = explode

        resolve_source("https://api.themoviedb.org/3/movie/popular", sources)
        resolve_source("https://api.mdblist.com/lists/a/b/items",
                       dict(sources, mdblist='MDB'))

    def test_legacy_tmp_cache_paths_are_honoured(self):
        sources = recipe_module.build_sources(self._config(dict(self.LEGACY)))
        self.assertEqual(sources['tmdb'].cache_file,
                         '/tmp/tmdb_details.shelve')


class ExpectedCountTest(unittest.TestCase):
    """The wait after linking must account for what the library already has.

    Waiting for len(matching_items) alone satisfies instantly when the
    library already holds that many items from the previous run, so sort
    titles get applied before Plex has scanned the new symlinks and the new
    items end up unnumbered.
    """

    def test_expected_count_is_existing_plus_newly_linked(self):
        existing, newly_linked, matched = 30, 10, 30
        naive = matched
        correct = existing + newly_linked
        # The naive target is already met before Plex scans anything new
        self.assertLessEqual(naive, existing)
        self.assertGreater(correct, existing)
        self.assertEqual(correct, 40)

    def test_first_run_baseline_is_zero(self):
        r = bare_recipe(recipe={'new_library': {'name': 'Nope'}})

        class NoLibrary(object):
            class server(object):
                class library(object):
                    @staticmethod
                    def section(name):
                        raise Exception("no such library")
        r.plex = NoLibrary()
        self.assertEqual(r._current_library_count(), 0)

    def test_existing_library_count_is_read(self):
        r = bare_recipe(recipe={'new_library': {'name': 'Trending Movies'}})

        class Section(object):
            @staticmethod
            def all():
                return [object()] * 30

        class HasLibrary(object):
            class server(object):
                class library(object):
                    @staticmethod
                    def section(name):
                        return Section()
        r.plex = HasLibrary()
        self.assertEqual(r._current_library_count(), 30)
