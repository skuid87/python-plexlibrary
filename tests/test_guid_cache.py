# -*- coding: utf-8 -*-
"""The Plex guid cache.

Modern Plex agents give items an opaque plex:// guid, so resolving the real
IMDb/TMDb/TheTVDB ids costs one API call per library item. This cache is what
stops every run paying that, which makes its write behaviour worth testing:
it used to rewrite the entire JSON file once per item.
"""
import datetime
import json
import os
import shutil
import tempfile
import unittest

from . import context  # noqa: F401

import recipe as recipe_module
from recipe import IdMap


class FakeGuid(object):
    def __init__(self, id_):
        self.id = id_


class FakeItem(object):
    """A library item as plexapi presents it under the modern agents."""

    def __init__(self, n, section=1, updated=1000):
        self.guid = 'plex://movie/%d' % n
        self.guids = [FakeGuid('imdb://tt%07d' % n),
                      FakeGuid('tmdb://%d' % n)]
        self.librarySectionID = section
        self.title = 'Movie %d' % n
        self.year = 2020
        self.updatedAt = datetime.datetime.fromtimestamp(updated)


class CacheWriteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, 'guids.json')
        self.map = IdMap(cache_file=self.path)
        self.writes = 0
        original = self.map._save_cache

        def counting_save():
            self.writes += 1
            return original()
        self.map._save_cache = counting_save

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_writes_are_batched_not_one_per_item(self):
        """This is the regression: it used to be one full rewrite per item."""
        items = [FakeItem(i) for i in range(100)]
        self.map.add_items(items)
        self.assertLessEqual(self.writes, 2, "cache written %d times for 100 "
                                             "items" % self.writes)

    def test_everything_is_persisted_by_the_end(self):
        self.map.add_items([FakeItem(i) for i in range(100)])
        with open(self.path) as f:
            saved = json.load(f)
        self.assertEqual(len(saved['1']), 100)

    def test_a_long_run_checkpoints_rather_than_only_saving_at_the_end(self):
        """An interrupted run should keep most of its progress."""
        count = recipe_module.CACHE_FLUSH_INTERVAL * 2
        self.map.add_items([FakeItem(i) for i in range(count)])
        self.assertGreaterEqual(self.writes, 2)

    def test_cached_guids_are_reused_without_touching_the_item(self):
        """A warm cache must not cost a per-item Plex API call."""
        self.map.add_items([FakeItem(1)])

        class Exploding(object):
            """Reading .guids is the API call the cache exists to avoid."""

            def __init__(self):
                self.guid = 'plex://movie/1'
                self.librarySectionID = 1
                self.title = 'Movie 1'
                self.year = 2020
                self.updatedAt = datetime.datetime.fromtimestamp(1000)

            @property
            def guids(self):
                raise AssertionError("should have come from the cache")

        second = IdMap(cache_file=self.path)
        second.add_items([Exploding()])
        self.assertIn('tt0000001', second.imdb)

    def test_a_newer_item_invalidates_its_cache_entry(self):
        self.map.add_items([FakeItem(1, updated=1000)])
        second = IdMap(cache_file=self.path)
        second.add_items([FakeItem(1, updated=2000)])
        with open(self.path) as f:
            saved = json.load(f)
        self.assertEqual(saved['1']['plex://movie/1']['updatedAt'], 2000)


class SectionSwitchTest(unittest.TestCase):
    """Switching sections replaces the in-memory cache, so pending writes
    have to be flushed first or they are silently lost."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, 'guids.json')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_both_sections_survive(self):
        id_map = IdMap(cache_file=self.path)
        id_map.add_items([FakeItem(1, section=1), FakeItem(2, section=2),
                          FakeItem(3, section=1)])
        with open(self.path) as f:
            saved = json.load(f)
        self.assertIn('1', saved)
        self.assertIn('2', saved)
        self.assertIn('plex://movie/3', saved['1'])

    def test_libraries_added_separately_all_persist(self):
        id_map = IdMap(cache_file=self.path)
        id_map.add_items([FakeItem(i, section=1) for i in range(5)])
        id_map.add_items([FakeItem(i, section=2) for i in range(5, 10)])
        with open(self.path) as f:
            saved = json.load(f)
        self.assertEqual(len(saved['1']), 5)
        self.assertEqual(len(saved['2']), 5)


class AtomicWriteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, 'guids.json')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_temp_file_is_left_behind(self):
        id_map = IdMap(cache_file=self.path)
        id_map.add_items([FakeItem(i) for i in range(10)])
        leftovers = [f for f in os.listdir(self.tmp) if f.endswith('.tmp')]
        self.assertEqual(leftovers, [])

    def test_a_failed_write_leaves_the_previous_cache_intact(self):
        id_map = IdMap(cache_file=self.path)
        id_map.add_items([FakeItem(1)])
        with open(self.path) as f:
            before = json.load(f)

        broken = IdMap(cache_file=self.path)
        real_open = open

        def failing_open(path, mode='r', *args, **kwargs):
            if str(path).endswith('.tmp'):
                raise OSError("disk full")
            return real_open(path, mode, *args, **kwargs)

        import builtins
        builtins.open = failing_open
        try:
            broken.add_items([FakeItem(2)])
        finally:
            builtins.open = real_open

        with open(self.path) as f:
            after = json.load(f)
        self.assertEqual(after, before)

    def test_a_corrupt_cache_file_is_recreated_rather_than_fatal(self):
        with open(self.path, 'w') as f:
            f.write("{ this is not json")
        id_map = IdMap(cache_file=self.path)
        id_map.add_items([FakeItem(1)])
        with open(self.path) as f:
            saved = json.load(f)
        self.assertIn('plex://movie/1', saved['1'])


if __name__ == '__main__':
    unittest.main()
