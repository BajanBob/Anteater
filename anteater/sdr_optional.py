"""SoapySDR is optional and comes from OS packages, not pip. Import it through here."""

from __future__ import annotations

from types import ModuleType
from typing import Any

SoapySDR: Any
try:  # pragma: no cover - depends on the host
    import SoapySDR as _soapy

    SoapySDR = _soapy
    HAVE_SOAPY = True
except Exception:  # ImportError, or a broken native library
    SoapySDR = None
    HAVE_SOAPY = False

INSTALL_HINT = (
    "SoapySDR is not installed. It is not on PyPI; install it from your OS packages, "
    "for example: apt install python3-soapysdr soapysdr-module-rtlsdr "
    "(macOS: brew install soapysdr soapyrtlsdr)."
)


class SdrUnavailable(RuntimeError):
    """Raised when an SDR feature is used without SoapySDR."""


def require_soapy() -> ModuleType:
    if not HAVE_SOAPY:
        raise SdrUnavailable(INSTALL_HINT)
    module: ModuleType = SoapySDR
    return module
