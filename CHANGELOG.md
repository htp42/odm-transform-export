# Changelog

## 0.1.0

- `core`: `odm-transform-export` library with the Medidata Rave transform taken from
  the ODM XML Validator, and an in-memory entry point, `transform_odm`.
- `core`: foreign-namespace elements such as `osb:DesignNotes` are now removed under
  RAVE-003, and `mdsol:` is declared on the root element. Before, OpenStudyBuilder
  exports produced XML with undeclared namespace prefixes.
- `osb-api-extension`: `GET /odm-transform-export-ext/rave-exports`.
- `osb-frontend-extension`: **Export for Medidata Rave** in the CRF Viewer export menu.
