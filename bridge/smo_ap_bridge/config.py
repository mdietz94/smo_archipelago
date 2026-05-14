"""Bridge configuration. Loaded from TOML, overridable via CLI/env."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ApConfig:
    host: str = "archipelago.gg"
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


@dataclass
class Config:
    ap: ApConfig = field(default_factory=ApConfig)
    switch: SwitchConfig = field(default_factory=SwitchConfig)
    bridge: BridgeOptions = field(default_factory=BridgeOptions)

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
