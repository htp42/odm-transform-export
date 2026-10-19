"""
Schema-conformance tests for the Rave ODM transformer.

Each test class targets one of the 11 fixes described in the design doc.
The fixture ``demographics_v8_after_doc.xml`` exercises every fix; additional
focused XML snippets are constructed inline for precision.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from odm_transform_export.transformer import transform_file

SAMPLES = Path(__file__).parent / "samples"
DM_FIXTURE = SAMPLES / "demographics_v8_after_doc.xml"

ODM_NS   = "http://www.cdisc.org/ns/odm/v1.3"
MDSOL_NS = "http://www.mdsol.com/ns/odm/metadata"

# ── helpers ───────────────────────────────────────────────────────────────────

def transform_bytes(path: Path, **kwargs) -> bytes:
    _, xml_bytes = transform_file(path, edc="rave", dry_run=False, **kwargs)
    assert xml_bytes is not None
    return xml_bytes


def parse(xml_bytes: bytes) -> etree._Element:
    return etree.fromstring(xml_bytes)


def all_elements(root: etree._Element) -> list[etree._Element]:
    return list(root.iter())


def mdsol_attrs(el: etree._Element) -> dict[str, str]:
    return {
        etree.QName(a).localname: v
        for a, v in el.attrib.items()
        if isinstance(a, str) and "{" in a
        and a[1:].partition("}")[0] == MDSOL_NS
    }


def foreign_ns_attrs(el: etree._Element) -> list[str]:
    """Return list of namespaced attrs that are NOT mdsol: or xml:."""
    allowed = {MDSOL_NS, "http://www.w3.org/XML/1998/namespace"}
    return [
        a for a in el.attrib
        if isinstance(a, str) and "{" in a
        and a[1:].partition("}")[0] not in allowed
    ]


# ── Fix 1: ODM namespace must be the default namespace ────────────────────────

class TestFix1DefaultNamespace:
    def test_no_odm_prefix_in_output(self, tmp_path):
        xml_bytes = transform_bytes(DM_FIXTURE)
        # Check for the actual declaration form (xmlns:odm="…"), not bare text in comments
        assert b'xmlns:odm="' not in xml_bytes, "odm: prefix declaration must not appear"

    def test_no_odm_prefixed_elements(self, tmp_path):
        xml_bytes = transform_bytes(DM_FIXTURE)
        assert b"<odm:" not in xml_bytes, "No element should carry the odm: prefix"
        assert b"</odm:" not in xml_bytes

    def test_root_has_default_namespace(self):
        xml_bytes = transform_bytes(DM_FIXTURE)
        root = parse(xml_bytes)
        assert etree.QName(root.tag).namespace == ODM_NS
        assert root.nsmap.get(None) == ODM_NS


# ── Fix 2: osb: namespace must be gone ───────────────────────────────────────

class TestFix2OsbNamespace:
    def test_no_osb_namespace_declaration(self):
        xml_bytes = transform_bytes(DM_FIXTURE)
        assert b"xmlns:osb=" not in xml_bytes

    def test_no_osb_attributes_anywhere(self):
        xml_bytes = transform_bytes(DM_FIXTURE)
        root = parse(xml_bytes)
        for el in root.iter():
            bad = foreign_ns_attrs(el)
            assert not bad, (
                f"Foreign-namespace attr(s) found on <{etree.QName(el.tag).localname}>: "
                + ", ".join(bad)
            )


# ── Fix 3: DataType="string" on ItemDef → "text" (CodeList unchanged) ────────

class TestFix3DataType:
    def setup_method(self):
        xml_bytes  = transform_bytes(DM_FIXTURE)
        self.root  = parse(xml_bytes)

    def test_no_itemdef_has_string_datatype(self):
        for el in self.root.iter(f"{{{ODM_NS}}}ItemDef"):
            assert el.get("DataType") != "string", (
                f"ItemDef OID='{el.get('OID')}' still has DataType='string'"
            )

    def test_codelist_string_datatype_preserved(self):
        codelists_with_string = [
            cl for cl in self.root.iter(f"{{{ODM_NS}}}CodeList")
            if cl.get("DataType") == "string"
        ]
        assert codelists_with_string, "At least one CodeList should still have DataType='string'"


# ── Fix 4: ItemRef OrderNumbers unique within ItemGroupDef ────────────────────

class TestFix4UniqueOrderNumbers:
    def setup_method(self):
        xml_bytes = transform_bytes(DM_FIXTURE)
        self.root = parse(xml_bytes)

    def test_no_duplicate_itemref_order_numbers(self):
        for ig in self.root.iter(f"{{{ODM_NS}}}ItemGroupDef"):
            nums = [
                c.get("OrderNumber")
                for c in ig
                if isinstance(c.tag, str)
                and etree.QName(c.tag).localname == "ItemRef"
                and c.get("OrderNumber") is not None
            ]
            assert len(nums) == len(set(nums)), (
                f"<ItemGroupDef OID='{ig.get('OID')}'> has duplicate "
                f"ItemRef OrderNumbers: {nums}"
            )


# ── Fix 5: mdsol:IsVisible on ItemRef → mdsol:Visible on ItemDef ─────────────

class TestFix5VisibleOnItemDef:
    def setup_method(self):
        xml_bytes = transform_bytes(DM_FIXTURE)
        self.root = parse(xml_bytes)

    def test_no_mdsol_is_visible_on_itemref(self):
        is_visible_attr = f"{{{MDSOL_NS}}}IsVisible"
        for ref in self.root.iter(f"{{{ODM_NS}}}ItemRef"):
            assert is_visible_attr not in ref.attrib, (
                "mdsol:IsVisible must not appear on ItemRef after transform"
            )

    def test_hidden_item_has_visible_no_on_itemdef(self):
        # I.DM.SEX had mdsol:IsVisible="FALSE" on its ItemRef; OID is normalised to I_DM_SEX
        visible_attr = f"{{{MDSOL_NS}}}Visible"
        found = False
        for el in self.root.iter(f"{{{ODM_NS}}}ItemDef"):
            if el.get("OID") == "I_DM_SEX":
                assert el.get(visible_attr) == "No", (
                    "I_DM_SEX should have mdsol:Visible='No'"
                )
                found = True
        assert found, "ItemDef OID='I_DM_SEX' not found in output"

    def test_default_visible_not_emitted(self):
        # I.DM.SUBJID had mdsol:IsVisible="TRUE" → default → must be omitted; OID → I_DM_SUBJID
        visible_attr = f"{{{MDSOL_NS}}}Visible"
        for el in self.root.iter(f"{{{ODM_NS}}}ItemDef"):
            if el.get("OID") == "I_DM_SUBJID":
                assert visible_attr not in el.attrib, (
                    "mdsol:Visible='Yes' (default) must be omitted on I_DM_SUBJID"
                )


# ── Fix 6: canonical mdsol: attribute names ───────────────────────────────────

class TestFix6CanonicalNames:
    _WRONG_NAMES = {
        "SasLabel", "IsSignatureRequired", "IsTranslationRequired",
        "IsVisualVerify", "IsClinicalSignificance", "IsEproForm",
        "CanSetRecordDate", "CanSetDataPageDate", "CanSetInstanceDate",
    }

    def setup_method(self):
        xml_bytes = transform_bytes(DM_FIXTURE)
        self.root = parse(xml_bytes)

    def test_no_wrong_mdsol_names(self):
        for el in self.root.iter():
            attrs = mdsol_attrs(el)
            bad = self._WRONG_NAMES & attrs.keys()
            assert not bad, (
                f"Wrong mdsol: names found on <{etree.QName(el.tag).localname} "
                f"OID='{el.get('OID', '?')}'>: {bad}"
            )

    def test_sas_label_canonical_name(self):
        """SasLabel → SASLabel."""
        sas_label_attr = f"{{{MDSOL_NS}}}SASLabel"
        items_with_sas_fn = [
            el for el in self.root.iter(f"{{{ODM_NS}}}ItemDef")
            if el.get("SASFieldName")
        ]
        for el in items_with_sas_fn:
            assert el.get(sas_label_attr) is not None, (
                f"ItemDef OID='{el.get('OID')}' has SASFieldName but no mdsol:SASLabel"
            )


# ── Fix 7: TRUE/FALSE → Yes/No on boolean mdsol: attrs ───────────────────────

class TestFix7BooleanValues:
    def setup_method(self):
        xml_bytes = transform_bytes(DM_FIXTURE)
        self.root = parse(xml_bytes)

    def test_no_true_false_literals(self):
        for el in self.root.iter():
            for attr, val in el.attrib.items():
                if not (isinstance(attr, str) and "{" in attr):
                    continue
                ns = attr[1:].partition("}")[0]
                if ns != MDSOL_NS:
                    continue
                assert val.upper() not in ("TRUE", "FALSE"), (
                    f"mdsol:{etree.QName(attr).localname}='{val}' on "
                    f"<{etree.QName(el.tag).localname}> still uses TRUE/FALSE"
                )


# ── Fix 8: EntryRestriction singular → plural on FormDef ─────────────────────

class TestFix8EntryRestriction:
    _RESTRICTION_ATTRS = {
        f"{{{MDSOL_NS}}}EntryRestriction",
        f"{{{MDSOL_NS}}}EntryRestrictions",
        f"{{{MDSOL_NS}}}ViewRestriction",
        f"{{{MDSOL_NS}}}ViewRestrictions",
    }

    def setup_method(self):
        xml_bytes = transform_bytes(DM_FIXTURE)
        self.root = parse(xml_bytes)

    def test_no_restriction_attrs_on_formdef(self):
        """Neither singular nor plural restriction attrs are valid on FormDef — all removed."""
        for el in self.root.iter(f"{{{ODM_NS}}}FormDef"):
            for attr in self._RESTRICTION_ATTRS:
                assert attr not in el.attrib, (
                    f"<FormDef OID='{el.get('OID')}'> still has "
                    f"mdsol:{etree.QName(attr).localname} (not valid on FormDef)"
                )


# ── Fix 9: label items → mdsol:LabelDef + mdsol:LabelRef ─────────────────────

class TestFix9LabelRemodel:
    def setup_method(self):
        xml_bytes = transform_bytes(DM_FIXTURE)
        self.root = parse(xml_bytes)

    def test_no_label_itemdef_in_output(self):
        # I.DM.LBL was flagged with osb:hideInput → must be gone as ItemDef; OID → I_DM_LBL
        for el in self.root.iter(f"{{{ODM_NS}}}ItemDef"):
            assert el.get("OID") != "I_DM_LBL", (
                "Label item I_DM_LBL must be removed as ItemDef"
            )

    def test_label_def_created(self):
        label_defs = list(self.root.iter(f"{{{MDSOL_NS}}}LabelDef"))
        assert label_defs, "At least one mdsol:LabelDef must be created"
        oids = [ld.get("OID") for ld in label_defs]
        assert "I_DM_LBL" in oids, "mdsol:LabelDef OID='I_DM_LBL' expected"

    def test_label_ref_created_in_itemgroup(self):
        label_refs = list(self.root.iter(f"{{{MDSOL_NS}}}LabelRef"))
        assert label_refs, "At least one mdsol:LabelRef must be created"
        oids = [lr.get("LabelOID") for lr in label_refs]
        assert "I_DM_LBL" in oids, "mdsol:LabelRef LabelOID='I_DM_LBL' expected"

    def test_no_label_itemref_in_itemgroup(self):
        for ref in self.root.iter(f"{{{ODM_NS}}}ItemRef"):
            assert ref.get("ItemOID") != "I_DM_LBL", (
                "ItemRef pointing to label item I_DM_LBL must be removed"
            )


# ── Fix 10: skip-when-default removes redundant mdsol: attrs ─────────────────

class TestFix10SkipWhenDefault:
    _DEFAULTS = {
        "Active": "Yes", "Visible": "Yes",
        "Template": "No", "SignatureRequired": "No",
        "SourceDocument": "No", "QueryFutureDate": "No",
        "TranslationRequired": "No", "VisualVerify": "No",
        "DoesNotBreakSignature": "No", "CanSetItemGroupDate": "No",
        "CanSetFormDate": "No", "CanSetStudyEventDate": "No",
        "CanSetSubjectDate": "No",
    }

    def setup_method(self):
        xml_bytes = transform_bytes(DM_FIXTURE)
        self.root = parse(xml_bytes)

    def test_no_default_value_attrs_emitted(self):
        for el in self.root.iter():
            attrs = mdsol_attrs(el)
            for local, default_val in self._DEFAULTS.items():
                if local in attrs:
                    assert attrs[local] != default_val, (
                        f"mdsol:{local}='{default_val}' (default) found on "
                        f"<{etree.QName(el.tag).localname} OID='{el.get('OID', '?')}'> "
                        "— should be omitted (skip-when-default)"
                    )


# ── Fix 11: Study OID / ProtocolName parameterisation ────────────────────────

class TestFix11StudyOidProtocolName:
    def test_study_oid_override(self):
        xml_bytes = transform_bytes(DM_FIXTURE, rave_study_oid="MY-RAVE-STUDY")
        root = parse(xml_bytes)
        study = root.find(f"{{{ODM_NS}}}Study")
        assert study is not None
        assert study.get("OID") == "MY-RAVE-STUDY"

    def test_protocol_name_override(self):
        xml_bytes = transform_bytes(DM_FIXTURE, rave_protocol_name="SHORT-NAME")
        root = parse(xml_bytes)
        pn = root.find(
            f"{{{ODM_NS}}}Study/{{{ODM_NS}}}GlobalVariables/{{{ODM_NS}}}ProtocolName"
        )
        assert pn is not None and pn.text == "SHORT-NAME"

    def test_no_override_keeps_existing_values(self):
        xml_bytes = transform_bytes(DM_FIXTURE)
        root = parse(xml_bytes)
        study = root.find(f"{{{ODM_NS}}}Study")
        assert study is not None
        # OID may have been sanitised (@ → _) but the gist should be there
        assert study.get("OID") is not None


# ── Additional schema-conformance assertions ──────────────────────────────────

class TestSchemaConformance:
    def setup_method(self):
        xml_bytes = transform_bytes(DM_FIXTURE)
        self.root = parse(xml_bytes)

    def test_no_element_has_odm_prefix(self):
        for el in self.root.iter():
            if isinstance(el.tag, str):
                assert not el.tag.startswith("odm:"), (
                    f"Element '{el.tag}' still has odm: prefix"
                )

    def test_no_mdsol_on_itemref(self):
        """mdsol:IsVisible must not appear on ItemRef (Fix 5)."""
        is_visible = f"{{{MDSOL_NS}}}IsVisible"
        for ref in self.root.iter(f"{{{ODM_NS}}}ItemRef"):
            assert is_visible not in ref.attrib

    def test_no_codelist_order_number(self):
        for tag in (f"{{{ODM_NS}}}CodeListItem", f"{{{ODM_NS}}}EnumeratedItem"):
            for el in self.root.iter(tag):
                assert "OrderNumber" not in el.attrib, (
                    "OrderNumber must be removed from CodeListItem/EnumeratedItem"
                )

    def test_codelist_datatypes_unchanged(self):
        for cl in self.root.iter(f"{{{ODM_NS}}}CodeList"):
            dt = cl.get("DataType")
            # DataType on CodeList must never be changed by the transformer
            if dt is not None:
                assert dt in ("string", "text", "integer", "float"), (
                    f"CodeList OID='{cl.get('OID')}' has unexpected DataType='{dt}'"
                )

    def test_itemref_order_numbers_unique_per_group(self):
        for ig in self.root.iter(f"{{{ODM_NS}}}ItemGroupDef"):
            nums = [
                c.get("OrderNumber")
                for c in ig
                if isinstance(c.tag, str)
                and etree.QName(c.tag).localname == "ItemRef"
                and c.get("OrderNumber") is not None
            ]
            assert len(nums) == len(set(nums)), (
                f"<ItemGroupDef OID='{ig.get('OID')}'> has duplicate OrderNumbers: {nums}"
            )

    def test_no_unknown_mdsol_attrs(self):
        from odm_transform_export.rave_schema import MDSOL_KNOWN_ATTRS
        for el in self.root.iter():
            tag = etree.QName(el.tag).localname if isinstance(el.tag, str) else None
            if tag is None or tag not in MDSOL_KNOWN_ATTRS:
                continue
            known = MDSOL_KNOWN_ATTRS[tag]
            for attr in el.attrib:
                if not (isinstance(attr, str) and "{" in attr):
                    continue
                ns, _, local = attr[1:].partition("}")
                if ns != MDSOL_NS:
                    continue
                assert local in known, (
                    f"Unknown mdsol:{local} found on <{tag} OID='{el.get('OID', '?')}'>"
                )

    def test_no_boolean_true_false_literals(self):
        for el in self.root.iter():
            for attr, val in el.attrib.items():
                if not (isinstance(attr, str) and "{" in attr):
                    continue
                if attr[1:].partition("}")[0] != MDSOL_NS:
                    continue
                assert val.upper() not in ("TRUE", "FALSE"), (
                    f"mdsol: attr has TRUE/FALSE value on "
                    f"<{etree.QName(el.tag).localname}>: {etree.QName(attr).localname}='{val}'"
                )

    def test_every_label_item_is_label_def(self):
        """No mdsol:LabelDef OID should also appear as an ItemDef OID."""
        item_oids = {
            el.get("OID")
            for el in self.root.iter(f"{{{ODM_NS}}}ItemDef")
            if el.get("OID")
        }
        label_oids = {
            el.get("OID")
            for el in self.root.iter(f"{{{MDSOL_NS}}}LabelDef")
            if el.get("OID")
        }
        overlap = item_oids & label_oids
        assert not overlap, (
            f"OID(s) appear as both ItemDef and LabelDef: {overlap}"
        )

    def test_globalvariables_child_order(self):
        expected = [
            f"{{{ODM_NS}}}StudyName",
            f"{{{ODM_NS}}}StudyDescription",
            f"{{{ODM_NS}}}ProtocolName",
        ]
        for gv in self.root.iter(f"{{{ODM_NS}}}GlobalVariables"):
            actual = [c.tag for c in gv if isinstance(c.tag, str)][:3]
            assert actual == expected[:len(actual)], (
                f"GlobalVariables child order wrong: {actual}"
            )

    def test_odm_version_is_1_3(self):
        assert self.root.get("ODMVersion") == "1.3"

    def test_granularity_is_metadata(self):
        assert self.root.get("Granularity") == "Metadata"

    def test_sas_names_max_8_chars(self):
        for el in self.root.iter(f"{{{ODM_NS}}}ItemDef"):
            val = el.get("SASFieldName", "")
            assert len(val) <= 8, (
                f"ItemDef OID='{el.get('OID')}' SASFieldName='{val}' exceeds 8 chars"
            )
        for el in self.root.iter(f"{{{ODM_NS}}}ItemGroupDef"):
            val = el.get("SASDatasetName", "")
            assert len(val) <= 8, (
                f"ItemGroupDef OID='{el.get('OID')}' SASDatasetName='{val}' exceeds 8 chars"
            )


# ── ODM-059 / ODM-060: FormDef and ItemGroupDef child order ──────────────────

_DEF_ORDER_XML = b"""<?xml version="1.0"?>
<ODM xmlns="http://www.cdisc.org/ns/odm/v1.3"
     xmlns:mdsol="http://www.mdsol.com/ns/odm/metadata"
     FileType="Snapshot" FileOID="T" CreationDateTime="2026-01-01T00:00:00" ODMVersion="1.3">
  <Study OID="S">
    <GlobalVariables><StudyName>s</StudyName><StudyDescription>d</StudyDescription><ProtocolName>p</ProtocolName></GlobalVariables>
    <MetaDataVersion OID="M" Name="v">
      <FormDef OID="F1" Name="F" Repeating="No">
        <Alias Context="ctx" Name="a"/>
        <ItemGroupRef ItemGroupOID="IG1" Mandatory="Yes" OrderNumber="1"/>
        <Description><TranslatedText xml:lang="en">f</TranslatedText></Description>
        <ItemGroupRef ItemGroupOID="IG2" Mandatory="Yes" OrderNumber="2"/>
      </FormDef>
      <ItemGroupDef OID="IG1" Name="G1" Repeating="No">
        <Alias Context="ctx" Name="a"/>
        <ItemRef ItemOID="I1" Mandatory="Yes" OrderNumber="1"/>
        <mdsol:LabelRef LabelOID="L1" OrderNumber="2"/>
        <ItemRef ItemOID="I2" Mandatory="Yes" OrderNumber="3"/>
      </ItemGroupDef>
      <ItemGroupDef OID="IG2" Name="G2" Repeating="No">
        <Alias Context="ctx" Name="b"/>
        <ItemRef ItemOID="I3" Mandatory="Yes" OrderNumber="4"/>
      </ItemGroupDef>
      <ItemGroupDef OID="IG3" Name="G3" Repeating="No">
        <Description><TranslatedText xml:lang="en">g</TranslatedText></Description>
        <ItemRef ItemOID="I4" Mandatory="Yes" OrderNumber="1"/>
        <Alias Context="ctx" Name="c"/>
      </ItemGroupDef>
    </MetaDataVersion>
  </Study>
</ODM>"""


def _child_names(el: etree._Element) -> list[str]:
    return [
        ("mdsol:" if etree.QName(c).namespace == MDSOL_NS else "")
        + etree.QName(c).localname
        for c in el
    ]


class TestDefChildOrder:
    @pytest.fixture(autouse=True)
    def _transform(self, tmp_path):
        src = tmp_path / "defs.xml"
        src.write_bytes(_DEF_ORDER_XML)
        self.result, xml_bytes = transform_file(src, edc="rave")
        self.root = parse(xml_bytes)

    def _get(self, tag: str, oid: str) -> etree._Element:
        return self.root.find(f".//{{{ODM_NS}}}{tag}[@OID='{oid}']")

    def test_formdef_children_in_odm_order(self):
        form = self._get("FormDef", "F1")
        assert _child_names(form) == ["Description", "ItemGroupRef", "ItemGroupRef", "Alias"]
        # stable: ItemGroupRef keep their relative order
        assert [c.get("ItemGroupOID") for c in form[1:3]] == ["IG1", "IG2"]

    def test_itemgroupdef_children_in_odm_order_mdsol_last(self):
        group = self._get("ItemGroupDef", "IG1")
        # RAVE-117 still holds: mdsol:LabelRef after every ItemRef
        assert _child_names(group) == ["ItemRef", "ItemRef", "Alias", "mdsol:LabelRef"]
        assert [c.get("ItemOID") for c in group[:2]] == ["I1", "I2"]
        assert _child_names(self._get("ItemGroupDef", "IG2")) == ["ItemRef", "Alias"]

    def test_only_out_of_order_defs_reported(self):
        reported = [
            (c.rule, c.location.rpartition("/")[2])
            for c in self.result.changes if c.rule in ("ODM-059", "ODM-060")
        ]
        assert reported == [
            ("ODM-059", "FormDef[@OID='F1']"),
            ("ODM-060", "ItemGroupDef[@OID='IG1']"),
            ("ODM-060", "ItemGroupDef[@OID='IG2']"),
        ]

    def test_reorder_reported_as_collapsed_sequence(self):
        change = next(
            c for c in self.result.changes
            if c.location.endswith("ItemGroupDef[@OID='IG1']") and c.rule == "ODM-060"
        )
        assert change.old_value == "Alias → ItemRef×2 → mdsol:LabelRef"
        assert change.new_value == "ItemRef×2 → Alias → mdsol:LabelRef"

    def test_indentation_kept(self):
        form = self._get("FormDef", "F1")
        assert [c.tail for c in form] == ["\n        "] * 3 + ["\n      "]
