"""memory_map.py: convert a SystemRDL register map into an ipxact:memoryMaps
element (IEEE 1685-2022 shape), ready to merge into a generated component.

Compiles the .rdl with systemrdl-compiler, exports it with peakrdl-ipxact
(which only supports IEEE 1685-2014), then rebuilds the result as
IEEE 1685-2022. The 2014 file is a throwaway temp file, nothing touches
disk outside build_memory_maps().
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

from systemrdl import RDLCompiler
from peakrdl_ipxact import IPXACTExporter

from ipxact_builder import _tag, _sub

NS_2014 = "http://www.accellera.org/XMLSchema/IPXACT/1685-2014"


def _rdl_to_ipxact_2014(rdl_path: Path, vendor: str, library: str, version: str) -> ET.Element:
    """Compile rdl_path and export it to an in-memory IEEE 1685-2014 <ipxact:component> tree."""
    rdlc = RDLCompiler()
    rdlc.compile_file(str(rdl_path))
    root = rdlc.elaborate()

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir) / "component.xml"
        IPXACTExporter(vendor=vendor, library=library, version=version).export(root.top, str(tmp_path))
        return ET.parse(tmp_path).getroot()


def _migrate(el: ET.Element) -> ET.Element:
    """
    Rebuild a 2014-shaped element as 2022-shaped. Every element copies
    across unchanged except <field>: bitWidth/volatile must come before
    resets, and access/modifiedWriteValue move into a new
    fieldAccessPolicies wrapper.
    """
    local = el.tag.rsplit("}", 1)[-1]

    if local != "field":
        new_el = ET.Element(_tag(local), el.attrib)
        # el.text here is peakrdl-ipxact's pretty-print whitespace, not content.
        if el.text and el.text.strip():
            new_el.text = el.text
        for child in el:
            new_el.append(_migrate(child))
        return new_el

    def find(tag):
        return el.find(f"{{{NS_2014}}}{tag}")

    field_el = ET.Element(_tag("field"))
    for tag in ("name", "displayName", "description", "bitOffset", "bitWidth", "volatile", "resets"):
        child = find(tag)
        if child is not None:
            field_el.append(_migrate(child))

    access                = find("access")
    modified_write_value  = find("modifiedWriteValue")
    if access is not None or modified_write_value is not None:
        policies_el = _sub(field_el, "fieldAccessPolicies")
        policy_el   = _sub(policies_el, "fieldAccessPolicy")
        if access is not None:
            policy_el.append(_migrate(access))
        if modified_write_value is not None:
            policy_el.append(_migrate(modified_write_value))

    return field_el


def build_memory_maps(rdl_path: Path, vendor: str, library: str, version: str) -> ET.Element | None:
    """
    Compile rdl_path via systemrdl-compiler and peakrdl-ipxact, and return
    an <ipxact:memoryMaps> element (2022 shape) ready to insert into a
    component document right before <ipxact:model>. Returns None if the
    compiled register map has no memoryMaps.
    """
    component_2014    = _rdl_to_ipxact_2014(rdl_path, vendor, library, version)
    memory_maps_2014  = component_2014.find(f"{{{NS_2014}}}memoryMaps")
    if memory_maps_2014 is None:
        return None

    print(f"[sv2ipxact] Merged register map from {rdl_path}", file=sys.stderr)
    return _migrate(memory_maps_2014)
