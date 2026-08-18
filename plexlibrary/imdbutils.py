# -*- coding: utf-8 -*-
"""IMDb chart scraping.

IMDb now sits behind AWS WAF bot protection: a plain request gets 403, and a
browser-shaped one gets a 202 carrying a JavaScript challenge instead of the
chart. There is no supported way to read the charts programmatically, so this
module's job is mostly to fail loudly and point somewhere that works.

Equivalent lists are available from MDBList (see mdblistutils) and TMDb (see
tmdbutils), both on free keys.
"""
import datetime

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
