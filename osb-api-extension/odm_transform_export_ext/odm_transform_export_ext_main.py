"""Exports OpenStudyBuilder CRFs as ODM XML that Medidata Rave can import."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from odm_transform_export import transform_odm
from pydantic import BaseModel, Field

from clinical_mdr_api.domains.odms.utils import TargetType
from clinical_mdr_api.services.odms.xml_exporter import OdmXmlExporterService
from common.auth import rbac
from common.auth.dependencies import security

router = APIRouter(
    tags=["ODM Transform Export"],
)


class TransformChange(BaseModel):
    status: Annotated[
        str,
        Field(description="`applied`, or `skipped` when the issue needs manual review"),
    ]
    rule: Annotated[str, Field(description="Rule code, e.g. `RAVE-030`")]
    description: Annotated[str, Field(description="What was changed or found")]
    location: Annotated[str, Field(description="Path of the affected element")]
    old_value: Annotated[str | None, Field(description="Value before the change")] = (
        None
    )
    new_value: Annotated[str | None, Field(description="Value after the change")] = None
    note: Annotated[
        str | None, Field(description="Why the issue was skipped, or a caveat")
    ] = None


class RaveExport(BaseModel):
    filename: Annotated[str, Field(description="Suggested file name for the XML")]
    xml: Annotated[str, Field(description="Rave-compatible ODM XML document")]
    applied_count: Annotated[int, Field(description="Number of changes applied")]
    skipped_count: Annotated[
        int, Field(description="Number of issues left for manual review")
    ]
    changes: Annotated[
        list[TransformChange], Field(description="Every change applied or skipped")
    ]


@router.get(
    "/rave-exports",
    dependencies=[security, rbac.LIBRARY_READ],
    summary="Export ODM XML for Medidata Rave",
    status_code=200,
)
def get_rave_export(
    target_type: TargetType,
    targets: Annotated[
        list[str],
        Query(
            description="List of UIDs and (optionally) versions separated by comma. E.g. `uid1,v1` or `uid1` for latest version.",
        ),
    ],
    rave_study_oid: Annotated[
        str | None,
        Query(
            description="Study OID to write in the export. Rave uses it to match the target project.",
        ),
    ] = None,
    rave_protocol_name: Annotated[
        str | None,
        Query(
            description="ProtocolName to write in the export, Rave's project name (50 characters max). Defaults to the exported one, truncated to 50 characters.",
            max_length=50,
        ),
    ] = None,
) -> RaveExport:
    """
    Exports the targets as ODM XML with all vendor namespaces, as the
    `/concepts/odms/metadata/xmls/export` endpoint does, then transforms the
    document for a Medidata Rave study design import.

    The response carries the transformed XML and the list of changes. Changes
    with status `skipped` are issues the transform does not fix automatically,
    such as a boolean DataType or a question longer than 255 characters; they
    have to be resolved in OpenStudyBuilder or in Rave.
    """
    odm_xml = OdmXmlExporterService(
        target_type, targets, ["*"], False, None, None
    ).get_odm_document()

    overrides = {
        name: value.strip()
        for name, value in (
            ("rave_study_oid", rave_study_oid),
            ("rave_protocol_name", rave_protocol_name),
        )
        if value and value.strip()
    }
    result, rave_xml = transform_odm(
        odm_xml, "rave", source_name="openstudybuilder_export.xml", **overrides
    )

    return RaveExport(
        filename=datetime.now().strftime("odm_rave_export_%Y%m%d_%H%M%S.xml"),
        xml=rave_xml.decode("utf-8"),
        applied_count=len(result.applied),
        skipped_count=len(result.skipped),
        changes=[
            TransformChange(
                status=change.status.value,
                rule=change.rule,
                description=change.description,
                location=change.location,
                old_value=change.old_value,
                new_value=change.new_value,
                note=change.note,
            )
            for change in result.changes
        ],
    )
