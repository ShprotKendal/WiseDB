# Contributing to WiseDB

## Setup

```bash
git clone https://github.com/ShprotKendal/WiseDB.git
cd WiseDB
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,server]"
```

Or run `./bootstrap.sh`.

## Build the C extension

```bash
pip install -e .
# or
python setup.py build_ext --inplace
```

## C / C++ core

```bash
cmake -B build
cmake --build build
ctest --test-dir build --output-on-failure
```

The v3 filesystem engine currently supports POSIX roots with private ownership. Only one writable process may open a root. Use a fresh temporary data root per C/Python test; close `Wiki`/`wisedb_engine` before reopening. Direct C smoke compilation should list `src/c/json.c` and the core sources explicitly; do not glob the standalone WAL/SSTable prototypes into the public v3 engine.

## Tests

```bash
pytest
```

C smoke test only:

```bash
cc -std=c11 -Wall -Wextra -Wpedantic -Werror -Iinclude -Isrc/c \
  src/c/json.c src/c/wisedb.c src/c/store.c src/c/format.c \
  src/c/index.c src/c/retrieve.c tests/c/test_core.c -lm \
  -o /tmp/wisedb_test_c && /tmp/wisedb_test_c
```

## Optional server

```bash
export WISEDB_DATA_DIR=./data
wisedb-server
```

## Pull requests

- Keep changes focused.
- Run `pytest` before opening a PR.
- Update README or `docs/AGENT_WISEDB.md` when changing public API.
