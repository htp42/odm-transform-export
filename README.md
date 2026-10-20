# ODM Transform Export

An [OpenStudyBuilder](https://openstudybuilder.com) extension that exports CRFs
as ODM XML that Medidata Rave can import.

It adds **Export for Medidata Rave** to the export menu of **Library → CRF
Viewer**. The CRF Viewer's forms are exported with all vendor namespaces, then
transformed for Rave: everything that can be fixed mechanically is fixed (namespace
layout, OIDs, DataTypes, `mdsol:` attributes, label items, field order, and more),
and the issues that need a person to decide are listed in the dialog.

## Repository layout

| Folder | What it is | Installed into OpenStudyBuilder as |
|--------|------------|------------------------------------|
| [`core/`](core/README.md) | `odm-transform-export` Python library: the ODM → Rave transform. Depends only on lxml. | A package in the extensions API's Python environment |
| [`osb-api-extension/`](osb-api-extension/README.md) | `odm_transform_export_ext`: extensions API endpoint that exports and transforms in one call | `api/extensions/odm_transform_export_ext/` |
| [`osb-frontend-extension/`](osb-frontend-extension/README.md) | `odm-transform-export`: the CRF Viewer menu entry and its dialog | `frontend/src/extensions/odm-transform-export/` |

## Requirements

- OpenStudyBuilder with the extensions API (port 8009 by default).
- An OpenStudyBuilder frontend with the **CRF Viewer export actions** extension
  point: `OdmViewer.vue` loads `src/extensions/*/crf-viewer/exportActions.js`.
  It is available in
  [htp42/openstudybuilder-solution-packaging](https://github.com/htp42/openstudybuilder-solution-packaging);
  without it the frontend extension is ignored.
- `mdsol:` vendor attributes defined as ODM vendor extensions in the
  OpenStudyBuilder library, for the Rave-specific settings to reach the export.
  See [Input expectations](core/README.md#input-expectations).

## Installing in an OpenStudyBuilder image build

Like the ELOBS Word Updater, this repository can be fetched at build time, so
none of its code has to be committed to OpenStudyBuilder.

`api/Dockerfile`, in the extensions API stage:

```dockerfile
FROM scratch AS odm-transform-export
ARG ODM_TRANSFORM_EXPORT_REF=main
ADD https://github.com/htp42/odm-transform-export.git#${ODM_TRANSFORM_EXPORT_REF} /

FROM final-stage AS extensionsapi-stage
COPY --from=odm-transform-export /osb-api-extension/odm_transform_export_ext ./extensions/odm_transform_export_ext
# Constrain to the already-installed versions so the extension can never change a locked package
RUN --mount=type=bind,from=odm-transform-export,source=/core,target=/tmp/odm-transform-export-core \
    uv pip freeze > /tmp/installed.txt \
    && uv pip install --no-cache --constraint /tmp/installed.txt /tmp/odm-transform-export-core \
    && rm /tmp/installed.txt && rm -rf ~/.cache
```

`frontend/Dockerfile`, before `RUN npm run build`:

```dockerfile
ARG ODM_TRANSFORM_EXPORT_REF=main
ADD https://github.com/htp42/odm-transform-export.git#${ODM_TRANSFORM_EXPORT_REF}:osb-frontend-extension/odm-transform-export ./src/extensions/odm-transform-export
```

Pass the same `ODM_TRANSFORM_EXPORT_REF` (branch, tag or commit SHA) to both
builds: the frontend calls the API extension's endpoint.

## Local development

From an OpenStudyBuilder checkout, with this repository cloned next to it:

```bash
cp -R ../odm-transform-export/osb-api-extension/odm_transform_export_ext api/extensions/
cp -R ../odm-transform-export/osb-frontend-extension/odm-transform-export frontend/src/extensions/

cd api
uv pip install -e ../../odm-transform-export/core
uv run poe extensions-api-dev
```

The core library has to be installed in the API's own environment: `poe` runs each
task through a fresh `uv run`, so `uv run --with-editable … poe …` does not reach
the extensions API. The extension then fails to import, and with `--reload` the
port stays open with no worker behind it, so requests hang without a response.
`uv sync` removes the package again; re-run the `uv pip install` after it.

Start the main API and the frontend as usual (`uv run poe dev`, `npm run dev`),
open **Library → CRF Viewer**, generate a preview and use the export menu.

## Tests

```bash
# Core library, standalone
cd core && pip install -e ".[dev]" && pytest

# API extension, from OpenStudyBuilder's api/ once the extension is copied in and
# the core library installed as above (needs the Neo4j database the API is
# configured for, like every extension test)
uv run pytest extensions/odm_transform_export_ext
```

## License

[Apache License 2.0](LICENSE)
