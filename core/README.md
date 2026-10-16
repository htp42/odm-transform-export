# odm-transform-export — core

Python library that transforms a CDISC ODM 1.3.2 XML export into ODM that
Medidata Rave accepts for a study design import. The only dependency is
[lxml](https://lxml.de).

It applies every fix that can be made mechanically (namespace layout, OID
characters and length, DataTypes, `mdsol:` attribute names, casing and order,
label items, duplicate `OrderNumber`s, and more) and reports the issues that need
a person to decide.

## Install

```bash
pip install ./core           # from a checkout of this repository
pip install -e "./core[dev]" # for development, with pytest
```

## Usage

```python
from odm_transform_export import ChangeStatus, transform_odm

result, rave_xml = transform_odm(
    odm_xml_bytes,
    "rave",
    rave_study_oid="MY_RAVE_PROJECT",       # optional: Study OID, used by Rave to match the project
    rave_protocol_name="Short protocol",    # optional: ProtocolName, 50 characters max
)

for change in result.changes:
    if change.status == ChangeStatus.SKIPPED:
        print(change.rule, change.location, change.description, change.note)
```

`transform_odm` raises `ValueError` for XML that cannot be parsed and for an
unknown EDC. `transform_file(path, "rave", output_path=...)` does the same from
and to files.

Each `Change` has a `status` (`applied`, or `skipped` when the fix is left to a
person), the `rule` code, a `description`, the element `location`, and optional
`old_value`, `new_value` and `note`.

### Left for manual review

| Rule | Why it is not fixed automatically |
|------|-----------------------------------|
| RAVE-030 | `DataType="boolean"` on an `ItemDef` needs a 0/1 codelist that cannot be generated |
| RAVE-031 | `CodeList` and `ItemDef` DataTypes disagree, and either side could be the wrong one |
| RAVE-040 | Question text over 255 characters; truncating would change its meaning |
| RAVE-060 | `text` field without `Length`; there is no safe default |
| RAVE-063 | `float` field without `SignificantDigits`; it depends on the measurement precision |

## Input expectations

The transform was built against OpenStudyBuilder ODM exports, exported with all
vendor namespaces (`allowed_namespaces=*`):

- Rave-specific settings come from `mdsol:` vendor attributes. OpenStudyBuilder
  only exports them when they are defined as ODM vendor extensions in the
  OpenStudyBuilder library.
- Items exported with `osb:hideInput="TRUE"` become `mdsol:LabelDef` /
  `mdsol:LabelRef` label rows. Without that attribute they stay regular fields.
- Codelist OIDs of the form `BASE@DATASET` are merged back to `BASE` when their
  content is identical.

## Origin

The transform rules come from the ODM XML Validator (`odm-validate` /
`odm-transform`): `rave.py` and `models.py` are copies of its `transforms/`
modules, and `rave_schema.py` holds the constants of its Rave rule set. Keep the
copies in sync when a rule changes.

`rave.py` differs from the validator's copy in one import line and one fix, and
`transformer.py` adds one more. Both fixes came from running real OpenStudyBuilder
exports, and should be ported back to the validator:

- `_strip_foreign_ns_elements` (RAVE-003) removes vendor *elements* such as
  `osb:DesignNotes`; the validator only removes vendor attributes.
- `transform_odm` declares `mdsol:` on the root before serialising. Without it,
  `mdsol:` attributes added to a document that declared no `mdsol` namespace get a
  generated `ns0` prefix, whose declaration is then stripped.

Without these fixes the output has undeclared namespace prefixes, so it is not
well-formed XML.

### Known gap

The transform does not reorder `FormDef` and `ItemGroupDef` children.
OpenStudyBuilder writes `Alias` before `ItemGroupRef` / `ItemRef`, which the ODM 1.3
schema does not allow (validator rules ODM-059 and ODM-060).

## Tests

```bash
cd core
pip install -e ".[dev]"
pytest
```
