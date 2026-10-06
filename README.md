# LynceusScan Extensions

> **Versión en español:** [README.es.md](README.es.md)

First-party and community extensions for
[LynceusScan](https://github.com/Danico19827/LynceusScan): extra processing
nodes and language packs, distributed as single `.lxpkg` files.

- Catalog: [CATALOG.md](CATALOG.md)
- LynceusScan core: https://github.com/Danico19827/LynceusScan
- Website & docs: https://danico19827.github.io/LynceusScan-Web/
- Issues: https://github.com/Danico19827/LynceusScan-Extensions/issues

## Install an extension

1. Download the `.lxpkg` you want from [Releases](../../releases).
2. In LynceusScan: **File → Extensions → Import extension...** (or drag &
   drop the file onto the dialog).
3. Extra nodes appear in the node library under their own category;
   language packs are enabled in **Tools → Preferences → General**.

Extensions load in-process and inherit the GPL. Nodes that declare an EULA
show it once before first use.

## What's in this repository

| Path | Contents |
|---|---|
| `nodes/forestry/` | Forestry analysis nodes: Area-Based Approach, Canopy Gaps, Height Strata (experimental, EULA) |
| `nodes/openmeteo/` | Open-Meteo weather query by survey location |
| `locales/` | Language packs (Spanish, Russian, Italian, Brazilian Portuguese) and the core string snapshot used by the tests |
| `tests/` | Headless test suite for the packs and the nodes |

## Development

Requirements: Python 3.13 (Windows), a
[core checkout](https://github.com/Danico19827/LynceusScan) and the pinned
dependencies:

```powershell
python -m pip install -r requirements.txt
```

Run the tests from this repository root with the core checkout on
`PYTHONPATH` (the nodes import core modules; the GUI is not needed):

```powershell
$env:PYTHONPATH = "C:\path\to\LynceusScan"
python -m unittest discover -s tests
```

The locale coverage test validates every pack against
`locales/_core_strings.json`, a snapshot of the core's user-visible strings.
The snapshot is regenerated from the core checkout on each core release
(helper: `write_core_strings_snapshot()` in the core's
`tests/test_i18n_extract.py`); never hand-edit it.

Building `.lxpkg` files (run from a core checkout):

```powershell
python -m lynceus.tools.extension_tool pack nodes\forestry\area_based.py -o area_based.lxpkg
python -m lynceus.tools.extension_tool pack locales\es -o es.lxpkg
python -m lynceus.tools.extension_tool validate area_based.lxpkg
```

## Contributing

- Bug reports and suggestions: open an issue.
- New nodes or packs: read the core's
  [authoring guide](https://github.com/Danico19827/LynceusScan/blob/main/EXTENSIONS.md)
  first (extensions must be GPL-3.0-or-later; they load in-process).
- Contributions are governed by [CLA.md](CLA.md): the author keeps authorship
  and attribution, and no contribution is relicenced commercially without the
  contributor's written consent.

## License

GPL-3.0-or-later (see `LICENSE`). Extensions are derivative works of the
core and inherit the GPL.
