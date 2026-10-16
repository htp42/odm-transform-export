# odm-transform-export — OSB Frontend Extension

A StudyBuilder frontend extension that adds **Export for Medidata Rave** to the
export menu of **Library → CRF Viewer**.

Requires the [OSB API extension](../osb-api-extension/README.md), and a frontend
whose `OdmViewer.vue` loads `src/extensions/*/crf-viewer/exportActions.js` (the
CRF Viewer export actions extension point). On a frontend without that extension
point, this extension is not loaded anywhere and has no effect.

## What it does

The entry is enabled once a CRF preview has been generated, like the built-in XML,
PDF and HTML entries, and exports the same forms. It opens a dialog with two
optional fields:

- **Rave study OID**: written as the Study OID, which Rave uses to match the
  target project.
- **Rave protocol name**: written as the ProtocolName, Rave's project name;
  50 characters at most.

**Export** downloads the transformed XML, then the dialog shows how many changes
were applied and lists any issue left for manual review, with its rule, location
and explanation. The applied changes can be expanded below.

## Layout

| File | Role |
|------|------|
| `crf-viewer/exportActions.js` | Registers the menu entry with the CRF Viewer |
| `components/RaveExportDialog.vue` | Dialog, export call and report |
| `components/ChangesTable.vue` | Table of transform changes |
| `api/extensions.js` | Client for `GET /odm-transform-export-ext/rave-exports` |
| `locales/en/` | Translations, under the `OdmTransformExport` key |

## Installation

Copy `odm-transform-export/` to the StudyBuilder frontend's `src/extensions/`.
It is picked up when the development server starts or the app is built, and its
API calls go to the extensions API at `EXTENSIONS_API_BASE_URL` from
`public/config.json`.

Run the frontend's checks from its `frontend/` directory:

```bash
npm run format
npm run lint
```
