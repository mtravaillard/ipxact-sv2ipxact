"""sv_parser.py — Parse a SystemVerilog module header into plain dicts.

Uses pyslang to parse the file into a SyntaxTree, then reads the tree as
JSON.  Only the module header is inspected (parameters and ports) — no
elaboration, no symbol resolution, no package loading needed.  Types,
values, and dimension expressions are taken verbatim from the source text,
which is exactly what IP-XACT expects.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pyslang


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
            modport    = _text(port_header.get("modport", {}).get("member", {}))
            result.append({
                "name":         name,
                "direction":    direction,
                "is_interface": True,
                "iface_type":   iface_name,
                "modport":      modport,
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
