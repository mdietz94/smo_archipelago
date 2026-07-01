-- SMO Archipelago — access-rule helpers.
-- Each function is referenced from access_rules as "$<name>|<arg>|<arg>...".
-- Names are snake_case ports of apworld/smo_archipelago/hooks/Rules.py.
--
-- Yaml options + goal selection live in the OPTIONS table (populated by
-- autotracking.lua from slot_data on connect). Defaults match the apworld's
-- defaults so offline-mode logic is reasonable when no AP slot_data has
-- arrived yet.

OPTIONS = OPTIONS or {
  capturesanity                 = false,  -- Toggle off
  include_cap_peace_moons       = true,
  include_cascade_peace_moons   = true,
  include_sand_peace_moons      = true,
  include_lake_peace_moons      = true,
  include_wooded_peace_moons    = true,
  include_lost_peace_moons      = true,
  include_metro_peace_moons     = true,
  include_snow_peace_moons      = true,
  include_seaside_peace_moons   = true,
  include_luncheon_peace_moons  = true,
  include_bowsers_peace_moons   = true,
  include_cloud_peace_moons     = true,
  include_deep_woods_moons      = true,
  include_minigame_moons        = true,
  include_hint_art_moons        = true,
  include_tourist_moons         = true,
  include_long_course_moons     = true,
  include_precision_capture_moons = true,
  goal                          = 0,      -- first victory variant
  talkatoo_mode                 = false,
  difficult_mode                = false,  -- Toggle off
}

-- ---------- core lookups

function has_one(code)
  local obj = Tracker:FindObjectForCode(code)
  if not obj then return false end
  if obj.Active ~= nil and obj.Active == true then return true end
  if obj.AcquiredCount ~= nil and obj.AcquiredCount > 0 then return true end
  return false
end

function has_count(code, n)
  local need = tonumber(n) or 0
  if need <= 0 then return true end
  local obj = Tracker:FindObjectForCode(code)
  if not obj then return false end
  if obj.AcquiredCount ~= nil then return obj.AcquiredCount >= need end
  return false
end

-- ---------- option-toggle queries

function is_opt(name)
  return OPTIONS[name] == true
end

function is_opt_off(name)
  return OPTIONS[name] ~= true
end

function is_goal(idx)
  local n = tonumber(idx)
  if not n then return false end
  return tonumber(OPTIONS.goal) == n
end

function capturesanity_off()
  return OPTIONS.capturesanity ~= true
end

-- ---------- kingdom moon credit

-- has_kingdom_moons(kingdom, n) — PowerMoon*1 + MultiMoon*3 >= n. Mirrors
-- Rules.py KingdomMoons (and the M6 phase-A in-game counter on the Switch).
function has_kingdom_moons(kingdom, n)
  local need = tonumber(n) or 0
  if need <= 0 then return true end
  local k = string.lower(kingdom)
  -- "Bowser's" -> "bowser_s" via the generator's code_for transform
  k = string.gsub(k, "[^a-z0-9]+", "_")
  k = string.gsub(k, "^_+", "")
  k = string.gsub(k, "_+$", "")
  local pm = Tracker:FindObjectForCode(k .. "_kingdom_power_moon")
  local mm = Tracker:FindObjectForCode(k .. "_kingdom_multi_moon")
  local pm_c = (pm and pm.AcquiredCount) or 0
  local mm_c = (mm and mm.AcquiredCount) or 0
  return (pm_c + 3 * mm_c) >= need
end

-- ---------- Rules.py ports (capturesanity-conditional and trivial)

function sand_peace()
  if capturesanity_off() then return true end
  return has_one("bullet_bill") and has_one("knucklotec_s_fist")
end

function lake_peace()  return true end

function wooded_peace()
  if capturesanity_off() then return true end
  return has_one("uproot")
end

function metro_peace()
  if capturesanity_off() then return true end
  return has_one("sherm") and has_one("manhole")
end

function snow_peace()
  if capturesanity_off() then return true end
  return has_one("ty_foo") and has_one("shiverian_racer")
end

function seaside_peace()
  if capturesanity_off() then return true end
  return has_one("gushen")
end

function snow_seaside_peace()
  if capturesanity_off() then return true end
  return (has_one("ty_foo") and has_one("shiverian_racer")) or has_one("gushen")
end

function luncheon_peace()
  if capturesanity_off() then return true end
  return has_one("hammer_bro") and has_one("meat") and has_one("lava_bubble")
end

function bowser_peace()
  if capturesanity_off() then return true end
  return has_one("pokio")
end

function post_night_metro()
  if capturesanity_off() then return true end
  return has_one("sherm")
end

function post_trumpeter()
  if capturesanity_off() then return true end
  return has_one("sherm")
end

function regional_cap()
  if capturesanity_off() then return true end
  return has_one("paragoomba")
end

function regional_cascade() return true end

function regional_sand()
  if capturesanity_off() then return true end
  return has_one("bullet_bill") and has_one("knucklotec_s_fist")
         and has_one("mini_rocket") and has_one("goomba")
end

function regional_lake()
  if capturesanity_off() then return true end
  return has_one("zipper")
end

function regional_wooded()
  if capturesanity_off() then return true end
  return has_one("sherm") and has_one("uproot") and has_one("boulder")
end

function regional_lost()
  if capturesanity_off() then return true end
  return has_one("wall_jump")  -- "Wall Jump" is a category not an item; degrades to false in current data
end

function regional_metro()
  if capturesanity_off() then return true end
  return has_one("manhole") and has_one("mini_rocket")
end

function regional_snow()
  if capturesanity_off() then return true end
  return has_one("ty_foo") and has_one("goomba")
end

function regional_seaside()
  if capturesanity_off() then return true end
  return has_one("gushen")
end

function regional_luncheon()
  if capturesanity_off() then return true end
  return has_one("hammer_bro") and has_one("volbonan")
         and has_one("meat") and has_one("lava_bubble")
end

function regional_bowser()
  if capturesanity_off() then return true end
  return has_one("pokio")
end

function regional_moon()
  if capturesanity_off() then return true end
  return has_one("parabones") and has_one("tropical_wiggler")
         and has_one("banzai_bill") and has_one("sherm")
end

function meat()
  if capturesanity_off() then return true end
  return has_one("hammer_bro") and has_one("meat")
end

function uproot_or_fire_bro()
  if capturesanity_off() then return true end
  return has_one("uproot") or has_one("fire_bro")
end

function lighthouse()
  if capturesanity_off() then return true end
  return has_one("gushen") or has_one("cheep_cheep")
end

function difficult_mode()
  return is_opt("difficult_mode")
end

function lake_difficult()
  return lake_peace() or difficult_mode()
end

function wooded_difficult()
  return difficult_mode() or capturesanity_off() or has_one("uproot")
end

-- Trivial helpers (currently always true in Rules.py; tightening these
-- in the apworld will flow through here without regenerating the pack).
function bullet_bill_skip()      return true end
function bullet_bill_small_skip() return true end
function bullet_bill_maze()      return true end
function into_the_lake()         return true end
function swim_or_cheep_cheep()   return true end
function swim_or_cap_jump()      return true end
function cheep_cheep_or_ground_pound() return true end
function maze_skip()             return true end
function sherm_or_long_jump()    return true end
function from_the_top_of_the_tower() return true end
function wall_jump_or_pole()     return true end
function tyfoo_or_scale_a_tall_wall() return true end
function post_early_luncheon()   return true end
function climb_to_the_meat()     return true end
function jump_high()             return true end
function scale_a_wall()          return true end
function scale_a_wall_no_triple_jump() return true end
function nice_frame()            return true end
function parabones_skip()        return true end

-- ItemValue("coins:N") — value-cache for the apworld's coin counter. Not
-- exercised by current locations.json; returns false so any future use
-- surfaces as out-of-logic until the value-tracking is wired up.
function item_value(spec)
  return false
end
