# Catalog

Every extension installs the same way: download its `.lxpkg` from
[Releases](../../releases) and import it with **File → Extensions →
Import extension...** (or drag & drop the file onto the dialog). Language
packs are enabled afterwards in **Tools → Preferences → General**; extra
nodes appear in the node library under their own category.

| File | Kind | Id | Name | Category | License | Notes |
|---|---|---|---|---|---|---|
| `area_based.lxpkg` | Node | `forestry.area_based` | Area-Based Approach | Raster / Analysis | GPL-3.0-or-later | Experimental (AI-assisted): EULA shown once before first use |
| `canopy_gaps.lxpkg` | Node | `forestry.canopy_gaps` | Canopy Gaps | Raster / Analysis | GPL-3.0-or-later | Experimental (AI-assisted): EULA shown once before first use |
| `height_strata.lxpkg` | Node | `forestry.height_strata` | Height Strata | Raster / Analysis | GPL-3.0-or-later | Experimental (AI-assisted): EULA shown once before first use |
| `openmeteo.lxpkg` | Node | `openmeteo.query` | Open-Meteo Query | Input | GPL-3.0-or-later | Requires network access to the Open-Meteo API |
| `es.lxpkg` | Locale pack | `es` | Español | — | GPL-3.0-or-later | Enables Spanish in Preferences |
| `ru.lxpkg` | Locale pack | `ru` | Русский | — | GPL-3.0-or-later | Enables Russian in Preferences |
| `it.lxpkg` | Locale pack | `it` | Italiano | — | GPL-3.0-or-later | Enables Italian in Preferences |
| `pt-BR.lxpkg` | Locale pack | `pt-BR` | Português (Brasil) | — | GPL-3.0-or-later | Enables Brazilian Portuguese in Preferences |

Notes:

- The forestry nodes ship an **EULA** (experimental, AI-assisted: verify
  every output with a qualified professional) and stamp the matching
  disclaimer into each product.
- Language packs are complete catalogs for the LynceusScan core string set;
  importing one only enables the language, it never activates it alone.
- `locales/_core_strings.json` (not distributed) is the core string snapshot
  consumed by the test suite, not by the application.
- The app version each pack targets is listed in its `manifest.json`
  (`compatibility`, when present).
