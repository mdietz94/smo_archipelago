"""Discrete-event timeline simulator.

Models one SMO player + one or more coplayer faucets against a real
Archipelago spoiler. See seed_sim package docstring for the playstyle model
("stay in kingdom until allowed to leave; fastest-first within; return for
cleanup when BK").
"""

from __future__ import annotations

import heapq
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterator

from .coplayer import CoplayerProfile, sample_interarrival
from .spoiler import SpherePlacement, SpoilerData
from .timing import KingdomTime, sample as sample_kingdom_time


SMO_GAME = "Manual_SMO_archipelago"

# How long SMO loses to "travel" between kingdoms (Odyssey takeoff/landing).
DEFAULT_TRAVEL_SEC = 120.0

# Soft-BK = the player has been in the global-fastest-fallback mode for this
# long without receiving a new progression item.
DEFAULT_BK_THRESHOLD_SEC = 1800.0

# Network/QoL delay between a coplayer finding an SMO item and SMO receiving it.
DEFAULT_DELIVERY_DELAY_SEC = 2.0


# --- Location -> kingdom map ----------------------------------------------

def kingdom_of_location(name: str) -> str | None:
    """Strip the `Kingdom: ` prefix from a location name.

    "Cap: Frog-Jumping Above the Fog" -> "Cap"
    "Capture: Goomba" -> None (captures aren't kingdom-scoped here)
    """
    if ":" not in name:
        return None
    head = name.split(":", 1)[0].strip()
    if head == "Capture":
        return None
    return head


# --- Progression-detection helpers ----------------------------------------

# Item names whose receipt may unlock new spheres. Tag broadly: any moon,
# kingdom unlock, capture, or movement action. False positives only cost a
# wasted reachable-recompute.
_PROGRESSION_HINTS = (
    "Power Moon", "Multi-Moon", "Kingdom",
    "Long Jump", "Roll", "Ground Pound", "Dive", "Cap Throw", "Cap Jump",
    "Wall Jump", "Triple Jump", "Backward Somersault", "Side Somersault",
    "Upward Throw", "Downward Throw", "Spin Throw", "Homing Cap Throw",
    "Hold", "Throw", "Swim", "Spin", "Dash", "Jaxi", "Motor scooter",
)


def looks_like_progression(item_name: str) -> bool:
    return any(h in item_name for h in _PROGRESSION_HINTS) or _is_capture_name(item_name)


# Captures: the apworld emits items with bare enemy names. Loaded lazily from
# the apworld data to avoid hardcoding the 42-name list here. Set via
# `register_capture_names()` before sim runs.
_KNOWN_CAPTURES: set[str] = set()


def register_capture_names(names: set[str]) -> None:
    _KNOWN_CAPTURES.clear()
    _KNOWN_CAPTURES.update(names)


def _is_capture_name(name: str) -> bool:
    return name in _KNOWN_CAPTURES


# --- Event types ----------------------------------------------------------

EV_SMO_CHECK_DONE = "smo_check_done"
EV_COPLAYER_CHECK_DONE = "coplayer_check_done"
EV_ITEM_RECEIVED = "item_received"


@dataclass(order=True)
class _Event:
    time: float
    seq: int            # tiebreaker for heap stability
    kind: str
    payload: dict = field(compare=False)


# --- Simulation result ----------------------------------------------------

@dataclass
class SimResult:
    seed: int
    spoiler_name: str
    finished: bool
    final_time_sec: float
    sphere_reached: int

    # Per-kingdom dwell + completion at first-exit.
    kingdom_dwell_sec: dict[str, float]
    kingdom_visit_order: list[str]
    completion_at_exit: dict[str, float]    # 0..1, what fraction of kingdom's
                                            # checks were done at first time leaving

    # Reachable-count timeline (sampled every minute).
    reachable_timeline: list[tuple[float, int]]

    # Soft-BK intervals: (start_sec, end_sec, kingdom_being_cleaned_up).
    soft_bk_intervals: list[tuple[float, float, str | None]]

    # Wait gaps between consecutive coplayer-sourced SMO items.
    coplayer_gaps_sec: list[float]

    # Per kingdom-unlock-event: count of prerequisite items by source slot.
    # {kingdom_name: {finder_slot: count}}
    unlock_source_counts: dict[str, Counter]


# --- The main loop --------------------------------------------------------

class _Simulator:
    """One run = (one spoiler) x (one sim RNG seed)."""

    def __init__(
        self,
        spoiler: SpoilerData,
        time_profile: dict[str, KingdomTime],
        coplayer_specs: list[tuple[CoplayerProfile, str]],   # (profile, matched_slot)
        rng: random.Random,
        time_cap_sec: float,
        bk_threshold_sec: float,
        travel_cost_sec: float,
        delivery_delay_sec: float,
        seed_id: int,
    ):
        self.spoiler = spoiler
        self.time_profile = time_profile
        self.rng = rng
        self.time_cap_sec = time_cap_sec
        self.bk_threshold_sec = bk_threshold_sec
        self.travel_cost_sec = travel_cost_sec
        self.delivery_delay_sec = delivery_delay_sec
        self.seed_id = seed_id

        self.smo_slot = spoiler.smo_slot().slot
        self.clock = 0.0
        self.seq = 0
        self.heap: list[_Event] = []

        # Pre-sample each SMO location's check time, so within a kingdom the
        # player can deterministically take fastest first.
        self.smo_locations = [
            p for p in spoiler.spheres if p.finder_slot == self.smo_slot
        ]
        self.per_location_time: dict[str, float] = {}
        for p in self.smo_locations:
            self.per_location_time[p.location] = sample_kingdom_time(
                time_profile, kingdom_of_location(p.location), rng,
            )
        self.smo_location_by_name = {p.location: p for p in self.smo_locations}

        # SMO state.
        self.smo_inventory: Counter = Counter()
        self.smo_checked: set[str] = set()
        self.smo_reachable: set[str] = set()
        self.smo_current_kingdom: str | None = None
        self.smo_kingdoms_visited: list[str] = []   # in arrival order; deduped on append
        self.smo_kingdom_dwell: dict[str, float] = {}
        self.smo_kingdom_completion_at_exit: dict[str, float] = {}
        self.smo_total_per_kingdom: Counter = Counter(
            kingdom_of_location(p.location) for p in self.smo_locations
        )
        self.smo_total_per_kingdom.pop(None, None)

        self.last_kingdom_switch_time = 0.0

        # Coplayer state. Match each --coplayer to a spoiler slot.
        self.coplayers: list[_CoplayerRuntime] = []
        slots_in_spoiler = [
            s for s in spoiler.slots.values()
            if s.slot != self.smo_slot
        ]
        # Index of unconsumed (placement, item) tuples per slot.
        per_slot_pool: dict[str, list[SpherePlacement]] = {}
        for p in spoiler.spheres:
            if p.finder_slot != self.smo_slot and p.recipient_slot == self.smo_slot:
                per_slot_pool.setdefault(p.finder_slot, []).append(p)
        # Total per-slot checks: use spoiler-known counts where available.
        per_slot_total: dict[str, int] = {
            s.slot: len(s.locations) for s in slots_in_spoiler
        }

        unmatched_specs = list(coplayer_specs)
        unmatched_slots = list(slots_in_spoiler)

        def consume_spec(slot_name: str) -> CoplayerProfile | None:
            for i, (prof, match) in enumerate(unmatched_specs):
                if match == slot_name:
                    unmatched_specs.pop(i)
                    return prof
            return None

        for s in slots_in_spoiler:
            prof = consume_spec(s.slot)
            if prof is None:
                # Try a positional match (first unmatched spec with no slot).
                for i, (p, match) in enumerate(unmatched_specs):
                    if match is None:
                        unmatched_specs.pop(i)
                        prof = p
                        break
            if prof is None:
                continue
            smo_bound_items = per_slot_pool.get(s.slot, [])
            total = per_slot_total.get(s.slot, len(smo_bound_items))
            self.coplayers.append(_CoplayerRuntime(
                slot=s.slot, profile=prof,
                smo_bound_queue=list(smo_bound_items),
                total_checks=max(total, len(smo_bound_items)),
                checks_made=0,
            ))

        # Tracking.
        self.reachable_timeline: list[tuple[float, int]] = []
        self.coplayer_item_arrivals: list[float] = []     # times we got an item from any coplayer
        self.unlock_source_counts: dict[str, Counter] = {}
        self.soft_bk_intervals: list[tuple[float, float, str | None]] = []
        self._soft_bk_open: tuple[float, str | None] | None = None
        self.in_bk_fallback = False
        self.last_progression_time = 0.0

        self.sphere_reached = 0
        self.finished = False

        # Initial reachable: walk sphere 1 (and any item-prerequisite-free
        # earlier locations). The spoiler doesn't tell us which locations
        # have no requires; we infer reachability by checking each location's
        # *placement sphere* and unlocking forward as items are received.
        self.spheres_by_index: dict[int, list[SpherePlacement]] = {}
        for p in spoiler.spheres:
            self.spheres_by_index.setdefault(p.sphere, []).append(p)
        self.smo_locations_by_sphere: dict[int, list[SpherePlacement]] = {}
        for p in self.smo_locations:
            self.smo_locations_by_sphere.setdefault(p.sphere, []).append(p)

        # Sphere unlock state: which sphere we've "passed" so far. Locations
        # in sphere N become reachable once we receive every progression item
        # placed in spheres < N. We approximate this conservatively: locations
        # in sphere N become reachable when *count of inventory progression
        # items* >= number of progression items in spheres < N for SMO.
        self._progression_items_in_earlier_spheres: dict[int, int] = {}
        running = 0
        for n in sorted(self.spheres_by_index):
            self._progression_items_in_earlier_spheres[n] = running
            for p in self.spheres_by_index[n]:
                if p.recipient_slot == self.smo_slot and looks_like_progression(p.item):
                    running += 1
        # Locations in sphere 1 are always reachable to start.
        self._unlock_smo_sphere(1)
        self._track_kingdom_for_initial()

    # ---- Initial setup helpers ----

    def _unlock_smo_sphere(self, n: int) -> None:
        for p in self.smo_locations_by_sphere.get(n, []):
            if p.location not in self.smo_checked:
                self.smo_reachable.add(p.location)
        self.sphere_reached = max(self.sphere_reached, n)

    def _maybe_unlock_more_spheres(self) -> None:
        # How many SMO-progression items have we received?
        received_prog = sum(
            1 for name, c in self.smo_inventory.items()
            if c > 0 and looks_like_progression(name)
        )
        # Note: items can repeat (e.g. Power Moon x 5), but our counter is by
        # name; we use total count of progression items including duplicates.
        received_total = sum(
            c for name, c in self.smo_inventory.items()
            if looks_like_progression(name)
        )
        for n in sorted(self.spheres_by_index):
            required = self._progression_items_in_earlier_spheres.get(n, 0)
            if received_total >= required:
                if any(p.location not in self.smo_checked and p.location not in self.smo_reachable
                       for p in self.smo_locations_by_sphere.get(n, [])):
                    self._unlock_smo_sphere(n)

    def _track_kingdom_for_initial(self) -> None:
        # Pick the kingdom with the most starting reachable locations.
        counts: Counter = Counter()
        for loc in self.smo_reachable:
            k = kingdom_of_location(loc)
            if k:
                counts[k] += 1
        if counts:
            self.smo_current_kingdom = counts.most_common(1)[0][0]
            self.smo_kingdoms_visited.append(self.smo_current_kingdom)
            self.smo_kingdom_dwell.setdefault(self.smo_current_kingdom, 0.0)

    # ---- Main run ----

    def run(self) -> SimResult:
        # Schedule each coplayer's first check.
        for cp in self.coplayers:
            self._push(self.clock + sample_interarrival(cp.profile, self.rng),
                       EV_COPLAYER_CHECK_DONE, {"slot": cp.slot})
        # And SMO's first check.
        self._schedule_next_smo_check()

        # Periodic reachable-timeline sample, every 60s sim time.
        sample_interval = 60.0
        self._push(sample_interval, "_sample", {})

        while self.heap and self.clock < self.time_cap_sec:
            ev = heapq.heappop(self.heap)
            self.clock = ev.time
            # Open / close soft-BK windows based on current state.
            self._update_bk_window(self.clock)

            if ev.kind == EV_SMO_CHECK_DONE:
                self._handle_smo_check_done(ev.payload)
            elif ev.kind == EV_COPLAYER_CHECK_DONE:
                self._handle_coplayer_check_done(ev.payload)
            elif ev.kind == EV_ITEM_RECEIVED:
                self._handle_item_received(ev.payload)
            elif ev.kind == "_sample":
                self.reachable_timeline.append((self.clock, len(self.smo_reachable)))
                if self.clock + sample_interval < self.time_cap_sec:
                    self._push(self.clock + sample_interval, "_sample", {})

            # Goal: every SMO location checked.
            if not self.smo_reachable and not any(
                p.location not in self.smo_checked for p in self.smo_locations
            ):
                self.finished = True
                break

        # Close any open soft-BK window.
        if self._soft_bk_open is not None:
            start, k = self._soft_bk_open
            self.soft_bk_intervals.append((start, self.clock, k))
            self._soft_bk_open = None

        # Close current kingdom dwell.
        if self.smo_current_kingdom is not None:
            self.smo_kingdom_dwell[self.smo_current_kingdom] = (
                self.smo_kingdom_dwell.get(self.smo_current_kingdom, 0.0)
                + (self.clock - self.last_kingdom_switch_time)
            )
            # Record completion-at-exit if not already.
            self.smo_kingdom_completion_at_exit.setdefault(
                self.smo_current_kingdom,
                self._completion_for(self.smo_current_kingdom),
            )

        # Coplayer gaps.
        gaps: list[float] = []
        prev = None
        for t in sorted(self.coplayer_item_arrivals):
            if prev is not None:
                gaps.append(t - prev)
            prev = t

        return SimResult(
            seed=self.seed_id,
            spoiler_name="",
            finished=self.finished,
            final_time_sec=self.clock,
            sphere_reached=self.sphere_reached,
            kingdom_dwell_sec=dict(self.smo_kingdom_dwell),
            kingdom_visit_order=list(self.smo_kingdoms_visited),
            completion_at_exit=dict(self.smo_kingdom_completion_at_exit),
            reachable_timeline=list(self.reachable_timeline),
            soft_bk_intervals=list(self.soft_bk_intervals),
            coplayer_gaps_sec=gaps,
            unlock_source_counts={k: Counter(v) for k, v in self.unlock_source_counts.items()},
        )

    # ---- Event handlers ----

    def _handle_smo_check_done(self, _payload: dict) -> None:
        loc = self._pick_next_smo_location()
        if loc is None:
            # Truly stuck; advance and retry.
            self._push(self.clock + 60.0, EV_SMO_CHECK_DONE, {})
            return

        # The placement tells us what item is at this location and who gets it.
        p = self.smo_location_by_name[loc]
        self.smo_checked.add(loc)
        self.smo_reachable.discard(loc)

        # Charge time for this check (already advanced clock when scheduled).
        # The actual check time is already baked into "when this event fires".

        # Deliver the item.
        if p.recipient_slot == self.smo_slot:
            # Self-deliver (no network delay).
            self._receive_smo_item(p.item, source_slot=self.smo_slot)
        # else: it's some other player's item; we don't simulate them, so it
        # just falls into the void. (We could enqueue a delivery to the
        # coplayer faucet, but their item-delivery model is decoupled from
        # what we send them.)

        # Schedule next SMO check.
        self._schedule_next_smo_check()

    def _handle_coplayer_check_done(self, payload: dict) -> None:
        slot = payload["slot"]
        cp = self._coplayer(slot)
        if cp is None:
            return
        cp.checks_made += 1

        # Probability this check produced an SMO item:
        # remaining_smo_bound / remaining_total_checks.
        remaining_total = max(1, cp.total_checks - cp.checks_made + 1)
        if cp.smo_bound_queue and self.rng.random() < (len(cp.smo_bound_queue) / remaining_total):
            placement = cp.smo_bound_queue.pop(0)
            self._push(self.clock + self.delivery_delay_sec,
                       EV_ITEM_RECEIVED,
                       {"item": placement.item, "source_slot": placement.finder_slot,
                        "from_coplayer": True})

        # Schedule next coplayer check.
        if cp.checks_made < cp.total_checks:
            self._push(self.clock + sample_interarrival(cp.profile, self.rng),
                       EV_COPLAYER_CHECK_DONE, {"slot": slot})

    def _handle_item_received(self, payload: dict) -> None:
        item = payload["item"]
        source_slot = payload["source_slot"]
        from_coplayer = bool(payload.get("from_coplayer"))
        self._receive_smo_item(item, source_slot=source_slot, from_coplayer=from_coplayer)

    def _receive_smo_item(self, item: str, source_slot: str, from_coplayer: bool = False) -> None:
        prev_reach = set(self.smo_reachable)
        prev_kingdoms = set(
            k for loc in prev_reach if (k := kingdom_of_location(loc))
        )
        self.smo_inventory[item] += 1
        if from_coplayer:
            self.coplayer_item_arrivals.append(self.clock)
        if looks_like_progression(item):
            self.last_progression_time = self.clock
            self._maybe_unlock_more_spheres()

        # Did any new kingdom become reachable?
        new_kingdoms = set(
            k for loc in self.smo_reachable if (k := kingdom_of_location(loc))
        ) - prev_kingdoms
        for k in new_kingdoms:
            self.unlock_source_counts.setdefault(k, Counter())[source_slot] += 1

    # ---- Picking the next SMO location ----

    def _pick_next_smo_location(self) -> str | None:
        # Priority 1: same kingdom, fastest first.
        in_kingdom = [
            loc for loc in self.smo_reachable
            if kingdom_of_location(loc) == self.smo_current_kingdom
        ]
        if in_kingdom:
            self.in_bk_fallback = False
            return min(in_kingdom, key=lambda L: self.per_location_time[L])

        # Priority 2: travel to a newly-unlocked-not-yet-visited kingdom.
        new_kingdoms = {
            k for loc in self.smo_reachable
            if (k := kingdom_of_location(loc)) and k not in self.smo_kingdoms_visited
        }
        if new_kingdoms:
            # Prefer the one with the most reachable locations (heuristic).
            best_k = max(new_kingdoms, key=lambda k: sum(
                1 for loc in self.smo_reachable if kingdom_of_location(loc) == k
            ))
            self._transition_kingdom(best_k)
            self.in_bk_fallback = False
            return self._pick_next_smo_location()

        # Priority 3: BK fallback — fastest across all visited kingdoms.
        if self.smo_reachable:
            chosen = min(self.smo_reachable, key=lambda L: self.per_location_time[L])
            # Travel cost if the chosen location isn't in the current kingdom.
            chosen_k = kingdom_of_location(chosen)
            if chosen_k and chosen_k != self.smo_current_kingdom:
                # "Travel" — charge the cost into the next check schedule.
                self._transition_kingdom(chosen_k, travel_cost=self.travel_cost_sec)
            self.in_bk_fallback = True
            return chosen
        return None

    def _transition_kingdom(self, new_kingdom: str, travel_cost: float = 0.0) -> None:
        if new_kingdom == self.smo_current_kingdom:
            return
        if self.smo_current_kingdom is not None:
            dwell = self.clock - self.last_kingdom_switch_time
            self.smo_kingdom_dwell[self.smo_current_kingdom] = (
                self.smo_kingdom_dwell.get(self.smo_current_kingdom, 0.0) + dwell
            )
            self.smo_kingdom_completion_at_exit.setdefault(
                self.smo_current_kingdom,
                self._completion_for(self.smo_current_kingdom),
            )
        self.smo_current_kingdom = new_kingdom
        if new_kingdom not in self.smo_kingdoms_visited:
            self.smo_kingdoms_visited.append(new_kingdom)
        self.smo_kingdom_dwell.setdefault(new_kingdom, 0.0)
        self.last_kingdom_switch_time = self.clock + travel_cost
        # The travel cost is taken out of the *next* check schedule by pushing
        # the clock forward when we schedule.
        if travel_cost > 0:
            self.clock += travel_cost

    def _completion_for(self, kingdom: str) -> float:
        total = self.smo_total_per_kingdom.get(kingdom, 0)
        if total == 0:
            return 0.0
        done = sum(
            1 for p in self.smo_locations
            if kingdom_of_location(p.location) == kingdom and p.location in self.smo_checked
        )
        return done / total

    def _schedule_next_smo_check(self) -> None:
        dt = sample_kingdom_time(self.time_profile, self.smo_current_kingdom, self.rng)
        self._push(self.clock + dt, EV_SMO_CHECK_DONE, {})

    def _update_bk_window(self, now: float) -> None:
        in_bk = self.in_bk_fallback and (now - self.last_progression_time) > 30
        if in_bk and self._soft_bk_open is None:
            self._soft_bk_open = (now, self.smo_current_kingdom)
        elif not in_bk and self._soft_bk_open is not None:
            start, k = self._soft_bk_open
            if now - start >= self.bk_threshold_sec:
                self.soft_bk_intervals.append((start, now, k))
            self._soft_bk_open = None

    def _coplayer(self, slot: str):
        for cp in self.coplayers:
            if cp.slot == slot:
                return cp
        return None

    def _push(self, time: float, kind: str, payload: dict) -> None:
        self.seq += 1
        heapq.heappush(self.heap, _Event(time, self.seq, kind, payload))


@dataclass
class _CoplayerRuntime:
    slot: str
    profile: CoplayerProfile
    smo_bound_queue: list[SpherePlacement]
    total_checks: int
    checks_made: int


# --- Public driver --------------------------------------------------------

def run_one(
    spoiler: SpoilerData,
    time_profile: dict[str, KingdomTime],
    coplayer_specs: list[tuple[CoplayerProfile, str | None]],
    *,
    sim_seed: int,
    time_cap_sec: float = 80 * 3600,
    bk_threshold_sec: float = DEFAULT_BK_THRESHOLD_SEC,
    travel_cost_sec: float = DEFAULT_TRAVEL_SEC,
    delivery_delay_sec: float = DEFAULT_DELIVERY_DELAY_SEC,
) -> SimResult:
    """Run one timeline simulation."""
    rng = random.Random(sim_seed)
    sim = _Simulator(
        spoiler=spoiler,
        time_profile=time_profile,
        coplayer_specs=[(p, s or "") for p, s in coplayer_specs],
        rng=rng,
        time_cap_sec=time_cap_sec,
        bk_threshold_sec=bk_threshold_sec,
        travel_cost_sec=travel_cost_sec,
        delivery_delay_sec=delivery_delay_sec,
        seed_id=sim_seed,
    )
    return sim.run()


def run_many(
    spoilers: list[SpoilerData],
    time_profile: dict[str, KingdomTime],
    coplayer_specs: list[tuple[CoplayerProfile, str | None]],
    *,
    base_seed: int,
    sims_per_spoiler: int,
    **run_kwargs,
) -> Iterator[SimResult]:
    """Generator: yields one SimResult per (spoiler, sim_index) pair."""
    sim_idx = 0
    for sp in spoilers:
        for _ in range(sims_per_spoiler):
            yield run_one(
                sp, time_profile, coplayer_specs,
                sim_seed=base_seed + sim_idx,
                **run_kwargs,
            )
            sim_idx += 1
