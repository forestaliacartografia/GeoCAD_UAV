"""
The rule every outbound request in this plugin obeys.

``urlopen`` is not an HTTP client: it honours ``file:``, ``ftp:`` and whatever
else is registered as a handler. That matters here because the addresses are
not all written by this plugin -- a DEM adapter carries a URL template the
operator can edit in the settings, and the cadastral endpoint is configurable
too. A template beginning with ``file://`` would make the download step read
the operator's own disk and hand the bytes back as if they had come from the
network.

One function, checked at the two places that open a socket, so neither
transport can be talked into a scheme nobody intended.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from ..core.errors import UnsafeUrlError

#: The only schemes this plugin will open.
ALLOWED_SCHEMES = ("http", "https")


def require_web_url(url) -> str:
    """Return the URL unchanged, or refuse it.

    Raises :class:`UnsafeUrlError` -- a typed error carrying an Italian
    message -- so a mistyped endpoint in the settings reaches the operator as
    a sentence rather than as a traceback or, worse, as a silent local read.
    """
    text = str(url or "")
    scheme = urlsplit(text).scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(
            "refused URL scheme {0!r}".format(scheme or "<none>"),
            user_message="L'indirizzo configurato non usa http o https.",
            hint="Correggi l'indirizzo del servizio nelle impostazioni.")
    return text
