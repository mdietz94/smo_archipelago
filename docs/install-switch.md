# Install on a modded Switch

> **The setup wizard handles all of this for you** — see
> [`first-time-setup.md`](first-time-setup.md). This page is the
> reference for what the wizard produces and how to do the install
> manually if you want to (or you're a developer iterating on the mod
> faster than re-running the wizard).

This assumes you already run Atmosphere CFW on a modded Switch and are familiar with copying files to the SD card. If you're new to Switch homebrew, **stop here** — set up Atmosphere first using one of the community guides; come back when you're booting custom firmware reliably.

## Game version

The module targets **Super Mario Odyssey 1.0.0** — the canonical version that every public SMO mod (lunakit, smo-online, smo-practice) and the OdysseyDecomp decompilation track. Install the 1.0.0 NSP natively (e.g. via Goldleaf or another NSP installer); do not use a runtime downgrade overlay alongside our mod. Check your version: `Settings → Data Management → Software → Super Mario Odyssey`.

## SD card layout

The setup wizard puts compiled outputs at `%APPDATA%\SMOArchipelago\build\cmake\`. Manual dev-loop builds (`cmake --install build` from `switch-mod/`) populate `sd-overlay/` with the same layout. Either way, what lands on the SD card is:

```
sd:/atmosphere/contents/0100000000010000/
  exefs/
    subsdk9                ← the AP module (Nintendo Switch executable, NSO format)
    main.npdm              ← module metadata (services, title id, etc.)
  romfs/
    ap_config.json         ← cosmetic; the mod does NOT read this at runtime
                              (retail Switch firmware blocks MountSdCardForDebug)
```

**Critical**: the path is `exefs/`, NOT `exefs_patch/`. `exefs_patch/` is for IPS-style binary patches against existing exefs files; we're adding a new file (`subsdk9` doesn't exist in stock SMO), so it must go in `exefs/`. Atmosphere silently ignores files in the wrong location.

## Configuring the bridge target

On startup the mod resolves the bridge address at runtime via UDP discovery in this order:

1. UDP probe → `127.0.0.1:17776` (covers Ryujinx-on-same-host).
2. UDP broadcast → `255.255.255.255:17776` (covers a normal home LAN).
3. UDP probe → the build-time-baked `BRIDGE_HOST:17776` (covers networks where broadcast is filtered but unicast traverses — some consumer routers, VLAN'd setups).
4. TCP fallback → `BRIDGE_HOST:17777` if every UDP probe fails.

SMOClient runs a `DiscoveryResponder` on UDP 17776; it replies with its current LAN IP so the answer is always routable. The baked-in `BRIDGE_HOST` is now only a fallback for steps 3 and 4 — most users never hit it.

The setup wizard captures your PC's LAN IP silently and bakes it as the fallback. Re-run `/setup` if your DHCP lease changes AND discovery somehow fails (e.g. firewall is dropping UDP — see Troubleshooting in [first-time-setup.md](first-time-setup.md)).

For manual / dev-loop builds:

```pwsh
cd C:\Users\maxwe\Documents\smo_archipelago
python scripts\build_switchmod.py `
    -DBRIDGE_HOST=192.168.1.187     # your PC's LAN IP (fallback only)
# Optional:
#   -DBRIDGE_PORT=17777
#   -DDISCOVERY_PORT=17776
#   -DBRIDGE_RETRY_MS=3000
#   -DBRIDGE_RECV_TIMEOUT_MS=200
```

The wrapper drives Windows-native CMake + LLVM 19 + Ninja against the Hakkun + OdysseyHeaders submodules. Editing `ap_config.json` on the SD after install has no effect — it's a documentation artifact only on retail.

### Multi-Switch setup

The bridge accepts N parallel Switch connections — real hardware + Ryujinx on the same LAN, or two real Switches if you have them. Open the Switches popup in SMOClient (click the top-bar pill next to the Connect bar) to see which Switches are connected and toggle which one is bound to the AP slot. Telemetry from inactive Switches is dropped; only the active Switch's checks forward to AP and only the active Switch receives item replays.

## Steps (manual install — the wizard automates this)

1. Power off the Switch and remove the SD card (or use the FTP / USB-mass-storage method).
2. Copy `switch-mod/sd-overlay/atmosphere/` (or `%APPDATA%\SMOArchipelago\build\cmake\` outputs into the `atmosphere/contents/0100000000010000/{exefs,romfs}/` layout above) onto the SD's `atmosphere/` directory.
3. Re-insert the SD.
4. Make sure your PC's firewall allows inbound TCP on `bridge_port` (default 17777) and inbound UDP on `discovery_port` (default 17776) from the Switch's IP. UDP 17776 is the discovery channel; TCP 17777 is the main message channel.

## Coexistence with smo-lunakit

Atmosphere only loads **one** `subsdk9` per title at a time. If you already use LunaKit, you must choose:

- **AP only**: replace LunaKit's `subsdk9` with ours.
- **LunaKit only**: don't install ours.
- **Both**: needs the M8 combined build (not yet released).

You can keep both binaries on disk — for example, save LunaKit's as `subsdk9.lunakit` and ours as `subsdk9.ap` — and rename whichever one you want active to `subsdk9`.

## M3 status — what works today

The M3 build delivers:

- A loadable `subsdk9` module that hooks 7 SMO functions (`HakoniwaSequence::drawMain`, `GameSystem::init`, `al::Scene::endInit`, `GameDataFile::setGotShine`, `PlayerHackKeeper::startHack`, `GameDataFile::setMainScenarioNo`, `GameDataFile::initializeData`). Goal detection fires when `WorldMapSelectHook` first sees Mario flown into PeachWorld (Mushroom Kingdom) via `tryChangeNextStageWithDemoWorldWarp` — vanilla SMO awards no Power Moon for clearing the main game, so Mushroom-arrival is the canonical "you've beaten Bowser" signal.
- The 4 game-event hooks are installed but no-op trampolines for M3 — symbol resolution is verified at boot, real bodies land in M4/M7.
- A worker thread that opens a TCP connection to the PC bridge, sends `HELLO`, and processes inbound items idempotently with exponential reconnect backoff.
- Bridge IP/port baked at compile time (cmake `-DBRIDGE_HOST=...`). The historical attempt to read `romfs/ap_config.json` at runtime was rolled back when `nn::fs::MountSdCardForDebug` turned out to be broken on retail firmware — fine in Ryujinx, fails on real hardware. Changing the bridge IP on a real Switch requires re-running the setup wizard.

**On-screen UI is deferred to M8.** For now, all status lives in:

- The Tracker tab in SMOClient's Kivy window (received items, kingdom progress).
- The mod log on the SD card at
  `sd:/atmosphere/contents/0100000000010000/smoap.log` — every `[smoap …]`
  line the mod produces. Tail it with your SD-card reader or via FTP.
- Atmosphere's `lm` log captures the same lines via `svcOutputDebugString`
  when enabled in `system_settings.ini`:
  ```
  [atmosphere]
  enable_log_manager = u8!0x1
  ```
  Logs land in `sd:/atmosphere/logs/` after a session.

## Bring-up

1. Launch SMOClient on the PC (Archipelago Launcher → "SMO Client", or
   double-click any `.meatballsap`).
2. Confirm the SMOClient log shows `Switch listen: 0.0.0.0:17777`.
3. Click Connect (or pre-fill via `--connect <host>:<port>`). The status
   should show "waiting for Switch" because of the two-stage gate.
4. Launch SMO on the Switch.
5. Within 1-2 seconds SMOClient should log `switch HELLO: mod=0.1.0+…
   smo=1.0.0` and AP should connect.

## Failure-mode table

| Symptom | Likely cause | Fix |
|---|---|---|
| SMO crashes on launch (boot loop or hang on splash) | NPDM/ABI mismatch, OR a hook symbol missed on 1.0.0 (we abort fail-loud) | Rebuild from clean. Check lm log for a `lookupSymbol FAILED` line naming the missing mangled symbol — cross-reference `switch-mod/syms/game/SmoApSymbols.sym` to find the entry, fix the mangle, rebuild. See the [smo-symbol-discovery skill](../.claude/skills/smo-symbol-discovery/SKILL.md). |
| No `switch HELLO` in SMOClient log | Stale bridge IP baked into `subsdk9` (PC moved networks since last setup), PC firewall blocking 17777, or NIFM not up on Switch | Type `/setup` in SMOClient to rebuild with current LAN IP; verify firewall rule (`New-NetFirewallRule -DisplayName "SMO AP" -Direction Inbound -Protocol TCP -LocalPort 17777 -Action Allow`); check mod log for `nn::nifm::Initialize failed` or `connect failed` lines |
| HELLO arrives but `hello_ack` never logged on Switch | SMOClient can't decode our HELLO (wire-protocol skew between mod and apworld versions) | SMOClient log shows the parse error; rebuild with `/setup` to refresh the mod to match the apworld |
| Switch ConnState stuck on `CONN` (heartbeat lines) | Socket connected but SMOClient not listening on the IP/port the mod was built against | Verify SMOClient is running; check `netstat -ano \| findstr 17777` on PC shows LISTENING |
| `failed to start AP client: Archipelago is not importable` in SMOClient logs | Archipelago submodule not added | `git submodule update --init --recursive` in your Archipelago checkout |
| Capture appears to work without the unlock item (M7+) | Capture-name mismatch between bridge and Switch's bit table | Re-run `python scripts/sync_capture_table.py` and rebuild |

## Coexistence with smo-lunakit

Atmosphere only loads **one** `subsdk9` per title at a time. If you already use LunaKit, you must choose:

- **AP only**: replace LunaKit's `subsdk9` (and `main.npdm`) with ours.
- **LunaKit only**: don't install ours.
- **Both**: needs the M8 combined build (not yet released).

Practical pattern: rename the inactive one to `subsdk9.lunakit` / `subsdk9.ap` on the SD card and toggle by renaming.
