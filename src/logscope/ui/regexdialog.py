"""Editor for user-defined regular-expression columnizers (one column per capture group)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core.timefields import DATE_TIME_LEVEL_MESSAGE, DATE_TIME_MESSAGE, SNIPPETS
from ..core.columnizers import (
    UNMATCHED, path_matches, regex_columnizer_from_spec, sanitize_regex_spec, split_file_patterns,
)

EXAMPLES = [
    ("Date | Time | Message (ISO 8601 and alike, optional [brackets])", DATE_TIME_MESSAGE),
    ("Date | Time | Level | Message", DATE_TIME_LEVEL_MESSAGE),
    ("Pipe separated: Timestamp | Level | Type | Thread | File:line # Text",
     r"^(?P<Timestamp>\S+)\s*\|\s*(?P<Level>\S+)\s*\|\s*(?P<Type>[^|]*?)\s*\|\s*(?P<Thread>[^|]*?)\s*\|"
     r"\s*(?P<Source>[^#|]*?)\s*#\s?(?P<Message>.*)$"),
    ("[Timestamp] followed by free text", r"^\[(?P<Timestamp>[^\]]+)\](?P<Message>.*)$"),
    ("Date Time Level Message", r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}[.,]?\d*)\s+(\w+)\s+(.*)$"),
    ("Named groups: time, thread, level, text", r"^(?<time>\S+ \S+) \[(?<thread>[^\]]*)\] (?<level>[A-Z]+)\s+(?<text>.*)$"),
    ("Apache / nginx access log", r'^(\S+) \S+ \S+ \[([^\]]+)\] "(\S+) (\S+)[^"]*" (\d{3}) (\S+)'),
    ("key=value pairs: user, action", r"user=(\w+).*?action=(\w+)"),
    ("Comma separated, 4 fields", r"^([^,]*),([^,]*),([^,]*),(.*)$"),
]
UNMATCHED_LABELS = {"last": "show the whole line in the last column", "first": "show the whole line in the first column",
                    "empty": "show nothing"}
PREVIEW_LINES = 40


class RegexColumnizerDialog(QDialog):
    def __init__(self, specs: list[dict], sample: list[str], parent=None, current_path: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Parsing rules - every capture group becomes a column")
        self._current_path = current_path
        self.resize(980, 640)
        self._sample = [s for s in sample if s.strip()][:PREVIEW_LINES]
        self._specs = [s for s in (sanitize_regex_spec(x) for x in specs) if s is not None]
        self._loading = False

        self.listw = QListWidget()
        self.listw.setMaximumWidth(230)
        add, dup, rem = QPushButton("New"), QPushButton("Duplicate"), QPushButton("Delete")
        up, down = QPushButton("Up"), QPushButton("Down")
        imp, exp = QPushButton("Import…"), QPushButton("Export…")
        add.clicked.connect(self._new)
        dup.clicked.connect(self._duplicate)
        rem.clicked.connect(self._delete)
        up.clicked.connect(lambda: self._move(-1))
        down.clicked.connect(lambda: self._move(1))
        imp.clicked.connect(self._import)
        exp.clicked.connect(self._export)
        up.setToolTip("Rules are checked from top to bottom: the first rule whose file pattern matches wins")
        left = QVBoxLayout()
        left.addWidget(QLabel("Rules (top = first checked)"))
        left.addWidget(self.listw, 1)
        for buttons in ((add, dup, rem), (up, down), (imp, exp)):
            row = QHBoxLayout()
            for b in buttons:
                row.addWidget(b)
            left.addLayout(row)

        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.name = QLineEdit()
        self.pattern = QLineEdit()
        self.pattern.setFont(mono)
        self.pattern.setPlaceholderText(r"e.g.  ^(\d{4}-\d\d-\d\d) (\S+) +(\w+) +(.*)$")
        self.files = QLineEdit()
        self.files.setPlaceholderText(r"e.g.  *.log; adaPTinsight*.txt; logs/app_*.log; re:/svc/.*\.log$")
        self.files.setToolTip("Files whose name matches one of these patterns (separated by ;) are parsed with this "
                              "rule automatically. Wildcards * and ?; a pattern with a folder part (logs/*.log) is "
                              "matched against the end of the full path; 're:' starts a regular expression on the "
                              "full path (folders are separated by /). Not case sensitive. Leave empty for a rule you pick by hand.")
        self.titles = QLineEdit()
        self.titles.setPlaceholderText("optional, comma separated: Date, Time, Level, Message")
        self.examples = QComboBox()
        self.examples.addItem("Insert an example…")
        for label, _pat in EXAMPLES:
            self.examples.addItem(label)
        self.examples.activated.connect(self._insert_example)
        self.snippets = QComboBox()
        self.snippets.addItem("Insert a predefined pattern at the cursor…")
        for label, _snip in SNIPPETS:
            self.snippets.addItem(label)
        self.snippets.activated.connect(self._insert_snippet)
        self.split_ts = QCheckBox("Show a Timestamp / DateTime column as two columns: Date and Time")
        self.ignore_case = QCheckBox("Ignore case")
        self.search = QCheckBox("Match anywhere in the line (not only from the start)")
        self.auto = QCheckBox("Use for automatic detection (when at least half of the first lines match)")
        self.unmatched = QComboBox()
        for key in UNMATCHED:
            self.unmatched.addItem(UNMATCHED_LABELS[key], key)

        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Applies to files", self.files)
        form.addRow("Pattern", self.pattern)
        form.addRow("", self.examples)
        form.addRow("", self.snippets)
        form.addRow("Column titles", self.titles)
        form.addRow("", self.split_ts)
        form.addRow("", self.ignore_case)
        form.addRow("", self.search)
        form.addRow("", self.auto)
        form.addRow("Lines that do not match", self.unmatched)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.preview = QTableWidget(0, 0)
        self.preview.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.preview.setFont(mono)
        hint = QLabel(
            "Python / .NET syntax: <code>(…)</code> is a column, <code>(?&lt;name&gt;…)</code> or "
            "<code>(?P&lt;name&gt;…)</code> names it. Text outside the groups is not shown - end the pattern "
            "with <code>(.*)</code> to keep the rest of the line. Lines without a match (e.g. stack-trace "
            "lines) are placed as chosen above.")
        hint.setWordWrap(True)
        right = QVBoxLayout()
        right.addLayout(form)
        right.addWidget(hint)
        right.addWidget(self.status)
        right.addWidget(QLabel("Preview (first lines of the current file)"))
        right.addWidget(self.preview, 1)
        self.editor = QWidget()
        self.editor.setLayout(right)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        body = QHBoxLayout()
        body.addLayout(left)
        body.addWidget(self.editor, 1)
        lay = QVBoxLayout(self)
        lay.addLayout(body, 1)
        lay.addWidget(box)

        for w in (self.name, self.pattern, self.titles, self.files):
            w.textChanged.connect(self._edited)
        for w in (self.split_ts, self.ignore_case, self.search, self.auto):
            w.toggled.connect(self._edited)
        self.unmatched.currentIndexChanged.connect(self._edited)
        self.listw.currentRowChanged.connect(self._select)
        self._refill(0)

    # ------------------------------------------------------------------ list
    def _refill(self, row: int) -> None:
        self.listw.blockSignals(True)
        self.listw.clear()
        for s in self._specs:
            self.listw.addItem(s["name"] or "(unnamed)")
        self.listw.blockSignals(False)
        if self._specs:
            self.listw.setCurrentRow(max(0, min(row, len(self._specs) - 1)))
            self._select(self.listw.currentRow())
        else:
            self._select(-1)

    def _new(self) -> None:
        names = {s["name"] for s in self._specs}
        n = 1
        while f"Regex {n}" in names:
            n += 1
        self._specs.append({"name": f"Regex {n}", "pattern": "", "ignore_case": False, "search": False,
                            "unmatched": "last", "names": [], "auto": False, "files": [],
                            "split_timestamp": True})
        self._refill(len(self._specs) - 1)
        self.pattern.setFocus()

    def _duplicate(self) -> None:
        r = self.listw.currentRow()
        if 0 <= r < len(self._specs):
            c = copy.deepcopy(self._specs[r])
            c["name"] += " copy"
            self._specs.insert(r + 1, c)
            self._refill(r + 1)

    def _delete(self) -> None:
        r = self.listw.currentRow()
        if 0 <= r < len(self._specs):
            del self._specs[r]
            self._refill(r)

    def _move(self, delta: int) -> None:
        r = self.listw.currentRow()
        t = r + delta
        if 0 <= r < len(self._specs) and 0 <= t < len(self._specs):
            self._specs[r], self._specs[t] = self._specs[t], self._specs[r]
            self._refill(t)

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export parsing rules", "logscope-rules.json", "JSON (*.json)")
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(self._specs, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Export", f"Could not write the file: {exc}")

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import parsing rules", "", "JSON (*.json);;All files (*)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Import", f"Could not read the file: {exc}")
            return
        if isinstance(data, dict):
            data = [data]
        added = 0
        names = {s["name"] for s in self._specs}
        for raw in data if isinstance(data, list) else []:
            spec = sanitize_regex_spec(raw)
            if spec is None:
                continue
            base, n = spec["name"], 1
            while spec["name"] in names:
                n += 1
                spec["name"] = f"{base} (imported{'' if n == 2 else ' ' + str(n - 1)})"
            names.add(spec["name"])
            self._specs.append(spec)
            added += 1
        if added:
            self._refill(len(self._specs) - 1)
        self.status.setText(f"{added} rule(s) imported.")

    def _select(self, row: int) -> None:
        self.editor.setEnabled(0 <= row < len(self._specs))
        self._loading = True
        try:
            if 0 <= row < len(self._specs):
                s = self._specs[row]
                self.name.setText(s["name"])
                self.pattern.setText(s["pattern"])
                self.titles.setText(", ".join(s["names"]))
                self.files.setText("; ".join(s["files"]))
                self.split_ts.setChecked(s["split_timestamp"])
                self.ignore_case.setChecked(s["ignore_case"])
                self.search.setChecked(s["search"])
                self.auto.setChecked(s["auto"])
                self.unmatched.setCurrentIndex(UNMATCHED.index(s["unmatched"]))
            else:
                for w in (self.name, self.pattern, self.titles, self.files):
                    w.clear()
        finally:
            self._loading = False
        self._update_preview()

    # ------------------------------------------------------------------ edits
    def _insert_example(self, index: int) -> None:
        if index > 0:
            self.pattern.setText(EXAMPLES[index - 1][1])
            if not self.name.text().strip() or self.name.text().startswith("Regex "):
                self.name.setText(EXAMPLES[index - 1][0])
        self.examples.setCurrentIndex(0)

    def _insert_snippet(self, index: int) -> None:
        if index > 0:
            self.pattern.insert(SNIPPETS[index - 1][1])
            self.pattern.setFocus()
        self.snippets.setCurrentIndex(0)

    def _edited(self, *_args) -> None:
        if self._loading:
            return
        r = self.listw.currentRow()
        if not 0 <= r < len(self._specs):
            return
        s = self._specs[r]
        s["name"] = self.name.text().strip()[:60]
        s["pattern"] = self.pattern.text()
        s["names"] = [t.strip() for t in self.titles.text().split(",")] if self.titles.text().strip() else []
        s["files"] = split_file_patterns(self.files.text())
        s["split_timestamp"] = self.split_ts.isChecked()
        s["ignore_case"] = self.ignore_case.isChecked()
        s["search"] = self.search.isChecked()
        s["auto"] = self.auto.isChecked()
        s["unmatched"] = self.unmatched.currentData() or "last"
        item = self.listw.item(r)
        if item is not None:
            item.setText(s["name"] or "(unnamed)")
        self._update_preview()

    def _update_preview(self) -> None:
        r = self.listw.currentRow()
        self.preview.setRowCount(0)
        self.preview.setColumnCount(0)
        if not 0 <= r < len(self._specs):
            self.status.setText("")
            return
        s = self._specs[r]
        if not s["pattern"]:
            self.status.setText("Enter a pattern.")
            return
        try:
            c = regex_columnizer_from_spec({**s, "name": s["name"] or "x"})
        except ValueError as exc:
            self.status.setText(f'<span style="color:#c0392b">{exc}</span>')
            return
        names = c.column_names()
        self.preview.setColumnCount(len(names))
        self.preview.setHorizontalHeaderLabels(names)
        hits = 0
        self.preview.setRowCount(len(self._sample))
        for i, line in enumerate(self._sample):
            if c._find(line):
                hits += 1
            for j, cell in enumerate(c.split(line)):
                self.preview.setItem(i, j, QTableWidgetItem(cell))
        self.preview.resizeColumnsToContents()
        if self._sample:
            msg = f"{len(names)} column(s); {hits} of {len(self._sample)} preview lines match."
        else:
            msg = f"{len(names)} column(s). Open a file to see a preview."
        if s["files"] and self._current_path:
            ok = path_matches(s["files"], self._current_path)
            msg += (f" The current file {'MATCHES' if ok else 'does not match'} the file patterns"
                    f" of this rule.")
        elif not s["files"]:
            msg += " No file patterns: pick this rule by hand (View > Columnizer)."
        self.status.setText(msg)

    # ------------------------------------------------------------------ result
    def _accept(self) -> None:
        for i, s in enumerate(self._specs):
            try:
                if not s["name"]:
                    raise ValueError("the name is empty")
                regex_columnizer_from_spec(s)
            except ValueError as exc:
                self.listw.setCurrentRow(i)
                QMessageBox.warning(self, "Regex columnizers", f"{s['name'] or '(unnamed)'}: {exc}")
                return
        self.accept()

    def specs(self) -> list[dict]:
        return [copy.deepcopy(s) for s in self._specs]
