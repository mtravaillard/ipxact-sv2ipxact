"""ipxact_builder.py: build IP-XACT (IEEE 1685-2022) component XML elements.

Generic XML namespace helpers plus the builders for the "plain" parts of a
component document: VLNV, component-level and module parameters, and wire /
structured / transactional ports. Bus-interface XML (driven by the metadata
file) lives in bus_interfaces.py.
"""

from __future__ import annotations

import sys
from xml.etree import ElementTree as ET

# ---------------------------------------------------------------------------
# XML namespace constants  (IP-XACT IEEE 1685-2022)
# ---------------------------------------------------------------------------

NS     = "http://www.accellera.org/XMLSchema/IPXACT/1685-2022"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"

ET.register_namespace("ipxact", NS)
ET.register_namespace("xsi",    NS_XSI)


def _tag(local: str) -> str:
    return f"{{{NS}}}{local}"


def _sub(parent: ET.Element, local: str, text: str | None = None) -> ET.Element:
    el = ET.SubElement(parent, _tag(local))
    if text is not None:
        el.text = text
    return el


# ---------------------------------------------------------------------------
# VLNV / parameters
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
        # No "dataType" attribute here: unlike moduleParameter, ipxact:parameter
        # only allows a coarse "type" (bit/byte/int/.../string) that can't losslessly
        # hold an arbitrary SV type string like "logic [3:0]" or "my_pkg::my_t".
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


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------

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
        # "arrays" is a sibling of "wire" under "port" per the schema, not
        # nested inside "wire" (portType's sequence is: wire, arrays, ...).
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
    is recorded (ipxact:structPortTypeDefs); no ipxact:subPorts are
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
        f"'{port['type_name']}', emitted as ipxact:structured with no "
        "field breakdown (width/subPorts unknown without elaboration).",
        file=sys.stderr,
    )


def _build_transactional_port(ports_el: ET.Element, port: dict) -> None:
    """
    Build the physical <ipxact:port> entry for a genuine SV `interface`-typed
    port (e.g. 'apb_if.slave apb'), as an ipxact:transactional port.

    This only records the port itself. The busInterface that describes its
    real bus VLNV and mode comes entirely from the metadata file's
    busInterfaces[...].interfacePort. See bus_interfaces.py, which requires
    every such port to be described there (no guessing).
    """
    port_el  = _sub(ports_el, "port")
    _sub(port_el, "name", port["name"])
    trans_el = _sub(port_el, "transactional")
    _sub(trans_el, "initiative", "requires")
