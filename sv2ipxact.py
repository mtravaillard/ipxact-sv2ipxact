#!/usr/bin/env python3
"""sv2ipxact.py — Convert a SystemVerilog module header to an IP-XACT 2022 component XML.

Uses pyslang to parse the SystemVerilog file into a SyntaxTree, then reads
the tree as JSON.  Only the module header is inspected (parameters and ports)
— no elaboration, no symbol resolution, no package loading needed.  Types,
values, and dimension expressions are taken verbatim from the source text,
which is exactly what IP-XACT expects.

Usage
-----
    python sv2ipxact.py
        --input   <module.sv>
        --output  <component.xml>
        --meta    <module.ipxact.json>
        [--define   <SYM> [<SYM> …]]

The --meta file is a small JSON document giving the VLNV vendor/library
(and optionally version, default "1.0") for the component — the "name"
field of the VLNV always comes from the parsed module name. It can also
declare bus interfaces, mapping discrete SV ports onto the logical ports
of a bus abstraction definition:

    {
        "vendor":  "CERN",
        "library": "IP_TEST",
        "version": "1.0",
        "busInterfaces": {
            "<interface-name>": {
                "bus":   "<vendor>:<library>:<bus-def-name>:<version>",
                "mode":  "initiator" | "target" | "master" | "slave",
                "ports": {"<LOGICAL_NAME>": "<physical_port_name>", ...}
            }
        }
    }

The abstraction definition is derived by convention as "<bus-def-name>_rtl"
at the same vendor/library/version as "bus" — it is not given explicitly.
Neither the logical names nor the mapped physical port names are validated
against the abstraction definition here (see the design-description
resolver steps for that) — but a mapped physical port IS checked to exist
on the parsed module.

A physical port value may address a named field of a struct/typedef-typed
port (see "structured" ports below) using dot notation, e.g.
"<physical_port_name>.<field_name>" — this emits an ipxact:subPort inside
the portMap's physicalPort. Further dots address nested fields. The field
name itself is not validated (no elaboration), only that the base port
exists and is struct/typedef-typed.

Ports whose declared type is a struct, union, or other named/scoped type
(rather than a builtin vector type) are emitted as ipxact:structured with
just the type name recorded (ipxact:structPortTypeDefs) — field widths and
sub-ports are not expanded, since that would require elaboration or
package loading, which this tool deliberately does not do.

A genuine SystemVerilog `interface`-typed port (e.g. 'apb_if.slave apb') is
emitted as an ipxact:transactional port. It already groups its own signals,
so instead of a "ports" mapping, its busInterfaces entry references it by
name:

    "<interface-name>": {
        "bus":           "<vendor>:<library>:<bus-def-name>:<version>",
        "mode":          "initiator" | "target" | "master" | "slave",
        "interfacePort": "<physical_port_name>"
    }

Every `interface`-typed port in the module MUST be referenced by exactly one
busInterfaces[...].interfacePort — there is no fallback guess for its real
bus VLNV or mode, so an undescribed one is a hard error, not a placeholder.

Modules
-------
    sv_parser.py       Parse the SV file into port/parameter dicts (pyslang).
    ipxact_builder.py   Build the "plain" component/port/parameter XML.
    bus_interfaces.py   Build and validate the metadata-driven busInterfaces.

Dependencies
------------
    pip install pyslang
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib   import Path
from xml.dom   import minidom
from xml.etree import ElementTree as ET

from sv_parser import _parse_sv, _find_module, _text, _extract_parameters, _extract_ports
from ipxact_builder import (
    NS, NS_XSI, _tag, _sub,
    _build_vlnv, _build_parameters, _build_module_parameters,
    _build_wire_port, _build_structured_port, _build_transactional_port,
)
from bus_interfaces import _build_meta_bus_interface, _validate_bus_interfaces


# ---------------------------------------------------------------------------
# Top-level generator
# ---------------------------------------------------------------------------

def generate_ipxact(sv_file: Path, out_file: Path, vendor: str, library: str,
                    version: str, defines: list[str],
                    bus_interfaces: dict[str, dict] | None = None) -> None:
    """Full pipeline: parse SyntaxTree JSON → extract header → write XML."""

    # ------------------------------------------------------------------ #
    # 1. Parse                                                             #
    # ------------------------------------------------------------------ #
    tree_json   = _parse_sv(sv_file, defines)
    module_node = _find_module(tree_json)
    header      = module_node.get("header", {})
    module_name = _text(header.get("name", {}))
    params      = _extract_parameters(header)
    ports       = _extract_ports(header)
    _validate_bus_interfaces(bus_interfaces or {}, ports)

    # ------------------------------------------------------------------ #
    # 2. Build XML                                                         #
    # ------------------------------------------------------------------ #
    root = ET.Element(_tag("component"))
    root.set(
        f"{{{NS_XSI}}}schemaLocation",
        f"{NS} http://www.accellera.org/XMLSchema/IPXACT/1685-2022/index.xsd",
    )

    _build_vlnv(root, vendor, library, module_name, version)
    _sub(root, "description",
         f"Auto-generated from {sv_file.name} by sv2ipxact")

    # busInterfaces container — attached to root only if the metadata file
    # declares any busInterfaces entries.
    bus_ifaces_el = ET.Element(_tag("busInterfaces"))

    # model
    model_el = _sub(root, "model")

    # views
    views_el = _sub(model_el, "views")
    view_el  = _sub(views_el, "view")
    _sub(view_el, "name", "rtl")
    _sub(view_el, "envIdentifier", "::")
    _sub(view_el, "componentInstantiationRef", f"{module_name}_rtl")

    # instantiations
    insts_el     = _sub(model_el, "instantiations")
    comp_inst_el = _sub(insts_el, "componentInstantiation")
    _sub(comp_inst_el, "name", f"{module_name}_rtl")
    _sub(comp_inst_el, "moduleName", module_name)
    _build_module_parameters(comp_inst_el, params)

    # ports
    ports_el = _sub(model_el, "ports")
    for port in ports:
        if port["is_interface"]:
            _build_transactional_port(ports_el, port)
        elif port["is_struct"]:
            _build_structured_port(ports_el, port)
        else:
            _build_wire_port(ports_el, port)

    for iface_name, iface in (bus_interfaces or {}).items():
        _build_meta_bus_interface(bus_ifaces_el, iface_name, iface)

    # Insert busInterfaces before model if any were declared.
    if len(bus_ifaces_el):
        root.insert(list(root).index(model_el), bus_ifaces_el)

    # component-level parameters
    _build_parameters(root, params)

    # ------------------------------------------------------------------ #
    # 3. Pretty-print and write                                            #
    # ------------------------------------------------------------------ #
    out_file.parent.mkdir(parents=True, exist_ok=True)
    raw    = ET.tostring(root, encoding="unicode", xml_declaration=False)
    pretty = minidom.parseString(raw).toprettyxml(indent="  ")
    # Strip the extra <?xml?> line minidom prepends — we add our own.
    body   = "\n".join(l for l in pretty.splitlines() if not l.startswith("<?xml"))
    out_file.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n",
        encoding="utf-8",
    )
    print(f"[sv2ipxact] Written: {out_file}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_meta(meta_file: Path) -> dict:
    """Read the --meta JSON file and return vendor/library/version/busInterfaces."""
    meta = json.loads(meta_file.read_text())
    try:
        vendor  = meta["vendor"]
        library = meta["library"]
    except KeyError as exc:
        sys.exit(f"ERROR: metadata file {meta_file} is missing required field {exc}")
    return {
        "vendor":         vendor,
        "library":        library,
        "version":        meta.get("version", "1.0"),
        "bus_interfaces": meta.get("busInterfaces", {}),
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Convert a SystemVerilog module to an IP-XACT 2022 component XML.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--input",  required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--meta",   required=True, type=Path,
                    help="JSON file with the component's vendor/library[/version]")
    p.add_argument("--define", nargs="+", default=[], metavar="SYM")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    if not args.input.exists():
        sys.exit(f"ERROR: input file not found: {args.input}")
    if not args.meta.exists():
        sys.exit(f"ERROR: metadata file not found: {args.meta}")
    meta = _load_meta(args.meta)
    generate_ipxact(
        sv_file        = args.input,
        out_file       = args.output,
        vendor         = meta["vendor"],
        library        = meta["library"],
        version        = meta["version"],
        defines        = args.define,
        bus_interfaces = meta["bus_interfaces"],
    )


if __name__ == "__main__":
    main()
