"""Runtime hook: use the SoapySDR driver modules bundled by hook-soapy.py."""

import os
import sys

_bundle = getattr(sys, "_MEIPASS", None)
if _bundle:
    _modules = os.path.join(_bundle, "SoapySDR", "modules")
    if os.path.isdir(_modules):
        os.environ.setdefault("SOAPY_SDR_PLUGIN_PATH", _modules)
