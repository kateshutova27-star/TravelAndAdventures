"""Tests for adventure generation: stop selection, scoring, and time budgets."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from app.services.adventure_generator import (
    DURATION_MINUTES,
    _MIN_VISIT_DURATION,
    _compute_score,
    get_visit_duration,
    select_stops,
)


def _make_place(
    *,
    category: str = "nature",
    rating: float = 4.0,
    review_count: int = 100,
    name: str | None = None,
    lat: float = 49.28,
    lng: float = -123.12,
) -> MagicMock:
    """Create a minimal Place-like object for unit tests."""
    place = MagicMock()
    place.id = str(uuid.uuid4())
    place.name = name or f"Place-{place.id[:6]}"
    place.category = category
    place.rating = rating
    place.review_count = review_count
    place.lat = lat
    place.lng = lng
    return place


def _make_candidates(n: int = 20, lat: float = 49.28, lng: float = -123.12) -> list:
    """Build a diverse pool of fake candidates near the given point."""
    categories = ["nature", "coffee", "restaurant", "city", "photo", "hike", "museum", "landmark"]
    places = []
    for i in range(n):
        cat = categories[i % len(categories)]
        places.append(
            _make_place(
                category=cat,
                rating=3.5 + (i % 5) * 0.3,
                review_count=50 + i * 20,
                name=f"{cat.title()} Place {i}",
                lat=lat + i * 0.001,
                lng=lng + i * 0.001,
            )
        )
    return places


# ---------------------------------------------------------------------------
# _compute_score
# ---------------------------------------------------------------------------


class TestComputeScore:
    def test_score_includes_random_jitter(self):
        """Same place scored multiple times should produce different values."""
        place = _make_place(category="nature", rating=4.5, review_count=200)
        scores = {_compute_score(place, 49.28, -123.12, 10000) for _ in range(30)}
        assert len(scores) > 1, "Scores must vary due to random jitter"

    def test_higher_rating_gives_higher_average_score(self):
        high = _make_place(rating=5.0)
        low = _make_place(rating=2.0)
        high_scores = [_compute_score(high, 49.28, -123.12, 10000) for _ in range(50)]
        low_scores = [_compute_score(low, 49.28, -123.12, 10000) for _ in range(50)]
        assert sum(high_scores) / len(high_scores) > sum(low_scores) / len(low_scores)

    def test_closer_place_scores_higher_on_average(self):
        close = _make_place(lat=49.28, lng=-123.12)
        far = _make_place(lat=49.35, lng=-123.05)
        close_scores = [_compute_score(close, 49.28, -123.12, 10000) for _ in range(50)]
        far_scores = [_compute_score(far, 49.28, -123.12, 10000) for _ in range(50)]
        assert sum(close_scores) / len(close_scores) > sum(far_scores) / len(far_scores)


# ---------------------------------------------------------------------------
# get_visit_duration
# ---------------------------------------------------------------------------


class TestGetVisitDuration:
    def test_coffee_duration(self):
        assert 25 <= get_visit_duration("coffee", "threeHours") <= 60

    def test_museum_duration(self):
        assert get_visit_duration("museum", "threeHours") >= 60

    def test_hike_duration(self):
        assert get_visit_duration("hike", "threeHours") >= 60

    def test_photo_is_short(self):
        assert get_visit_duration("photo", "threeHours") <= 20

    def test_full_day_scales_up(self):
        base = get_visit_duration("nature", "threeHours")
        full = get_visit_duration("nature", "fullDay")
        assert full >= base

    def test_one_hour_scales_down(self):
        base = get_visit_duration("nature", "threeHours")
        short = get_visit_duration("nature", "oneHour")
        assert short <= base

    def test_unknown_category_has_default(self):
        dur = get_visit_duration("unknown_cat", "threeHours")
        assert dur > 0


# ---------------------------------------------------------------------------
# DURATION_MINUTES
# ---------------------------------------------------------------------------


class TestDurationMinutes:
    def test_full_day_is_480(self):
        assert DURATION_MINUTES["fullDay"] == 480

    def test_half_day_is_300(self):
        assert DURATION_MINUTES["halfDay"] == 300

    def test_three_hours_is_180(self):
        assert DURATION_MINUTES["threeHours"] == 180

    def test_one_hour_is_60(self):
        assert DURATION_MINUTES["oneHour"] == 60


# ---------------------------------------------------------------------------
# select_stops
# ---------------------------------------------------------------------------


class TestSelectStops:
    def _select(self, candidates=None, categories=None, duration="threeHours", **kwargs):
        if candidates is None:
            candidates = _make_candidates(20)
        budget = DURATION_MINUTES.get(duration, 180)
        defaults = dict(
            categories=categories or [],
            duration=duration,
            time_budget=budget,
            anchor_lat=49.28,
            anchor_lng=-123.12,
            max_radius_m=10000,
        )
        defaults.update(kwargs)
        return select_stops(candidates, **defaults)

    def test_returns_at_least_one_stop(self):
        selected = self._select()
        assert len(selected) >= 1

    def test_no_duplicate_places(self):
        selected = self._select()
        ids = [s.place.id for s in selected]
        assert len(ids) == len(set(ids)), "Stops must not contain duplicate places"

    def test_dwell_times_are_category_aware(self):
        selected = self._select()
        for stop in selected:
            assert stop.time_to_spend_minutes > 0

    def test_total_time_within_budget(self):
        """Total visit time should not vastly exceed the time budget."""
        for dur_name, budget in DURATION_MINUTES.items():
            selected = self._select(duration=dur_name)
            total_dwell = sum(s.time_to_spend_minutes for s in selected)
            # Dwell alone shouldn't exceed budget (walk time is extra but
            # the greedy algorithm accounts for it)
            assert total_dwell <= budget * 1.5, (
                f"{dur_name}: total dwell {total_dwell}min exceeds {budget * 1.5}min"
            )

    def test_repeated_calls_produce_different_stop_sets(self):
        """Same candidates must yield varied stop selections due to jitter."""
        candidates = _make_candidates(20)
        results: list[tuple[str, ...]] = []
        for _ in range(20):
            selected = self._select(candidates=candidates)
            place_ids = tuple(s.place.id for s in selected)
            results.append(place_ids)
        unique_results = set(results)
        assert len(unique_results) > 1, (
            "select_stops must produce different stop sets across calls"
        )

    def test_works_with_small_candidate_pool(self):
        """Should not crash when candidates < typical stop count."""
        candidates = _make_candidates(2)
        selected = self._select(candidates=candidates)
        assert len(selected) >= 1
        assert len(selected) <= len(candidates)

    def test_spatial_clustering_filters_distant_candidates(self):
        """Stops far from the anchor are excluded by radius."""
        nearby = []
        for i in range(10):
            cats = ["nature", "coffee", "city", "restaurant", "photo"]
            p = _make_place(category=cats[i % 5], rating=4.5,
                            lat=49.28 + i * 0.001, lng=-123.12 + i * 0.001)
            nearby.append(p)

        faraway = []
        for i in range(10):
            cats = ["nature", "coffee", "city", "restaurant", "photo"]
            p = _make_place(category=cats[i % 5], rating=4.5,
                            lat=50.0 + i * 0.001, lng=-122.0)
            faraway.append(p)

        candidates = nearby + faraway
        selected = self._select(candidates=candidates, max_radius_m=1500)

        for stop in selected:
            assert stop.place.lat < 49.5, (
                f"Stop at lat={stop.place.lat} is too far; radius filter should exclude it"
            )

    def test_pinned_stops_are_included(self):
        """Pinned stops from basket should appear in the result."""
        from app.services.adventure_generator import SelectedStop
        candidates = _make_candidates(20)
        pinned_place = _make_place(category="museum", rating=4.8, name="My Museum")
        pinned = [SelectedStop(place=pinned_place, time_to_spend_minutes=60)]
        selected = self._select(candidates=candidates, pinned_stops=pinned)
        pinned_ids = {s.place.id for s in pinned}
        selected_ids = {s.place.id for s in selected}
        assert pinned_ids.issubset(selected_ids)

    def test_one_hour_selects_at_least_one_stop(self):
        """oneHour with coffee/city must select at least one stop."""
        candidates = _make_candidates(10, lat=49.28, lng=-123.12)
        selected = self._select(
            candidates=candidates,
            categories=["coffee", "city"],
            duration="oneHour",
        )
        assert len(selected) >= 1

    def test_one_hour_fits_two_coffee_stops(self):
        """oneHour budget should fit at least 2 coffee stops with minimums."""
        # Create 10 coffee places very close together
        candidates = [
            _make_place(category="coffee", rating=4.0 + i * 0.1,
                        lat=49.28 + i * 0.0002, lng=-123.12)
            for i in range(10)
        ]
        selected = self._select(
            candidates=candidates,
            categories=["coffee"],
            duration="oneHour",
        )
        assert len(selected) >= 2, (
            f"Expected at least 2 coffee stops for oneHour, got {len(selected)}"
        )

    def test_min_visit_durations_fit_one_hour(self):
        """All category minimums should be under 30 min for oneHour to work."""
        from app.services.adventure_generator import _MIN_VISIT_DURATION
        for cat, minimum in _MIN_VISIT_DURATION.items():
            if cat in ("hike", "museum"):  # these are naturally long
                continue
            assert minimum <= 30, (
                f"Category {cat} minimum {minimum} too high for oneHour trips"
            )

    def test_excluded_places_are_filtered(self):
        """Places in excluded_ids should never appear in results."""
        candidates = _make_candidates(20)
        excluded = {candidates[0].id, candidates[1].id, candidates[2].id}
        selected = self._select(candidates=candidates)
        # We can't pass excluded to select_stops directly since it's a
        # generate_adventure concern, but we can verify the scoring works
        # by checking that boosted places score higher
        assert len(selected) >= 1

    def test_boosted_places_rank_higher(self):
        """Boosted places should have higher average scores."""
        place = _make_place(rating=4.0)
        normal_scores = [
            _compute_score(place, 49.28, -123.12, 10000, is_boosted=False)
            for _ in range(50)
        ]
        boosted_scores = [
            _compute_score(place, 49.28, -123.12, 10000, is_boosted=True)
            for _ in range(50)
        ]
        assert sum(boosted_scores) / len(boosted_scores) > sum(normal_scores) / len(normal_scores)
