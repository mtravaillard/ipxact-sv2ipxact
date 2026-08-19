# sv2ipxact

Convert a SystemVerilog module header into an [IEEE 1685-2022](https://www.accellera.org/downloads/standards/ip-xact) (IP-XACT) `component` XML file.

Only the module header (parameters and ports) is inspected via [`pyslang`](https://pypi.org/project/pyslang/), with no elaboration, no package loading, no symbol resolution. Types, values, and dimension expressions are taken verbatim from the source text, which is exactly what IP-XACT's expression model expects. Every shape of output this tool produces has been checked against the real IEEE 1685-2022 XSD with `xmllint`.

## Usage

```sh
python sv2ipxact.py \
    --input   my_module.sv \
    --output  my_module.xml \
    --meta    my_module.ipxact.json \
    [--define SYM ...]
```

## Metadata file

A small JSON file supplies what can't be inferred from the SV source alone: the VLNV, and bus port mapping:

```json
{
    "vendor":  "Vendor",
    "library": "Library",
    "version": "1.0",
    "busInterfaces": {
        "apb": {
            "bus":   "Vendor:Library:Bus:1.0",
            "mode":  "target",
            "ports": {"PSEL": "psel", "PADDR": "bus_req_i.paddr"}
        }
    }
}
```

- `vendor` / `library` / `version` form the VLNV, the component's `name` always comes from the parsed module name, not from this file.
- `busInterfaces` maps logical bus signals onto physical SV ports. A physical port value can be a plain port name, a dotted `port.field` reference into a struct/typedef-typed port, or, via `interfacePort` instead of `ports`, a genuine SV `interface`-typed port. Every `interface`-typed port in the module must be described this way; there is no fallback guess for its bus VLNV or mode.

See the module docstring in `sv2ipxact.py` for the full schema and more examples.

## Layout

| File | Responsibility |
|---|---|
| `sv2ipxact.py` | CLI entry point and orchestration |
| `sv_parser.py` | Parses the SV file into port/parameter dicts, via `pyslang` |
| `ipxact_builder.py` | Builds the "plain" component/port/parameter XML |
| `bus_interfaces.py` | Builds and validates the metadata-driven `busInterfaces` |

## Dependencies

- Python 3
- [`pyslang`](https://pypi.org/project/pyslang/) >= 11.0.0 (`pip install pyslang`)

## Tests

`test/` contains a CMake-based example suite. `test/sv2ipxact.cmake` exposes a `sv2ipxact()` CMake function; `test/CMakeLists.txt` uses it to generate IP-XACT components for a handful of examples: a trivial combinational module (`adder`), two full-size open-source RISC-V cores exercising struct-typed ports (`cva6`, `ibex`), and a minimal APB4 peripheral shown both with discrete bus signals and with packed-struct ports (`apb_gpio`, `apb_gpio_packed`) against a hand-written APB4 bus/abstraction definition (`test/bus_library/apb4/`).

```sh
cd test
python3 -m venv .venv && .venv/bin/pip install -r python_requirements.txt
mkdir build && cd build
cmake ..
make check
```

`make check` builds every example, then validates each generated component against the IEEE 1685-2022 XSD (shipped in `ipxact-2022/schema/1685-2022/`) with `xmllint`.

## Status

This tool is part of a plan to improve [SoCMake](https://github.com/HEP-SoC/SoCMake) by using IP-XACT for SoC generation.

**Implemented:**
- Component generation from SV module headers
- Parameter and port extraction
- Metadata-driven bus interface mapping (discrete, struct, and SV `interface`-typed ports)

**Not yet implemented:**
- Register/memory-map merging from SystemRDL, via PeakRDL-ipxact
- Semantic-consistency-rule (SCR) validation
- More