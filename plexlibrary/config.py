# -*- coding: utf-8 -*-
import os

from utils import YAMLBase


class ConfigParser(YAMLBase):
    def __init__(self, filepath=None):
        if not filepath:
            # FIXME?
            parent_dir = (os.path.abspath(os.path.join(
                os.path.dirname(__file__), os.path.pardir)))
            filepath = os.path.join(parent_dir, 'config.yml')

        super(ConfigParser, self).__init__(filepath)

    def validate(self):
        if not self.get('plex'):
            raise Exception("Missing 'plex' in config")
        else:
            if 'baseurl' not in self['plex']:
                raise Exception("Missing 'baseurl' in 'plex'")
            if 'token' not in self['plex']:
                raise Exception("Missing 'token' in 'plex'")

        # Every source is optional now: Trakt has restricted new API keys to
        # VIP accounts, so a config with only tmdb and/or mdblist is normal.
        # Which credentials a run actually needs depends on the recipe's
        # source_list_urls, and is checked when the sources are resolved.
        if not any((
                (self.get('tmdb') or {}).get('api_key'),
                (self.get('mdblist') or {}).get('api_key'),
                (self.get('trakt') or {}).get('username'),
        )):
            raise Exception(
                "No list source is configured. Set at least one of "
                "'tmdb: api_key', 'mdblist: api_key' or 'trakt: username' "
                "in config.yml")

        return True
