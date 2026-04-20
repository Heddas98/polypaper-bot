"""Observability helpers — telemetry primitives that don't gate runtime.

Modules here MUST be safe to import even when their telemetry feature is
turned off (default OFF). Each module exposes an `enabled()` function and
gates expensive operations behind it. Designed to be enabled per-deploy
via ENV without any code changes.
"""
