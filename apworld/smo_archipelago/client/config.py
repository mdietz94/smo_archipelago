"""Bridge configuration. Loaded from TOML, overridable via CLI/env."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ApConfig:
    host: str = ""
    port: int = 38281
    slot: str = ""
    password: str = ""
    items_handling: int = 0b111


@dataclass
class SwitchConfig:
    listen_host: str = "0.0.0.0"
    listen_port: int = 17777


@dataclass
class BridgeOptions:
    log_level: str = "INFO"
    web_tracker: bool = True
    web_port: int = 8000
    # Path to a local Archipelago checkout. The bridge needs this to import
    # CommonClient. Resolution order: this field -> SMOAP_AP_PATH env var ->
    # default `<repo>/vendor/Archipelago` (typically a git submodule).
    archipelago_path: str = ""
    # Paths to the raw-ID resolution tables. Default to the data/ siblings.
    shine_map_path: str = ""
    capture_map_path: str = ""


@dataclass
class DeathLinkOptions:
    """DeathLink (one player dies, everyone dies). Off by default."""
    enabled: bool = False


@dataclass
class ColorsConfig:
    """Maps AP item classification -> SMO per-stage shine-animation palette index.

    SMO ships a per-stage color animation for shines; we trampoline
    rs::setStageShineAnimFrame to substitute our index whenever the bridge
    has scouted the shine. Indices are stage-specific (the same number can
    map to different visual colors across kingdoms), so the defaults below
    are intentionally conservative — bump per kingdom in slot_data overrides
    later if needed.

    A palette of 0 means "leave the stage default frame untouched"; the
    Switch treats this as "no override" and runs orig() unchanged.
    """
    enabled: bool = True
    progression: int = 1
    useful: int = 2
    trap: int = 3
    filler: int = 0

    def for_classification(self, classification: str) -> int:
        """Look up the palette index for a wire-form classification string.

        Unknown strings (including None-as-empty) fall through to filler.
        """
        if classification == "progression":
            return self.progression
        if classification == "useful":
            return self.useful
        if classification == "trap":
            return self.trap
        return self.filler


@dataclass
class Config:
    ap: ApConfig = field(default_factory=ApConfig)
    switch: SwitchConfig = field(default_factory=SwitchConfig)
    bridge: BridgeOptions = field(default_factory=BridgeOptions)
    deathlink: DeathLinkOptions = field(default_factory=DeathLinkOptions)
    colors: ColorsConfig = field(default_factory=ColorsConfig)

    @classmethod
    def load(cls, path: Path | str | None) -> "Config":
        cfg = cls()
        if path is not None:
            with open(path, "rb") as f:
                raw = tomllib.load(f)
            if "ap" in raw:
                cfg.ap = ApConfig(**{**cfg.ap.__dict__, **raw["ap"]})
            if "switch" in raw:
                cfg.switch = SwitchConfig(**{**cfg.switch.__dict__, **raw["switch"]})
            if "bridge" in raw:
                cfg.bridge = BridgeOptions(**{**cfg.bridge.__dict__, **raw["bridge"]})
            if "deathlink" in raw:
                cfg.deathlink = DeathLinkOptions(**{**cfg.deathlink.__dict__, **raw["deathlink"]})
            if "colors" in raw:
                cfg.colors = ColorsConfig(**{**cfg.colors.__dict__, **raw["colors"]})

        env_password = os.environ.get("SMOAP_PASSWORD")
        if env_password:
            cfg.ap.password = env_password
        return cfg

    def apply_overrides(
        self,
        ap_addr: str | None = None,
        slot: str | None = None,
        web_tracker: bool | None = None,
        log_level: str | None = None,
        archipelago_path: str | None = None,
    ) -> None:
        if archipelago_path is not None:
            self.bridge.archipelago_path = archipelago_path
        if ap_addr:
            host, _, port = ap_addr.partition(":")
            self.ap.host = host
            if port:
                self.ap.port = int(port)
        if slot is not None:
            self.ap.slot = slot
        if web_tracker is not None:
            self.bridge.web_tracker = web_tracker
        if log_level is not None:
            self.bridge.log_level = log_level
