"""
Regression tests against a real OpenStudyBuilder export.

``osb_export_informed_consent.xml`` is the unmodified output of
``POST /odms/metadata/xmls/export?targets=OdmForm_000001&target_type=form&allowed_namespaces=*``
on the OpenStudyBuilder demo database. Unlike the hand-written fixtures it
carries osb: vendor *elements* (osb:DesignNotes, osb:CompletionInstructions,
osb:DisplayText, osb:DomainColor), not only osb: attributes, and writes Alias
before ItemGroupRef / ItemRef.
"""
from __future__ import annotations

from pathlib import Path

from lxml import etree

from odm_transform_export import ChangeStatus, transform_file, transform_odm

OSB_EXPORT = Path(__file__).parent / "samples" / "osb_export_informed_consent.xml"

ODM_NS   = "http://www.cdisc.org/ns/odm/v1.3"
MDSOL_NS = "http://www.mdsol.com/ns/odm/metadata"
XML_NS   = "http://www.w3.org/XML/1998/namespace"


def test_real_export_becomes_well_formed_rave_odm():
    _, out = transform_file(OSB_EXPORT, "rave")

    root = etree.fromstring(out)  # raises if a prefix was left undeclared
    assert root.tag == f"{{{ODM_NS}}}ODM"
    assert set(root.nsmap.values()) <= {ODM_NS, MDSOL_NS}


def test_real_export_keeps_no_foreign_namespace_content():
    _, out = transform_file(OSB_EXPORT, "rave")
    root = etree.fromstring(out)

    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        assert etree.QName(el.tag).namespace in {ODM_NS, MDSOL_NS}, el.tag
        for attr in el.attrib:
            if attr.startswith("{"):
                assert attr[1:].partition("}")[0] in {MDSOL_NS, XML_NS}, attr


def test_real_export_reports_removed_elements():
    result, _ = transform_file(OSB_EXPORT, "rave")

    element_strip = [
        c for c in result.changes
        if c.rule == "RAVE-003" and "element" in c.description
    ]
    assert len(element_strip) == 1
    assert element_strip[0].status == ChangeStatus.APPLIED
    assert "osb:DesignNotes" in element_strip[0].old_value


def test_real_export_def_children_in_odm_order():
    result, out = transform_file(OSB_EXPORT, "rave")
    root = etree.fromstring(out)

    form = root.find(f".//{{{ODM_NS}}}FormDef")
    assert [etree.QName(c).localname for c in form] == [
        "Description", "ItemGroupRef", "ItemGroupRef", "Alias",
    ]
    for group in root.iter(f"{{{ODM_NS}}}ItemGroupDef"):
        names = [etree.QName(c).localname for c in group]
        assert names == sorted(names, key=["Description", "ItemRef", "Alias"].index)
    assert {c.rule for c in result.changes} >= {"ODM-059", "ODM-060"}


def test_foreign_elements_removed_with_their_children_mdsol_kept():
    xml = b"""<?xml version="1.0"?>
<ODM xmlns="http://www.cdisc.org/ns/odm/v1.3"
     xmlns:mdsol="http://www.mdsol.com/ns/odm/metadata"
     xmlns:osb="http://openstudybuilder.org" ODMVersion="1.3.2">
  <Study OID="S">
    <GlobalVariables><StudyName>s</StudyName><StudyDescription>d</StudyDescription><ProtocolName>p</ProtocolName></GlobalVariables>
    <MetaDataVersion OID="M" Name="v">
      <ItemDef OID="I1" Name="i" DataType="text" Length="10">
        <osb:DesignNotes><osb:Nested>x</osb:Nested></osb:DesignNotes>
        <mdsol:HelpText>help</mdsol:HelpText>
      </ItemDef>
    </MetaDataVersion>
  </Study>
</ODM>"""
    result, out = transform_odm(xml, "rave")
    root = etree.fromstring(out)
    item = root.find(f".//{{{ODM_NS}}}ItemDef")

    assert [etree.QName(c).localname for c in item] == ["HelpText"]
    removed = next(c for c in result.changes if c.rule == "RAVE-003")
    assert removed.old_value == "1 element(s): ItemDef/osb:DesignNotes"
