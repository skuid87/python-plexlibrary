# -*- coding: utf-8 -*-
"""MDBList as a source of ranked lists.

MDBList aggregates Trakt, IMDb, TMDb, Letterboxd and Rotten Tomatoes lists and
re-serves them under a single free API key (1000 requests/day), which makes it
a drop-in replacement for both the Trakt endpoints and the IMDb chart scraping
this project used to rely on. Crucially its items already carry IMDb, TMDb and
TheTVDB ids, so no extra lookups are needed to match against Plex.

Get a free key from https://mdblist.com/preferences

Supported source_list_urls:

    https://api.mdblist.com/lists/official/moviemeter/items
    https://api.mdblist.com/lists/garycrawfordgc/latest-tv-shows/items
    https://mdblist.com/lists/garycrawfordgc/latest-tv-shows   (as copied from the site)
    https://api.mdblist.com/external/lists/12345/items         (an imported IMDb/Letterboxd list)
    https://api.mdblist.com/catalog/movie?genre=horror&sort=imdbrating&limit=100
    mdblist://lists/official/moviemeter/items                  (shorthand)

`limit` is treated as the total number of items to collect. `page_size` caps
how many items each request asks for (default and maximum 1000); it is only
useful for exercising cursor pagination, since most lists fit in one page.
Every other query parameter is passed through to MDBList.
"""
import datetime

try:
    from urllib.parse import urlparse, parse_qsl
except ImportError:  # pragma: no cover - py2
    from urlparse import urlparse, parse_qsl

import requests

import logs
from utils import SourceListError

BASE_URL = "https://api.mdblist.com"
DEFAULT_LIMIT = 1000
MAX_PAGE_SIZE = 1000
MAX_PAGES = 20


class MDBList(object):
    HOSTS = ('api.mdblist.com', 'mdblist.com', 'www.mdblist.com')

    def __init__(self, api_key):
        if not api_key:
            raise SourceListError(
                "An MDBList API key is required. Get a free one from "
                "https://mdblist.com/preferences and add it to config.yml "
                "under mdblist: api_key")
        self.api_key = api_key

    @classmethod
    def handles(cls, url):
        parsed = urlparse(url)
        if parsed.scheme == 'mdblist':
            return True
        return parsed.netloc.lower() in cls.HOSTS

    def _parse_url(self, url):
        """Return (api_path, params, limit, page_size)."""
        parsed = urlparse(url)
        params = dict(parse_qsl(parsed.query))
        limit = int(params.pop('limit', DEFAULT_LIMIT) or DEFAULT_LIMIT)
        # `page_size` is ours, not MDBList's: it caps how many items each
        # request asks for. Lists rarely exceed one page, so lowering it is
        # the only practical way to exercise cursor pagination.
        page_size = int(params.pop('page_size', MAX_PAGE_SIZE)
                        or MAX_PAGE_SIZE)
        page_size = max(1, min(page_size, MAX_PAGE_SIZE))

        if parsed.scheme == 'mdblist':
            path = "{}{}".format(parsed.netloc, parsed.path)
        else:
            path = parsed.path

        path = path.strip('/')
        if not path:
            raise SourceListError(
                "Could not work out an MDBList endpoint from: {}".format(url))

        segments = path.split('/')

        # A website list URL (mdblist.com/lists/user/listname) points at the
        # list itself; the API wants its items.
        if segments[0] == 'lists' and segments[-1] != 'items':
            if len(segments) >= 3:
                path = path + '/items'
        elif segments[0] == 'external' and segments[-1] != 'items':
            if len(segments) >= 3:
                path = path + '/items'

        return path, params, limit, page_size

    def _get(self, path, params):
        request_params = dict(params)
        request_params['apikey'] = self.api_key
        url = "{base}/{path}".format(base=BASE_URL, path=path)

        try:
            r = requests.get(url, params=request_params, timeout=30)
        except requests.RequestException as e:
            raise SourceListError(
                "Could not reach MDBList ({}): {}".format(url, e))

        if r.status_code == 401:
            raise SourceListError(
                "MDBList rejected the API key (401). Check the `mdblist: "
                "api_key` value in config.yml against "
                "https://mdblist.com/preferences")
        if r.status_code in (402, 429):
            raise SourceListError(
                "MDBList quota exhausted ({}). The free tier allows 1000 "
                "requests/day.".format(r.status_code))
        if r.status_code == 404:
            raise SourceListError(
                "MDBList has no such list: {}".format(url))
        if r.status_code != 200:
            raise SourceListError(
                "MDBList request failed ({}): {}".format(r.status_code, url))

        try:
            return r.json(), r.headers
        except ValueError as e:
            raise SourceListError(
                "MDBList returned a non-JSON response for {}: {}".format(
                    url, e))

    def _fetch(self, path, params, limit, media_key, page_size=MAX_PAGE_SIZE):
        """Page through an MDBList endpoint using cursor pagination."""
        collected = []
        cursor = None
        pages = 0

        while len(collected) < limit and pages < MAX_PAGES:
            page_params = dict(params)
            page_params['limit'] = min(limit - len(collected), page_size)
            if cursor:
                page_params['cursor'] = cursor

            data, headers = self._get(path, page_params)
            pages += 1

            if not isinstance(data, dict):
                raise SourceListError(
                    "Unexpected MDBList response shape for {}".format(path))

            entries = data.get(media_key) or []
            if not entries:
                break
            collected.extend(entries)

            pagination = data.get('pagination') or {}
            cursor = pagination.get('next_cursor') or data.get('next_cursor')
            if not cursor:
                break

        if pages > 1:
            logs.info(u"  fetched {} items across {} requests".format(
                len(collected), pages))
        return collected[:limit]

    def _item_ids(self, entry):
        ids = entry.get('ids') or {}
        imdb_id = entry.get('imdb_id') or ids.get('imdb')
        tmdb_id = entry.get('id') or ids.get('tmdb')
        tvdb_id = entry.get('tvdb_id') or ids.get('tvdb')
        return imdb_id, tmdb_id, tvdb_id

    def _release_date(self, entry):
        released = entry.get('released') or entry.get('release_date')
        if released:
            try:
                return datetime.datetime.strptime(
                    str(released)[:10], '%Y-%m-%d').date()
            except ValueError:
                pass
        return None

    def add_items(self, item_type, url, item_list=None, item_ids=None,
                  max_age=0):
        if item_list is None:
            item_list = []
        if item_ids is None:
            item_ids = []

        path, params, limit, page_size = self._parse_url(url)

        # MDBList speaks 'show', the recipes speak 'tv'
        media_key = 'movies' if item_type == 'movie' else 'shows'
        params.setdefault('mediatype',
                          'movie' if item_type == 'movie' else 'show')

        logs.info(u"Retrieving the MDBList list: {}".format(url))
        entries = self._fetch(path, params, limit, media_key, page_size)

        cutoff_year = None
        if max_age:
            cutoff_year = datetime.datetime.now().year - (max_age - 1)

        for entry in entries:
            imdb_id, tmdb_id, tvdb_id = self._item_ids(entry)
            if not any((imdb_id, tmdb_id, tvdb_id)):
                logs.warning(u"Skipping MDBList item with no usable ids: "
                             u"{}".format(entry.get('title')))
                continue

            year = entry.get('release_year')
            release_date = self._release_date(entry)

            if cutoff_year and year and int(year) < cutoff_year:
                continue

            if imdb_id and imdb_id in item_ids:
                continue
            if tmdb_id and 'tmdb' + str(tmdb_id) in item_ids:
                continue

            item = {
                'id': imdb_id,
                'tmdb_id': str(tmdb_id) if tmdb_id else None,
                'title': entry.get('title'),
                'year': year,
            }
            if item_type == 'tv':
                item['tvdb_id'] = str(tvdb_id) if tvdb_id else None
            if release_date:
                item['release_date'] = release_date

            item_list.append(item)
            if imdb_id:
                item_ids.append(imdb_id)
            if tmdb_id:
                item_ids.append('tmdb' + str(tmdb_id))
            if tvdb_id:
                item_ids.append('tvdb' + str(tvdb_id))

        return item_list, item_ids
