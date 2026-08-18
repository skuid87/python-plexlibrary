# -*- coding: utf-8 -*-
import os
from datetime import datetime

import ruamel.yaml


def resolve_cache_path(path, default):
    """Expand ~ and env vars in a cache path and make sure its parent exists.

    /tmp gets cleared on reboot, so the defaults live under ~/.cache now; that
    only works if the directory is actually created.
    """
    path = os.path.expandvars(os.path.expanduser(path or default))
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent)
        except OSError:
            pass
    return path


class SourceListError(Exception):
    """Raised when a source list cannot be retrieved or comes back empty.

    Always fatal: continuing with an empty item list makes every item in the
    destination library look unmatched, which the cleanup phase would then
    happily unlink.
    """


class Colors(object):
    RED = u'\033[1;31m'
    BLUE = u'\033[1;34m'
    CYAN = u'\033[1;36m'
    GREEN = u'\033[0;32m'
    RESET = u'\033[0;0m'
    BOLD = u'\033[;1m'
    REVERSE = u'\033[;7m'


class YAMLBase(object):
    def __init__(self, filename):
        self.filename = filename

        yaml = ruamel.yaml.YAML()
        yaml.preserve_quotes = True
        with open(self.filename, 'r') as f:
            try:
                self.data = yaml.load(f)
            except ruamel.yaml.YAMLError as e:
                raise e

    def __getitem__(self, k):
        return self.data[k]

    def __iter__(self, k):
        return self.data.itervalues()

    def __setitem__(self, k, v):
        self.data[k] = v

    def get(self, k, default=None):
        if k in self.data:
            return self.data[k]
        else:
            return default

    def save(self):
        yaml = ruamel.yaml.YAML()
        with open(self.filename, 'w') as f:
            yaml.dump(self.data, f)


def add_years(years, from_date=None):
    if from_date is None:
        from_date = datetime.now()
    try:
        return from_date.replace(year=from_date.year + years)
    except ValueError:
        # Must be 2/29!
        return from_date.replace(month=2, day=28,
                                 year=from_date.year + years)
