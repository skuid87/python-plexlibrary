# -*- coding: utf-8 -*-
"""recipe
"""

import datetime
import errno
import os
import random
import subprocess
import sys
import time
import logs
import json

import plexapi

import plexutils
import tmdb
import tmdbutils
import mdblistutils
import imdbutils
from config import ConfigParser
from recipes import RecipeParser
from utils import Colors, SourceListError, add_years, resolve_cache_path

try:
    import traktutils
except ImportError:  # pragma: no cover
    # The `trakt` package is unmaintained and Trakt has closed off new API
    # keys; Trakt support is optional now, so don't let a missing dependency
    # stop the other sources from working.
    traktutils = None


class IdMap():
    def __init__(self, matching_only=False, cache_file=None,
                 match_imdb=None, match_tmdb=None, match_tvdb=None):
        self.items = set()
        self.imdb = {}
        self.tmdb = {}
        self.tvdb = {}
        self.matching_only = matching_only
        self.cache_file = resolve_cache_path(
            cache_file, '~/.cache/plexlibrary/plex_guid_cache.json')
        self.cache = None
        self.cache_section = None
        self.cache_section_id = None
        if matching_only:
            self.match_imdb = match_imdb or []
            self.match_tmdb = match_tmdb or []
            self.match_tvdb = match_tvdb or []

    def add_libraries(self, libraries):
        for library in libraries:
            self.add_items(library.all())

    def add_items(self, items):
        while items:
            self.add_item(items.pop(0))  # Pop to save on memory

    def add_item(self, item):
        if item.guid.startswith('plex'):
            guids = self._get_guids(item)
            for guid in guids:
                self._add_id(guid, item)
        else:
            self._add_id(item.guid, item)

    def get(self, imdb=None, tmdb=None, tvdb=None):
        item = None
        if imdb:
            item = self.imdb.get(imdb)
        if not item and tmdb:
            item = self.tmdb.get(tmdb)
        if not item and tvdb:
            item = self.tvdb.get(tvdb)
        return item if item in self.items else None

    def pop_item(self, item):
        return self.pop(item=item)

    def pop(self, imdb=None, tmdb=None, tvdb=None, item=None):
        if imdb:
            item = self.imdb.pop(imdb, None)
        if not item and tmdb:
            item = self.tmdb.pop(tmdb, None)
        if not item and tvdb:
            item = self.tvdb.pop(tvdb, None)
        if not item:
            return None
        self._popall(item)
        try:
            self.items.remove(item)
        except KeyError:
            logs.warning("Item didn't exist in map set, collision?")
        return item

    def _get_guids(self, item):
        self._load_cache(str(item.librarySectionID))
        guids = []
        try:
            ts = item.updatedAt.timestamp()
        except AttributeError:
            logs.warning("Missing updatedAt timestamp for {} ({})".format(
                item.title, item.year))
            ts = 0
        try:
            if (item.guid in self.cache and
                    self.cache[item.guid]['updatedAt'] >= ts):
                guids = self.cache[item.guid]['guids']
        except (KeyError, TypeError):
            logs.warning("Cache error, overwriting")
            guids = self.cache[item.guid]
            self.cache[item.guid] = {
                'guids': guids,
                'updatedAt': ts
            }
            self._save_cache()
        if not guids:
            guids = [guid.id for guid in item.guids]
            if guids:
                self.cache[item.guid] = {
                    'guids': guids,
                    'updatedAt': ts
                }
                self._save_cache()
        return guids

    def _load_cache(self, section_id):
        if self.cache and self.cache_section_id == section_id:
            return
        if not os.path.isfile(self.cache_file):
            with open(self.cache_file, 'w') as f:
                json.dump(dict(), f)
        with open(self.cache_file, 'r') as f:
            try:
                self._cache = json.load(f)
            except Exception as e:
                logs.warning("Unable to read cache, recreating ({})".format(e))
                self._cache = dict()
        self.cache = self._cache.get(section_id, dict())

        self.cache_section_id = section_id

    def _save_cache(self):
        self._cache[self.cache_section_id] = self.cache
        with open(self.cache_file, 'w') as f:
            json.dump(self._cache, f)

    def _add_id(self, guid, item):
        try:
            source, id_ = guid.split('://', 1)
        except ValueError:
            logs.warning(f"Unknown guid: {guid}")
            return
        id_ = id_.split('?')[0]
        if 'imdb' in source:
            if '/' in id_:
                id_ = id_.split('/')[-2]
            if self.matching_only and not id_ in self.match_imdb:
                return
            self.imdb[id_] = item
        elif 'tmdb' in source or 'themoviedb' in source:
            if self.matching_only and not id_ in self.match_tmdb:
                return
            self.tmdb[id_] = item
        elif 'tvdb' in source or 'thetvdb' in source:
            if '/' in id_:
                id_ = id_.split('/')[-2]
            if self.matching_only and not id_ in self.match_tvdb:
                return
            self.tvdb[id_] = item
        else:
            logs.warning(f"Unknown guid: {guid}. "
                         f"Possibly unmatched: {item.title} ({item.year})")
        self.items.add(item)

    def _popall(self, item):
        items = []
        for d in (self.imdb, self.tmdb, self.tvdb):
            keys = []
            for k, v in d.items():
                if v is item:
                    keys.append(k)
            for k in keys:
                items.append(d.pop(k))
        return items


def build_sources(config, trakt_oauth=False):
    """Construct the list-source providers a config can support.

    Deliberately free of any Plex dependency, so source lists can be fetched
    and inspected without a server (see --check-source).
    """
    sources = {'trakt': None, 'tmdb': None, 'mdblist': None,
               'tmdb_source': None, 'imdb': None}

    trakt_config = config.get('trakt') or {}
    if trakt_config.get('username') and traktutils:
        sources['trakt'] = traktutils.Trakt(
            trakt_config['username'],
            client_id=trakt_config.get('client_id', ''),
            client_secret=trakt_config.get('client_secret', ''),
            oauth_token=trakt_config.get('oauth_token', ''),
            oauth=trakt_oauth,
            config=config)

    tmdb_config = config.get('tmdb') or {}
    if tmdb_config.get('api_key'):
        sources['tmdb'] = tmdb.TMDb(tmdb_config['api_key'],
                                    cache_file=tmdb_config.get('cache_file'))

    mdblist_config = config.get('mdblist') or {}
    if mdblist_config.get('api_key'):
        sources['mdblist'] = mdblistutils.MDBList(mdblist_config['api_key'])

    if sources['tmdb']:
        sources['tmdb_source'] = tmdbutils.TMDbSource(sources['tmdb'])
    sources['imdb'] = imdbutils.IMDb(sources['tmdb'])

    return sources


def resolve_source(url, sources):
    """Pick the provider for a source list URL.

    Raises with an actionable message rather than falling through to a silent
    no-op, which is how a dead source used to wipe a library.
    """
    if 'api.trakt.tv' in url:
        if not sources.get('trakt'):
            raise SourceListError(
                "'{}' needs Trakt, but no Trakt credentials are configured. "
                "Note that Trakt has restricted new API keys to VIP accounts; "
                "MDBList mirrors most Trakt lists on a free key.".format(url))
        return sources['trakt']
    if mdblistutils.MDBList.handles(url):
        if not sources.get('mdblist'):
            raise SourceListError(
                "'{}' needs MDBList, but no API key is configured. Get a free "
                "key from https://mdblist.com/preferences and add it to "
                "config.yml under mdblist: api_key".format(url))
        return sources['mdblist']
    if tmdbutils.TMDbSource.handles(url):
        if not sources.get('tmdb_source'):
            raise SourceListError(
                "'{}' needs TMDb, but no API key is configured. Add one to "
                "config.yml under tmdb: api_key".format(url))
        return sources['tmdb_source']
    if 'imdb.com' in url:
        return sources['imdb']
    raise SourceListError("Unsupported source list: {url}".format(url=url))


class Recipe():
    plex = None
    trakt = None
    tmdb = None
    mdblist = None
    tmdb_source = None

    def __init__(self, recipe_name, sort_only=False, config_file=None, use_playlists=False):
        self.recipe_name = recipe_name
        self.use_playlists = use_playlists

        self.config = ConfigParser(config_file)
        self.recipe = RecipeParser(recipe_name)

        if not self.config.validate():
            raise Exception("Error(s) in config")

        if not self.recipe.validate(use_playlists=use_playlists):
            raise Exception("Error(s) in recipe")

        if self.recipe['library_type'].lower().startswith('movie'):
            self.library_type = 'movie'
        elif self.recipe['library_type'].lower().startswith('tv'):
            self.library_type = 'tv'
        else:
            raise Exception("Library type should be 'movie' or 'tv'")

        self.source_library_config = self.recipe['source_libraries']

        self.plex = plexutils.Plex(self.config['plex']['baseurl'],
                                   self.config['plex']['token'])

        sources = build_sources(
            self.config, trakt_oauth=self.recipe.get('trakt_oauth', False))
        self.trakt = sources['trakt']
        self.tmdb = sources['tmdb']
        self.mdblist = sources['mdblist']
        self.tmdb_source = sources['tmdb_source']
        self.imdb = sources['imdb']
        self._sources = sources

        if self.trakt and self.trakt.oauth_token:
            self.config['trakt']['oauth_token'] = self.trakt.oauth_token

        self.source_map = IdMap(matching_only=True,
                                cache_file=self.config.get('guid_cache_file'))
        self.dest_map = IdMap(cache_file=self.config.get('guid_cache_file'))



    def _list_settings(self):
        """The recipe section that governs this run (playlist or library)."""
        if self.use_playlists:
            return self.recipe.get('new_playlist') or {}
        return self.recipe.get('new_library') or {}

    def _source_for(self, url):
        return resolve_source(url, self._sources)

    def _get_source_lists(self):
        item_list = []  # TODO Replace with dict, scrap item_ids?
        item_ids = []

        max_age = self._list_settings().get('max_age', 0) or 0

        for url in self.recipe['source_list_urls']:
            source = self._source_for(url)
            before = len(item_list)
            (item_list, item_ids) = source.add_items(
                self.library_type, url, item_list, item_ids, max_age)
            added = len(item_list) - before
            logs.info(u"  {} new items from {}".format(added, url))
            if not added:
                logs.warning(u"Source list returned no new items: {}".format(
                    url))

        if not item_list:
            # Everything downstream treats "not in the source list" as
            # "remove from the library", so an empty list here would unlink
            # the entire destination library. Refuse instead.
            raise SourceListError(
                "No items were returned by any source list, so there is "
                "nothing to build. Refusing to continue, because carrying on "
                "would treat every item already in "
                "'{}' as stale and remove it.".format(
                    self._list_settings().get('name', 'the destination')))

        if self.recipe['weighted_sorting']['enabled']:
            if (self.config.get('tmdb') or {}).get('api_key'):
                logs.info(u"Getting data from TMDb to add weighted sorting...")
                item_list = self.weighted_sorting(item_list)
            else:
                logs.warning(u"Warning: TMDd API key is required "
                             u"for weighted sorting")
        return item_list, item_ids

    def _get_plex_libraries(self):
        source_libraries = []
        for library_config in self.source_library_config:
            logs.info(u"Trying to match with items from the '{}' library ".format(
                library_config['name']))
            try:
                source_library = self.plex.server.library.section(
                    library_config['name'])
            except:  # FIXME
                raise Exception("The '{}' library does not exist".format(
                    library_config['name']))

            source_libraries.append(source_library)
        return source_libraries

    def _get_matching_items(self, source_libraries, item_list):
        matching_items = []
        missing_items = []
        matching_total = 0
        nonmatching_idx = []
        max_count = self._list_settings().get('max_count', 0) or 0

        for i, item in enumerate(item_list):
            if 0 < max_count <= matching_total:
                nonmatching_idx.append(i)
                continue
            res = self.source_map.get(item.get('id'), item.get('tmdb_id'),
                                      item.get('tvdb_id'))

            if not res:
                missing_items.append((i, item))
                nonmatching_idx.append(i)
                continue

            matching_total += 1
            matching_items += res

            if not self.use_playlists and self.recipe['new_library']['sort_title']['absolute']:
                logs.info(u"{} {} ({})".format(
                    i + 1, item['title'], item['year']))
            else:
                logs.info(u"{} {} ({})".format(
                    matching_total, item['title'], item['year']))

        if not self.use_playlists and not self.recipe['new_library']['sort_title']['absolute']:
            for i in reversed(nonmatching_idx):
                del item_list[i]

        return matching_items, missing_items, matching_total, nonmatching_idx, max_count

    def _remove_stale_link(self, link_path, expected_target=None):
        """Remove a symlink that dangles or points somewhere unexpected.

        os.path.exists() follows symlinks, so a broken link looks absent to
        it while os.path.lexists() still sees it. Link creation is guarded on
        lexists(), so without this a broken link is never repaired and its
        item silently never appears in the new library.

        Returns True if the path was cleared.
        """
        if not os.path.islink(link_path):
            return False

        dangling = not os.path.exists(link_path)
        mispointed = False
        if not dangling and expected_target:
            try:
                mispointed = (os.path.realpath(link_path) !=
                              os.path.realpath(expected_target))
            except OSError:
                mispointed = False

        if not (dangling or mispointed):
            return False

        try:
            os.unlink(link_path)
            logs.info(u"Removed {} symlink: '{}'".format(
                "broken" if dangling else "outdated", link_path))
            return True
        except OSError as e:
            logs.error(u"Could not remove stale symlink '{}': {}".format(
                link_path, e))
            return False

    def _create_symbolic_links(self, matching_items, matching_total):
        logs.info(u"Creating symlinks for {count} matching items in the "
                  u"library...".format(count=matching_total))

        try:
            if not os.path.exists(self.recipe['new_library']['folder']):
                os.mkdir(self.recipe['new_library']['folder'])
        except:
            logs.error(u"Unable to create the new library folder "
                       u"'{folder}'.".format(folder=self.recipe['new_library']['folder']))
            logs.info(u"Exiting script.")
            return 0

        count = 0
        updated_paths = []
        new_items = []
        if self.library_type == 'movie':
            for movie in matching_items:
                for part in movie.iterParts():
                    old_path_file = part.file
                    old_path, file_name = os.path.split(old_path_file)

                    folder_name = ''
                    for library_config in self.source_library_config:
                        for f in self.plex.get_library_paths(library_name=library_config['name']):
                            f = os.path.abspath(f)
                            if old_path.lower().startswith(f.lower()):
                                folder_name = os.path.relpath(old_path, f)
                                break
                        else:
                            continue

                        if folder_name == '.':
                            new_path = os.path.join(
                                self.recipe['new_library']['folder'],
                                file_name)
                            dir = False
                            if os.path.islink(new_path):
                                self._remove_stale_link(new_path,
                                                        old_path_file)
                        else:
                            new_path = os.path.join(
                                self.recipe['new_library']['folder'],
                                folder_name)
                            dir = True
                            parent_path = os.path.dirname(
                                os.path.abspath(new_path))
                            if not os.path.exists(parent_path):
                                try:
                                    os.makedirs(parent_path)
                                except OSError as e:
                                    if e.errno == errno.EEXIST \
                                            and os.path.isdir(parent_path):
                                        pass
                                    else:
                                        raise
                            # Clear anything stale so the link below can be
                            # (re)created
                            if os.path.lexists(new_path):
                                if os.path.islink(new_path):
                                    self._remove_stale_link(new_path, old_path)
                                elif os.path.isdir(new_path) and not os.listdir(new_path):
                                    os.rmdir(new_path)

                        if (dir and not os.path.lexists(new_path)) \
                                or not dir and not os.path.lexists(new_path):
                            try:
                                source_path = old_path if dir else old_path_file
                                logs.info(u"Linking: '{}' -> '{}'".format(source_path, new_path))
                                if not os.path.exists(source_path):
                                    logs.warning(u"Source path does not exist: '{}'".format(source_path))

                                if os.name == 'nt':
                                    if dir:
                                        subprocess.call(['mklink', '/D',
                                                         new_path, old_path],
                                                        shell=True)
                                    else:
                                        subprocess.call(['mklink', new_path,
                                                         old_path_file],
                                                        shell=True)
                                else:
                                    if dir:
                                        os.symlink(old_path, new_path)
                                    else:
                                        os.symlink(old_path_file, new_path)
                                
                                if not os.path.exists(new_path):
                                    logs.warning(u"Created link is broken (destination not found): '{}'".format(new_path))

                                count += 1
                                new_items.append(movie)
                                updated_paths.append(new_path)
                            except Exception as e:
                                logs.error(u"Symlink failed for {path}: {e}".format(
                                    path=new_path, e=e))
        else:
            for tv_show in matching_items:
                done = False
                if done:
                    continue
                for episode in tv_show.episodes():
                    if done:
                        break
                    for part in episode.iterParts():
                        old_path_file = part.file
                        old_path, file_name = os.path.split(old_path_file)

                        folder_name = ''
                        for library_config in self.source_library_config:
                            for f in self.plex.get_library_paths(library_name=library_config['name']):
                                if old_path.lower().startswith(f.lower()):
                                    old_path = os.path.join(f,
                                                            old_path.replace(
                                                                f, '').strip(
                                                                os.sep).split(
                                                                os.sep)[0])
                                    folder_name = os.path.relpath(old_path, f)
                                    break
                            else:
                                continue

                            new_path = os.path.join(
                                self.recipe['new_library']['folder'],
                                folder_name)

                            if not os.path.exists(new_path):
                                try:
                                    if os.name == 'nt':
                                        subprocess.call(['mklink', '/D',
                                                         new_path, old_path],
                                                        shell=True)
                                    else:
                                        os.symlink(old_path, new_path)
                                    count += 1
                                    new_items.append(tv_show)
                                    updated_paths.append(new_path)
                                    done = True
                                    break
                                except Exception as e:
                                    logs.error(u"Symlink failed for {path}: {e}"
                                               .format(path=new_path, e=e))
                            else:
                                done = True
                                break

        logs.info(u"Created symlinks for {count} new items:".format(count=count))
        for item in new_items:
            logs.info(u"{title} ({year})".format(title=item.title, year=getattr(item, 'year', None)))
        return count

    def _verify_new_library_and_get_items(self, create_if_not_found=False, expected_count=None):
        # Check if the new library exists in Plex
        try:
            new_library = self.plex.server.library.section(
                self.recipe['new_library']['name'])
            logs.info(u"Library already exists in Plex. Scanning the library...")

            new_library.update()
        except plexapi.exceptions.NotFound:
            if create_if_not_found:
                self.plex.create_new_library(
                    self.recipe['new_library']['name'],
                    self.recipe['new_library']['folder'],
                    self.library_type)
                new_library = self.plex.server.library.section(
                    self.recipe['new_library']['name'])
            else:
                raise Exception("Library '{library}' does not exist".format(
                    library=self.recipe['new_library']['name']))

        # Wait for metadata to finish downloading before continuing
        logs.info(u"Waiting for metadata to finish downloading...")
        time.sleep(10)
        
        start_time = time.time()
        last_scan_time = start_time
        
        while True:
            new_library = self.plex.server.library.section(
                self.recipe['new_library']['name'])
            
            # If we expect a specific count, wait for it regardless of refreshing status
            if expected_count is not None:
                item_count = len(new_library.all())
                if item_count >= expected_count:
                    break
                
                current_time = time.time()
                if current_time - start_time > 300:  # 5 minute timeout
                    logs.warning(u"Timed out waiting for library to contain {expected} items.".format(
                        expected=expected_count))
                    break
                    
                if current_time - last_scan_time > 30: # Re-trigger scan every 30s
                    logs.info(u"Re-triggering library scan...")
                    new_library.update()
                    last_scan_time = current_time
                    
                logs.info(u"Waiting for library to contain {expected} items (current: {current})...".format(
                    expected=expected_count, current=item_count))
            # Otherwise fall back to refreshing status
            elif not new_library.refreshing:
                break
                
            time.sleep(5)

        # Retrieve a list of items from the new library
        logs.info(u"Retrieving a list of items from the '{library}' library in "
                  u"Plex...".format(library=self.recipe['new_library']['name']))
        return new_library, new_library.all()

    def _modify_sort_titles_and_cleanup(self, item_list, new_library,
                                        sort_only=False, expected_count=None):
        if self.recipe['new_library']['sort']:
            logs.info(u"Setting the sort titles for the '{}' library".format(
                self.recipe['new_library']['name']))
        if self.recipe['new_library']['sort_title']['absolute']:
            for i, m in enumerate(item_list):
                item = self.dest_map.pop(m.get('id'), m.get('tmdb_id'), m.get('tvdb_id'))
                if item and self.recipe['new_library']['sort']:
                    self.plex.set_sort_title(
                        new_library.key, item.ratingKey, i + 1, m['title'],
                        self.library_type,
                        self.recipe['new_library']['sort_title']['format'],
                        self.recipe['new_library']['sort_title']['visible']
                    )
        else:
            i = 0
            for m in item_list:
                i += 1
                item = self.dest_map.pop(m.get('id'), m.get('tmdb_id'), m.get('tvdb_id'))
                if item and self.recipe['new_library']['sort']:
                    self.plex.set_sort_title(
                        new_library.key, item.ratingKey, i, m['title'],
                        self.library_type,
                        self.recipe['new_library']['sort_title']['format'],
                        self.recipe['new_library']['sort_title']['visible']
                    )
        unmatched_items = list(self.dest_map.items)
        if not sort_only and (
                self.recipe['new_library']['remove_from_library'] or
                self.recipe['new_library'].get('remove_old', False)):
            # Remove old items that no longer qualify
            self._remove_old_items_from_library(unmatched_items)
        elif sort_only:
            return True
        if self.recipe['new_library']['sort'] and \
                not self.recipe['new_library']['remove_from_library']:
            unmatched_items.sort(key=lambda x: x.titleSort)
            while unmatched_items:
                item = unmatched_items.pop(0)
                i += 1
                logs.info(u"{} {} ({})".format(i, item.title, item.year))
                self.plex.set_sort_title(
                    new_library.key, item.ratingKey, i, item.title,
                    self.library_type,
                    self.recipe['new_library']['sort_title']['format'],
                    self.recipe['new_library']['sort_title']['visible'])
        all_new_items = self._cleanup_new_library(new_library=new_library, expected_count=expected_count)
        return all_new_items

    def _remove_old_items_from_library(self, unmatched_items):
        logs.info(u"Removing symlinks for items "
                  "which no longer qualify ".format(library=self.recipe['new_library']['name']))
        count = 0
        updated_paths = []
        deleted_items = []
        max_date = add_years(
            (self.recipe['new_library'].get('max_age') or 0) * -1)
        if self.library_type == 'movie':
            for movie in unmatched_items:
                if not self.recipe['new_library']['remove_from_library']:
                    # Only remove older than max_age
                    if not self.recipe['new_library'].get('max_age') \
                            or (movie.originallyAvailableAt and
                                max_date < movie.originallyAvailableAt):
                        continue

                for part in movie.iterParts():
                    old_path_file = part.file
                    old_path, file_name = os.path.split(old_path_file)

                    folder_name = os.path.relpath(
                        old_path, self.recipe['new_library']['folder'])

                    if folder_name == '.':
                        new_path = os.path.join(
                            self.recipe['new_library']['folder'],
                            file_name)
                        dir = False
                    else:
                        new_path = os.path.join(
                            self.recipe['new_library']['folder'],
                            folder_name)
                        dir = True

                    if (dir and os.path.exists(new_path)) or (
                            not dir and os.path.isfile(new_path)):
                        try:
                            if os.name == 'nt':
                                # Python 3.2+ only
                                if sys.version_info < (3, 2):
                                    assert os.path.islink(new_path)
                                if dir:
                                    os.rmdir(new_path)
                                else:
                                    os.remove(new_path)
                            else:
                                assert os.path.islink(new_path)
                                os.unlink(new_path)
                            count += 1
                            deleted_items.append(movie)
                            updated_paths.append(new_path)
                        except Exception as e:
                            logs.error(u"Remove symlink failed for "
                                       "{path}: {e}".format(path=new_path, e=e))
        else:
            for tv_show in unmatched_items:
                done = False
                if done:
                    continue
                for episode in tv_show.episodes():
                    if done:
                        break
                    for part in episode.iterParts():
                        if done:
                            break
                        old_path_file = part.file
                        old_path, file_name = os.path.split(old_path_file)

                        folder_name = ''
                        new_library_folder = \
                            self.recipe['new_library']['folder']
                        old_path = os.path.join(
                            new_library_folder,
                            old_path.replace(new_library_folder, '').strip(
                                os.sep).split(os.sep)[0])
                        folder_name = os.path.relpath(old_path,
                                                      new_library_folder)

                        new_path = os.path.join(
                            self.recipe['new_library']['folder'],
                            folder_name)
                        if os.path.exists(new_path):
                            try:
                                if os.name == 'nt':
                                    # Python 3.2+ only
                                    if sys.version_info < (3, 2):
                                        assert os.path.islink(new_path)
                                    os.rmdir(new_path)
                                else:
                                    assert os.path.islink(new_path)
                                    os.unlink(new_path)
                                count += 1
                                deleted_items.append(tv_show)
                                updated_paths.append(new_path)
                                done = True
                                break
                            except Exception as e:
                                logs.error(u"Remove symlink failed for "
                                           "{path}: {e}".format(path=new_path,
                                                                e=e))
                        else:
                            done = True
                            break

        logs.info(u"Removed symlinks for {count} items.".format(count=count))
        for item in deleted_items:
            logs.info(u"{title} ({year})".format(title=item.title,
                                                 year=item.year))

    def _cleanup_new_library(self, new_library, expected_count=None):
        # Scan the library to clean up the deleted items
        logs.info(u"Scanning the '{library}' library...".format(
            library=self.recipe['new_library']['name']))
        new_library.update()
        time.sleep(10)
        
        start_time = time.time()
        last_scan_time = start_time
        
        while True:
            new_library = self.plex.server.library.section(
                self.recipe['new_library']['name'])
                
            if expected_count is not None:
                item_count = len(new_library.all())
                if item_count == expected_count:
                    break
                    
                current_time = time.time()
                if current_time - start_time > 300:  # 5 minute timeout
                    logs.warning(u"Timed out waiting for library to contain {expected} items.".format(
                        expected=expected_count))
                    break
                    
                if current_time - last_scan_time > 30: # Re-trigger scan every 30s
                    logs.info(u"Re-triggering library scan...")
                    new_library.update()
                    last_scan_time = current_time
                    
                logs.info(u"Waiting for library to contain {expected} items (current: {current})...".format(
                    expected=expected_count, current=item_count))
            elif not new_library.refreshing:
                break
                
            time.sleep(5)
            
        new_library.emptyTrash()
        return new_library.all()

    def _run(self, share_playlist_to_all=False):
        # Get the trakt lists
        item_list, item_ids = self._get_source_lists()
        force_imdb_id_match = False

        # Get list of items from the Plex server
        source_libraries = self._get_plex_libraries()

        # Populate source library guid map
        for item in item_list:
            if item.get('id'):
                self.source_map.match_imdb.append(item['id'])
            if item.get('tmdb_id'):
                self.source_map.match_tmdb.append(item['tmdb_id'])
            if item.get('tvdb_id'):
                self.source_map.match_tvdb.append(item['tvdb_id'])
        self.source_map.add_libraries(source_libraries)

        # Create a list of matching items
        matching_items, missing_items, matching_total, nonmatching_idx, max_count = self._get_matching_items(
            source_libraries, item_list)

        if self.use_playlists:
            # Start playlist process
            if self.recipe['new_playlist']['remove_from_playlist'] or self.recipe['new_playlist'].get('remove_old',
                                                                                                      False):
                # Start playlist over again
                self.plex.reset_playlist(playlist_name=self.recipe['new_playlist']['name'], new_items=matching_items,
                                         user_names=self.recipe['new_playlist'].get('share_to_users', []),
                                         all_users=(share_playlist_to_all if share_playlist_to_all else
                                                    self.recipe['new_playlist'].get('share_to_all', False)))
            else:
                # Keep existing items
                self.plex.add_to_playlist_for_users(playlist_name=self.recipe['new_playlist']['name'],
                                                    items=matching_items,
                                                    user_names=self.recipe['new_playlist'].get('share_to_users', []),
                                                    all_users=(share_playlist_to_all if share_playlist_to_all else
                                                               self.recipe['new_playlist'].get('share_to_all', False)))
            playlist_items = self.plex.get_playlist_items(playlist_name=self.recipe['new_playlist']['name'])
            return missing_items, (len(playlist_items) if playlist_items else 0)
        else:
            # Start library process
            # Create symlinks for all items in your library on the trakt watched
            count = self._create_symbolic_links(matching_items=matching_items, matching_total=matching_total)
            # Post-process new library
            logs.info(u"Creating the '{}' library in Plex...".format(
                self.recipe['new_library']['name']))
            new_library, all_new_items = self._verify_new_library_and_get_items(create_if_not_found=True, expected_count=len(matching_items))
            self.dest_map.add_items(all_new_items)
            # Modify the sort titles
            all_new_items = self._modify_sort_titles_and_cleanup(
                    item_list, new_library, sort_only=False, expected_count=len(matching_items))
            return missing_items, len(all_new_items)

    def _run_sort_only(self):
        item_list, item_ids = self._get_source_lists()
        force_imdb_id_match = False

        # Get existing library and its items
        new_library, all_new_items = self._verify_new_library_and_get_items(create_if_not_found=False)
        self.dest_map.add_items(all_new_items)
        # Modify the sort titles
        self._modify_sort_titles_and_cleanup(item_list, new_library,
                                             sort_only=True)
        return len(all_new_items)

    def run(self, sort_only=False, share_playlist_to_all=False):
        if sort_only:
            logs.info(u"Running the recipe '{}', sorting only".format(
                self.recipe_name))
            list_count = self._run_sort_only()
            logs.info(u"Number of items in the new {library_or_playlist}: {count}".format(
                count=list_count, library_or_playlist=('playlist' if self.use_playlists else 'library')))
        else:
            logs.info(u"Running the recipe '{}'".format(self.recipe_name))
            missing_items, list_count = self._run(share_playlist_to_all=share_playlist_to_all)
            logs.info(u"Number of items in the new {library_or_playlist}: {count}".format(
                count=list_count, library_or_playlist=('playlist' if self.use_playlists else 'library')))
            logs.info(u"Number of missing items: {count}".format(
                count=len(missing_items)))
            for idx, item in missing_items:
                logs.info(u"{idx}\t{release}\t{imdb_id}\t{title} ({year})".format(
                    idx=idx + 1, release=item.get('release_date', ''),
                    imdb_id=item['id'], title=item['title'],
                    year=item['year']))

    def weighted_sorting(self, item_list):
        def _get_non_theatrical_release(release_dates):
            # Returns earliest release date that is not theatrical
            # TODO PREDB
            types = {}
            for country in release_dates.get('results', []):
                # FIXME Look at others too?
                if country['iso_3166_1'] != 'US':
                    continue
                for d in country['release_dates']:
                    if d['type'] in (4, 5, 6):
                        # 4: Digital, 5: Physical, 6: TV
                        types[str(d['type'])] = datetime.datetime.strptime(
                            d['release_date'], '%Y-%m-%dT%H:%M:%S.%fZ').date()
                break

            release_date = None
            for t, d in types.items():
                if not release_date or d < release_date:
                    release_date = d

            return release_date

        def _get_age_weight(days):
            if self.library_type == 'movie':
                # Everything younger than this will get 1
                min_days = 180
                # Everything older than this will get 0
                max_days = (float(self._list_settings().get('max_age') or 0)
                            / 4.0 * 365.25 or 360)
            else:
                min_days = 14
                max_days = (float(self._list_settings().get('max_age') or 0)
                            / 4.0 * 365.25 or 180)
            if days <= min_days:
                return 1
            elif days >= max_days:
                return 0
            else:
                return 1 - (days - min_days) / (max_days - min_days)

        total_items = len(item_list)

        weights = self.recipe['weighted_sorting']['weights']

        # TMDB details
        today = datetime.date.today()
        total_tmdb_vote = 0.0
        tmdb_votes = []
        for i, m in enumerate(item_list):
            m['original_idx'] = i + 1
            details = self.tmdb.get_details(m['tmdb_id'], self.library_type)
            if not details:
                logs.warning(u"Warning: No TMDb data for {}".format(m['title']))
                continue
            m['tmdb_popularity'] = float(details['popularity'])
            m['tmdb_vote'] = float(details['vote_average'])
            m['tmdb_vote_count'] = int(details['vote_count'])
            if self.library_type == 'movie':
                if self.recipe['weighted_sorting']['better_release_date']:
                    m['release_date'] = _get_non_theatrical_release(
                        details['release_dates']) or \
                                        datetime.datetime.strptime(
                                            details['release_date'],
                                            '%Y-%m-%d').date()
                else:
                    m['release_date'] = datetime.datetime.strptime(
                        details['release_date'], '%Y-%m-%d').date()
                item_age_td = today - m['release_date']
            elif self.library_type == 'tv':
                try:
                    m['last_air_date'] = datetime.datetime.strptime(
                        details['last_air_date'], '%Y-%m-%d').date()
                except TypeError:
                    m['last_air_date'] = today
                item_age_td = today - m['last_air_date']
            m['genres'] = [g['name'].lower() for g in details['genres']]
            m['age'] = item_age_td.days
            if (self.library_type == 'tv' or m['tmdb_vote_count'] > 150 or
                    m['age'] > 50):
                tmdb_votes.append(m['tmdb_vote'])
            total_tmdb_vote += m['tmdb_vote']
            item_list[i] = m

        tmdb_votes.sort()

        for i, m in enumerate(item_list):
            # Distribute all weights evenly from 0 to 1 (times global factor)
            # More weight means it'll go higher in the final list
            index_weight = float(total_items - i) / float(total_items)
            m['index_weight'] = index_weight * weights['index']
            if m.get('tmdb_popularity'):
                if (self.library_type == 'tv' or
                        m.get('tmdb_vote_count') > 150 or m['age'] > 50):
                    vote_weight = ((tmdb_votes.index(m['tmdb_vote']) + 1)
                                   / float(len(tmdb_votes)))
                else:
                    # Assume below average rating for new/less voted items
                    vote_weight = 0.25
                age_weight = _get_age_weight(float(m['age']))

                if weights.get('random'):
                    random_weight = random.random()
                    m['random_weight'] = random_weight * weights['random']
                else:
                    m['random_weight'] = 0.0

                m['vote_weight'] = vote_weight * weights['vote']
                m['age_weight'] = age_weight * weights['age']

                weight = (m['index_weight'] + m['vote_weight']
                          + m['age_weight'] + m['random_weight'])
                for genre, value in weights['genre_bias'].items():
                    if genre.lower() in m['genres']:
                        weight *= value

                m['weight'] = weight
            else:
                m['vote_weight'] = 0.0
                m['age_weight'] = 0.0
                m['weight'] = index_weight
            item_list[i] = m

        item_list.sort(key=lambda m: m['weight'], reverse=True)

        for i, m in enumerate(item_list):
            if (i + 1) < m['original_idx']:
                net = Colors.GREEN + u'↑'
            elif (i + 1) > m['original_idx']:
                net = Colors.RED + u'↓'
            else:
                net = u' '
            net += str(abs(i + 1 - m['original_idx'])).rjust(3)
            try:
                # TODO
                logs.info(u"{} {:>3}: trnd:{:>3}, w_trnd:{:0<5}; vote:{}, "
                          "w_vote:{:0<5}; age:{:>4}, w_age:{:0<5}; w_rnd:{:0<5}; "
                          "w_cmb:{:0<5}; {} {}{}"
                          .format(net, i + 1, m['original_idx'],
                                  round(m['index_weight'], 3),
                                  m.get('tmdb_vote', 0.0),
                                  round(m['vote_weight'], 3), m.get('age', 0),
                                  round(m['age_weight'], 3),
                                  round(m.get('random_weight', 0), 3),
                                  round(m['weight'], 3), str(m['title']),
                                  str(m['year']), Colors.RESET))
            except UnicodeEncodeError:
                pass

        return item_list
