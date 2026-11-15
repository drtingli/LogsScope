"""Example columnizer plugin.

Copy files like this one into  <settings dir>/plugins/  (Windows:
%APPDATA%\\LogScope\\plugins, portable mode: LogScopeData/plugins) or pass
``--plugins <folder>``.  The file only has to define ``register(registry)``.
"""

import re

from logscope.core.columnizers import Columnizer

_PAIR = re.compile(r'(\w+)=("[^"]*"|\S+)')


class KeyValueColumnizer(Columnizer):
    """Lines like:  ts=... level=info msg="hello"  ->  one column per key."""

    name = "key=value"
    KEYS = ["ts", "level", "msg"]          # edit to taste

    def column_names(self):
        return self.KEYS + ["other"]

    def split(self, line):
        found = {k: v.strip('"') for k, v in _PAIR.findall(line)}
        cells = [found.pop(k, "") for k in self.KEYS]
        cells.append(" ".join(f"{k}={v}" for k, v in found.items()))
        return cells

    def score(self, sample):
        sample = [s for s in sample if s.strip()]
        if not sample:
            return 0.0
        return 0.9 * sum(1 for s in sample if len(_PAIR.findall(s)) >= 3) / len(sample)


def register(registry):
    registry.register(KeyValueColumnizer())
