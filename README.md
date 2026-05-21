# Spicy Meatball Overdrive

An Archipelago client for **Super Mario Odyssey** on a modded Nintendo Switch.

This project provides an in-game module that:

- Detects moons / captures / scenario events on Switch and reports them as AP location checks.
- Receives AP items (moons, captures, kingdoms) and applies them to the live game.
- Enforces capture locks (cannot possess Frog / Yoshi / T-Rex / etc. until the AP item is received).
- Surfaces progress through a tracker tab in SMO Client and an in-game HUD overlay.

> ⚠️ **Status: pre-alpha.** Core gameplay loop works end-to-end (Ryujinx and real Switch), but rough edges remain — see [the open issues](../../issues) before joining a serious multiworld.

**Community:** join the [Discord](https://discord.gg/DQDzYjJdn3) for setup help, multiworld matchmaking, and bug reports.

## Requirements

- **Super Mario Odyssey 1.0.0**
- **Switch firmware 21.x or 22, with Atmosphere CFW** — or an **emulator**
- **Windows PC** on the same LAN as the Switch
- **Archipelago, Python 3.12, LLVM 19, msys2 mingw64 g++, CMake, Ninja, hactool, prod.keys**

See [`docs/first-time-setup.md`](docs/first-time-setup.md) for the full prereq table with install links.

## Installation

1. **Download `meatballs.apworld`** from the [Releases page](../../releases).
2. **Drop it into your Archipelago install's `custom_worlds/`** directory.
3. **Open the Archipelago Launcher and click "SMO Client"** in the Clients list.
4. **Run `/setup`** in the SMO Client command bar. The setup wizard walks you through prereq checks → SMO NSP pick → moon/capture extraction → Switch-mod compile → deploy to SD card (or Ryujinx). You only need to do this **once per machine** (or again after upgrading to a new SMO Archipelago release). Your PC's LAN IP is auto-detected and baked in as a fallback; runtime UDP discovery handles routine IP changes automatically.
5. **Boot SMO.** The mod loads on game start and dials your PC every couple seconds until SMO Client is listening.
6. **Join a multiworld.** Type the host/port and your slot name into the Connect bar in SMO Client and click *Connect* — exactly like any other Archipelago client.

> ⚠️ **Start a new save before opening SMO Client.** The Switch mod talks to SMO Client as soon as the client is listening — well before you click *Connect* — and any moon/capture/scenario event the game fires from that moment on is a candidate to be reported as a fresh check. Loading a save with prior progress can replay state-restore events that look identical to fresh collects. Boot SMO and start a new game first, then open SMO Client.

Detailed walkthrough: [`docs/first-time-setup.md`](docs/first-time-setup.md).

After setup, joining additional multiworlds is the same as any other Archipelago client — open SMO Client from the Archipelago Launcher and connect to the AP server. No rebuild required when changing host, slot, password, or apworld version.

## How the game plays differently from vanilla SMO

A few behaviors only make sense once you know what the mod is doing. The
short version: **moons and captures aren't yours until AP gives them to
you**, and the in-game UI is your honest indicator of what AP has sent.

### Goal: arrive in the Mushroom Kingdom

The win condition is the same as vanilla SMO's main-story ending —
**defeat Bowser on the Moon Kingdom, complete the spark-pylon escape
sequence, and watch the post-wedding cutscene drop Mario in the
Mushroom Kingdom**. There's no completion Power Moon to collect; the
moment Mario touches down outside Peach's Castle your slot is marked
complete.

### How do I know it is working?

The earliest in-game signal is **Cappy himself**. Shortly after you
acquire him in the Cap Kingdom intro, Cappy should pop a speech bubble
that reads *"Connected to Archipelago"* — that's the mod confirming the
Switch ↔ SMO Client ↔ AP-server chain is live. From that point on Cappy
will narrate item arrivals from other players (e.g. *"Got Frog from
P3!"*), and will also announce *"Disconnected from Archipelago"* if
SMO Client drops, replaying anything you collected during the gap once
it reconnects.

If you never see the "Connected" bubble after Cappy joins you, check
SMO Client's Tracker tab and the AP-server log — SMO Client isn't
reaching the Switch.

### Your Capture List is the source of truth for what you can capture

Cappy's in-game Capture List (the menu showing every hat-throw target
you've ever met) doubles as your **AP unlock list**. If a creature
appears there, AP has sent you its capture item and you can use it
freely. If it isn't there yet, you haven't been granted it.

You can still *try* to capture anything — see below.

### Captures you don't own snap back after ~4 seconds

Hat-toss onto a capture you have not unlocked and you'll briefly play
as the creature (Frog, Bullet Bill, T-Rex, ...) — then Mario gets yanked
back out and the enemy may despawn.

### Linear kingdom order is enforced at the two world-map forks

The apworld ships a linear kingdom chain, and the Switch mod backs it up
at SMO's two world-map fork points:

| Fork | You must collect first | Before you can go to |
| --- | --- | --- |
| After Sand Kingdom | 8 Lake Kingdom moons (AP credit) | Wooded Kingdom |
| After Metro Kingdom | 10 Snow Kingdom moons (AP credit) | Seaside Kingdom |

While a fork is gated, **both slots on the cutscene world-select will
show the prereq kingdom** (e.g. both "Lake") — pick either and you fly
to Lake. Once you have the required moons, the fork shows its real
options.

### Moons appear in the kingdom they're for

SMO's in-game moon counter (the HUD number and the Odyssey ship's fuel
gauge) only ever shows the **AP-credit balance for the kingdom you're
currently in**. Moons AP has sent you for other kingdoms are waiting
silently — fly to that kingdom and the counter will reflect them.

Practical consequences:

- Pre-existing moons from before you connected to AP won't show in the
  HUD counter. They still exist in the shine list, but they don't fund
  travel — only AP-granted moons do.
- The Odyssey ship will refuse to launch if your current-kingdom
  AP-credit balance is below the cost.
- The tracker tab in SMO Client (and the PopTracker pack, if you're
  using it) is the canonical view of what you have everywhere.

### Collecting a moon for a different kingdom: HUD blips down, then back up

When you pick up a local moon and AP routes it to another kingdom (or
to another player), you may see the HUD counter briefly tick down and
then bounce back. This is the AP-credit-only counter recomputing
around the deposit cycle, and it's **working correctly** — the
underlying balance is unchanged. Watch the moon-get cutscene's label:
it shows you exactly where the moon went (e.g. "Sent Snow Kingdom Power
Moon -> P3").

### Changing AP server or slot after setup

**Doesn't require a rebuild.** Open SMO Client from the Archipelago
Launcher and connect like any other AP client. Works for any multiworld
you join, as long as the SMO mod on your Switch matches the SMO
Archipelago version the seed was generated against.
See [`docs/changing-servers.md`](docs/changing-servers.md) for the full
rebuild-vs-no-rebuild matrix.

### Changing your PC's LAN IP

**Usually handled automatically.** The Switch mod probes for SMO Client
over UDP on every boot (loopback → LAN broadcast → baked-in fallback), so
a new DHCP lease or moving between LANs typically Just Works. Only re-run
`/setup` if UDP discovery is blocked on your new network (firewall,
corporate LAN) AND the baked-in fallback IP is no longer reachable.

## Credits

- [empathy-mp3](https://github.com/empathy-mp3/SMO-manual-AP) — upstream apworld this fork descends from.
- [fruityloops1](https://github.com/fruityloops1/LibHakkun) — LibHakkun subsdk runtime.
- [MonsterDruide1](https://github.com/MonsterDruide1/OdysseyHeaders) — OdysseyHeaders SMO type layouts.
- [Amethyst-szs](https://github.com/Amethyst-szs/smo-lunakit) — LunaKit (referenced for SMO modding context).
- [ArchipelagoMW](https://github.com/ArchipelagoMW/Archipelago) — Archipelago.

## License

See [LICENSE](LICENSE).

---

**For contributors:** project architecture, milestone status, build/test workflows, and the wire protocol live in [`CLAUDE.md`](CLAUDE.md), [`docs/architecture.md`](docs/architecture.md), [`docs/wire-protocol.md`](docs/wire-protocol.md), and [`docs/milestones.md`](docs/milestones.md). Project skills under `.claude/skills/` cover the build (`smo-build`), loopback test (`smo-loopback-test`), C++ host tests (`smo-host-tests`), symbol discovery (`smo-symbol-discovery`), data extraction (`smo-extract-data`), and PopTracker pack (`smo-poptracker`).
