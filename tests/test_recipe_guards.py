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
                   'trakt': 'TRAKT'}
        self.assertEqual(
            resolve_source("https://api.mdblist.com/lists/a/b/items", sources),
            'MDB')
        self.assertEqual(
            resolve_source("https://api.themoviedb.org/3/movie/popular",
                           sources), 'TMDB')
        self.assertEqual(
            resolve_source("https://www.imdb.com/chart/top/", sources), 'IMDB')
        self.assertEqual(
            resolve_source("https://api.trakt.tv/movies/trending", sources),
            'TRAKT')


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
