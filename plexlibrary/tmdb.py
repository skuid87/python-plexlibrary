# -*- coding: utf-8 -*-
import json
import shelve
import time
from pickle import UnpicklingError

import requests

import logs
from utils import resolve_cache_path

BASE_URL = "https://api.themoviedb.org/3"

# TMDb retired its old 40-requests-per-10-seconds limit; the current ceiling is
# in the 40-50 requests/second range. Stay well under it rather than sleeping
# 10 seconds every 40 calls like this used to.
MAX_REQUESTS_PER_SECOND = 20

CACHE_TTL = 3600 * 24


class TMDb(object):
    api_key = None
    cache_file = None

    def __init__(self, api_key, cache_file=None):
        self.api_key = api_key
        self.cache_file = resolve_cache_path(
            cache_file, '~/.cache/plexlibrary/tmdb_details.shelve')
        self._last_request = 0.0

    # -- cache ------------------------------------------------------------

    def _cache_key(self, kind, id_, library_type):
        """Namespaced cache key.

        Movie 123 and TV show 123 are different titles, but the old cache
        keyed on the bare id, so whichever was fetched first won and the
        other silently got the wrong metadata.
        """
        return "{}:{}:{}".format(kind, library_type, id_)

    def _cache_get(self, key):
        try:
            cache = shelve.open(self.cache_file)
        except Exception as e:
            logs.warning(u"Unable to open TMDb cache ({}), skipping cache".format(e))
            return None
        try:
            item = cache.get(key)
        except (EOFError, UnpicklingError, KeyError):
            cache.close()
            # Corrupt cache, start over
            shelve.open(self.cache_file, 'n').close()
            return None
        except Exception as e:
            logs.error(u"Error reading TMDb cache: {}".format(e))
            cache.close()
            return None
        cache.close()
        if item and item.get('cached', 0) + CACHE_TTL > int(time.time()):
            return item
        return None

    def _cache_set(self, key, value):
        if not isinstance(value, dict):
            return value
        value['cached'] = int(time.time())
        try:
            cache = shelve.open(self.cache_file)
        except Exception as e:
            logs.warning(u"Unable to write TMDb cache ({})".format(e))
            return value
        cache[key] = value
        cache.close()
        return value

    # -- http -------------------------------------------------------------

    def _throttle(self):
        min_interval = 1.0 / MAX_REQUESTS_PER_SECOND
        elapsed = time.time() - self._last_request
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_request = time.time()

    def get_json(self, path, params=None, _retried=False):
        """GET an arbitrary TMDb v3 path. Returns the decoded body or None."""
        if not self.api_key:
            raise Exception("A TMDb API key is required for this operation")
        request_params = {'api_key': self.api_key}
        if params:
            request_params.update(params)

        self._throttle()
        url = "{base}/{path}".format(base=BASE_URL, path=path.lstrip('/'))
        r = requests.get(url, params=request_params, timeout=30)

        if r.status_code == 429 and not _retried:
            retry_after = int(r.headers.get('Retry-After', 1))
            logs.warning(u"TMDb rate limited, waiting {}s".format(retry_after))
            time.sleep(retry_after)
            return self.get_json(path, params=params, _retried=True)

        if r.status_code != 200:
            logs.warning(u"TMDb request failed ({}): {}".format(
                r.status_code, url))
            return None

        return json.loads(r.text)

    # -- api --------------------------------------------------------------

    def get_details(self, tmdb_id, library_type='movie'):
        if library_type not in ('movie', 'tv'):
            raise Exception("Library type should be 'movie' or 'tv'")

        key = self._cache_key('details', tmdb_id, library_type)
        cached = self._cache_get(key)
        if cached:
            return cached

        params = {}
        if library_type == 'movie':
            params['append_to_response'] = 'release_dates'
        item = self.get_json("{}/{}".format(library_type, tmdb_id), params)
        if not item:
            return None
        return self._cache_set(key, item)

    def get_external_ids(self, tmdb_id, library_type='movie'):
        """Return {'imdb_id': ..., 'tvdb_id': ...} for a TMDb id.

        For movies the ids come free with the details call (which weighted
        sorting needs anyway), so this is usually a cache hit.
        """
        if library_type not in ('movie', 'tv'):
            raise Exception("Library type should be 'movie' or 'tv'")

        if library_type == 'movie':
            details = self.get_details(tmdb_id, 'movie')
            if not details:
                return {'imdb_id': None, 'tvdb_id': None}
            return {'imdb_id': details.get('imdb_id'), 'tvdb_id': None}

        key = self._cache_key('external', tmdb_id, library_type)
        cached = self._cache_get(key)
        if cached:
            return {'imdb_id': cached.get('imdb_id'),
                    'tvdb_id': cached.get('tvdb_id')}

        item = self.get_json("tv/{}/external_ids".format(tmdb_id))
        if not item:
            return {'imdb_id': None, 'tvdb_id': None}
        self._cache_set(key, item)
        return {'imdb_id': item.get('imdb_id'), 'tvdb_id': item.get('tvdb_id')}

    def get_imdb_id(self, tmdb_id, library_type='movie'):
        return self.get_external_ids(tmdb_id, library_type).get('imdb_id')

    def get_tmdb_from_imdb(self, imdb_id, library_type):
        if library_type not in ('movie', 'tv'):
            raise Exception("Library type should be 'movie' or 'tv'")

        key = self._cache_key('find', imdb_id, library_type)
        cached = self._cache_get(key)
        if cached:
            return cached

        item = self.get_json("find/{}".format(imdb_id),
                             {'external_source': 'imdb_id'})
        if not item:
            return None

        if library_type == 'movie':
            results = item.get('movie_results')
        else:
            results = item.get('tv_results')

        if not results:
            return None

        return self._cache_set(key, results[0])
