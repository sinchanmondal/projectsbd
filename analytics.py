"""
analytics.py
============
Standalone powerlifting analytics engine for **Apex SBD**.

Everything in this module is pure Python (``math`` + ``statistics``) with no
GUI or database dependencies. Functions accept plain numbers, or any object
that satisfies :class:`SessionLike` (``database.SessionRecord`` does).

Implemented metrics
-------------------
* SBD total and per-session progress deltas (+/- kg vs. the previous session)
* DOTS score using the sex-specific polynomial coefficients
* Strength-to-bodyweight multipliers (Squat/BW, Bench/BW, Deadlift/BW, Total/BW)
* One-rep-max estimation (Epley, Brzycki, Lombardi and their average)
* Rolling averages, least-squares trendlines and descriptive statistics
* Personal-record (PR) detection and progress-rate estimation
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date
from typing import Literal, Mapping, Optional, Protocol, Sequence

Sex = Literal["male", "female"]

LIFTS: tuple[str, ...] = ("squat", "bench", "deadlift")
PR_CATEGORIES: tuple[str, ...] = ("squat", "bench", "deadlift", "total")

#: DOTS polynomial coefficients (a, b, c, d, e) for a·BW⁴ + b·BW³ + c·BW² + d·BW + e.
DOTS_COEFFICIENTS: Mapping[str, tuple[float, float, float, float, float]] = {
    "male": (-0.0000010930, 0.0007391293, -0.1918759221, 24.0900756, -307.75076),
    "female": (-0.0000010706, 0.0005158568, -0.1126655495, 13.6175032, -57.96288),
}

#: Bodyweight (kg) is clamped to these bounds before the polynomial is applied.
DOTS_BODYWEIGHT_BOUNDS: Mapping[str, tuple[float, float]] = {
    "male": (40.0, 210.0),
    "female": (40.0, 150.0),
}

#: Approximate, community-style DOTS tiers as (lower bound, label), highest first.
DOTS_TIERS: tuple[tuple[float, str], ...] = (
    (525.0, "World class"),
    (450.0, "Elite"),
    (375.0, "Advanced"),
    (300.0, "Intermediate"),
    (200.0, "Novice"),
    (0.0, "Developing"),
)

ONE_RM_FORMULAS: tuple[str, ...] = ("epley", "brzycki", "lombardi")
MAX_REPS_FOR_ESTIMATE: int = 15
_EPSILON: float = 1e-9


# ---------------------------------------------------------------------------
# Structural input type
# ---------------------------------------------------------------------------
class SessionLike(Protocol):
    """Structural type for one logged training session."""

    @property
    def id(self) -> int:
        """Unique, stable identifier of the session."""

    @property
    def session_date(self) -> date:
        """Calendar date the session was performed."""

    @property
    def bodyweight(self) -> float:
        """Lifter bodyweight in kilograms."""

    @property
    def squat(self) -> float:
        """Best successful squat single in kilograms."""

    @property
    def bench(self) -> float:
        """Best successful bench-press single in kilograms."""

    @property
    def deadlift(self) -> float:
        """Best successful deadlift single in kilograms."""

    @property
    def notes(self) -> str:
        """Free-text notes (not used in any calculation)."""


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StrengthRatios:
    """Lift-to-bodyweight multipliers (e.g. ``squat == 2.1`` means 2.1× BW)."""

    squat: float
    bench: float
    deadlift: float
    total: float


@dataclass(frozen=True)
class SessionDelta:
    """Change of every metric relative to the chronologically previous session."""

    days_since_previous: int
    bodyweight: float
    squat: float
    bench: float
    deadlift: float
    total: float
    dots: float


@dataclass(frozen=True)
class PersonalRecord:
    """Best value achieved for one category (a lift or the SBD total)."""

    category: str
    value: float
    session_id: int
    session_date: date
    previous_best: Optional[float]

    @property
    def improvement(self) -> Optional[float]:
        """Kilograms gained over the previous best, or ``None`` for a baseline record."""
        if self.previous_best is None:
            return None
        return round(self.value - self.previous_best, 2)

    @property
    def is_baseline(self) -> bool:
        """``True`` when the record comes from the first session (nothing was beaten)."""
        return self.previous_best is None


@dataclass(frozen=True)
class SessionMetrics:
    """A session together with every derived metric the dashboard needs."""

    session: SessionLike
    total: float
    dots: float
    ratios: StrengthRatios
    delta: Optional[SessionDelta]
    pr_categories: tuple[str, ...]
    rolling_total: float

    @property
    def is_pr_session(self) -> bool:
        """``True`` if at least one lift or the total beat its previous best."""
        return bool(self.pr_categories)


@dataclass(frozen=True)
class LinearFit:
    """Ordinary least-squares fit ``y = slope · x + intercept``."""

    slope: float
    intercept: float
    r_squared: float
    sample_size: int

    def predict(self, x: float) -> float:
        """Return the fitted ``y`` value for ``x``."""
        return self.slope * x + self.intercept


@dataclass(frozen=True)
class StatSummary:
    """Descriptive statistics of a numeric series."""

    count: int
    mean: float
    median: float
    stdev: float
    minimum: float
    maximum: float

    @property
    def spread(self) -> float:
        """Range of the series (maximum − minimum)."""
        return self.maximum - self.minimum


@dataclass(frozen=True)
class ProgressTrend:
    """Rate of progress for one category, derived from a linear fit over time."""

    category: str
    kg_per_week: float
    r_squared: float
    span_days: int
    sample_size: int


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
def _require_finite(value: float, label: str) -> float:
    """Return ``value`` as a float, raising ``ValueError`` if it is not a finite number."""
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be numeric.") from None
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number.")
    return number


def normalize_sex(value: str) -> Sex:
    """Normalise user input such as ``"M"`` or ``"Female"`` to ``"male"``/``"female"``."""
    key = str(value).strip().lower()
    if key in ("male", "m", "man", "men"):
        return "male"
    if key in ("female", "f", "woman", "women"):
        return "female"
    raise ValueError(f"Unknown sex '{value}'. Expected 'male' or 'female'.")


# ---------------------------------------------------------------------------
# Core powerlifting formulas
# ---------------------------------------------------------------------------
def sbd_total(squat: float, bench: float, deadlift: float) -> float:
    """Return the SBD total (squat + bench + deadlift) in kilograms."""
    values = [_require_finite(v, name) for v, name in ((squat, "Squat"), (bench, "Bench"), (deadlift, "Deadlift"))]
    if any(v < 0 for v in values):
        raise ValueError("Lift values cannot be negative.")
    return round(sum(values), 2)


def clamp_bodyweight(bodyweight: float, sex: str) -> float:
    """Clamp ``bodyweight`` to the range over which the DOTS polynomial is defined."""
    low, high = DOTS_BODYWEIGHT_BOUNDS[normalize_sex(sex)]
    return min(max(_require_finite(bodyweight, "Bodyweight"), low), high)


def dots_denominator(bodyweight: float, sex: str) -> float:
    """Evaluate the DOTS polynomial a·BW⁴ + b·BW³ + c·BW² + d·BW + e."""
    bw = _require_finite(bodyweight, "Bodyweight")
    if bw <= 0:
        raise ValueError("Bodyweight must be positive to compute DOTS.")
    a, b, c, d, e = DOTS_COEFFICIENTS[normalize_sex(sex)]
    x = clamp_bodyweight(bw, sex)
    return a * x**4 + b * x**3 + c * x**2 + d * x + e


def dots_coefficient(bodyweight: float, sex: str) -> float:
    """Return the DOTS multiplier ``500 / polynomial(BW)`` for a bodyweight and sex."""
    return 500.0 / dots_denominator(bodyweight, sex)


def dots_score(total: float, bodyweight: float, sex: str) -> float:
    """Return the DOTS score: ``total × 500 / (a·BW⁴ + b·BW³ + c·BW² + d·BW + e)``."""
    total_kg = _require_finite(total, "Total")
    if total_kg < 0:
        raise ValueError("Total cannot be negative.")
    return round(total_kg * dots_coefficient(bodyweight, sex), 2)


def total_for_dots(target_dots: float, bodyweight: float, sex: str) -> float:
    """Return the SBD total (kg) needed at ``bodyweight`` to score ``target_dots``."""
    return _require_finite(target_dots, "DOTS") / dots_coefficient(bodyweight, sex)


def dots_tier(score: float) -> str:
    """Map a DOTS score to an approximate descriptive strength tier."""
    value = _require_finite(score, "DOTS")
    for lower_bound, label in DOTS_TIERS:
        if value >= lower_bound:
            return label
    return DOTS_TIERS[-1][1]


def strength_ratios(squat: float, bench: float, deadlift: float, bodyweight: float) -> StrengthRatios:
    """Return Squat/BW, Bench/BW, Deadlift/BW and Total/BW multipliers."""
    bw = _require_finite(bodyweight, "Bodyweight")
    if bw <= 0:
        raise ValueError("Bodyweight must be positive to compute ratios.")
    total = sbd_total(squat, bench, deadlift)
    return StrengthRatios(
        squat=round(squat / bw, 3),
        bench=round(bench / bw, 3),
        deadlift=round(deadlift / bw, 3),
        total=round(total / bw, 3),
    )


# ---------------------------------------------------------------------------
# One-rep-max estimation
# ---------------------------------------------------------------------------
def estimate_1rm(weight: float, reps: int, formula: str = "epley") -> float:
    """Estimate a one-rep max from a set of ``reps`` at ``weight`` kg.

    Supported formulas: ``epley``, ``brzycki``, ``lombardi`` and ``average``
    (mean of the three). Estimates are only offered for 1–15 reps because
    accuracy degrades quickly beyond that.
    """
    load = _require_finite(weight, "Weight")
    if load <= 0:
        raise ValueError("Weight must be greater than 0 kg.")
    if isinstance(reps, bool) or not isinstance(reps, (int, float)) or int(reps) != reps:
        raise ValueError("Reps must be a whole number.")
    count = int(reps)
    if not 1 <= count <= MAX_REPS_FOR_ESTIMATE:
        raise ValueError(f"Reps must be between 1 and {MAX_REPS_FOR_ESTIMATE} for a reliable estimate.")
    if count == 1:
        return round(load, 2)

    key = formula.strip().lower()
    if key == "epley":
        estimate = load * (1.0 + count / 30.0)
    elif key == "brzycki":
        estimate = load * 36.0 / (37.0 - count)
    elif key == "lombardi":
        estimate = load * count**0.10
    elif key == "average":
        estimate = statistics.fmean(estimate_1rm(load, count, name) for name in ONE_RM_FORMULAS)
    else:
        raise ValueError(f"Unknown 1RM formula '{formula}'. Choose from {', '.join(ONE_RM_FORMULAS)} or average.")
    return round(estimate, 2)


def estimate_1rm_all(weight: float, reps: int) -> dict[str, float]:
    """Return estimates from every supported formula plus their ``average``."""
    results = {name: estimate_1rm(weight, reps, name) for name in ONE_RM_FORMULAS}
    results["average"] = round(statistics.fmean(results.values()), 2)
    return results


def round_to_increment(value: float, increment: float = 2.5, mode: str = "nearest") -> float:
    """Round ``value`` to a loadable plate increment (``nearest``, ``down`` or ``up``)."""
    number = _require_finite(value, "Value")
    step = _require_finite(increment, "Increment")
    if step <= 0:
        raise ValueError("Increment must be positive.")
    ratio = number / step
    if mode == "nearest":
        rounded = math.floor(ratio + 0.5)
    elif mode == "down":
        rounded = math.floor(ratio + _EPSILON)
    elif mode == "up":
        rounded = math.ceil(ratio - _EPSILON)
    else:
        raise ValueError("Mode must be 'nearest', 'down' or 'up'.")
    return round(rounded * step, 2)


# ---------------------------------------------------------------------------
# Generic statistics
# ---------------------------------------------------------------------------
def rolling_average(values: Sequence[float], window: int = 3) -> list[float]:
    """Return the trailing rolling mean of ``values`` (uses fewer points at the start)."""
    if window < 1:
        raise ValueError("Window must be at least 1.")
    numbers = [_require_finite(v, "Value") for v in values]
    result: list[float] = []
    for index in range(len(numbers)):
        chunk = numbers[max(0, index - window + 1): index + 1]
        result.append(round(statistics.fmean(chunk), 2))
    return result


def linear_regression(xs: Sequence[float], ys: Sequence[float]) -> Optional[LinearFit]:
    """Fit ``y = slope·x + intercept`` by least squares.

    Returns ``None`` when fewer than two points exist or every ``x`` is identical.
    """
    if len(xs) != len(ys):
        raise ValueError("xs and ys must have the same length.")
    n = len(xs)
    if n < 2:
        return None
    x_vals = [_require_finite(x, "x") for x in xs]
    y_vals = [_require_finite(y, "y") for y in ys]
    mean_x = statistics.fmean(x_vals)
    mean_y = statistics.fmean(y_vals)
    sxx = sum((x - mean_x) ** 2 for x in x_vals)
    if sxx < _EPSILON:
        return None
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(x_vals, y_vals))
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    ss_tot = sum((y - mean_y) ** 2 for y in y_vals)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(x_vals, y_vals))
    r_squared = 1.0 if ss_tot < _EPSILON else max(0.0, 1.0 - ss_res / ss_tot)
    return LinearFit(slope=slope, intercept=intercept, r_squared=r_squared, sample_size=n)


def summary_statistics(values: Sequence[float]) -> Optional[StatSummary]:
    """Return count/mean/median/stdev/min/max for ``values``, or ``None`` if empty."""
    numbers = [_require_finite(v, "Value") for v in values]
    if not numbers:
        return None
    return StatSummary(
        count=len(numbers),
        mean=round(statistics.fmean(numbers), 2),
        median=round(statistics.median(numbers), 2),
        stdev=round(statistics.stdev(numbers), 2) if len(numbers) > 1 else 0.0,
        minimum=min(numbers),
        maximum=max(numbers),
    )


# ---------------------------------------------------------------------------
# Session-level analytics
# ---------------------------------------------------------------------------
def sort_sessions(sessions: Sequence[SessionLike]) -> list[SessionLike]:
    """Return sessions in chronological order (date, then id as a tie-breaker)."""
    return sorted(sessions, key=lambda s: (s.session_date, s.id))


def category_value(session: SessionLike, category: str) -> float:
    """Return a lift value or the SBD total of ``session`` for ``category``."""
    if category == "total":
        return sbd_total(session.squat, session.bench, session.deadlift)
    if category in LIFTS:
        return float(getattr(session, category))
    raise ValueError(f"Unknown category '{category}'.")


def scan_personal_records(
    sessions: Sequence[SessionLike],
) -> tuple[list[PersonalRecord], dict[str, PersonalRecord], dict[int, tuple[str, ...]]]:
    """Walk sessions chronologically and track personal records.

    Returns a tuple of:
        * every PR-breaking event (baselines from the first session excluded),
        * the current best record per category,
        * a mapping ``session_id -> categories that were PRs in that session``.

    Matching a previous best is not a PR; the record keeps its earliest date.
    """
    events: list[PersonalRecord] = []
    current: dict[str, PersonalRecord] = {}
    per_session: dict[int, tuple[str, ...]] = {}
    for session in sort_sessions(sessions):
        broken: list[str] = []
        for category in PR_CATEGORIES:
            value = category_value(session, category)
            best = current.get(category)
            if best is None:
                current[category] = PersonalRecord(category, value, session.id, session.session_date, None)
            elif value > best.value + _EPSILON:
                record = PersonalRecord(category, value, session.id, session.session_date, best.value)
                current[category] = record
                events.append(record)
                broken.append(category)
        per_session[session.id] = tuple(broken)
    return events, current, per_session


def detect_pr_events(sessions: Sequence[SessionLike]) -> list[PersonalRecord]:
    """Return every PR-breaking event in chronological order."""
    return scan_personal_records(sessions)[0]


def current_personal_records(sessions: Sequence[SessionLike]) -> dict[str, PersonalRecord]:
    """Return the best record for each of squat, bench, deadlift and total."""
    return scan_personal_records(sessions)[1]


def analyze_sessions(sessions: Sequence[SessionLike], sex: str, rolling_window: int = 3) -> list[SessionMetrics]:
    """Compute totals, DOTS, ratios, deltas, PR flags and a rolling total per session.

    The result is ordered chronologically (oldest first).
    """
    lifter_sex = normalize_sex(sex)
    ordered = sort_sessions(sessions)
    _, _, pr_map = scan_personal_records(ordered)
    totals = [sbd_total(s.squat, s.bench, s.deadlift) for s in ordered]
    rolling = rolling_average(totals, rolling_window) if totals else []

    results: list[SessionMetrics] = []
    previous: Optional[SessionMetrics] = None
    for session, total, rolling_total in zip(ordered, totals, rolling):
        dots = dots_score(total, session.bodyweight, lifter_sex)
        delta: Optional[SessionDelta] = None
        if previous is not None:
            before = previous.session
            delta = SessionDelta(
                days_since_previous=(session.session_date - before.session_date).days,
                bodyweight=round(session.bodyweight - before.bodyweight, 2),
                squat=round(session.squat - before.squat, 2),
                bench=round(session.bench - before.bench, 2),
                deadlift=round(session.deadlift - before.deadlift, 2),
                total=round(total - previous.total, 2),
                dots=round(dots - previous.dots, 2),
            )
        metrics = SessionMetrics(
            session=session,
            total=total,
            dots=dots,
            ratios=strength_ratios(session.squat, session.bench, session.deadlift, session.bodyweight),
            delta=delta,
            pr_categories=pr_map.get(session.id, ()),
            rolling_total=rolling_total,
        )
        results.append(metrics)
        previous = metrics
    return results


def progress_trend(sessions: Sequence[SessionLike], category: str = "total") -> Optional[ProgressTrend]:
    """Estimate kg gained per week for ``category`` with a linear fit over time.

    Returns ``None`` if there are fewer than two sessions on distinct dates.
    """
    ordered = sort_sessions(sessions)
    if len(ordered) < 2:
        return None
    origin = ordered[0].session_date
    xs = [float((s.session_date - origin).days) for s in ordered]
    ys = [category_value(s, category) for s in ordered]
    fit = linear_regression(xs, ys)
    if fit is None:
        return None
    return ProgressTrend(
        category=category,
        kg_per_week=round(fit.slope * 7.0, 2),
        r_squared=round(fit.r_squared, 4),
        span_days=int(xs[-1]),
        sample_size=len(ordered),
    )


if __name__ == "__main__":
    # Quick self-check: a 550 kg total at 83.9 kg bodyweight.
    print(f"DOTS (male, 83.9 kg, 550 kg total): {dots_score(550, 83.9, 'male'):.2f}")
    print(f"DOTS (female, 63.0 kg, 400 kg total): {dots_score(400, 63.0, 'female'):.2f}")
    print(f"1RM estimates for 160 kg x 5: {estimate_1rm_all(160, 5)}")
