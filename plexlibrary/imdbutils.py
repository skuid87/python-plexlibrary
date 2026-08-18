# -*- coding: utf-8 -*-
"""IMDb sources.

Two classes, because there are two very different situations:

IMDb        Scrapes the chart pages, which no longer works. IMDb sits behind
            AWS WAF bot protection: a plain request gets 403 and a
            browser-shaped one gets a 202 carrying a JavaScript challenge.
            Kept so those URLs fail loudly and point somewhere that works.

IMDbChart   Reads the same charts as JSON from the GraphQL endpoint IMDb's
            own site uses, which is how Kometa sources its IMDb defaults.
            Undocumented, and subject to IMDb's data-use terms -- see the
            class docstring.

Equivalent lists are also available from MDBList (see mdblistutils) and TMDb
(see tmdbutils), both on free keys.
"""
import datetime

try:
    from urllib.parse import urlparse, parse_qsl
except ImportError:  # pragma: no cover - py2
    from urlparse import urlparse, parse_qsl

import requests
from lxml import html

import logs
from utils import SourceListError, add_years

ALTERNATIVES = (
    "IMDb no longer serves its charts to scripts (AWS WAF bot protection).\n"
    "        Equivalent free sources:\n"
    "          - https://api.mdblist.com/lists/official/moviemeter/items"
    "  (mirrors this exact chart)\n"
    "          - https://api.themoviedb.org/3/movie/top_rated\n"
    "          - https://api.themoviedb.org/3/trending/movie/week"
)


class IMDb(object):
    def __init__(self, tmdb):
        self.tmdb = tmdb

    def _handle_request(self, url):
        r = requests.get(url, timeout=30)

        body = r.content or b''

        # Check for the challenge before the status code: IMDb serves it with
        # a 202, and "we were challenged" is a more useful diagnosis than
        # "unexpected status".
        if b'awsWafCookieDomainList' in body or b'challenge.js' in body:
            raise SourceListError(
                "IMDb served a bot-protection challenge (HTTP {}) instead of "
                "{}.\n        {}".format(r.status_code, url, ALTERNATIVES))

        if r.status_code != 200:
            raise SourceListError(
                "IMDb returned HTTP {} for {}.\n        {}".format(
                    r.status_code, url, ALTERNATIVES))

        tree = html.fromstring(body)

        titles = tree.xpath("//table[contains(@class, 'chart')]"
                            "//td[@class='titleColumn']/a/text()")
        years = tree.xpath("//table[contains(@class, 'chart')]"
                           "//td[@class='titleColumn']/span/text()")
        ids = tree.xpath("//table[contains(@class, 'chart')]"
                         "//td[@class='ratingColumn']/div//@data-titleid")

        if not ids:
            raise SourceListError(
                "Could not parse any titles out of {}. IMDb's markup has "
                "changed since this parser was written.\n        {}".format(
                    url, ALTERNATIVES))

        return ids, titles, years

    def add_movies(self, url, movie_list=None, movie_ids=None, max_age=0):
        if movie_list is None:
            movie_list = []
        if movie_ids is None:
            movie_ids = []
        max_date = add_years(max_age * -1)
        logs.info(u"Retrieving the IMDb list: {}".format(url))

        (imdb_ids, imdb_titles, imdb_years) = self._handle_request(url)
        for i, imdb_id in enumerate(imdb_ids):
            if imdb_id in movie_ids:
                continue

            tmdb_data = None
            if self.tmdb:
                tmdb_data = self.tmdb.get_tmdb_from_imdb(imdb_id, 'movie')

            if tmdb_data and tmdb_data.get('release_date'):
                date = datetime.datetime.strptime(tmdb_data['release_date'],
                                                  '%Y-%m-%d')
            elif i < len(imdb_years) and imdb_years[i]:
                date = datetime.datetime(
                    int(str(imdb_years[i]).strip("()")), 12, 31)
            else:
                date = datetime.datetime.now()

            if max_age != 0 and (max_date > date):
                continue

            movie_list.append({
                'id': imdb_id,
                'tmdb_id': str(tmdb_data['id']) if tmdb_data else None,
                'title': (tmdb_data['title'] if tmdb_data
                          else imdb_titles[i] if i < len(imdb_titles)
                          else imdb_id),
                'year': date.year,
            })
            movie_ids.append(imdb_id)
            if tmdb_data and tmdb_data.get('id'):
                movie_ids.append('tmdb' + str(tmdb_data['id']))

        return movie_list, movie_ids

    def add_shows(self, url, show_list=None, show_ids=None, max_age=0):
        if show_list is None:
            show_list = []
        if show_ids is None:
            show_ids = []
        curyear = datetime.datetime.now().year
        logs.info(u"Retrieving the IMDb list: {}".format(url))

        (imdb_ids, imdb_titles, imdb_years) = self._handle_request(url)
        for i, imdb_id in enumerate(imdb_ids):
            if imdb_id in show_ids:
                continue

            tmdb_data = None
            external = {}
            if self.tmdb:
                tmdb_data = self.tmdb.get_tmdb_from_imdb(imdb_id, 'tv')
                if tmdb_data and tmdb_data.get('id'):
                    external = self.tmdb.get_external_ids(tmdb_data['id'], 'tv')

            if tmdb_data and tmdb_data.get('first_air_date'):
                year = datetime.datetime.strptime(
                    tmdb_data['first_air_date'], '%Y-%m-%d').year
            elif i < len(imdb_years) and imdb_years[i]:
                year = int(str(imdb_years[i]).strip("()"))
            else:
                year = curyear

            if max_age != 0 and (curyear - (max_age - 1)) > year:
                continue

            tvdb_id = external.get('tvdb_id')
            show_list.append({
                'id': imdb_id,
                'tvdb_id': str(tvdb_id) if tvdb_id else None,
                'tmdb_id': str(tmdb_data['id']) if tmdb_data else None,
                'title': (tmdb_data['name'] if tmdb_data
                          else imdb_titles[i] if i < len(imdb_titles)
                          else imdb_id),
                'year': year,
            })
            show_ids.append(imdb_id)
            if tmdb_data and tmdb_data.get('id'):
                show_ids.append('tmdb' + str(tmdb_data['id']))
            if tvdb_id:
                show_ids.append('tvdb' + str(tvdb_id))

        return show_list, show_ids

    def add_items(self, item_type, url, item_list=None, item_ids=None,
                  max_age=0):
        if item_type == 'movie':
            return self.add_movies(url, movie_list=item_list,
                                   movie_ids=item_ids, max_age=max_age)
        elif item_type == 'tv':
            return self.add_shows(url, show_list=item_list,
                                  show_ids=item_ids, max_age=max_age)

CHARTS = {
    # chartTitles query
    'top_movies': {'query': 'chartTitles', 'chart_type': 'TOP_RATED_MOVIES',
                   'first': 250, 'media': 'movie'},
    'top_shows': {'query': 'chartTitles', 'chart_type': 'TOP_RATED_TV_SHOWS',
                  'first': 250, 'media': 'tv'},
    'popular_movies': {'query': 'chartTitles',
                       'chart_type': 'MOST_POPULAR_MOVIES',
                       'first': 100, 'media': 'movie'},
    'popular_shows': {'query': 'chartTitles',
                      'chart_type': 'MOST_POPULAR_TV_SHOWS',
                      'first': 100, 'media': 'tv'},
    'lowest_rated': {'query': 'chartTitles',
                     'chart_type': 'LOWEST_RATED_MOVIES',
                     'first': 100, 'media': 'movie'},
    'top_english': {'query': 'chartTitles',
                    'chart_type': 'TOP_RATED_ENGLISH_MOVIES',
                    'first': 250, 'media': 'movie'},
    'top_indian': {'query': 'chartTitles',
                   'chart_type': 'TOP_RATED_INDIAN_MOVIES',
                   'first': 250, 'media': 'movie'},
    'top_tamil': {'query': 'chartTitles',
                  'chart_type': 'TOP_RATED_TAMIL_MOVIES',
                  'first': 250, 'media': 'movie'},
    'top_telugu': {'query': 'chartTitles',
                   'chart_type': 'TOP_RATED_TELUGU_MOVIES',
                   'first': 250, 'media': 'movie'},
    'top_malayalam': {'query': 'chartTitles',
                      'chart_type': 'TOP_RATED_MALAYALAM_MOVIES',
                      'first': 250, 'media': 'movie'},
    # boxOfficeWeekendChart query
    'box_office': {'query': 'boxOffice', 'first': 50, 'media': 'movie'},
}

# The chart pages on imdb.com, so existing recipes keep working: the same
# chart is served as JSON here instead of unscrapeable HTML.
CHART_URL_ALIASES = {
    'top': 'top_movies',
    'toptv': 'top_shows',
    'moviemeter': 'popular_movies',
    'tvmeter': 'popular_shows',
    'bottom': 'lowest_rated',
    'boxoffice': 'box_office',
    'top-english-movies': 'top_english',
    'top-indian-movies': 'top_indian',
    'top-rated-indian-movies': 'top_indian',
    'top-rated-tamil-movies': 'top_tamil',
    'top-rated-telugu-movies': 'top_telugu',
    'top-rated-malayalam-movies': 'top_malayalam',
}


class IMDbChart(object):
    """IMDb charts via IMDb's GraphQL endpoint.

    The chart pages cannot be scraped (see IMDb above), but the charts
    themselves are served as JSON by the endpoint IMDb's own site uses. This
    is the same route Kometa takes for its IMDb chart defaults.

    Two things to be aware of. The endpoint is undocumented and identifies
    callers via an `x-imdb-client-name` header, so IMDb can change or close
    it off without notice -- hence the loud, specific errors below. And every
    response carries this disclaimer:

        Public, commercial, and/or non-private use of the IMDb data provided
        by this API is not allowed. For limited non-commercial use of IMDb
        data and the associated requirements see
        https://help.imdb.com/article/imdb/general-information/can-i-use-imdb-data-in-my-software/G5JTRESSHJBBHTGX

    MDBList mirrors several of these charts if you would rather not depend on
    it.

    Supported source_list_urls:

        imdb://chart/top_movies
        imdb://chart/top_indian?limit=100
        imdb://chart/top_shows            (with library_type: tv)
        https://www.imdb.com/chart/top/   (as copied from the site)
    """

    GRAPHQL_URL = "https://api.graphql.imdb.com/"

    def __init__(self, tmdb=None):
        self.tmdb = tmdb

    @classmethod
    def handles(cls, url):
        parsed = urlparse(url)
        if parsed.scheme == 'imdb':
            return True
        if parsed.netloc.lower().replace('www.', '') == 'imdb.com':
            return '/chart/' in parsed.path or parsed.path.rstrip('/').endswith('/chart')
        return False

    @classmethod
    def _chart_name(cls, url):
        parsed = urlparse(url)
        if parsed.scheme == 'imdb':
            segments = ("{}{}".format(parsed.netloc, parsed.path)).strip('/').split('/')
        else:
            segments = parsed.path.strip('/').split('/')
        segments = [s for s in segments if s and s != 'chart']
        if not segments:
            raise SourceListError(
                "No IMDb chart named in: {}\n        Available charts: "
                "{}".format(url, ', '.join(sorted(CHARTS))))
        name = segments[-1].lower()
        name = CHART_URL_ALIASES.get(name, name)
        if name not in CHARTS:
            raise SourceListError(
                "Unknown IMDb chart '{}'.\n        Available charts: "
                "{}".format(name, ', '.join(sorted(CHARTS))))
        return name

    def _query(self, chart, first):
        if chart['query'] == 'chartTitles':
            return ("{ chartTitles(chart: { chartType: %s }, first: %d) "
                    "{ edges { node { id titleText { text } "
                    "releaseYear { year } } } total } }"
                    % (chart['chart_type'], first))
        return ("{ boxOfficeWeekendChart(limit: %d) { entries { title "
                "{ id titleText { text } releaseYear { year } } } } }" % first)

    def _request(self, query):
        try:
            r = requests.post(
                self.GRAPHQL_URL,
                headers={'content-type': 'application/json',
                         'x-imdb-client-name': 'imdb-web-next'},
                json={'query': query},
                timeout=30)
        except requests.RequestException as e:
            raise SourceListError(
                "Could not reach the IMDb API: {}".format(e))

        if r.status_code != 200:
            raise SourceListError(
                "The IMDb API returned HTTP {}. This endpoint is "
                "undocumented and may have changed.\n        {}".format(
                    r.status_code, ALTERNATIVES))
        try:
            body = r.json()
        except ValueError:
            raise SourceListError(
                "The IMDb API returned a non-JSON response.\n        "
                "{}".format(ALTERNATIVES))

        if body.get('errors'):
            raise SourceListError(
                "The IMDb API reported an error: {}".format(
                    body['errors'][0].get('message', body['errors'][0])))
        return body.get('data') or {}

    def _nodes(self, chart, data):
        if chart['query'] == 'chartTitles':
            container = data.get('chartTitles') or {}
            return [edge.get('node') or {}
                    for edge in container.get('edges') or []]
        container = data.get('boxOfficeWeekendChart') or {}
        return [entry.get('title') or {}
                for entry in container.get('entries') or []]

    def add_items(self, item_type, url, item_list=None, item_ids=None,
                  max_age=0):
        if item_list is None:
            item_list = []
        if item_ids is None:
            item_ids = []

        name = self._chart_name(url)
        chart = CHARTS[name]

        if chart['media'] != item_type:
            available = sorted(k for k, v in CHARTS.items()
                               if v['media'] == item_type)
            raise SourceListError(
                "The IMDb chart '{}' holds {}s, but this recipe's "
                "library_type is '{}'.\n        {} charts: {}".format(
                    name, chart['media'], item_type, item_type,
                    ', '.join(available)))

        params = dict(parse_qsl(urlparse(url).query))
        limit = int(params.get('limit') or chart['first'])
        limit = max(1, min(limit, chart['first']))

        logs.info(u"Retrieving the IMDb chart: {} ({})".format(name, url))
        data = self._request(self._query(chart, limit))
        nodes = self._nodes(chart, data)

        if not nodes:
            raise SourceListError(
                "The IMDb chart '{}' came back empty.\n        {}".format(
                    name, ALTERNATIVES))

        max_date = add_years(max_age * -1) if max_age else None

        for node in nodes[:limit]:
            imdb_id = node.get('id')
            if not imdb_id or imdb_id in item_ids:
                continue

            title = (node.get('titleText') or {}).get('text')
            year = (node.get('releaseYear') or {}).get('year')

            if max_date and year and int(year) < max_date.year:
                continue

            tmdb_id = None
            tvdb_id = None
            if self.tmdb:
                found = self.tmdb.get_tmdb_from_imdb(imdb_id, item_type)
                if found and found.get('id'):
                    tmdb_id = str(found['id'])
                    if item_type == 'tv':
                        external = self.tmdb.get_external_ids(found['id'], 'tv')
                        if external.get('tvdb_id'):
                            tvdb_id = str(external['tvdb_id'])

            item = {
                'id': imdb_id,
                'tmdb_id': tmdb_id,
                'tvdb_id': tvdb_id,
                'title': title,
                'year': year,
            }
            item_list.append(item)
            item_ids.append(imdb_id)
            if tmdb_id:
                item_ids.append('tmdb' + tmdb_id)
            if tvdb_id:
                item_ids.append('tvdb' + tvdb_id)

        return item_list, item_ids
