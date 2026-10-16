"""
Tests for the in-memory entry point `transform_odm`.

The rule-by-rule behaviour is covered in test_transform.py through
`transform_file`, which delegates to `transform_odm`.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from odm_transform_export import ChangeStatus, transform_file, transform_odm

DM_FIXTURE = Path(__file__).parent / "samples" / "demographics_v8_after_doc.xml"
ODM_NS = "http://www.cdisc.org/ns/odm/v1.3"


def test_bytes_and_file_entry_points_agree():
    xml = DM_FIXTURE.read_bytes()

    from_bytes, from_bytes_xml = transform_odm(xml, "rave")
    from_file, from_file_xml = transform_file(DM_FIXTURE, "rave")

    assert [c.rule for c in from_bytes.changes] == [c.rule for c in from_file.changes]
    # CreationDateTime and the provenance comment carry a timestamp
    strip = lambda b: b"\n".join(  # noqa: E731
        line for line in b.splitlines()
        if b"CreationDateTime" not in line and b"Transformed for" not in line
    )
    assert strip(from_bytes_xml) == strip(from_file_xml)


def test_output_is_rave_header():
    _, out = transform_odm(DM_FIXTURE.read_bytes(), "rave")
    root = etree.fromstring(out)

    assert root.tag == f"{{{ODM_NS}}}ODM"
    assert root.get("ODMVersion") == "1.3"
    assert root.get("Granularity") == "Metadata"


def test_overrides_are_forwarded():
    _, out = transform_odm(
        DM_FIXTURE.read_bytes(),
        "rave",
        rave_study_oid="MY_RAVE_PROJECT",
        rave_protocol_name="Short name",
    )
    root = etree.fromstring(out)
    study = root.find(f"{{{ODM_NS}}}Study")

    assert study.get("OID") == "MY_RAVE_PROJECT"
    assert study.findtext(f".//{{{ODM_NS}}}ProtocolName") == "Short name"


def test_source_name_labels_result():
    result, _ = transform_odm(DM_FIXTURE.read_bytes(), "rave", source_name="osb.xml")
    assert result.source_file == "osb.xml"


def test_dry_run_returns_no_bytes():
    result, out = transform_odm(DM_FIXTURE.read_bytes(), "rave", dry_run=True)
    assert out is None
    assert any(c.status == ChangeStatus.APPLIED for c in result.changes)


def test_unparsable_xml_raises_value_error():
    with pytest.raises(ValueError, match="Cannot parse"):
        transform_odm(b"<ODM><Study>", "rave")


def test_unknown_edc_raises_value_error():
    with pytest.raises(ValueError, match="Unknown EDC"):
        transform_odm(DM_FIXTURE.read_bytes(), "redcap")


def test_entities_are_not_expanded():
    xml = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE ODM [<!ENTITY secret SYSTEM "file:///etc/hosts">]>'
        b'<ODM xmlns="http://www.cdisc.org/ns/odm/v1.3" ODMVersion="1.3.2">'
        b'<Study OID="S"><GlobalVariables><StudyName>s</StudyName>'
        b"<StudyDescription>d</StudyDescription><ProtocolName>p</ProtocolName>"
        b'</GlobalVariables><MetaDataVersion OID="M" Name="v">'
        b'<FormDef OID="F" Name="f" Repeating="No"><Description>'
        b"<TranslatedText>&secret;</TranslatedText>"
        b"</Description></FormDef></MetaDataVersion></Study></ODM>"
    )
    _, out = transform_odm(xml, "rave")
    assert b"&secret;" in out
    assert b"localhost" not in out
