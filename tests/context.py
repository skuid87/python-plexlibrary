"""Put the plexlibrary modules on sys.path.

The package uses flat, top-level imports (`import logs`, `from utils import
...`) which only resolve when its own directory is on sys.path -- which is
how it is run (`python3 plexlibrary`). Tests need the same arrangement.
"""
import os
import sys

PACKAGE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'plexlibrary')

if PACKAGE_DIR not in sys.path:
    sys.path.insert(0, PACKAGE_DIR)
