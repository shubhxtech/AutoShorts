"""AutoShorts: automated short-video studio."""

import os as _os

__version__ = "0.3.0"

# The project used to be called ClipForge: keep old CLIPFORGE_* environment variables working.
for _k, _v in list(_os.environ.items()):
    if _k.startswith("CLIPFORGE_") and "AUTOSHORTS_" + _k[10:] not in _os.environ:
        _os.environ["AUTOSHORTS_" + _k[10:]] = _v
