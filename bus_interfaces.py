"""bus_interfaces.py — Build and validate metadata-driven ipxact:busInterfaces.

Everything here is derived from the --meta file's "busInterfaces" object,
not from parsing the SystemVerilog itself (see sv_parser.py for that). This
is deliberately the only source of truth for a bus interface's VLNV and
mode — no guessing from port/modport names.
"""

from __future__ import annotations

import sys
from xml.etree import ElementTree as ET

from ipxact_builder import _sub

# Accepted values for a metadata busInterfaces[].mode field, mapped to the
# IEEE 1685-2022 interfaceMode element name.
_MODE_TAG = {
    "initiator": "initiator",
    "master":    "initiator",
    "target":    "target",
    "slave":     "target",
}


def _parse_vlnv(vlnv: str, what: str) -> dict[str, str]:
    """Parse a 'vendor:library:name:version' string into its four fields."""
    parts = vlnv.split(":")
    if len(parts) != 4:
        sys.exit(
            f"ERROR: {what} must be a 'vendor:library:name:version' VLNV "
            f"string, got: {vlnv!r}"
        )
    vendor, library, name, version = parts
    return {"vendor": vendor, "library": library, "name": name, "version": version}


def _set_vlnv_attrs(el: ET.Element, vlnv: dict) -> None:
    for attr in ("vendor", "library", "name", "version"):
        el.set(attr, str(vlnv.get(attr, "")))


def _split_physical_port(physical: str) -> tuple[str, list[str]]:
    """
    Split a metadata 'ports' physical-port value into (port_name, subport_path).

    'apb_req_i' -> ('apb_req_i', []) — maps to the whole port.
    'apb_req_i.psel' -> ('apb_req_i', ['psel']) — maps to a named field of a
    struct/typedef-typed port (ipxact:physicalPort/ipxact:subPort). Further
    dots address nested fields, one ipxact:subPort per path segment.
    """
    parts = physical.split(".")
    return parts[0], parts[1:]


def _build_meta_bus_interface(bus_ifaces_el: ET.Element, name: str, iface: dict) -> None:
    """
    Build an <ipxact:busInterface> from a metadata-file busInterfaces[name] entry.

    Either a discrete/struct-signal mapping:

        <interface-name>: {
            "bus":   "<vendor>:<library>:<bus-def-name>:<version>",
            "mode":  "initiator" | "target" | "master" | "slave",
            "ports": {"<LOGICAL_NAME>": "<physical_port_name>", ...}
        }

    or a reference to a genuine SV `interface`-typed port, which already
    groups its own signals so no port-level mapping is needed:

        <interface-name>: {
            "bus":           "<vendor>:<library>:<bus-def-name>:<version>",
            "mode":          "initiator" | "target" | "master" | "slave",
            "interfacePort": "<physical_port_name>"
        }

    The abstraction definition is derived by convention as "<bus-def-name>_rtl"
    at the same vendor/library/version as "bus". Physical port existence and
    mapping completeness are not validated here (see _validate_bus_interfaces
    and the design-description resolver steps for that).
    """
    mode = str(iface.get("mode", "")).lower()
    mode_tag = _MODE_TAG.get(mode)
    if mode_tag is None:
        sys.exit(
            f"ERROR: busInterface '{name}': unknown mode '{iface.get('mode')}' "
            "(expected initiator/target or master/slave)"
        )

    bus_type = _parse_vlnv(iface.get("bus", ""), f"busInterface '{name}' bus")

    bi_el = _sub(bus_ifaces_el, "busInterface")
    _sub(bi_el, "name", name)

    bt_el = _sub(bi_el, "busType")
    _set_vlnv_attrs(bt_el, bus_type)

    # An interfacePort already groups its own signals (it's a genuine SV
    # `interface`), so there's no logical<->physical wire mapping to declare
    # — just the bus identity and mode.
    if not iface.get("interfacePort"):
        abs_type = {**bus_type, "name": f"{bus_type['name']}_rtl"}
        types_el = _sub(bi_el, "abstractionTypes")
        type_el  = _sub(types_el, "abstractionType")
        ref_el   = _sub(type_el, "abstractionRef")
        _set_vlnv_attrs(ref_el, abs_type)

        port_maps = iface.get("ports", {})
        if port_maps:
            maps_el = _sub(type_el, "portMaps")
            for logical, physical in port_maps.items():
                port_name, sub_path = _split_physical_port(physical)
                map_el = _sub(maps_el, "portMap")
                log_el = _sub(map_el, "logicalPort")
                _sub(log_el, "name", logical)
                phy_el = _sub(map_el, "physicalPort")
                _sub(phy_el, "name", port_name)
                for sub_name in sub_path:
                    sub_el = _sub(phy_el, "subPort")
                    _sub(sub_el, "name", sub_name)

    _sub(bi_el, mode_tag)


def _validate_bus_interfaces(bus_interfaces: dict[str, dict], ports: list[dict]) -> None:
    """
    Verify the metadata file's busInterfaces against the parsed module:

    - Every physical port mapped in a "ports" entry actually exists, and a
      dotted sub-field reference (e.g. 'apb_req_i.psel') only targets a
      struct/typedef-typed port (the field name itself can't be checked
      without elaboration).
    - Every "interfacePort" reference exists and is a genuine SV
      `interface`-typed port, and an entry doesn't set both "ports" and
      "interfacePort" (ambiguous — pick one).
    - Every genuine SV `interface`-typed port in the module is referenced by
      exactly one busInterfaces[...].interfacePort — there is no fallback
      guess for its bus VLNV/mode, so an undescribed one is an error, not a
      silently-placeholder'd component.

    Exits with every mismatch found, rather than stopping at the first one.
    """
    by_name = {p["name"]: p for p in ports}
    errors = []
    referenced_iface_ports = set()

    for iface_name, iface in bus_interfaces.items():
        interface_port = iface.get("interfacePort")
        port_maps       = iface.get("ports", {})

        if interface_port:
            if port_maps:
                errors.append(
                    f"busInterface '{iface_name}': has both 'interfacePort' and "
                    "'ports' — an interfacePort already groups its own signals, "
                    "use only one"
                )
            port = by_name.get(interface_port)
            if port is None:
                errors.append(
                    f"busInterface '{iface_name}': interfacePort '{interface_port}' "
                    "is not a port of this module"
                )
            elif not port["is_interface"]:
                errors.append(
                    f"busInterface '{iface_name}': interfacePort '{interface_port}' "
                    "is not a SystemVerilog interface-typed port"
                )
            else:
                referenced_iface_ports.add(interface_port)
            continue

        for logical, physical in port_maps.items():
            port_name, sub_path = _split_physical_port(physical)
            port = by_name.get(port_name)
            if port is None:
                errors.append(
                    f"busInterface '{iface_name}': logical port '{logical}' maps to "
                    f"'{physical}', which is not a port of this module"
                )
            elif sub_path and not port["is_struct"]:
                errors.append(
                    f"busInterface '{iface_name}': logical port '{logical}' maps to "
                    f"'{physical}', but '{port_name}' is not a struct/typedef-typed "
                    "port — it has no sub-fields to map into"
                )

    for port in ports:
        if port["is_interface"] and port["name"] not in referenced_iface_ports:
            errors.append(
                f"interface port '{port['name']}' (modport '{port['modport']}') has no "
                "matching busInterfaces entry — add one with "
                f"\"interfacePort\": \"{port['name']}\" in the metadata file"
            )

    if errors:
        sys.exit("ERROR: invalid busInterfaces in metadata file:\n  " + "\n  ".join(errors))
