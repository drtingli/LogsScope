# Contributing

Thank you for your interest in contributing to LogScope.

## Development setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e .[dev]
```

## Running tests

```bash
pytest
# or
python -m unittest discover -s tests
```

## Guidelines

- Keep `core/` Qt-free and testable without a window.
- Run the tests relevant to your change before submitting a pull request.
- Use `tempfile` and `pathlib` for file handling; avoid hard-coded paths.
- Do not include personal paths, machine names or secrets in code or tests.