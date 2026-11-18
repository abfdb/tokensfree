"""Per-provider circuit breaker.

Pure in-memory state machine: closed -> open -> half_open -> closed.
One probe is leased at a time in half_open, so a still-broken provider is not hit
by a thundering herd when the open window expires.
"""
import math


class CircuitBreaker:
    def __init__(self, *, threshold=5, window_s=60.0, open_s=30.0):
        if type(threshold) is not int or threshold < 1:
            raise ValueError("threshold must be a positive int")
        if type(window_s) not in (int, float) or isinstance(window_s, bool) or not math.isfinite(window_s) or window_s < 0:
            raise ValueError("window_s must be a finite non-negative number")
        if type(open_s) not in (int, float) or isinstance(open_s, bool) or not math.isfinite(open_s) or open_s <= 0:
            raise ValueError("open_s must be a finite positive number")
        self.threshold = threshold
        self.window_s = float(window_s)
        self.open_s = float(open_s)
        self._state = "closed"
        self._fail_streak = 0
        self._window_start = None
        self._window_fails = 0
        self._open_until = None
        self._probe_in_flight = False

    @property
    def state(self):
        return self._state

    @property
    def fail_streak(self):
        return self._fail_streak

    @property
    def window_fails(self):
        return self._window_fails

    @property
    def open_until(self):
        return self._open_until

    def _roll_window(self, now):
        if self._window_start is None or now - self._window_start > self.window_s:
            self._window_start = now
            self._window_fails = 0

    def allows(self, now):
        if self._state == "closed":
            return True
        if self._state == "open":
            if now >= self._open_until:
                self._state = "half_open"
                self._probe_in_flight = True
                return True
            return False
        return False

    def record_success(self, now):
        # A success starts a fresh accounting window: earlier failures stop counting.
        self._window_start = now
        self._window_fails = 0
        self._state = "closed"
        self._fail_streak = 0
        self._open_until = None
        self._probe_in_flight = False

    def record_failure(self, now):
        self._roll_window(now)
        self._fail_streak += 1
        self._window_fails += 1
        if self._state == "half_open" or self._fail_streak >= self.threshold or self._window_fails >= self.threshold:
            self._state = "open"
            self._open_until = float(now + self.open_s)
            self._probe_in_flight = False

    def snapshot(self):
        return {
            "state": self._state,
            "fail_streak": self._fail_streak,
            "window_fails": self._window_fails,
            "open_until": self._open_until,
        }
