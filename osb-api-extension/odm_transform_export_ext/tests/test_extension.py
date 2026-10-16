import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from clinical_mdr_api.domains.odms.utils import TargetType
from extensions.extensions_api import app

# The extensions loader imports each extension's main module as a top-level module
extension = sys.modules["odm_transform_export_ext_main"]

URL = "/odm-transform-export-ext/rave-exports"

# Shaped like an OpenStudyBuilder export: `odm` declared as a prefix but unused
# on the elements, `osb:` vendor attributes, ODM 1.3.2 header values
OSB_EXPORT = b"""<?xml version="1.0" encoding="utf-8"?>
<ODM xmlns:odm="http://www.cdisc.org/ns/odm/v1.3" xmlns:osb="http://openstudybuilder.org"
     ODMVersion="1.3.2" FileType="Snapshot" FileOID="OID.1"
     CreationDateTime="2026-10-16 10:00:00.123456" Granularity="All">
  <Study OID="OSB-Study">
    <GlobalVariables>
      <StudyName>Demo</StudyName>
      <StudyDescription>Demo study</StudyDescription>
      <ProtocolName>DEMO-001</ProtocolName>
    </GlobalVariables>
    <MetaDataVersion OID="MDV.1" Name="v1">
      <FormDef OID="F.DM" Name="Demographics" Repeating="No" osb:version="1.0">
        <ItemGroupRef ItemGroupOID="IG.DM" Mandatory="Yes" OrderNumber="1"/>
      </FormDef>
      <ItemGroupDef OID="IG.DM" Name="DM" Repeating="No">
        <ItemRef ItemOID="I.SEX" Mandatory="Yes" OrderNumber="1"/>
        <ItemRef ItemOID="I.CONSENT" Mandatory="Yes" OrderNumber="2"/>
      </ItemGroupDef>
      <ItemDef OID="I.SEX" Name="Sex" DataType="string" Length="1"/>
      <ItemDef OID="I.CONSENT" Name="Consent" DataType="boolean"/>
    </MetaDataVersion>
  </Study>
</ODM>
"""


@pytest.fixture(scope="module")
def api_client():
    """Create FastAPI test client"""
    yield TestClient(app)


@pytest.fixture
def exporter():
    with patch.object(extension, "OdmXmlExporterService") as exporter_class:
        exporter_class.return_value.get_odm_document.return_value = OSB_EXPORT
        yield exporter_class


def test_exports_targets_with_all_vendor_namespaces(api_client, exporter):
    res = api_client.get(
        URL, params={"target_type": "form", "targets": ["OdmForm_1", "OdmForm_2"]}
    )

    assert res.status_code == 200
    exporter.assert_called_once_with(
        TargetType.FORM, ["OdmForm_1", "OdmForm_2"], ["*"], False, None, None
    )


def test_returns_rave_compatible_xml(api_client, exporter):
    res = api_client.get(URL, params={"target_type": "form", "targets": "OdmForm_1"})
    body = res.json()

    assert res.status_code == 200
    assert body["filename"].startswith("odm_rave_export_")
    assert body["filename"].endswith(".xml")
    assert 'xmlns="http://www.cdisc.org/ns/odm/v1.3"' in body["xml"]
    assert 'ODMVersion="1.3"' in body["xml"]
    assert 'Granularity="Metadata"' in body["xml"]
    assert "osb:" not in body["xml"]
    assert 'DataType="text"' in body["xml"]


def test_reports_applied_and_skipped_changes(api_client, exporter):
    body = api_client.get(
        URL, params={"target_type": "form", "targets": "OdmForm_1"}
    ).json()

    statuses = [change["status"] for change in body["changes"]]
    assert body["applied_count"] == statuses.count("applied") > 0
    assert body["skipped_count"] == statuses.count("skipped")
    # DataType="boolean" cannot be fixed automatically
    assert any(
        change["rule"] == "RAVE-030" and change["status"] == "skipped"
        for change in body["changes"]
    )


def test_overrides_study_oid_and_protocol_name(api_client, exporter):
    body = api_client.get(
        URL,
        params={
            "target_type": "form",
            "targets": "OdmForm_1",
            "rave_study_oid": "MY_RAVE_PROJECT",
            "rave_protocol_name": " Short name ",
        },
    ).json()

    assert '<Study OID="MY_RAVE_PROJECT">' in body["xml"]
    assert "<ProtocolName>Short name</ProtocolName>" in body["xml"]


def test_ignores_blank_overrides(api_client, exporter):
    body = api_client.get(
        URL,
        params={
            "target_type": "form",
            "targets": "OdmForm_1",
            "rave_study_oid": "  ",
            "rave_protocol_name": "",
        },
    ).json()

    assert '<Study OID="OSB-Study">' in body["xml"]
    assert "<ProtocolName>DEMO-001</ProtocolName>" in body["xml"]


def test_rejects_protocol_name_over_50_characters(api_client, exporter):
    res = api_client.get(
        URL,
        params={
            "target_type": "form",
            "targets": "OdmForm_1",
            "rave_protocol_name": "x" * 51,
        },
    )

    assert res.status_code == 400
    exporter.assert_not_called()


def test_requires_targets(api_client, exporter):
    res = api_client.get(URL, params={"target_type": "form"})

    assert res.status_code == 400
    exporter.assert_not_called()
