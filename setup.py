"""Build WiseDB Cython extension and package."""

from __future__ import annotations

import sys
from pathlib import Path

from Cython.Build import cythonize
from setuptools import Extension, find_packages, setup

ROOT = Path(__file__).parent

extra_link = ["m"] if sys.platform != "win32" else []

sources = [
    "src/wisedb/_engine.pyx",
    "src/c/json.c",
    "src/c/wisedb.c",
    "src/c/store.c",
    "src/c/format.c",
    "src/c/index.c",
    "src/c/settings.c",
    "src/c/retrieve.c",
    "src/c/catalog_filter.c",
]

ext = Extension(
    "wisedb._engine",
    sources=sources,
    include_dirs=["include", "src/c"],
    libraries=extra_link,
)

setup(
    name="wisedb",
    version="3.0.0",
    package_dir={"": "src"},
    packages=find_packages("src"),
    package_data={
        "wisedb.system_wiki": ["*.md", "wql/*.md"],
    },
    include_package_data=True,
    ext_modules=cythonize(
        ext,
        compiler_directives={"language_level": "3"},
    ),
    python_requires=">=3.10",
)
