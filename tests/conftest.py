"""Общие фикстуры: изолированные каталоги данных до импорта приложения."""
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="tdr-test-")
os.environ["DATA_DIR"] = os.path.join(_TMP, "data")
os.environ["PRESETS_DIR"] = os.path.join(_TMP, "data", "presets")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))