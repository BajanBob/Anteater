"""PyInstaller finds hooks by module name; the real hook is hook-soapy.py next to this file."""

from pathlib import Path

exec(compile(Path(__file__).with_name("hook-soapy.py").read_text(), "hook-soapy.py", "exec"))
