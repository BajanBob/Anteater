"""PyInstaller hook for SoapySDR (module name ``SoapySDR``).

SoapySDR comes from OS packages, so it is only bundled when it is importable
on the build machine. Its device drivers are plug-in shared libraries in
``<prefix>/lib/SoapySDR/modules<ABI>/``; they are copied to ``SoapySDR/modules``
inside the bundle, and rthook-soapy.py points SOAPY_SDR_PLUGIN_PATH at them.

PyInstaller looks hooks up by module name, so hook-SoapySDR.py runs this file.
"""

from __future__ import annotations

import glob
import os

from PyInstaller.utils.hooks import collect_dynamic_libs

binaries = collect_dynamic_libs("SoapySDR")
hiddenimports = ["_SoapySDR"]

try:
    import SoapySDR  # noqa: F401

    root = SoapySDR.getRootPath()
    for lib_dir in ("lib", "lib64", os.path.join("lib", "x86_64-linux-gnu"), os.path.join("lib", "aarch64-linux-gnu")):
        for module in glob.glob(os.path.join(root, lib_dir, "SoapySDR", "modules*", "*")):
            if module.endswith((".so", ".dylib", ".dll")):
                binaries.append((module, "SoapySDR/modules"))
except Exception:  # not installed at build time: the executable runs without SDR support
    pass
