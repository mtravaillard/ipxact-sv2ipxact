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

Dependencies
------------
    pip install pyslang
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib        import Path
from xml.etree      import ElementTree as ET
from xml.dom        import minidom

import pyslang


# ---------------------------------------------------------------------------
# XML namespace constants  (IP-XACT IEEE 1685-2022)
# ---------------------------------------------------------------------------

NS     = "http://www.accellera.org/XMLSchema/IPXACT/1685-2022"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"
NS_SV  = "http://www.cern.ch/sv2ipxact"

ET.register_namespace("ipxact", NS)
ET.register_namespace("xsi",    NS_XSI)
ET.register_namespace("sv",     NS_SV)

def _tag(local: str) -> str:
    return f"{{{NS}}}{local}"

def _sv_tag(local: str) -> str:
    return f"{{{NS_SV}}}{local}"

def _sub(parent: ET.Element, local: str, text: str | None = None) -> ET.Element:
    el = ET.SubElement(parent, _tag(local))
    if text is not None:
        el.text = text
    return el


# ---------------------------------------------------------------------------
# Parsing: SyntaxTree → JSON dict
# ---------------------------------------------------------------------------

def _parse_sv(sv_file: Path, defines: list[str]) -> dict:
    """
    Parse the SV file with pyslang and return the SyntaxTree as a dict.

    We use fromFile() with preprocessorDefines so that `ifdef blocks are
    resolved by the slang preprocessor before we read anything.  The result
    is the concrete syntax tree (CST) serialised to JSON — no elaboration,
    no package resolution required.
    """
    if defines:
        sm   = pyslang.SourceManager()
        bag  = pyslang.Bag()
        opts = pyslang.parsing.PreprocessorOptions()
        opts.predefines = list(defines)
        bag.preprocessorOptions = opts
        tree = pyslang.syntax.SyntaxTree.fromFile(str(sv_file), sm, bag)
    else:
        tree = pyslang.syntax.SyntaxTree.fromFile(str(sv_file))

    return json.loads(tree.to_json())


def _find_module(tree_json: dict) -> dict:
    """Return the first ModuleDeclaration node in the tree."""
    for member in tree_json.get("root", {}).get("members", []):
        if member.get("kind") == "ModuleDeclaration":
            return member
    sys.exit("ERROR: no module declaration found in input file")


# ---------------------------------------------------------------------------
# JSON helpers: extract text tokens recursively
# ---------------------------------------------------------------------------

def _text(node: dict | None) -> str:
    """
    Recursively collect all 'text' leaf values from a JSON node, joined
    without spaces.  This reassembles any expression exactly as written
    in the source (e.g. 'DATA_W-1', 'pkg::CONST', '2**N').
    Trivia (whitespace, comments) is intentionally skipped.
    """
    if node is None:
        return ""
    if isinstance(node, str):
        return ""                        # bare strings are key names, not text
    if isinstance(node, list):
        return "".join(_text(n) for n in node)
    if isinstance(node, dict):
        kind = node.get("kind", "")
        # Skip trivia nodes (whitespace, comments, newlines).
        if kind in ("Whitespace", "EndOfLine", "BlockComment", "LineComment"):
            return ""
        # If this node carries a 'text' leaf, return it — don't recurse further.
        if "text" in node and not any(
            isinstance(v, dict) for v in node.values()
        ):
            return node["text"]
        # Otherwise recurse into all values except 'kind'.
        return "".join(
            _text(v) for k, v in node.items() if k != "kind"
        )
    return ""


def _range_bounds(dim: dict) -> tuple[str, str]:
    """
    Extract (left, right) from a VariableDimension node.
    Returns the raw source text for each bound so parametric expressions
    survive verbatim (e.g. 'DATA_W-1', '0').
    """
    spec = dim.get("specifier", {})
    sel  = spec.get("selector", {})
    left  = _text(sel.get("left"))
    right = _text(sel.get("right"))
    return left, right


# ---------------------------------------------------------------------------
# Parameter extraction
# ---------------------------------------------------------------------------

def _type_text(type_node: dict) -> str:
    """
    Return the data type string for a parameter as written in the source.

    We walk the type node and collect the keyword plus any dimensions,
    e.g. 'int', 'bit', 'int unsigned', 'logic [3:0]', 'my_pkg::my_t'.
    """
    if not type_node:
        return "int"            # implicit type — SV default is int for parameter

    kind = type_node.get("kind", "")

    # Scalar builtins: just return the keyword text directly.
    if "keyword" in type_node and kind not in ("LogicType", "BitType", "RegType"):
        kw = _text(type_node["keyword"])
        # Check for 'unsigned'/'signed' modifier if present.
        signing = _text(type_node.get("signing")) if "signing" in type_node else ""
        return (kw + " " + signing).strip()

    # Logic / bit / reg — may have packed dimensions.
    if kind in ("LogicType", "BitType", "RegType"):
        kw   = _text(type_node.get("keyword", {}))
        dims = type_node.get("dimensions", [])
        if dims:
            dim_str = "".join(
                f"[{_range_bounds(d)[0]}:{_range_bounds(d)[1]}]"
                for d in dims
                if d.get("kind") == "VariableDimension"
            )
            return f"{kw} {dim_str}".strip()
        return kw

    # Named / scoped type (user-defined, package-qualified): reassemble verbatim.
    if kind in ("NamedType", "ScopedType"):
        return _text(type_node)

    # IntType, LongIntType, ShortIntType, ByteType, etc.
    if "keyword" in type_node:
        kw      = _text(type_node["keyword"])
        signing = _text(type_node.get("signing", {}))
        return (kw + " " + signing).strip() if signing else kw

    # Fallback: reassemble whatever is there.
    return _text(type_node).strip() or "int"


def _value_text(declarator: dict) -> str:
    """Return the default value expression text for a parameter declarator."""
    init = declarator.get("initializer") or declarator.get("assignment")
    if not init:
        return ""
    # EqualsValueClause  → expr
    expr = init.get("expr") or init.get("type")
    return _text(expr).strip()


def _extract_parameters(header: dict) -> list[dict]:
    """
    Return a list of parameter dicts with keys: name, dataType, value.

    Only 'parameter' keywords are included — 'localparam' nodes have
    keyword.kind == 'LocalParamKeyword' and are skipped.
    """
    params_node = header.get("parameters")
    if not params_node:
        return []

    result = []
    for decl in params_node.get("declarations", []):
        kind = decl.get("kind")

        # Skip commas and anything that is not a parameter declaration.
        if kind not in ("ParameterDeclaration", "TypeParameterDeclaration"):
            continue

        # Skip localparams.
        kw_kind = decl.get("keyword", {}).get("kind", "")
        if kw_kind == "LocalParamKeyword":
            continue

        if kind == "TypeParameterDeclaration":
            # 'parameter type T = ...'
            for ta in decl.get("declarators", []):
                if ta.get("kind") != "TypeAssignment":
                    continue
                name  = _text(ta.get("name"))
                value = _value_text(ta)
                result.append({"name": name, "dataType": "type", "value": value})
        else:
            # 'parameter <type> NAME = <value>'
            dtype = _type_text(decl.get("type"))
            for d in decl.get("declarators", []):
                if d.get("kind") != "Declarator":
                    continue
                name  = _text(d.get("name"))
                value = _value_text(d)
                result.append({"name": name, "dataType": dtype, "value": value})

    return result


# ---------------------------------------------------------------------------
# Port extraction
# ---------------------------------------------------------------------------

# Map SV direction keyword kinds to IP-XACT direction strings.
_DIRECTION_MAP = {
    "InputKeyword":  "in",
    "OutputKeyword": "out",
    "InOutKeyword":  "inout",
    "RefKeyword":    "inout",   # best approximation in IP-XACT
}


def _extract_packed_dims(data_type: dict) -> list[tuple[str, str]]:
    """Return packed (vector) dimensions from a dataType node."""
    dims = []
    for d in data_type.get("dimensions", []):
        if d.get("kind") == "VariableDimension":
            dims.append(_range_bounds(d))
    return dims


def _extract_unpacked_dims(declarator: dict) -> list[tuple[str, str]]:
    """Return unpacked (array) dimensions from a declarator node."""
    dims = []
    for d in declarator.get("dimensions", []):
        if d.get("kind") == "VariableDimension":
            dims.append(_range_bounds(d))
    return dims


def _extract_ports(header: dict) -> list[dict]:
    """
    Return a list of port dicts with keys:
        name, direction, packed_dims, unpacked_dims, is_interface, is_struct, type_name
    """
    ports_node = header.get("ports", {})
    result     = []

    # Carry the last seen direction across ports that omit it (ANSI implicit).
    last_direction = "in"

    for port in ports_node.get("ports", []):
        if port.get("kind") not in ("ImplicitAnsiPort", "ExplicitAnsiPort"):
            continue

        port_header = port.get("header", {})
        declarator  = port.get("declarator", {})
        name        = _text(declarator.get("name", {}))

        # Direction — may be absent if inherited from previous port.
        dir_node = port_header.get("direction", {})
        dir_kind = dir_node.get("kind", "")
        direction = _DIRECTION_MAP.get(dir_kind, last_direction)
        if dir_kind:
            last_direction = direction

        # Interface port: header kind is InterfacePortHeader.
        if port_header.get("kind") == "InterfacePortHeader":
            iface_name = _text(port_header.get("nameOrKeyword", {}))
            result.append({
                "name":         name,
                "direction":    direction,
                "is_interface": True,
                "iface_type":   iface_name,
                "packed_dims":  [],
                "unpacked_dims": [],
            })
            continue

        data_type     = port_header.get("dataType", {})
        packed_dims   = _extract_packed_dims(data_type)
        unpacked_dims = _extract_unpacked_dims(declarator)

        # A NamedType/ScopedType base (e.g. 'apb_req_t', 'my_pkg::my_req_t',
        # or a 'parameter type' default) is a struct/union/typedef reference,
        # not a builtin vector type — its width and field layout are unknown
        # without elaboration, which this tool deliberately does not do.
        is_struct = data_type.get("kind") in ("NamedType", "ScopedType")
        type_name = _text(data_type) if is_struct else ""

        result.append({
            "name":          name,
            "direction":     direction,
            "is_interface":  False,
            "is_struct":     is_struct,
            "type_name":     type_name,
            "packed_dims":   packed_dims,
            "unpacked_dims": unpacked_dims,
        })

    return result


# ---------------------------------------------------------------------------
# IP-XACT XML builders
# ---------------------------------------------------------------------------

def _build_vlnv(parent: ET.Element, vendor: str, library: str,
                name: str, version: str) -> None:
    _sub(parent, "vendor",  vendor)
    _sub(parent, "library", library)
    _sub(parent, "name",    name)
    _sub(parent, "version", version)


def _build_parameters(parent: ET.Element, params: list[dict]) -> None:
    if not params:
        return
    params_el = _sub(parent, "parameters")
    for p in params:
        el = _sub(params_el, "parameter")
        _sub(el, "name",  p["name"])
        _sub(el, "value", p["value"] or "0")
        el.set("resolve", "user")


def _build_module_parameters(parent: ET.Element, params: list[dict]) -> None:
    if not params:
        return
    mp_el = _sub(parent, "moduleParameters")
    for p in params:
        el = _sub(mp_el, "moduleParameter")
        _sub(el, "name",  p["name"])
        _sub(el, "value", p["value"] or "0")
        el.set("dataType", p["dataType"])


def _build_wire_port(ports_el: ET.Element, port: dict) -> None:
    port_el = _sub(ports_el, "port")
    _sub(port_el, "name", port["name"])
    wire_el = _sub(port_el, "wire")
    _sub(wire_el, "direction", port["direction"])

    if port["packed_dims"]:
        vectors_el = _sub(wire_el, "vectors")
        for left, right in port["packed_dims"]:
            vec_el = _sub(vectors_el, "vector")
            _sub(vec_el, "left",  left)
            _sub(vec_el, "right", right)

    if port["unpacked_dims"]:
        arrays_el = _sub(port_el, "arrays")
        for left, right in port["unpacked_dims"]:
            arr_el = _sub(arrays_el, "array")
            _sub(arr_el, "left",  left)
            _sub(arr_el, "right", right)


def _build_structured_port(ports_el: ET.Element, port: dict) -> None:
    """
    Build an <ipxact:structured> port for a struct/union/typedef-typed SV
    port (e.g. 'input apb_req_t apb_req_i'). Field layout and width are
    unknown without elaboration or package loading, so only the type name
    is recorded (ipxact:structPortTypeDefs) — no ipxact:subPorts are
    emitted. See ipxact:portStructuredType in port.xsd.
    """
    port_el = _sub(ports_el, "port")
    _sub(port_el, "name", port["name"])

    struct_el = _sub(port_el, "structured")
    kind_el   = _sub(struct_el, "struct")
    kind_el.set("direction", port["direction"])

    defs_el = _sub(struct_el, "structPortTypeDefs")
    def_el  = _sub(defs_el, "structPortTypeDef")
    _sub(def_el, "typeName", port["type_name"])

    if port["unpacked_dims"]:
        arrays_el = _sub(port_el, "arrays")
        for left, right in port["unpacked_dims"]:
            arr_el = _sub(arrays_el, "array")
            _sub(arr_el, "left",  left)
            _sub(arr_el, "right", right)

    print(
        f"WARNING: port '{port['name']}' has struct/typedef type "
        f"'{port['type_name']}' — emitted as ipxact:structured with no "
        "field breakdown (width/subPorts unknown without elaboration).",
        file=sys.stderr,
    )


def _build_interface_port(ports_el: ET.Element, bus_ifaces_el: ET.Element,
                          port: dict) -> None:
    # Transactional port entry.
    port_el  = _sub(ports_el, "port")
    _sub(port_el, "name", port["name"])
    trans_el = _sub(port_el, "transactional")
    _sub(trans_el, "direction", "requires")
    _sub(trans_el, "initiative", "requires")

    # Placeholder busInterface — VLNV unknown until user supplies a mapping.
    bi_el = _sub(bus_ifaces_el, "busInterface")
    _sub(bi_el, "name", port["name"])
    bt_el = _sub(bi_el, "busType")
    bt_el.set("vendor",  "unknown")
    bt_el.set("library", "unknown")
    bt_el.set("name",    port.get("iface_type", "unknown"))
    bt_el.set("version", "1.0")

    print(
        f"WARNING: interface port '{port['name']}' emitted with placeholder "
        "busInterface VLNV (vendor=unknown).",
        file=sys.stderr,
    )


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
    Build an <ipxact:busInterface> from a metadata-file busInterfaces[name] entry:

        <interface-name>: {
            "bus":   "<vendor>:<library>:<bus-def-name>:<version>",
            "mode":  "initiator" | "target" | "master" | "slave",
            "ports": {"<LOGICAL_NAME>": "<physical_port_name>", ...}
        }

    The abstraction definition is derived by convention as "<bus-def-name>_rtl"
    at the same vendor/library/version as "bus". Physical port existence and
    mapping completeness are not validated here (see the design-description
    resolver steps for that).
    """
    mode = str(iface.get("mode", "")).lower()
    mode_tag = _MODE_TAG.get(mode)
    if mode_tag is None:
        sys.exit(
            f"ERROR: busInterface '{name}': unknown mode '{iface.get('mode')}' "
            "(expected initiator/target or master/slave)"
        )

    bus_type = _parse_vlnv(iface.get("bus", ""), f"busInterface '{name}' bus")
    abs_type = {**bus_type, "name": f"{bus_type['name']}_rtl"}

    bi_el = _sub(bus_ifaces_el, "busInterface")
    _sub(bi_el, "name", name)

    bt_el = _sub(bi_el, "busType")
    _set_vlnv_attrs(bt_el, bus_type)

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


def _validate_bus_port_maps(bus_interfaces: dict[str, dict], ports: list[dict]) -> None:
    """
    Verify that every physical port mapped in busInterfaces[...].ports
    actually exists on the parsed module, and that a dotted sub-field
    reference (e.g. 'apb_req_i.psel') only targets a struct/typedef-typed
    port. The sub-field name itself can't be checked without elaboration.
    Exits with every mismatch found across all interfaces, rather than
    stopping at the first one.
    """
    by_name = {p["name"]: p for p in ports}
    errors = []
    for iface_name, iface in bus_interfaces.items():
        for logical, physical in iface.get("ports", {}).items():
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
    if errors:
        sys.exit("ERROR: invalid port mapping(s) in metadata file:\n  " + "\n  ".join(errors))


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
    _validate_bus_port_maps(bus_interfaces or {}, ports)

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

    # busInterfaces container — attached to root only if interface ports exist.
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
            _build_interface_port(ports_el, bus_ifaces_el, port)
        elif port["is_struct"]:
            _build_structured_port(ports_el, port)
        else:
            _build_wire_port(ports_el, port)

    for iface_name, iface in (bus_interfaces or {}).items():
        _build_meta_bus_interface(bus_ifaces_el, iface_name, iface)

    # Insert busInterfaces before model if any interface ports were found.
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