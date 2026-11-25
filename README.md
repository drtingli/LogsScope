# LogScope

A lightweight local tool for analyzing log files, with flexible highlighting and filtering plus live monitoring.

LogScope is a desktop log viewer and tail tool written in Python with PySide6.

## Features

- Tail / follow tail, multi-tab, drag-and-drop open, recent files
- Regex search (previous / next / all) and per-panel find (Ctrl+F, F3)
- Filter view (F4 or Ctrl+Shift+F; regex, invert, spread context, tail filter, filter-to-new-tab)
- Line highlighting rules (whole line or matched words, custom colors)
- Bookmarks with tags and emoji markers
- Columnizers with auto-detection and user-defined regex rules
- UTF-8 / UTF-16 / GBK support, truncation/rotation detection
- Portable mode
- Multi-file time sync and merged views
- Detached windows and flexible layouts
- Adjustable fonts, colors, grid lines and row spacing

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e .[dev]
```

## Quick start

```bash
logscope path\to\app.log          # open a file
logscope                          # restore the previous session
logscope --portable app.log       # keep settings next to the program
logscope --version                # print the version
```

Tests and benchmarks:

```bash
pytest
python tools/bench.py --mb 500
python tools/logwriter.py demo.log --rate 20000 --seconds 60 --rotate-mb 5
```

## Building an executable

On Windows, run from CMD:

```cmd
powershell -ExecutionPolicy Bypass -File "packaging\build_exe.ps1"
```

Or from PowerShell:

```powershell
.\packaging\build_exe.ps1
```

On Linux/macOS:

```bash
./packaging/build_exe.sh
```

On Windows with WSL, run the same script inside WSL:

```bash
cd /mnt/d/Dev/my_projects/logscope
chmod +x packaging/build_exe.sh
./packaging/build_exe.sh
```

For better performance, copy the project into the WSL filesystem first, e.g. `~/logscope`.

The result is in `dist/LogScope/` (Windows: `LogScope.exe`).

## Project layout

```
src/logscope/core/   Qt-free logic (reader, search, filters, columnizers, settings, ...)
src/logscope/ui/     PySide6 user interface
plugins/             Example columnizer plugins
tests/               Unit tests
packaging/           Windows executable build files
tools/               Benchmark and demo log writer
```

## Contributing

Contributions are welcome. Please open an issue or pull request on GitHub.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.