"""
config_cmd.py — CLI for viewing and editing SOLVENT configuration.

Usage:
    solvent config              show all current config values
    solvent config get <key>    print a single value
    solvent config set <key> <value>  update a value and save
    solvent config reset        restore all defaults
    solvent config --json       output as JSON
"""

from __future__ import annotations

import json
import sys
from dataclasses import fields
from typing import Any


def _load_or_default():
    from .config import SolventConfig, load_config
    return load_config() or SolventConfig()


def _coerce(field_type: str, value: str) -> Any:
    """Convert a CLI string value to the appropriate Python type."""
    if field_type in ("bool",):
        if value.lower() in ("true", "1", "yes", "on"):
            return True
        if value.lower() in ("false", "0", "no", "off"):
            return False
        raise ValueError(f"expected true/false, got {value!r}")
    if field_type == "int":
        return int(value)
    if field_type == "float":
        return float(value)
    return value  # str and anything else


def _field_type_name(f) -> str:
    """Return a simplified type name string for a dataclass field."""
    t = f.type
    if hasattr(t, "__name__"):
        return t.__name__
    s = str(t)
    for prefix in ("typing.", "<class '", "'"):
        s = s.replace(prefix, "")
    return s.rstrip("'>")


def cmd_show(*, as_json: bool = False) -> None:
    cfg = _load_or_default()
    data = cfg.to_dict()
    if as_json:
        print(json.dumps(data, indent=2))
        return
    col = max(len(k) for k in data) + 2
    for k, v in data.items():
        print(f"  {k:<{col}} {v}")


def cmd_get(key: str, *, as_json: bool = False) -> int:
    cfg = _load_or_default()
    data = cfg.to_dict()
    if key not in data:
        print(f"[solvent config] unknown key: {key!r}", file=sys.stderr)
        return 1
    val = data[key]
    if as_json:
        print(json.dumps(val))
    else:
        print(val)
    return 0


def cmd_set(key: str, value: str) -> int:
    from .config import SolventConfig, load_config, save_config

    cfg = load_config() or SolventConfig()
    known_fields = {f.name: f for f in fields(SolventConfig)}
    if key not in known_fields:
        print(f"[solvent config] unknown key: {key!r}", file=sys.stderr)
        print(f"  Valid keys: {', '.join(sorted(known_fields))}", file=sys.stderr)
        return 1

    f = known_fields[key]
    type_name = _field_type_name(f)
    try:
        coerced = _coerce(type_name, value)
    except (ValueError, TypeError) as e:
        print(f"[solvent config] bad value for {key!r} ({type_name}): {e}", file=sys.stderr)
        return 1

    setattr(cfg, key, coerced)
    try:
        cfg.validate()
    except ValueError as e:
        print(f"[solvent config] validation error: {e}", file=sys.stderr)
        return 1

    save_config(cfg)
    print(f"  {key} = {coerced}")
    return 0


def cmd_reset() -> int:
    from .config import SolventConfig, save_config
    cfg = SolventConfig()
    save_config(cfg)
    print("[solvent config] reset to defaults")
    return 0


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(
        description="View and edit SOLVENT configuration.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="output as JSON")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("show", help="show all config values (default)")
    g = sub.add_parser("get", help="get a single config value")
    g.add_argument("key")
    s = sub.add_parser("set", help="set a config value")
    s.add_argument("key")
    s.add_argument("value")
    sub.add_parser("reset", help="reset all values to defaults")

    args = p.parse_args()

    if args.cmd is None or args.cmd == "show":
        cmd_show(as_json=args.as_json)
        sys.exit(0)
    elif args.cmd == "get":
        sys.exit(cmd_get(args.key, as_json=args.as_json))
    elif args.cmd == "set":
        sys.exit(cmd_set(args.key, args.value))
    elif args.cmd == "reset":
        sys.exit(cmd_reset())


if __name__ == "__main__":
    main()
