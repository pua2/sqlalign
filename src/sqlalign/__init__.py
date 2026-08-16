"""sqlalign -- a SQL formatter for the house columnar-alignment style."""
from importlib.metadata import PackageNotFoundError, version

# Read from the installed distribution rather than written here: the literal this
# replaces still said "0.1.0" at release 1.1.0, because nothing forces the two to
# be edited together. pyproject.toml is now the single place a release bumps.
try:
    __version__ = version("sqlalign")
except PackageNotFoundError:            # a source tree that was never installed
    __version__ = "0.0.0+unknown"
