# Changing servers, slots, and your PC

There are two categories of "I want to change something" — they have very
different costs. Knowing which is which saves time.

## TL;DR

| What you want to change | Rebuild required? | How |
|---|---|---|
| AP server address (host:port) | **No.** | Connect SMO Client like any other AP client. |
| AP slot name | **No.** | Same as above. |
| AP password | **No.** | Same as above. |
| Switch listen port (PC side) | **No.** | Edit `host.yaml` or pass `--switch-port`. |
| Your PC's LAN IP | **Usually no** — runtime UDP discovery finds your PC automatically. Rebuild only if discovery is broken (firewall dropping UDP, exotic network). | If you do need to rebuild: open SMO Client and type `/setup`. |
| Switching to a different PC | **Yes.** Set up on the new PC. | Run the wizard on the new PC. |

## Per-session: server, slot, password

These all live in SMO Client's runtime configuration — connect like any
other Archipelago client. The Switch mod doesn't know or care about them,
so changing them never requires a rebuild.

## Per-machine: your PC's LAN IP

In most cases, **this no longer requires a rebuild**. On boot the mod
probes for SMO Client over UDP (loopback → LAN broadcast → baked-in
fallback unicast), and SMO Client replies with its current LAN IP. As long
as your firewall lets UDP 17776 through, the Switch finds your PC
regardless of which DHCP lease or LAN it's on.

The setup wizard still bakes a fallback IP into the module at compile time
(retail Switch firmware can't read runtime config from the SD card), but
that fallback is only used if every UDP probe fails. Most users never hit
it.

### When you *do* still need to re-run setup

- You moved to a network where UDP discovery is blocked (corporate LAN,
  some VLAN'd setups, a firewall rule dropping UDP 17776) **and** the
  baked-in fallback IP is no longer reachable.
- You switched to a different PC to run SMO Client.

### Quickest path

1. Open SMO Client from the Archipelago Launcher (click "SMO Client" in
   the Clients list).
2. Type `/setup` in the command bar.
3. The wizard opens in a fresh window. Walk forward — you can usually
   skip-by-rechecking the prereqs and extraction pages (their outputs are
   cached at `%APPDATA%/SMOArchipelago/data/`). The wizard auto-detects
   your current LAN IP silently and bakes it as the new fallback.
4. Restart your Switch / Ryujinx so it picks up the new module.

### What the wizard does behind the scenes

`cmake` reconfigures with the detected IP, recompiles, and re-deploys the
resulting `subsdk9` to the same destination you picked originally (SD card
or Ryujinx). Build takes about a minute.

## Per-machine: deploy target (SD ↔ Ryujinx)

If you developed against Ryujinx and now want to play on a real Switch (or
vice versa), re-run setup and pick the other deploy target. **No rebuild
needed** — the build artifacts (`subsdk9`, `main.npdm`, `ap_config.json`)
are the same bytes for both targets. The wizard remembers your last choice
in `%APPDATA%/SMOArchipelago/setup_state.json`, so subsequent re-runs
default to that target.

## What never needs a rebuild

Nothing else does. Items, locations, regions, options, the apworld's
internal logic — those all live in the apworld zip, which is loaded fresh
every time SMO Client or AP-server starts. Update the apworld by replacing
the file in `custom_worlds/` and restarting.

A Switch-mod **wire-protocol** change (rare; new message types added
between SMO Archipelago releases) does require both an updated SMO Client
AND a re-deployed Switch module. The wizard's `/setup` flow handles both
in one shot.
