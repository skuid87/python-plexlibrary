#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Automated Plex library utility

This utility creates or maintains a Plex library
based on a configuration recipe.

Disclaimer:
    Use at your own risk! I am not responsible
    for damages to your Plex server or libraries.

Credit:
    Originally based on
    https://gist.github.com/JonnyWong16/f5b9af386ea58e19bf18c09f2681df23
    by /u/SwiftPanda16
"""

import argparse
import sys

import recipes
from config import ConfigParser
from recipe import Recipe, build_sources, resolve_source
from utils import SourceListError


def die(message):
    """Report a user-facing failure without a traceback."""
    sys.stdout.flush()  # keep the error below whatever we already printed
    sys.stderr.write("Error: {}\n".format(message))
    sys.exit(1)


def load_config(config_file):
    try:
        return ConfigParser(config_file)
    except IOError as e:
        die("could not read the config file: {}\n"
            "       Copy config-template.yml to config.yml, or pass "
            "--config PATH.".format(e))


def list_recipes(directory=None):
    print("Available recipes:")
    for name in recipes.get_recipes(directory):
        print("    {}".format(name))


def check_source(url, library_type='movie', max_age=0, config_file=None):
    """Fetch a single source list and report what came back.

    Talks only to the list API -- no Plex server is contacted, nothing is
    written, and no symlinks are touched. Use it to verify credentials and
    see the ids a list actually provides before wiring it into a recipe.
    """
    config = load_config(config_file)
    sources = build_sources(config)
    try:
        source = resolve_source(url, sources)
    except SourceListError as e:
        die(str(e))

    print("Source   : {}".format(type(source).__name__))
    print("URL      : {}".format(url))
    print("Type     : {}{}".format(
        library_type, "  (max_age {})".format(max_age) if max_age else ""))
    print("")

    try:
        items, _ = source.add_items(library_type, url, [], [], max_age)
    except SourceListError as e:
        die(str(e))

    if not items:
        print("NO ITEMS RETURNED.")
        print("A recipe using this URL would abort rather than treat every "
              "item in\nthe destination library as stale.")
        return 1

    counts = {'imdb': 0, 'tmdb': 0, 'tvdb': 0, 'none': 0}
    for item in items:
        has = False
        for key, field in (('imdb', 'id'), ('tmdb', 'tmdb_id'),
                           ('tvdb', 'tvdb_id')):
            if item.get(field):
                counts[key] += 1
                has = True
        if not has:
            counts['none'] += 1

    print("{} items returned.\n".format(len(items)))
    print("  {:<4} {:<45} {:<6} {:<11} {:<9} {}".format(
        "#", "title", "year", "imdb", "tmdb", "tvdb"))
    print("  " + "-" * 88)
    for i, item in enumerate(items[:10], 1):
        title = (item.get('title') or '')[:44]
        print("  {:<4} {:<45} {:<6} {:<11} {:<9} {}".format(
            i, title, str(item.get('year') or '-'),
            str(item.get('id') or '-'), str(item.get('tmdb_id') or '-'),
            str(item.get('tvdb_id') or '-')))
    if len(items) > 10:
        print("  ... and {} more".format(len(items) - 10))

    total = len(items)
    print("\nid coverage (these are what Plex matching relies on):")
    for key in ('imdb', 'tmdb', 'tvdb'):
        print("  {:<5}: {:>4}/{:<4} ({:.0f}%)".format(
            key, counts[key], total, 100.0 * counts[key] / total))

    if counts['none']:
        print("\nWARNING: {} item(s) carry no usable id and could never "
              "match.".format(counts['none']))
        return 1

    print("\nOK: every item carries at least one id Plex can be matched on.")
    return 0


def main():
    parser = argparse.ArgumentParser(
        prog='plexlibrary',
        description=("This utility creates or maintains a Plex library "
                     "based on a configuration recipe."),
        usage='%(prog)s [options] [<recipe>]',
    )
    parser.add_argument('recipe', nargs='?',
                        help='Create a library using this recipe')
    parser.add_argument(
        '-l', '--list-recipes', action='store_true',
        help='list available recipes')
    parser.add_argument(
        '-s', '--sort-only', action='store_true', help='only sort the library')
    parser.add_argument(
        '-p', '--playlists', action='store_true', help='make playlists rather than libraries'
    )
    parser.add_argument(
        '-e', '--everyone', action='store_true', help='share playlist with all users (overrides settings in recipe)'
    )
    parser.add_argument(
        '--check-source', metavar='URL',
        help='fetch a single source list and report what came back, without '
             'contacting Plex or changing anything')
    parser.add_argument(
        '--type', choices=['movie', 'tv'], default='movie',
        help="library type to use with --check-source (default: movie)")
    parser.add_argument(
        '--max-age', type=int, default=0, metavar='YEARS',
        help='apply a max_age filter when using --check-source')
    parser.add_argument(
        '-c', '--config', metavar='PATH',
        help='path to the config file (default: config.yml in the base '
             'directory)')

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    args = parser.parse_args()
    if args.list_recipes:
        list_recipes()
        sys.exit(0)

    if args.check_source:
        sys.exit(check_source(args.check_source, library_type=args.type,
                              max_age=args.max_age,
                              config_file=args.config))

    if args.recipe not in recipes.get_recipes():
        print("Error: No such recipe")
        list_recipes()
        sys.exit(1)

    try:
        r = Recipe(recipe_name=args.recipe, use_playlists=args.playlists,
                   config_file=args.config)
        r.run(sort_only=args.sort_only, share_playlist_to_all=args.everyone)
    except SourceListError as e:
        die(str(e))

    print("Done!")


if __name__ == "__main__":
    main()
