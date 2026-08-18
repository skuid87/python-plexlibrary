Python-PlexLibrary
==================

Python command line utility for creating and maintaining dynamic Plex
libraries and playlists based on "recipes".

E.g. Create a library or playlist consisting of all movies or tv shows on an
IMDb chart or in a curated list that exist in your main library, and set the
sort titles accordingly (sort only available for libraries).

Disclaimer
----------
This is still a work in progress, so major changes may occur in new versions.

Source status
-------------

The list sources this project can read have changed considerably:

* **MDBList** *(recommended)* — free API key, 1000 requests/day. Aggregates
  Trakt, IMDb, TMDb, Letterboxd and Rotten Tomatoes lists, and its items
  already carry IMDb, TMDb and TheTVDB ids. Get a key from
  https://mdblist.com/preferences

* **TMDb** *(recommended)* — free for non-commercial use, and already required
  for weighted sorting, so it needs no extra credentials. Covers
  ``trending``, ``popular``, ``top_rated``, ``discover`` and public lists.

* **Trakt** *(legacy, optional)* — Trakt now restricts creating API
  applications to VIP accounts, and some existing keys have been revoked.
  Still supported if you already hold a working ``client_id``, otherwise use
  MDBList, which mirrors most Trakt lists.

* **IMDb charts** — the chart *pages* cannot be scraped (IMDb serves scripted
  requests a bot-protection challenge), but the charts themselves are served
  as JSON by the GraphQL endpoint IMDb's own site uses, which is the route
  Kometa takes for its IMDb defaults. No API key needed. That endpoint is
  undocumented and subject to IMDb's data-use terms, which its responses
  state as: *"Public, commercial, and/or non-private use of the IMDb data
  provided by this API is not allowed. For limited non-commercial use of IMDb
  data and the associated requirements see* `help.imdb.com
  <https://help.imdb.com/article/imdb/general-information/can-i-use-imdb-data-in-my-software/G5JTRESSHJBBHTGX>`_ *"*.
  MDBList mirrors several of the same charts if you would rather not depend
  on it. Non-chart imdb.com URLs still fail with an explicit error rather
  than quietly building an empty library.

* **TheTVDB** *(removed)* — the v3 API was shut down and v4 is paid. TheTVDB
  ids are now resolved through TMDb instead.

Requirements
------------

* Python 3

* The Movie Database API key (recommended)

  * https://developer.themoviedb.org/docs

  * Required for weighted sorting, and for matching library items whose Plex
    agent doesn't supply an IMDb/TheTVDB id directly

  * Also usable as a list source

* MDBList API key (recommended)

  * https://mdblist.com/preferences

  * Required for any ``mdblist`` source list

* (optional) An existing trakt.tv ``client_id``, if you have one

Getting started
---------------

1. Clone or download this repo.

2. Install Python and pip if you haven't already.

3. Install the requirements:

   .. code-block:: shell

       pip install -r requirements.txt

4. Copy config-template.yml to config.yml and edit it with your information.

  * Here's a guide if you're unfamiliar with YAML syntax. **Most notably you need to use spaces instead of tabs!** http://docs.ansible.com/ansible/latest/YAMLSyntax.html

5. Check out the recipe examples under recipes/examples. Copy an example to recipes/ and edit it with the appropriate information.

Source list URLs
----------------

``source_list_urls`` accepts any mix of the following. ``limit`` is the number
of items to take from that list; every other query parameter is passed through
to the underlying API.

MDBList::

    https://api.mdblist.com/lists/official/moviemeter/items?limit=100
    https://api.mdblist.com/lists/{user}/{listname}/items
    https://mdblist.com/lists/{user}/{listname}          # as copied from the site
    https://api.mdblist.com/external/lists/{id}/items    # an imported IMDb/Letterboxd list
    https://api.mdblist.com/catalog/movie?genre=horror&sort=imdbrating

``limit`` is the total number of items to collect. ``page_size`` caps how many
items each request asks for (max 1000) and exists mainly to exercise cursor
pagination, since most lists fit in a single page.

IMDb charts::

    imdb://chart/top_movies              # IMDb Top 250
    imdb://chart/top_indian?limit=100    # also top_english/tamil/telugu/malayalam
    imdb://chart/popular_movies          # IMDb MovieMeter
    imdb://chart/box_office              # weekend box office
    imdb://chart/top_shows               # with library_type: tv
    https://www.imdb.com/chart/top/      # website URLs map to the same charts

TMDb::

    https://api.themoviedb.org/3/trending/movie/week?limit=100
    https://api.themoviedb.org/3/movie/top_rated
    https://api.themoviedb.org/3/tv/popular
    https://api.themoviedb.org/3/discover/movie?sort_by=vote_average.desc&vote_count.gte=500
    https://api.themoviedb.org/3/list/{id}               # a public TMDb list

Trakt (only if you already hold a client_id)::

    https://api.trakt.tv/movies/trending?limit=50

Checking a source without a Plex server
---------------------------------------

The list sources are independent of Plex, so you can verify credentials and
inspect what a list actually returns before wiring it into a recipe:

.. code-block:: shell

    python3 plexlibrary --check-source 'https://api.mdblist.com/lists/adamosborne01/imdb-top-250/items?limit=250'
    python3 plexlibrary --check-source 'https://api.mdblist.com/lists/official/moviemeter/items' --type tv
    python3 plexlibrary --check-source 'https://api.themoviedb.org/3/trending/movie/week?limit=20'

This only talks to the list API. No Plex server is contacted, nothing is
written and no symlinks are touched. It prints the first few items with their
IMDb/TMDb/TheTVDB ids and reports id coverage, since those ids are what Plex
matching depends on. It exits non-zero if the list is empty or if any item
carries no usable id.

Usage
-----
In the base directory, run:

.. code-block:: shell

    python3 plexlibrary -h

for details on how to use the utility.

.. code-block:: shell

    python3 plexlibrary -l

lists available recipes.

To run a recipe named "movies_trending", run:

.. code-block:: shell

    python3 plexlibrary movies_trending

**(If you're on Windows, you might have to run as admin)**

When you're happy with the results, automate the recipe in cron_ or equivalent (automated tasks in Windows https://technet.microsoft.com/en-us/library/cc748993(v=ws.11).aspx).

.. _cron: https://code.tutsplus.com/tutorials/scheduling-tasks-with-cron-jobs--net-8800

**Pro tip!** Edit the new library and uncheck *"Include in dashboard"*. Othewise if you start watching something that exists in multiple libraries, all items will show up on the On Deck. This makes it so that only the item in your main library shows up.

Tests
-----

The test suite uses only the standard library, so it needs no extra
dependencies -- but it does import the project's modules, so run it inside the
virtualenv where the requirements are installed:

.. code-block:: shell

    python3 -m unittest discover -s tests -t .

It covers source dispatch, URL parsing, item normalisation, cursor pagination,
error mapping, the empty-list guard and stale symlink handling. Nothing in it
touches the network or a Plex server, so it is safe to run anywhere.

A note on removals
------------------

Items that are no longer in the source lists get their symlinks removed (when
``remove_from_library`` is set). That means a source returning nothing would
otherwise look like "everything is stale". The recipe now aborts if no source
list returns any items, so a broken or misconfigured source can't empty a
library.

Planned features
----------------
See issues.

Credit
------
Original functionality is based on https://gist.github.com/JonnyWong16/b1aa2c0f604ed92b9b3afaa6db18e5fd
