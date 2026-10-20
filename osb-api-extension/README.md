# odm-transform-export — OSB API Extension

An extension for the OpenStudyBuilder extensions API. Its single endpoint exports
ODM targets the way the CRF Viewer's XML export does, with all vendor namespaces,
and transforms the document for a Medidata Rave study design import with the
[`odm-transform-export` core library](../core/README.md).

The extension calls OpenStudyBuilder's ODM exporter in-process
(`clinical_mdr_api.services.odms.xml_exporter.OdmXmlExporterService`), so it makes
no HTTP call to the main API and needs no extra configuration. That service is
internal to OpenStudyBuilder: if its constructor changes in a new release, the
extensions API fails to start with this extension installed. Run the tests below
against each new OpenStudyBuilder version.

## Installation

1. Install the core library in the extensions API's own Python environment:
   `uv pip install -e ../../odm-transform-export/core` from OpenStudyBuilder's
   `api/` (Docker builds: see the [root README](../README.md)). An overlay such as
   `uv run --with-editable … poe …` does not work, because `poe` starts the API
   through a fresh `uv run`.
2. Copy `odm_transform_export_ext/` to OpenStudyBuilder's `api/extensions/`.
3. Restart the extensions API. The extension is mounted at
   `/odm-transform-export-ext`.

## API

### `GET /odm-transform-export-ext/rave-exports`

Requires the `Library.Read` role, like the ODM XML export.

| Query parameter | Required | Description |
|-----------------|----------|-------------|
| `target_type` | Yes | ODM target type: `study_event`, `study`, `form`, `item_group` or `item` |
| `targets` | Yes, repeatable | Target UIDs, optionally with a version: `uid1,v1`, or `uid1` for the latest version |
| `rave_study_oid` | No | Study OID to write in the export; Rave uses it to match the target project |
| `rave_protocol_name` | No | ProtocolName to write, Rave's project name (50 characters max). Defaults to the exported one, truncated to 50 characters |

Response (`application/json`):

```json
{
  "filename": "odm_rave_export_20261016_135549.xml",
  "xml": "<?xml version='1.0' encoding='UTF-8'?>…",
  "applied_count": 149,
  "skipped_count": 0,
  "changes": [
    {
      "status": "applied",
      "rule": "RAVE-001",
      "description": "ODMVersion corrected for Rave.",
      "location": "/ODM",
      "old_value": "1.3.2",
      "new_value": "1.3",
      "note": null
    }
  ]
}
```

Changes with status `skipped` are issues the transform leaves to a person; see
[Left for manual review](../core/README.md#left-for-manual-review).

## Tests

The tests run inside OpenStudyBuilder's extensions API, through its extension
loader. The ODM exporter is mocked, but OpenStudyBuilder's authentication
dependency writes the request user to Neo4j, so the database configured in
`api/.env` must be reachable, as for every extension test. From OpenStudyBuilder's
`api/`, with the extension copied in and the core library installed (step 1):

```bash
uv run pytest extensions/odm_transform_export_ext
```

OpenStudyBuilder's route-permission test (`uv run poe extensions-testauth`)
expects every route to be listed in `extensions/tests/auth/integration/routes.py`.
When it runs with this extension installed, add:

```python
("/odm-transform-export-ext/rave-exports", "GET", {"Library.Read"}),
```
