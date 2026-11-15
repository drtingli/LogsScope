"""State of the little activity LED in front of a tab's file name (Qt-free, so it can be tested).

* The LED is lit only while the tab *follows the tail*; otherwise it shows a grey, switched-off LED.
* A burst of new lines makes it flash: ``flash()`` sets the brightness to 1.0 and ``tick()`` (called by a
  timer) lets it fade.  A flash that arrives while the LED is still bright is ignored, so a steady stream of
  lines gives a steady blink (about 8 times a second) instead of a LED that is permanently on.
"""

from __future__ import annotations

from .theme import blend

TICK_MS = 30
DECAY = 0.12                  # brightness lost per tick: from full to off in about a quarter of a second
RETRIGGER_BELOW = 0.55        # a new flash is accepted once the LED has faded below this

ON_COLOR = "#4ade80"          # bright green
IDLE_COLOR = "#1d5a34"        # following, nothing new: dark green
OFF_COLOR = "#9aa0a6"         # not following: grey


class LedState:
    def __init__(self, following: bool = False) -> None:
        self.following = bool(following)
        self.level = 0.0

    def set_following(self, on: bool) -> None:
        self.following = bool(on)
        if not self.following:
            self.level = 0.0

    def flash(self) -> bool:
        """New lines arrived.  True when the LED must be redrawn / the fade timer started."""
        if not self.following or self.level >= RETRIGGER_BELOW:
            return False
        self.level = 1.0
        return True

    def tick(self) -> bool:
        """One step of the fade.  True while the LED still has to be animated."""
        if self.level <= 0.0:
            return False
        self.level = max(0.0, self.level - DECAY)
        return self.level > 0.0

    @property
    def animating(self) -> bool:
        return self.level > 0.0

    def color(self) -> str:
        """Fill colour for the present state, '#rrggbb'."""
        if not self.following:
            return OFF_COLOR
        return blend(ON_COLOR, IDLE_COLOR, max(0.0, min(1.0, self.level)))

    def glow(self) -> float:
        """0..1 strength of the halo around the LED."""
        return self.level if self.following else 0.0
