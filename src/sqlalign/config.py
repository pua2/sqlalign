import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Width:
    width: int = 100
    grace: int = 5
    floor: int = 60

    # `width = 0` means OFF: never break for length. It is a sentinel rather
    # than a separate boolean because the two are the same decision: a team
    # that does not wrap has no width to state, and because published guides
    # disagree so hard on the number (50/80/88/100/120) that "none of the above"
    # is a real position. GitLab's is `off`.
    OFF = 0

    def limit(self, anchor: int) -> int:
        """Effective break threshold for a construct anchored at column `anchor`.

        With the width off this is effectively infinite, so nothing wraps for
        length. Layout still breaks where the STRUCTURE says to: one select
        item per line, a clause per line, because those are not width
        decisions; only the length-driven breaks go away.
        """
        if self.width == self.OFF:
            return sys.maxsize
        return max(self.width, anchor + self.floor) + self.grace
