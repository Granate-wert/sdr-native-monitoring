# Contributing

## Before making changes

1. Read [README.md](README.md) and [ARCHITECTURE.md](ARCHITECTURE.md).
2. Inspect `git status` and preserve unrelated work.
3. Define a bounded change and its acceptance tests.
4. Treat all external DFL and RF recordings as read-only.

Local agent instructions and internal design/review documents may exist in an
ignored `docs/` directory. They are not part of the public source contract.

## Engineering rules

- Keep the common C++ core independent from Python, Qt, WinAPI, device SDKs and
  CUDA.
- Do not call Python per sample or per FFT.
- Keep all queues, caches and export buffers bounded.
- Make loss, gaps and overflow observable.
- Preserve frequency/time axes, units, sequence, configuration generation and
  source provenance.
- Keep CPU as a complete reference path.
- Do not emit calibrated physical power without an applicable profile.
- Keep long operations cancellable.
- Use `.part` files and atomic completion for large exports.
- Do not route live I/Q through the DFL parser.
- Do not move raw I/Q ownership into the GUI thread.

## Scope and commits

Prefer one coherent architectural or implementation package per commit. A
commit should include:

- production code for the bounded scope;
- focused unit or contract tests;
- updated public README/architecture text when behavior changes;
- explicit limitations for unverified devices or platforms.

Do not claim a backend, platform or receiver is supported only because an
interface, option or scaffold exists.

## Verification

Run relevant Python tests:

```powershell
py -m unittest discover -s tests -v
```

Run static checks:

```powershell
ruff check esw_dfl tests
mypy --python-version 3.11 esw_dfl/sdr/contracts.py esw_dfl/sdr/native_api.py esw_dfl/sdr/reference_dsp.py esw_dfl/sdr/synthetic.py esw_dfl/domain.py
```

For native changes, configure/build the affected preset and run CTest:

```powershell
.\build_native_sdr.ps1 -Clean -PythonExecutable ".\.venv\Scripts\python.exe"
```

GUI changes additionally require manual checks at 100% and at least 200% DPI.
Parser changes require read-only smoke checks against every locally available
reference input.

## Staged-content audit

Before committing:

```powershell
git diff --check
git status --short
git diff --cached --stat
```

Do not commit:

- DFL files, raw I/Q, captures or derived measurement exports;
- user paths, screenshots, machine inventories or activity logs;
- credentials, access tokens, private keys or service configuration;
- generated executables, libraries, symbols, CMake output, Rust `target/`,
  caches or `.part` files;
- internal agent instructions or local review/report material.

Small deterministic synthetic golden fixtures are allowed when their generator
and schema are included and their contents contain no captured measurements.

## Pull requests

Describe:

- the bounded scope;
- contract or architecture effects;
- exact verification commands and results;
- supported and unverified targets;
- known limitations;
- whether cancellation, overflow and loss behavior changed.
