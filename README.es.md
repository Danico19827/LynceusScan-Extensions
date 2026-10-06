# Extensiones de LynceusScan

> **English version:** [README.md](README.md)

Extensiones propias y de la comunidad para
[LynceusScan](https://github.com/Danico19827/LynceusScan): nodos de
procesamiento y packs de idioma, distribuidos como archivos `.lxpkg`
individuales.

- Catálogo: [CATALOG.md](CATALOG.md)
- Núcleo de LynceusScan: https://github.com/Danico19827/LynceusScan
- Sitio y docs: https://danico19827.github.io/LynceusScan-Web/
- Issues: https://github.com/Danico19827/LynceusScan-Extensions/issues

## Instalar una extensión

1. Descarga el `.lxpkg` que quieras desde [Releases](../../releases).
2. En LynceusScan: **Archivo → Extensiones → Importar extensión...** (o
   arrastra el archivo al diálogo).
3. Los nodos extra aparecen en la librería bajo su propia categoría; los
   packs de idioma se habilitan en **Herramientas → Preferencias → General**.

Las extensiones cargan in-process y heredan la GPL. Los nodos que declaran
una EULA la muestran una vez antes del primer uso.

## Qué hay en este repositorio

| Ruta | Contenido |
|---|---|
| `nodes/forestry/` | Nodos de análisis forestal: Area-Based Approach, Canopy Gaps, Height Strata (experimentales, EULA) |
| `nodes/openmeteo/` | Consulta meteorológica a Open-Meteo según la ubicación del levantamiento |
| `locales/` | Packs de idioma (español, ruso, italiano, portugués brasileño) y el snapshot de strings del core que usan los tests |
| `tests/` | Suite headless de los packs y los nodos |

## Desarrollo

Requisitos: Python 3.13 (Windows), un
[checkout del core](https://github.com/Danico19827/LynceusScan) y las
dependencias pinneadas:

```powershell
python -m pip install -r requirements.txt
```

Corré los tests desde la raíz de este repositorio con el checkout del core en
`PYTHONPATH` (los nodos importan módulos del core; no hace falta la GUI):

```powershell
$env:PYTHONPATH = "C:\ruta\al\LynceusScan"
python -m unittest discover -s tests
```

El test de cobertura de idiomas valida cada pack contra
`locales/_core_strings.json`, un snapshot de los strings visibles del core.
El snapshot se regenera desde el checkout del core en cada release del core
(helper: `write_core_strings_snapshot()` en
`tests/test_i18n_extract.py` del core); nunca se edita a mano.

Generar archivos `.lxpkg` (desde un checkout del core):

```powershell
python -m lynceus.tools.extension_tool pack nodes\forestry\area_based.py -o area_based.lxpkg
python -m lynceus.tools.extension_tool pack locales\es -o es.lxpkg
python -m lynceus.tools.extension_tool validate area_based.lxpkg
```

## Contribuir

- Reportes de errores y sugerencias: abrí un issue.
- Nodos o packs nuevos: leé primero la
  [guía de autoría](https://github.com/Danico19827/LynceusScan/blob/main/EXTENSIONES.es.md)
  del core (las extensiones deben ser GPL-3.0-or-later; cargan in-process).
- Las contribuciones se rigen por [CLA.md](CLA.md): el contribuidor conserva
  la autoría y la atribución, y ninguna contribución se relicencia
  comercialmente sin su consentimiento escrito.

## Licencia

GPL-3.0-or-later (ver `LICENSE`). Las extensiones son obras derivadas del
core y heredan la GPL.
