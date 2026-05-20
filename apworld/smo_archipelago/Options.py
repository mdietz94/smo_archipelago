from Options import FreeText, NumericOption, Toggle, DefaultOnToggle, Choice, TextChoice, Range, NamedRange, PerGameCommonOptions, DeathLink
from dataclasses import make_dataclass
from .hooks.Options import before_options_defined, after_options_defined
from .Data import category_table, game_table
from .Locations import victory_names
from .Items import item_table


class FillerTrapPercent(Range):
    """How many fillers will be replaced with traps. 0 means no additional traps, 100 means all fillers are traps."""
    range_end = 100

meatballs_options = before_options_defined({})

if len(victory_names) > 1:
    goal = {'option_' + v: i for i, v in enumerate(victory_names)}
    meatballs_options['goal'] = type('goal', (Choice,), goal)
    meatballs_options['goal'].__doc__ = "Choose your victory condition."

if any(item.get('trap') for item in item_table):
    meatballs_options["filler_traps"] = FillerTrapPercent

if game_table.get("death_link"):
    meatballs_options["death_link"] = DeathLink

for category in category_table:
    for option_name in category_table[category].get("yaml_option", []):
        if option_name[0] == "!":
            option_name = option_name[1:]
        if option_name not in meatballs_options:
            meatballs_options[option_name] = type(option_name, (DefaultOnToggle,), {"default": True})
            meatballs_options[option_name].__doc__ = "Should items/locations linked to this option be enabled?"

meatballs_options = after_options_defined(meatballs_options)
meatballs_options_data = make_dataclass('MeatballsOptionsClass', meatballs_options.items(), bases=(PerGameCommonOptions,))
