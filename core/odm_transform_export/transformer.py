"""
Parse, transform and serialise an ODM XML document for an EDC.

`transform_odm` works on bytes, so callers that already hold the document in
memory (such as the OpenStudyBuilder API extension) never touch the disk.
`transform_file` is the path-based wrapper used by the tests.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree

from .models import TransformResult
from .rave import transform_for_rave

EDC_TRANSFORMERS: dict[str, Callable[..., TransformResult]] = {
    "rave":          transform_for_rave,
    "medidata-rave": transform_for_rave,
    "medidata rave": transform_for_rave,
}

_ODM_URI = b"http://www.cdisc.org/ns/odm/v1.3"
_MDSOL_URI = "http://www.mdsol.com/ns/odm/metadata"


def detect_namespace(root: etree._Element) -> str | None:
    ns = root.nsmap.get(None) or root.nsmap.get("odm")
    if ns:
        return ns
    m = re.match(r"\{(.+)\}", root.tag)
    return m.group(1) if m else None


def _normalize_namespace_output(xml_bytes: bytes) -> bytes:
    """
    Fix 1: If the ODM namespace is serialized with a prefix (xmlns:odm=…),
    convert it to the default namespace declaration (xmlns=…) and strip the
    prefix from all element names.  lxml preserves the original prefix from the
    input document; Rave's RWS schema validator requires the default namespace.

    Fix 2 (declaration side): also strip any remaining xmlns:osb=… or other
    foreign-vendor namespace declarations from the root element.  Attribute-level
    stripping is done in the transform itself; this only cleans up the root decl.
    """
    # ── Fix 1: odm: prefix → default namespace ───────────────────────────────
    m = re.search(rb'xmlns:(\w+)="' + re.escape(_ODM_URI) + rb'"', xml_bytes)
    if m:
        prefix   = m.group(1)
        xml_bytes = xml_bytes.replace(m.group(0), b'xmlns="' + _ODM_URI + b'"')
        xml_bytes = re.sub(rb'<'  + prefix + rb':', b'<',  xml_bytes)
        xml_bytes = re.sub(rb'</' + prefix + rb':', b'</', xml_bytes)

    # ── Fix 2: strip foreign namespace declarations (osb:, etc.) ─────────────
    # Remove any xmlns:xxx that is NOT xmlns, xmlns:mdsol, xmlns:xml, or the ODM xmlns.
    xml_bytes = re.sub(
        rb'\s+xmlns:(?!mdsol\b)(?!xml\b)\w+="(?!' +
        re.escape(_ODM_URI) + rb')[^"]*"',
        b'',
        xml_bytes,
    )

    return xml_bytes


def _promote_to_namespace(root: etree._Element, ns: str) -> None:
    """Move all unnamespaced elements into `ns` in-place."""
    for el in root.iter():
        if isinstance(el.tag, str) and not el.tag.startswith("{"):
            el.tag = f"{{{ns}}}{el.tag}"


def transform_odm(
    xml_bytes: bytes,
    edc: str,
    source_name: str = "odm.xml",
    dry_run: bool = False,
    **kwargs,
) -> tuple[TransformResult, bytes | None]:
    """
    Transform an ODM XML document held in memory.

    `source_name` only labels the result. Extra keyword arguments (e.g.
    rave_study_oid, rave_protocol_name) are forwarded to the EDC-specific runner.

    Returns (TransformResult, serialised_xml_bytes); the bytes are None when
    dry_run=True. Raises ValueError for unparsable XML or an unknown EDC.
    """
    runner = EDC_TRANSFORMERS.get(edc.lower().strip())
    if runner is None:
        known = ", ".join(sorted(EDC_TRANSFORMERS))
        raise ValueError(f"Unknown EDC '{edc}'. Supported: {known}.")

    try:
        # resolve_entities/no_network: the document may come from an upload
        parser = etree.XMLParser(
            remove_blank_text=False, resolve_entities=False, no_network=True
        )
        root = etree.fromstring(xml_bytes, parser)
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"Cannot parse '{source_name}': {exc}") from exc

    ns      = detect_namespace(root)
    odm_uri = ns or "http://www.cdisc.org/ns/odm/v1.3"
    nsmap   = {"odm": odm_uri}

    # OSB exports declare xmlns:odm= as a *prefix* but write elements with no
    # namespace prefix (e.g. <ODM>, <Study>).  lxml then stores those elements
    # in the *empty* namespace, so XPath queries using the ODM namespace find
    # nothing and every transform is a no-op.  Promote all unnamespaced elements
    # to the ODM namespace before running any transforms so XPath works correctly.
    if not root.tag.startswith("{"):
        _promote_to_namespace(root, odm_uri)

    result = runner(root, nsmap, source_name, **kwargs)

    if dry_run:
        return result, None

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    root.set("CreationDateTime", now)

    comment = etree.Comment(
        f" Transformed for {result.edc} by odm-transform-export on {now} "
    )
    root.addprevious(comment)

    # Declare mdsol: on the root. When the input declared no mdsol namespace,
    # the mdsol: attributes the transform adds (e.g. mdsol:SASLabel) get an
    # lxml-generated prefix such as ns0, whose declaration Fix 2 below would then
    # strip, leaving an undeclared prefix. Unused declarations (xmlns:osb once
    # every osb: attribute and element is gone) are dropped at the same time.
    etree.cleanup_namespaces(root, top_nsmap={"mdsol": _MDSOL_URI})

    out_bytes = etree.tostring(
        root.getroottree(),
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=True,
    )

    # Post-process serialized bytes:
    #   Fix 1 — convert odm: element prefix to default namespace
    #   Fix 2 — strip foreign xmlns declarations (osb:, etc.)
    return result, _normalize_namespace_output(out_bytes)


def transform_file(
    xml_path: str | Path,
    edc: str,
    output_path: str | Path | None = None,
    dry_run: bool = False,
    **kwargs,
) -> tuple[TransformResult, bytes | None]:
    """
    Read, transform, and optionally write an ODM XML file.

    Same contract as `transform_odm`; the result is also written to
    `output_path` when one is given.
    """
    path = Path(xml_path)
    result, out_bytes = transform_odm(
        path.read_bytes(), edc, source_name=str(path), dry_run=dry_run, **kwargs
    )
    if out_bytes is not None and output_path is not None:
        Path(output_path).write_bytes(out_bytes)
    return result, out_bytes
