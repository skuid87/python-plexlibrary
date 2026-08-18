# -*- coding: utf-8 -*-
"""TMDb as a source of ranked lists.

Replaces the Trakt list endpoints for the common "trending"/"popular"/
"top rated" recipes. TMDb API keys are still free for non-commercial use and
the key is already required for weighted sorting, so this needs no new
credentials.

Supported source_list_urls:

    https://api.themoviedb.org/3/trending/movie/week?limit=100
    https://api.themoviedb.org/3/movie/popular
    https://api.themoviedb.org/3/movie/top_rated?limit=250
    https://api.themoviedb.org/3/trending/tv/week
    https://api.themoviedb.org/3/tv/top_rated
    https://api.themoviedb.org/3/discover/movie?sort_by=vote_average.desc&vote_count.gte=500
    https://api.themoviedb.org/3/list/8261445          (a public TMDb list)
    https://www.themoviedb.org/list/8261445            (same, as copied from the site)
    tmdb://trending/movie/week                         (shorthand)

Any query parameter other than `limit` is passed straight through to TMDb, so
the full `discover` filter set is available.
"""
import datetime

try:
    from urllib.parse import urlparse, parse_qsl
except ImportError:  # pragma: no cover - py2
    from urlparse import urlparse, parse_qsl

import logs
from utils import SourceListError, add_years

DEFAULT_LIMIT = 100
PAGE_SIZE = 20
MAX_PAGES = 100


class TMDbSource(object):
    HOSTS = ('api.themoviedb.org', 'themoviedb.org', 'www.themoviedb.org')

    def __init__(self, tmdb):
        self.tmdb = tmdb

    @classmethod
    def handles(cls, url):
        parsed = urlparse(url)
        if parsed.scheme == 'tmdb':
            return True
        return parsed.netloc.lower() in cls.HOSTS

    def _parse_url(self, url):
        """Return (path, params, limit)."""
        parsed = urlparse(url)
        params = dict(parse_qsl(parsed.query))
        limit = int(params.pop('limit', DEFAULT_LIMIT) or DEFAULT_LIMIT)

        if parsed.scheme == 'tmdb':
            # tmdb://trending/movie/week -> trending/movie/week
            path = "{}{}".format(parsed.netloc, parsed.path)
        else:
            path = parsed.path
            # Strip the API version prefix if present
            if path.startswith('/3/'):
                path = path[3:]

        path = path.strip('/')
        if not path:
            raise SourceListError(
                "Could not work out a TMDb endpoint from: {}".format(url))
        return path, params, limit

    def _fetch(self, path, params, limit):
        """Page through a TMDb endpoint until `limit` items are collected."""
        collected = []
        page = 1
        while len(collected) < limit and page <= MAX_PAGES:
            page_params = dict(params)
            page_params['page'] = page
            data = self.tmdb.get_json(path, page_params)
            if data is None:
                raise SourceListError(
                    "TMDb returned no data for '{}'. Check the endpoint and "
                    "your API key.".format(path))

            # Ranked endpoints use `results`; v3 lists use `items`.
            results = data.get('results')
            if results is None:
                results = data.get('items')
            if not results:
                break

            collected.extend(results)

            total_pages = data.get('total_pages')
            if not total_pages or page >= total_pages:
                break
            if len(results) < PAGE_SIZE and not data.get('total_pages'):
                break
            page += 1

        return collected[:limit]

    def _normalise(self, entry, library_type):
        """Map a TMDb result onto the internal item shape."""
        media_type = entry.get('media_type', library_type)
        if media_type == 'movie':
            title = entry.get('title') or entry.get('original_title')
            date_str = entry.get('release_date')
        else:
            title = entry.get('name') or entry.get('original_name')
            date_str = entry.get('first_air_date')

        date = None
        if date_str:
            try:
                date = datetime.datetime.strptime(date_str, '%Y-%m-%d')
            except ValueError:
                date = None

        return title, date, media_type

    def add_items(self, item_type, url, item_list=None, item_ids=None,
                  max_age=0):
        if item_list is None:
            item_list = []
        if item_ids is None:
            item_ids = []

        path, params, limit = self._parse_url(url)
        logs.info(u"Retrieving the TMDb list: {}".format(url))

        entries = self._fetch(path, params, limit)
        max_date = add_years(max_age * -1) if max_age else None

        for entry in entries:
            tmdb_id = entry.get('id')
            if not tmdb_id:
                continue

            title, date, media_type = self._normalise(entry, item_type)

            # A mixed `trending/all` feed can carry both; keep what we asked for
            if media_type not in (item_type, 'movie', 'tv'):
                continue
            if item_type == 'movie' and media_type == 'tv':
                continue
            if item_type == 'tv' and media_type == 'movie':
                continue

            if 'tmdb' + str(tmdb_id) in item_ids:
                continue

            if max_date and date and max_date > date:
                continue

            external = self.tmdb.get_external_ids(tmdb_id, item_type)
            imdb_id = external.get('imdb_id')
            if imdb_id and imdb_id in item_ids:
                continue

            item = {
                'id': imdb_id,
                'tmdb_id': str(tmdb_id),
                'title': title,
                'year': date.year if date else None,
            }
            if item_type == 'tv':
                tvdb_id = external.get('tvdb_id')
                item['tvdb_id'] = str(tvdb_id) if tvdb_id else None
            if date:
                item['release_date'] = date.date()

            item_list.append(item)
            item_ids.append('tmdb' + str(tmdb_id))
            if imdb_id:
                item_ids.append(imdb_id)
            if item_type == 'tv' and item.get('tvdb_id'):
                item_ids.append('tvdb' + item['tvdb_id'])

        return item_list, item_ids
