import logging
import typing
from pathlib import Path
from typing import Callable, Optional

import Utils
import settings
from worlds.generic.Rules import forbid_items_for_player
from worlds.LauncherComponents import (
    Component,
    SuffixIdentifier,
    components,
    Type,
    launch as launch_or_subprocess,
)

from .Data import item_table, location_table, region_table, category_table, meta_table
from .Game import game_name, filler_item_name, starting_items
from .Meta import world_description, world_webworld, enable_region_diagram
from .Locations import location_id_to_name, location_name_to_id, location_name_to_location, location_name_groups, victory_names
from .Items import item_id_to_name, item_name_to_id, item_name_to_item, item_name_groups
from .DataValidation import runGenerationDataValidation, runPreFillDataValidation

from .Regions import create_regions
from .Items import SMOItem
from .Rules import set_rules
from .Options import meatballs_options_data
from .Helpers import is_option_enabled, is_item_enabled, get_option_value

from BaseClasses import ItemClassification, Tutorial, Item
from Options import PerGameCommonOptions
from worlds.AutoWorld import World, WebWorld

from .hooks.World import \
    before_create_regions, after_create_regions, \
    before_create_items_starting, before_create_items_filler, after_create_items, \
    before_create_item, after_create_item, \
    before_set_rules, after_set_rules, \
    before_generate_basic, after_generate_basic, \
    before_fill_slot_data, after_fill_slot_data, before_write_spoiler
from .hooks.Data import hook_interpret_slot_data

class SMOSettings(settings.Group):
    """SMO Client settings. Lives in `~/.archipelago/host.yaml` under the
    `meatballs_options:` key (Archipelago derives this from the apworld
    zip's stem — our zip is `meatballs.apworld`, so the loaded module is
    `worlds.meatballs` and the settings key is `meatballs_options`). NOT
    the AP game name ("Spicy Meatball Overdrive") and NOT the in-repo
    source folder name (`smo_archipelago/`). Auto-created with defaults
    on first load; users edit to override.

    Example yaml block:

      meatballs_options:
        switch_listen_host: "0.0.0.0"
        switch_listen_port: 17777
        deathlink_default: false
    """

    class SwitchListenHost(str):
        """Bind address for the Switch TCP server (default 0.0.0.0)."""

    class SwitchListenPort(int):
        """Port for the Switch TCP server (default 17777). The Switch mod
        is built against this — change the mod's BRIDGE_HOST/PORT too."""

    class ShineMapPath(settings.UserFilePath):
        """Path to a custom shine_map.json. Default empty falls back to
        apworld/smo_archipelago/client/data/shine_map.json. Generated
        per-machine by scripts/extract_shine_map.py — see
        docs/extract-moon-data.md."""
        description = "shine_map.json"

    class CaptureMapPath(settings.UserFilePath):
        """Path to a custom capture_map.json. Default empty falls back to
        apworld/smo_archipelago/client/data/capture_map.json. Generated
        alongside shine_map.json."""
        description = "capture_map.json"

    switch_listen_host: SwitchListenHost = SwitchListenHost("0.0.0.0")
    switch_listen_port: SwitchListenPort = SwitchListenPort(17777)
    shine_map_path: ShineMapPath = ShineMapPath("")
    capture_map_path: CaptureMapPath = CaptureMapPath("")
    deathlink_default: typing.Union[settings.Bool, bool] = False


class SMOWorld(World):
    __doc__ = world_description
    game: str = game_name
    web = world_webworld

    options_dataclass = meatballs_options_data
    settings: typing.ClassVar[SMOSettings]
    data_version = 2
    required_client_version = (0, 3, 4)

    # These properties are set from the imports of the same name above.
    item_table = item_table
    location_table = location_table # this is likely imported from Data instead of Locations because the Game Complete location should not be in here, but is used for lookups
    category_table = category_table

    item_id_to_name = item_id_to_name
    item_name_to_id = item_name_to_id
    item_name_to_item = item_name_to_item
    item_name_groups = item_name_groups

    item_counts = {}
    start_inventory = {}

    location_id_to_name = location_id_to_name
    location_name_to_id = location_name_to_id
    location_name_to_location = location_name_to_location
    location_name_groups = location_name_groups
    victory_names = victory_names

    def interpret_slot_data(self, slot_data: dict[str, any]):
        #this is called by tools like UT

        regen = False
        for key, value in slot_data.items():
            if key in self.options_dataclass.type_hints:
                getattr(self.options, key).value = value
                regen = True

        regen = hook_interpret_slot_data(self, self.player, slot_data) or regen
        return regen

    @classmethod
    def stage_assert_generate(cls, multiworld) -> None:
        runGenerationDataValidation()


    def create_regions(self):
        before_create_regions(self, self.multiworld, self.player)

        create_regions(self, self.multiworld, self.player)

        location_game_complete = self.multiworld.get_location(victory_names[get_option_value(self.multiworld, self.player, 'goal')], self.player)
        location_game_complete.address = None

        for unused_goal in [self.multiworld.get_location(name, self.player) for name in victory_names if name != location_game_complete.name]:
            unused_goal.parent_region.locations.remove(unused_goal)

        location_game_complete.place_locked_item(
            SMOItem("__Victory__", ItemClassification.progression, None, player=self.player))

        after_create_regions(self, self.multiworld, self.player)

    def create_items(self):
        # Generate item pool
        pool = []
        traps = []
        configured_item_names = self.item_id_to_name.copy()

        for name in configured_item_names.values():
            if name == "__Victory__": continue
            if name == filler_item_name: continue

            item = self.item_name_to_item[name]
            item_count = int(item.get("count", 1))

            if item.get("trap"):
                traps.append(name)

            if "category" in item:
                if not is_item_enabled(self.multiworld, self.player, item):
                    item_count = 0

            if item_count == 0: continue

            for i in range(item_count):
                new_item = self.create_item(name)
                pool.append(new_item)

            if item.get("early"): # only early
                self.multiworld.early_items[self.player][name] = item_count
            if item.get("local"): # only local
                if name not in self.multiworld.local_items[self.player].value:
                    self.options.local_items.value.add(name)

        pool = before_create_items_starting(pool, self, self.multiworld, self.player)

        items_started = []

        if starting_items:
            for starting_item_block in starting_items:
                # if there's a condition on having a previous item, check for any of them
                # if not found in items started, this starting item rule shouldn't execute, and check the next one
                if "if_previous_item" in starting_item_block:
                    matching_items = [item for item in items_started if item.name in starting_item_block["if_previous_item"]]

                    if len(matching_items) == 0:
                        continue

                # start with the full pool of items
                items = pool

                # if the setting lists specific item names, limit the items to just those
                if "items" in starting_item_block:
                    items = [item for item in pool if item.name in starting_item_block["items"]]

                # if the setting lists specific item categories, limit the items to ones that have any of those categories
                if "item_categories" in starting_item_block:
                    items_in_categories = [item["name"] for item in self.item_name_to_item.values() if "category" in item and len(set(starting_item_block["item_categories"]).intersection(item["category"])) > 0]
                    items = [item for item in pool if item.name in items_in_categories]

                self.random.shuffle(items)

                # if the setting lists a specific number of random items that should be pulled, only use a subset equal to that number
                if "random" in starting_item_block:
                    items = items[0:starting_item_block["random"]]

                for starting_item in items:
                    items_started.append(starting_item)
                    self.multiworld.push_precollected(starting_item)
                    pool.remove(starting_item)

        self.start_inventory = {i.name: items_started.count(i) for i in items_started}

        pool = before_create_items_filler(pool, self, self.multiworld, self.player)
        pool = self.adjust_filler_items(pool, traps)
        pool = after_create_items(pool, self, self.multiworld, self.player)

        # need to put all of the items in the pool so we can have a full state for placement
        # then will remove specific item placements below from the overall pool
        self.multiworld.itempool += pool

    def create_item(self, name: str) -> Item:
        name = before_create_item(name, self, self.multiworld, self.player)

        item = self.item_name_to_item[name]
        classification = ItemClassification.filler

        if "trap" in item and item["trap"]:
            classification = ItemClassification.trap

        if "useful" in item and item["useful"]:
            classification = ItemClassification.useful

        if "progression" in item and item["progression"]:
            classification = ItemClassification.progression

        if "progression_skip_balancing" in item and item["progression_skip_balancing"]:
            classification = ItemClassification.progression_skip_balancing

        item_object = SMOItem(name, classification,
                        self.item_name_to_id[name], player=self.player)

        item_object = after_create_item(item_object, self, self.multiworld, self.player)

        return item_object

    def set_rules(self):
        before_set_rules(self, self.multiworld, self.player)

        set_rules(self, self.multiworld, self.player)

        after_set_rules(self, self.multiworld, self.player)

    def generate_basic(self):
        before_generate_basic(self, self.multiworld, self.player)

        # Handle item forbidding
        forbid_locations_map = {location['name']: location for location in location_name_to_location.values() if "dont_place_item" in location or "dont_place_item_category" in location}
        locations_with_forbid = [l for l in self.multiworld.get_unfilled_locations(player=self.player) if l.name in forbid_locations_map.keys()]
        for location in locations_with_forbid:
            location_data = forbid_locations_map[location.name]
            forbidden_item_names = []

            if "dont_place_item" in location_data:
                if len(location_data["dont_place_item"]) == 0:
                    continue

                forbidden_item_names.extend([i["name"] for i in item_name_to_item.values() if i["name"] in location_data["dont_place_item"]])

            if "dont_place_item_category" in location_data:
                if len(location_data["dont_place_item_category"]) == 0:
                    continue

                forbidden_item_names.extend([i["name"] for i in item_name_to_item.values() if "category" in i and set(i["category"]).intersection(location_data["dont_place_item_category"])])

            if len(forbidden_item_names) > 0:
                forbid_items_for_player(location, forbidden_item_names, self.player)
                forbidden_item_names.clear()

        # Handle specific item placements using fill_restrictive
        placement_locations_map = {location['name']: location for location in location_name_to_location.values() if "place_item" in location or "place_item_category" in location}
        locations_with_placements = [l for l in self.multiworld.get_unfilled_locations(player=self.player) if l.name in placement_locations_map.keys()]
        for location in locations_with_placements:
            location_data = placement_locations_map[location.name]
            eligible_items = []

            if "place_item" in location_data:
                if len(location_data["place_item"]) == 0:
                    continue

                eligible_items = [item for item in self.multiworld.itempool if item.name in location_data["place_item"] and item.player == self.player]

                if len(eligible_items) == 0:
                    raise Exception("Could not find a suitable item to place at %s. No items that match %s." % (location_data["name"], ", ".join(location_data["place_item"])))

            if "place_item_category" in location_data:
                if len(location_data["place_item_category"]) == 0:
                    continue

                eligible_item_names = [i["name"] for i in item_name_to_item.values() if "category" in i and set(i["category"]).intersection(location_data["place_item_category"])]
                eligible_items = [item for item in self.multiworld.itempool if item.name in eligible_item_names and item.player == self.player]

                if len(eligible_items) == 0:
                    raise Exception("Could not find a suitable item to place at %s. No items that match categories %s." % (location_data["name"], ", ".join(location_data["place_item_category"])))

            if "dont_place_item" in location_data:
                if len(location_data["dont_place_item"]) == 0:
                    continue

                eligible_items = [item for item in eligible_items if item.name not in location_data["dont_place_item"]]

                if len(eligible_items) == 0:
                    raise Exception("Could not find a suitable item to place at %s. No items that match placed_items(_category) because of forbidden %s." % (location_data["name"], ", ".join(location_data["dont_place_item"])))

            if "dont_place_item_category" in location_data:
                if len(location_data["dont_place_item_category"]) == 0:
                    continue

                forbidden_item_names = [i["name"] for i in item_name_to_item.values() if "category" in i and set(i["category"]).intersection(location_data["dont_place_item_category"])]

                eligible_items = [item for item in eligible_items if item.name not in forbidden_item_names]

                if len(eligible_items) == 0:
                    raise Exception("Could not find a suitable item to place at %s. No items that match placed_items(_category) because of forbidden categories %s." % (location_data["name"], ", ".join(location_data["dont_place_item_category"])))
                forbidden_item_names.clear()


            # if we made it here and items is empty, then we encountered an unknown issue... but also can't do anything to place, so error
            if len(eligible_items) == 0:
                raise Exception("Custom item placement at location %s failed." % (location_data["name"]))

            item_to_place = self.random.choice(eligible_items)
            location.place_locked_item(item_to_place)

            # remove the item we're about to place from the pool so it isn't placed twice
            self.multiworld.itempool.remove(item_to_place)


        after_generate_basic(self, self.multiworld, self.player)

        # Enable this in meta.json to generate a diagram of regions/locations. Only works on 0.4.4+
        if enable_region_diagram:
            from Utils import visualize_regions
            visualize_regions(self.multiworld.get_region("Menu", self.player), f"{self.game}_{self.player}.puml")

    def pre_fill(self):
        # DataValidation after all the hooks are done but before fill
        runPreFillDataValidation(self, self.multiworld)

    def fill_slot_data(self):
        slot_data = before_fill_slot_data({}, self, self.multiworld, self.player)

        # slot_data["DeathLink"] = bool(self.multiworld.death_link[self.player].value)
        common_options = set(PerGameCommonOptions.type_hints.keys())
        for option_key, _ in self.options_dataclass.type_hints.items():
            if option_key in common_options:
                continue
            slot_data[option_key] = get_option_value(self.multiworld, self.player, option_key)

        slot_data = after_fill_slot_data(slot_data, self, self.multiworld, self.player)

        return slot_data

    def generate_output(self, output_directory: str):
        # `.meatballsap` is the only per-player artifact this apworld ships. It's
        # the entry point the Launcher routes to launch_smo_client when
        # double-clicked, triggering either the first-run wizard or a
        # pre-filled SMOClient launch. See _setup/smoap_file.py for schema.
        #
        # server_address is intentionally empty: the generator doesn't know
        # where the user will host (could be local, archipelago.gg, a
        # friend's box, ...). SMOClient prompts via the GUI Connect bar
        # when it's empty; the user can manually set it post-gen by editing
        # the file if they want a perpetual default.
        base = self.multiworld.get_out_file_name_base(self.player)
        from ._setup.smoap_file import SmoapFile
        smoap = SmoapFile(
            slot_name=self.multiworld.get_player_name(self.player),
            seed_name=str(getattr(self.multiworld, "seed_name", "") or ""),
            server_address="",
        )
        smoap.write(Path(output_directory) / f"{base}.meatballsap")

    def write_spoiler(self, spoiler_handle):
        before_write_spoiler(self, self.multiworld, spoiler_handle)

    ###
    # Non-standard AP world methods
    ###

    def add_filler_items(self, item_pool, traps):
        Utils.deprecate("Use adjust_filler_items instead.")
        return self.adjust_filler_items(item_pool, traps)

    def adjust_filler_items(self, item_pool, traps):
        extras = len(self.multiworld.get_unfilled_locations(player=self.player)) - len(item_pool)

        if extras > 0:
            trap_percent = get_option_value(self.multiworld, self.player, "filler_traps")
            if not traps:
                trap_percent = 0

            trap_count = extras * trap_percent // 100
            filler_count = extras - trap_count

            for _ in range(0, trap_count):
                extra_item = self.create_item(self.random.choice(traps))
                item_pool.append(extra_item)

            for _ in range(0, filler_count):
                extra_item = self.create_item(filler_item_name)
                item_pool.append(extra_item)
        elif extras < 0:
            logging.warning(f"{self.game} has more items than locations. {abs(extras)} non-progression items will be removed at random.")
            fillers = [item for item in item_pool if item.classification == ItemClassification.filler]
            traps = [item for item in item_pool if item.classification == ItemClassification.trap]
            useful = [item for item in item_pool if item.classification == ItemClassification.useful]
            self.random.shuffle(fillers)
            self.random.shuffle(traps)
            self.random.shuffle(useful)
            for _ in range(0, abs(extras)):
                popped = None
                if fillers:
                    popped = fillers.pop()
                elif traps:
                    popped = traps.pop()
                elif useful:
                    popped = useful.pop()
                else:
                    logging.warning("Could not remove enough non-progression items from the pool.")
                    break
                item_pool.remove(popped)

        return item_pool

    def get_item_counts(self, player: Optional[int] = None, reset: bool = False) -> dict[str, int]:
        """returns the player real item count"""
        if player is None:
            player = self.player
        if not self.item_counts.get(player, {}) or reset:
            real_pool = self.multiworld.get_items()
            self.item_counts[player] = {i.name: real_pool.count(i) for i in real_pool if i.player == player}
        return self.item_counts.get(player)

###
# Non-world client methods
###

from ._setup.launcher_errors import visible_errors as _visible_errors


@_visible_errors("SMO Client launcher")
def launch_smo_client(*args):
    """Archipelago Launcher entry point for the SMO Client (real Switch).

    Triggered by double-clicking a `.meatballsap` file (the Component's
    `SuffixIdentifier('.meatballsap')` registers the extension globally) or by
    clicking the "SMO Client" button directly. Always launches SMOClient;
    when a `.meatballsap` is provided its slot_name / server_address / password
    are expanded into CLI overrides so the Connect bar lands pre-filled.

    The setup wizard (toolchain install, NSP extract, mod build + deploy)
    is no longer auto-triggered here. Users invoke it via the `/setup`
    slash command inside SMOClient — that path covers both first-time
    setup and re-runs (bridge IP changed, apworld updated, switching
    deploy target, ...).

    Kept lazy-importing CommonClient / Kivy so headless gen hosts that
    never touch this function don't pay the import cost.
    """
    smoap_path = next((a for a in args if a.endswith(".meatballsap")), None)
    final_args = list(args)
    if smoap_path:
        try:
            from ._setup.smoap_file import parse_smoap, smoap_to_launch_args
            s = parse_smoap(Path(smoap_path))
            # Drop the .meatballsap arg itself (SMOClient's argparser doesn't
            # know about it) and prepend the expanded credentials.
            final_args = [a for a in final_args if not a.endswith(".meatballsap")]
            final_args = smoap_to_launch_args(s) + final_args
        except Exception as e:
            # Don't block the launch — log and let SMOClient open with no
            # pre-fill so the user can connect manually.
            logging.getLogger(__name__).warning(
                "could not parse %s: %s; launching SMOClient without pre-fill",
                smoap_path, e,
            )
            final_args = [a for a in final_args if not a.endswith(".meatballsap")]

    _run_smo_client_with_args(*final_args)


@_visible_errors("SMOClient subprocess bootstrap")
def _run_smo_client_with_args(*args: str) -> None:
    """Module-level subprocess entry: launch SMOClient with given args.

    Top-level callable (not a closure) so the subprocess-pickling path can
    reach it by qualified name. Used by both `launch_smo_client` (for the
    setup-done path) and by the wizard's "Launch SMOClient now" button (for
    the setup-just-finished path).

    Uses `launch` (not `launch_subprocess`) so a file-association invocation
    — where no Launcher GUI Kivy is yet running — boots SMOClient inline in
    the main Launcher process. Spawning via `multiprocessing.Process` from
    inside a PyInstaller-frozen ArchipelagoLauncher.exe yields a child whose
    Kivy can't read its bundled `style.kv` out of `library.zip`."""
    from .client.main import launch as smoclient_launch
    launch_or_subprocess(smoclient_launch, name="SMOClient", args=args)


@_visible_errors("Setup wizard")
def _run_setup_wizard_no_smoap() -> None:
    """Module-level subprocess entry: open the setup wizard.

    Invoked by the `/setup` slash command in SMOClient (which goes
    through `launch_subprocess` so SMOClient stays open while the
    wizard runs in its own window). The wizard handles first-time
    setup and re-runs alike — bridge IP changes, apworld updates,
    switching deploy targets."""
    from ._setup.wizard import run_setup_wizard
    run_setup_wizard()


def add_client_to_launcher() -> None:
    """Register the "SMO Client" Component with the Archipelago Launcher.

    Idempotent: re-importing this module (e.g. AP's apworld autodiscover
    can call us more than once across reloads) won't create duplicates."""
    for c in components:
        if c.display_name == "SMO Client":
            return
    components.append(Component(
        "SMO Client",
        func=launch_smo_client,
        component_type=Type.CLIENT,
        file_identifier=SuffixIdentifier('.meatballsap'),
        game_name=game_name,
    ))


add_client_to_launcher()
