"""Discovery of action specifications exposed by extension modules."""

from dataclasses import dataclass
from importlib import resources
from importlib import import_module
from typing import Optional, Tuple

from core.action_registry import ActionSpec, _builtin_instant_action_types


@dataclass(frozen=True)
class DiscoveredModule:
    module: str
    title: str
    specs: Tuple[ActionSpec, ...]
    panel_suppressed: bool = False
    panel_template: Optional[str] = None


def default_module_names() -> Tuple[str, ...]:
    return (
        "extensions.clamp",
        "extensions.pio",
        "extensions.ezio",
        "extensions.facility",
    )


def _read_panel(module_name: str) -> Optional[str]:
    try:
        panel = resources.files(module_name).joinpath("panel.html")
        return panel.read_text(encoding="utf-8") if panel.is_file() else None
    except Exception as exc:
        print(f"[ACTION MODULE PANEL SKIP] {module_name}: {exc}")
        return None


def _module_specs(module, config):
    """Ask a module for its ActionSpecs, passing config when it accepts one.

    Some parameters only have a valid domain in config — the elevator's
    station list comes from elevator_motion_rules, so the module cannot offer
    a dropdown without seeing it. Third-party modules listed in
    [[action_modules]] still declare the old no-argument form, so try the
    config form first and fall back rather than breaking them.
    """
    action_specs = module.action_specs
    try:
        return action_specs(config)
    except TypeError as exc:
        # Only a signature mismatch falls back; a TypeError raised inside the
        # module is a real bug and must not be retried into a confusing state.
        if "positional argument" not in str(exc):
            raise
        return action_specs()


def discover_action_modules(
    config, *, default_modules=None, reserved_types=None
) -> tuple[DiscoveredModule, ...]:
    configured = config.action_modules
    disabled = {entry.module for entry in configured if not entry.enabled}
    disabled_types = {
        entry.action_type
        for entry in getattr(config, "actions", [])
        if not bool(getattr(entry, "enabled", True))
    }
    names = [
        name
        for name in (
            default_module_names() if default_modules is None else default_modules
        )
        if name not in disabled
    ]
    seen_names = set(names)
    for entry in configured:
        if entry.enabled and entry.module not in seen_names:
            names.append(entry.module)
            seen_names.add(entry.module)

    seen = (
        set(_builtin_instant_action_types())
        if reserved_types is None
        else set(reserved_types)
    )
    discovered = []
    for name in names:
        try:
            module = import_module(name)
            specs = tuple(_module_specs(module, config))
            if not specs:
                raise ValueError("action_specs() returned no specs")
            if not all(isinstance(spec, ActionSpec) for spec in specs):
                raise TypeError("action_specs() must return only ActionSpec entries")
            action_types = [spec.action_type for spec in specs]
            if len(set(action_types)) != len(action_types):
                raise ValueError("duplicate action_type within module")
            clashes = seen.intersection(action_types)
            if clashes:
                raise ValueError(
                    f"action_type already registered: {sorted(clashes)}"
                )
            title = getattr(module, "MODULE_TITLE", name.rsplit(".", 1)[-1])
            seen.update(action_types)
            kept_specs = tuple(
                spec
                for spec in specs
                if spec.enabled and spec.action_type not in disabled_types
            )
            panel_suppressed = len(kept_specs) != len(specs)
            if not kept_specs:
                raise ValueError("all actions disabled")
            discovered.append(
                DiscoveredModule(
                    module=name,
                    title=title,
                    specs=kept_specs,
                    panel_suppressed=panel_suppressed,
                    panel_template=(
                        None if panel_suppressed else _read_panel(name)
                    ),
                )
            )
        except Exception as exc:
            print(f"[ACTION MODULE SKIP] {name}: {exc}")
            continue
    return tuple(discovered)


def first_party_action_specs(config) -> tuple[ActionSpec, ...]:
    extension_specs = tuple(
        spec for module in discover_action_modules(config) for spec in module.specs
    )
    from core.action_bridge import action_specs as builtin_bridge_specs
    from extensions.recipes import action_specs as recipe_action_specs

    bridge_specs = builtin_bridge_specs()
    composable_specs = extension_specs + bridge_specs
    configured_action_types = {
        action.action_type
        for action in getattr(config, "actions", ())
        if bool(getattr(action, "enabled", True))
    }
    return composable_specs + recipe_action_specs(
        getattr(config, "recipes", ()),
        composable_specs,
        configured_action_types,
    )
