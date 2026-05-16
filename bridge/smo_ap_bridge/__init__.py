"""Spicy Meatball Overdrive bridge.

A small Python service that connects:
  - Archipelago server (websocket, deflate)  <->  this bridge  <->  SMO Switch module (line-JSON TCP)

Module layout:
  config.py         configuration loading
  protocol.py       wire-format dataclasses (Switch <-> Bridge)
  state.py          authoritative bridge state (mirror of game)
  datapackage.py   AP id <-> name + classification (Moon/Capture/Kingdom/Shop)
  switch_server.py asyncio TCP server for the Switch
  ap_client.py     CommonContext subclass talking to AP
  tracker_web.py   optional Flask web tracker
  logging_setup.py logging config
  __main__.py      entry point
"""

__version__ = "0.1.0"
