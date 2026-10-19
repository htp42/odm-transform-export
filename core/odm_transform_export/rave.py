"""
Medidata Rave ODM transformation rules.

Transforms an ODM XML tree (lxml) in-place to make it Rave-compatible.
Returns a TransformResult describing every change made or skipped.

Transform pipeline (in execution order):
  ODM-003   Namespace promotion  — empty-ns elements lifted to the ODM namespace
  RAVE-001  ODMVersion           — set to "1.3"
  RAVE-002  Granularity          — set to "Metadata"
  ODM-004   CreationDateTime     — space→T separator; microseconds stripped
  ODM-052   GlobalVariables order — StudyName → StudyDescription → ProtocolName
  Fix-11    Study OID / ProtocolName override (rave_study_oid / rave_protocol_name)
  RAVE-011  CodeList OID dedup   — @suffix stripped from CodeList OIDs; byte-identical
            codelists merged into one; non-identical fallback to @ → _ (runs BEFORE
            OID sanitisation so @ chars are still present when dedup runs)
  RAVE-010  OID characters       — non-[A-Za-z0-9_] → '_'; leading non-letter → 'X'
  RAVE-020  OID length           — truncated to per-element limit; refs updated
  RAVE-030  DataType (ItemDef)   — string→text, double→float, URI→text
  RAVE-114  Label remodel        — osb:hideInput items → mdsol:LabelDef/LabelRef
  RAVE-003  Foreign namespace    — all non-mdsol/non-xml namespaced attrs removed,
            then all non-ODM/non-mdsol elements (e.g. osb:DesignNotes)
  RAVE-101  OrderNumber          — removed from CodeListItem / EnumeratedItem
  RAVE-103  ItemRef OrderNumbers — unique within each ItemGroupDef (renumber if dups)
  RAVE-105  ItemGroupRef OrderNumbers — unique within each FormDef (renumber if dups)
  RAVE-104  ItemRef OrderNumbers — globally unique within each FormDef across all
            ItemGroupDefs (renumber if cross-group duplicates detected)
  RAVE-106  FormRef OrderNumbers — unique within each StudyEventDef (renumber if dups)
  RAVE-111  mdsol:IsVisible      — moved from ItemRef to ItemDef as mdsol:Visible
  ODM-055   SDSVarName           — removed when not a valid sasName
  RAVE-133  mdsol:SASLabel       — populated from SASFieldName when absent
  ODM-055   SASFieldName/Dataset — truncated to 8 chars (collision-safe)
  RAVE-080  StudyEventDef Type   — "Common" → "Scheduled"
  RAVE-110  mdsol: casing        — camelCase vendor attrs renamed to PascalCase
  RAVE-115  mdsol: renames       — ALS-derived names → canonical Rave doc names
  RAVE-113  EntryRestriction     — restriction attrs removed from FormDef (not valid)
  RAVE-112  Boolean values       — TRUE/FALSE → Yes/No on mdsol: attrs
  Fix-10    Skip-when-default    — mdsol: attrs matching Rave defaults removed
  RAVE-116  ItemDef attr→child   — mdsol: attrs promoted to child elements
  RAVE-117  LabelRef order       — mdsol:LabelRef moved after all ItemRef in ItemGroupDef
  ODM-059   FormDef child order  — Description → ItemGroupRef → ArchiveLayout → Alias,
            then mdsol: elements (stable sort)
  ODM-060   ItemGroupDef child order — Description → ItemRef → Alias, then mdsol:
            elements (stable sort; keeps RAVE-117)
  RAVE-118  ItemDef body order   — child elements reordered to canonical sequence
  RAVE-130  mdsol: unknowns      — attrs not in the Rave schema removed

Skipped (require human judgment):
  RAVE-030  boolean DataType     — needs a 0/1 codelist; cannot generate automatically
  RAVE-031  DataType mismatch    — ambiguous which side is correct
  RAVE-040  Question text > 255  — truncating would lose content
  RAVE-060  Missing Length       — no safe default
  RAVE-063  Float SignificantDigits — no safe default

NOT done by this transformer (by design):
  - CodeList DataType is never changed (string is valid for CodeList per Rave doc)
  - OrderNumber on ItemRef / ItemGroupRef / FormRef / StudyEventRef is preserved
    (Rave doc maps it to display ordering; stripping loses that information)
  - mdsol: attribute names are never invented — only names from the Rave doc are emitted
"""
from __future__ import annotations

import copy
import re
from collections import defaultdict
from lxml import etree

from .models import Change, ChangeStatus, TransformResult
from .rave_schema import (
    MDSOL_KNOWN_ATTRS, MDSOL_RENAMES, MDSOL_BOOLEAN_DEFAULTS,
    ITEMDEF_MDSOL_ATTR_TO_CHILD, _ITEMDEF_BODY_ORDER,
)

RAVE     = "Medidata Rave"
ODM_NS   = "http://www.cdisc.org/ns/odm/v1.3"
_MDSOL_NS = "http://www.mdsol.com/ns/odm/metadata"
_XML_NS   = "http://www.w3.org/XML/1998/namespace"

_SASNAME_PATTERN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,7}$')
_GV_ORDER        = ["StudyName", "StudyDescription", "ProtocolName"]

# ODM 1.3 child sequences checked by ODM-059 / ODM-060 (rules/base.py)
_DEF_CHILD_ORDERS: list[tuple[str, list[str], str]] = [
    ("FormDef",      ["Description", "ItemGroupRef", "ArchiveLayout", "Alias"], "ODM-059"),
    ("ItemGroupDef", ["Description", "ItemRef", "Alias"],                       "ODM-060"),
]

RAVE_OID_PATTERN = re.compile(r'^[A-Za-z][A-Za-z0-9_]*$')

RAVE_OID_LIMITS: dict[str, int] = {
    "StudyEventDef": 50,
    "FormDef":       50,
    "ItemGroupDef":  50,
    "ItemDef":       50,
    "CodeList":      255,
    "MethodDef":     50,
    "ConditionDef":  50,
}

# Map: ref element → (ref attribute, definition element tag)
REF_ATTRS: dict[str, tuple[str, str]] = {
    "StudyEventRef":      ("StudyEventOID",      "StudyEventDef"),
    "FormRef":            ("FormOID",             "FormDef"),
    "ItemGroupRef":       ("ItemGroupOID",        "ItemGroupDef"),
    "ItemRef":            ("ItemOID",             "ItemDef"),
    "CodeListRef":        ("CodeListOID",         "CodeList"),
    "MethodRef":          ("MethodOID",           "MethodDef"),
    "ConditionRef":       ("ConditionOID",        "ConditionDef"),
    "MeasurementUnitRef": ("MeasurementUnitOID",  "MeasurementUnit"),
}

# Elements whose OID Rave enforces strictly (must be [A-Za-z][A-Za-z0-9_]*)
STRICT_OID_ELEMENTS = {
    "StudyEventDef", "FormDef", "ItemGroupDef", "ItemDef",
    "MethodDef", "ConditionDef",
}

# All elements with an OID that we sanitise
ALL_OID_ELEMENTS = STRICT_OID_ELEMENTS | {"CodeList", "MeasurementUnit"}


def q(nsmap: dict, tag: str) -> str:
    return f"{{{nsmap['odm']}}}{tag}"


def loc(el: etree._Element) -> str:
    parts = []
    e = el
    while e is not None:
        local = etree.QName(e.tag).localname
        oid   = e.get("OID") or e.get("Name") or ""
        parts.append(f"{local}[@OID='{oid}']" if oid else local)
        e = e.getparent()
    parts.reverse()
    return "/" + "/".join(parts[1:])


# ── OID registry (collision-safe renaming) ───────────────────────────────────

class OIDRegistry:
    """Tracks OID renames and ensures uniqueness after truncation / sanitisation."""

    def __init__(self, existing_oids: set[str]) -> None:
        self._used: set[str] = set(existing_oids)
        self._renames: dict[str, str] = {}

    def rename(self, old: str, desired: str, max_len: int) -> str:
        if old == desired:
            return old
        if old in self._renames:
            return self._renames[old]
        candidate = desired[:max_len]
        suffix = 1
        while candidate in self._used and candidate != old:
            tail = str(suffix)
            candidate = desired[:max_len - len(tail)] + tail
            suffix += 1
        self._renames[old] = candidate
        self._used.discard(old)
        self._used.add(candidate)
        return candidate

    def resolve(self, oid: str) -> str:
        return self._renames.get(oid, oid)

    def has_rename(self, oid: str) -> bool:
        return oid in self._renames


class SASNameRegistry:
    """Collision-safe truncation for SASFieldName / SASDatasetName (max 8 chars)."""

    def __init__(self) -> None:
        self._used: dict[str, set[str]] = {}

    def register(self, scope: str, name: str) -> None:
        self._used.setdefault(scope, set()).add(name)

    def truncate(self, scope: str, name: str, max_len: int = 8) -> str:
        used = self._used.setdefault(scope, set())
        candidate = name[:max_len]
        suffix = 1
        while candidate in used:
            tail = str(suffix)
            candidate = name[:max_len - len(tail)] + tail
            suffix += 1
        used.add(candidate)
        return candidate


# ── Main transform entry point ───────────────────────────────────────────────

def transform_for_rave(
    root: etree._Element,
    nsmap: dict,
    source_file: str,
    *,
    rave_study_oid: str | None = None,
    rave_protocol_name: str | None = None,
) -> TransformResult:
    result = TransformResult(source_file=source_file, edc=RAVE)

    # 1. Element namespace promotion (ODM-003: elements in empty ns when odm: is prefix)
    _promote_namespace(root, nsmap, result)

    # 2. ODM header fixes (RAVE-001, RAVE-002, ODM-004)
    _fix_odm_header(root, result)

    # 3. GlobalVariables child order (ODM-052)
    _fix_global_variables_order(root, nsmap, result)

    # 4. Study OID / ProtocolName override (Fix 11)
    _fix_study_id_and_protocol(root, nsmap, result, rave_study_oid, rave_protocol_name)

    # 4b. Dedup CodeList elements with @-suffix OIDs (RAVE-011)
    #     Must run BEFORE OID planning so merged/stripped OIDs are what the registry sees.
    _dedup_codelists(root, nsmap, result)

    # 5. Collect existing OIDs and plan all renames in one pass
    existing = {
        el.get("OID")
        for tag in ALL_OID_ELEMENTS
        for el in root.iter(q(nsmap, tag))
        if el.get("OID")
    }
    registry = OIDRegistry(existing)
    _plan_oid_renames(root, nsmap, registry)
    _apply_oid_renames(root, nsmap, registry, result)

    # 6. DataType fixes on ItemDef only (RAVE-030; CodeList DataType untouched)
    _fix_datatypes(root, nsmap, result)

    # 7. Remodel label items as mdsol:LabelDef/LabelRef (RAVE-114)
    #    Must run BEFORE foreign-namespace strip so osb:hideInput is still present.
    _remodel_label_items(root, nsmap, result)

    # 8. Strip foreign-namespace attributes (RAVE-003: osb:* and any other non-mdsol)
    _fix_foreign_ns_attrs(root, result)

    # 8b. Strip foreign-namespace elements (RAVE-003: osb:DesignNotes, …). Their
    #     xmlns declarations are dropped on output, so keeping them would leave
    #     undeclared prefixes and a document that is not well-formed.
    _strip_foreign_ns_elements(root, nsmap, result)

    # 9. Remove OrderNumber from CodeListItem / EnumeratedItem (RAVE-101)
    _remove_codelist_item_order_number(root, nsmap, result)

    # 9b. Remove SASFormatName from CodeList (RAVE-134)
    _drop_codelist_sas_format_name(root, nsmap, result)

    # 10. Fix duplicate ItemRef OrderNumbers within each ItemGroupDef (RAVE-103)
    _fix_itemref_order_numbers(root, nsmap, result)

    # 11. Move mdsol:IsVisible from ItemRef → mdsol:Visible on ItemDef (RAVE-111)
    _move_visible_to_itemdef(root, nsmap, result)

    # 12. Strip invalid SDSVarName values (ODM-055)
    _strip_invalid_sds_var_names(root, nsmap, result)

    # 13. Populate mdsol:SASLabel from SASFieldName when absent (RAVE-133)
    #     Must run BEFORE SAS name truncation to capture the original long name.
    _populate_sas_label(root, nsmap, result)

    # 14. Truncate SASFieldName / SASDatasetName to 8 chars (ODM-055)
    _fix_sas_names(root, nsmap, result)

    # 15. StudyEventDef Type="Common" → "Scheduled" (RAVE-080)
    _fix_study_event_type(root, nsmap, result)

    # 16. camelCase mdsol: attrs → PascalCase (RAVE-110)
    _fix_mdsol_casing(root, result)

    # 17. Semantic renames: ALS-derived names → canonical Rave doc names (RAVE-115)
    _rename_mdsol_attrs(root, result)

    # 18. Rename singular EntryRestriction/ViewRestriction attrs on FormDef (RAVE-113)
    _fix_entry_restrictions(root, nsmap, result)

    # 19. Convert TRUE/FALSE → Yes/No on all mdsol: boolean attrs (RAVE-112)
    _fix_mdsol_boolean_values(root, result)

    # 20. Remove mdsol: attrs whose value matches the Rave-documented default (Fix 10)
    _drop_mdsol_defaults(root, result)

    # 21. Promote ItemDef mdsol: attrs that must be child elements (RAVE-116)
    _promote_itemdef_child_attrs(root, nsmap, result)

    # 22. Split plural restriction/group child elements into singular (RAVE-116)
    #     Runs after attr promotion so promoted plural elements are also split.
    _split_plural_child_elements(root, result)

    # 23. Sort ItemGroupDef: all ItemRef before mdsol:LabelRef (RAVE-117)
    _sort_itemgroup_label_refs(root, nsmap, result)

    # 23b. Reorder FormDef / ItemGroupDef children per ODM 1.3 (ODM-059, ODM-060)
    #      Runs after RAVE-117; mdsol: elements stay after the ODM ones, so every
    #      mdsol:LabelRef remains after every ItemRef.
    _reorder_def_children(root, nsmap, result)

    # 24. Reorder ItemDef body children per Rave required sequence (RAVE-118)
    _reorder_itemdef_body(root, nsmap, result)

    # 25. Remove unknown mdsol: attrs not in the Rave schema whitelist (RAVE-130)
    #     Runs last so all renames have already been applied.
    _strip_unknown_mdsol_attrs(root, result)

    # 26. Fix duplicate ItemGroupRef OrderNumbers within each FormDef (RAVE-105)
    _fix_itemgroupref_order_numbers(root, nsmap, result)

    # 27. Fix cross-group ItemRef / LabelRef OrderNumbers within each FormDef (RAVE-104)
    #     Runs after RAVE-103 (within-group dedup) and RAVE-105 (ItemGroupRef renumber)
    #     so each group's items are already in their final positions.
    _fix_itemref_order_numbers_across_form(root, nsmap, result)

    # 28a. Fix duplicate FormRef OrderNumbers within each StudyEventDef (RAVE-106)
    _fix_formref_order_numbers(root, nsmap, result)

    # 28. Record issues that we deliberately skip (human review needed)
    _record_skips(root, nsmap, result)

    return result


# ── Namespace promotion (ODM-003) ────────────────────────────────────────────

def _promote_namespace(
    root: etree._Element, nsmap: dict, result: TransformResult
) -> None:
    odm_uri = nsmap.get("odm", "")
    if not odm_uri or etree.QName(root.tag).namespace is not None:
        return
    if odm_uri not in root.nsmap.values():
        return
    count = 0
    for el in root.iter():
        if isinstance(el.tag, str) and "{" not in el.tag:
            el.tag = f"{{{odm_uri}}}{el.tag}"
            count += 1
    if count:
        result.changes.append(Change(
            status=ChangeStatus.APPLIED,
            rule="ODM-003",
            description=f"ODM namespace promoted on {count} elements (xmlns:odm= → xmlns=).",
            location="/ODM",
            old_value="xmlns:odm (prefixed)",
            new_value="xmlns (default)",
            note="Rave requires the ODM namespace as the default namespace.",
        ))


# ── ODM header (RAVE-001, RAVE-002, ODM-004) ─────────────────────────────────

def _fix_datetime_str(dt: str) -> str:
    fixed = re.sub(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})', r'\1T\2', dt)
    return re.sub(r'(T\d{2}:\d{2}:\d{2})\.\d+', r'\1', fixed)


def _fix_odm_header(root: etree._Element, result: TransformResult) -> None:
    version = root.get("ODMVersion")
    if version and version != "1.3":
        root.set("ODMVersion", "1.3")
        result.changes.append(Change(
            status=ChangeStatus.APPLIED, rule="RAVE-001",
            description="ODMVersion corrected for Rave.",
            location="/ODM", old_value=version, new_value="1.3",
        ))

    granularity = root.get("Granularity")
    if granularity and granularity != "Metadata":
        root.set("Granularity", "Metadata")
        result.changes.append(Change(
            status=ChangeStatus.APPLIED, rule="RAVE-002",
            description="Granularity set to 'Metadata' for Rave design import.",
            location="/ODM", old_value=granularity, new_value="Metadata",
        ))

    dt = root.get("CreationDateTime")
    if dt:
        fixed = _fix_datetime_str(dt)
        if fixed != dt:
            root.set("CreationDateTime", fixed)
            result.changes.append(Change(
                status=ChangeStatus.APPLIED, rule="ODM-004",
                description="CreationDateTime reformatted to valid ODM datetime.",
                location="/ODM", old_value=dt, new_value=fixed,
            ))


# ── GlobalVariables order (ODM-052) ──────────────────────────────────────────

def _fix_global_variables_order(
    root: etree._Element, nsmap: dict, result: TransformResult
) -> None:
    for study in root.iter(q(nsmap, "Study")):
        gv = study.find(q(nsmap, "GlobalVariables"))
        if gv is None:
            continue
        present = {
            etree.QName(c.tag).localname: c
            for c in gv
            if isinstance(c.tag, str)
            and etree.QName(c.tag).localname in set(_GV_ORDER)
        }
        if len(present) < 2:
            continue
        actual   = [
            etree.QName(c.tag).localname for c in gv
            if isinstance(c.tag, str) and etree.QName(c.tag).localname in present
        ]
        expected = [r for r in _GV_ORDER if r in present]
        if actual == expected:
            continue
        first_idx = next(
            i for i, c in enumerate(list(gv))
            if isinstance(c.tag, str) and etree.QName(c.tag).localname in present
        )
        for el in present.values():
            gv.remove(el)
        for i, name in enumerate(expected):
            gv.insert(first_idx + i, present[name])
        result.changes.append(Change(
            status=ChangeStatus.APPLIED, rule="ODM-052",
            description="<GlobalVariables> children reordered to ODM schema sequence.",
            location=loc(gv),
            old_value=" → ".join(actual), new_value=" → ".join(expected),
        ))


# ── Study OID / ProtocolName override (Fix 11) ───────────────────────────────

def _fix_study_id_and_protocol(
    root: etree._Element,
    nsmap: dict,
    result: TransformResult,
    rave_study_oid: str | None,
    rave_protocol_name: str | None,
) -> None:
    for study in root.iter(q(nsmap, "Study")):
        if rave_study_oid is not None:
            old = study.get("OID", "")
            if old != rave_study_oid:
                study.set("OID", rave_study_oid)
                result.changes.append(Change(
                    status=ChangeStatus.APPLIED, rule="RAVE-004",
                    description="Study OID set to the provided rave_study_oid value.",
                    location=loc(study), old_value=old, new_value=rave_study_oid,
                    note="Rave uses the Study OID to match the target project.",
                ))

        gv = study.find(q(nsmap, "GlobalVariables"))
        if gv is None:
            continue

        if rave_protocol_name is not None:
            pn = gv.find(q(nsmap, "ProtocolName"))
            if pn is not None:
                old = pn.text or ""
                if old != rave_protocol_name:
                    pn.text = rave_protocol_name
                    result.changes.append(Change(
                        status=ChangeStatus.APPLIED, rule="RAVE-004",
                        description="ProtocolName set to the provided rave_protocol_name value.",
                        location=loc(pn), old_value=old, new_value=rave_protocol_name,
                        note="Rave maps ProtocolName to the internal project name.",
                    ))

        # Sync StudyName and StudyDescription to the current ProtocolName value
        pn_el = gv.find(q(nsmap, "ProtocolName"))
        protocol_name = pn_el.text if pn_el is not None else None
        if protocol_name:
            for sibling_tag in ("StudyName", "StudyDescription"):
                el = gv.find(q(nsmap, sibling_tag))
                if el is not None and (el.text or "") != protocol_name:
                    old = el.text or ""
                    el.text = protocol_name
                    result.changes.append(Change(
                        status=ChangeStatus.APPLIED, rule="RAVE-004",
                        description=f"{sibling_tag} set to match ProtocolName.",
                        location=loc(el), old_value=old, new_value=protocol_name,
                    ))


# ── CodeList deduplication (RAVE-011) ────────────────────────────────────────

def _strip_codelist_oid(oid: str) -> str | None:
    """Return the part of oid before the first '@', if it is a valid Rave OID.

    OSB encodes dataset-scoped codelists as BASE@DATASET (e.g. ACN@AEACN).
    Stripping the @DATASET suffix yields the canonical base OID.
    Returns None when there is no '@' or when the prefix is not a valid Rave OID.
    """
    at_pos = oid.find('@')
    if at_pos <= 0:
        return None
    prefix = oid[:at_pos]
    return prefix if RAVE_OID_PATTERN.match(prefix) else None


def _codelist_content_key(cl: etree._Element) -> str:
    """Canonical string for a CodeList's content, ignoring OID, Name, and tail whitespace."""
    cl_copy = copy.deepcopy(cl)
    for attr in ("OID", "Name"):
        cl_copy.attrib.pop(attr, None)
    cl_copy.tail = None  # exclude inter-element whitespace from comparison
    return etree.tostring(cl_copy, encoding="unicode")


def _dedup_codelists(
    root: etree._Element, nsmap: dict, result: TransformResult
) -> None:
    """
    Strip the @DATASET suffix from CodeList OIDs and merge byte-identical codelists.

    Algorithm per stripped candidate OID:
      • If all CodeLists that strip to the same OID are byte-identical in content
        (DataType + child elements, ignoring OID / Name): keep one, remove the rest,
        update every CodeListRef in the document to point to the canonical OID.
      • If a clean CodeList with the stripped OID already exists and the dirty
        members are identical to it: remove the dirty members.
      • If content differs within a group: leave all members untouched; the
        standard OID sanitisation step will replace @ with _ as before.

    Must run before _plan_oid_renames so the registry sees the already-clean OIDs.
    """
    cl_tag     = q(nsmap, "CodeList")
    cl_ref_tag = q(nsmap, "CodeListRef")

    # Collect (oid, element, parent) for every CodeList
    all_cls: list[tuple[str, etree._Element, etree._Element]] = []
    for el in root.iter(cl_tag):
        oid    = el.get("OID", "")
        parent = el.getparent()
        if oid and parent is not None:
            all_cls.append((oid, el, parent))

    clean_oid_to_el: dict[str, etree._Element] = {
        oid: el for oid, el, _ in all_cls if RAVE_OID_PATTERN.match(oid)
    }

    # Group dirty CodeLists by their stripped candidate
    groups: dict[str, list[tuple[str, etree._Element, etree._Element]]] = defaultdict(list)
    for oid, el, parent in all_cls:
        if RAVE_OID_PATTERN.match(oid):
            continue  # already clean
        stripped = _strip_codelist_oid(oid)
        if stripped is not None:
            groups[stripped].append((oid, el, parent))
        # OIDs with no @ (or invalid prefix) fall through to _sanitise_oid later

    if not groups:
        return

    def _update_refs(old_oid: str, new_oid: str) -> None:
        for ref in root.iter(cl_ref_tag):
            if ref.get("CodeListOID") == old_oid:
                ref.set("CodeListOID", new_oid)

    for stripped, dirty_members in groups.items():
        existing_clean = clean_oid_to_el.get(stripped)

        if existing_clean is not None:
            # A clean CodeList with the stripped OID already exists.
            # Merge dirty members if they are content-identical to it.
            canonical_key = _codelist_content_key(existing_clean)
            identical_members = [
                (oid, el, parent) for oid, el, parent in dirty_members
                if _codelist_content_key(el) == canonical_key
            ]
            non_identical = [
                m for m in dirty_members if m not in identical_members
            ]
            for oid, el, parent in identical_members:
                parent.remove(el)
                _update_refs(oid, stripped)
                result.changes.append(Change(
                    status=ChangeStatus.APPLIED, rule="RAVE-011",
                    description=(
                        f"<CodeList OID='{oid}'> merged into '{stripped}' "
                        "(byte-identical to existing clean CodeList; @suffix stripped)."
                    ),
                    location=loc(existing_clean),
                    old_value=oid, new_value=stripped,
                    note="All CodeListRef elements updated to the canonical OID.",
                ))
            # non_identical members are left for _sanitise_oid (@ → _)

        else:
            # No existing clean CodeList. Check if all dirty members are identical.
            content_keys = [_codelist_content_key(el) for _, el, _ in dirty_members]
            if len(set(content_keys)) == 1:
                # All identical — rename first, remove the rest
                canon_oid, canon_el, _ = dirty_members[0]
                canon_el.set("OID", stripped)
                _update_refs(canon_oid, stripped)
                result.changes.append(Change(
                    status=ChangeStatus.APPLIED, rule="RAVE-011",
                    description=(
                        f"<CodeList OID='{canon_oid}'> OID stripped to "
                        f"'{stripped}' (@suffix removed)."
                    ),
                    location=loc(canon_el),
                    old_value=canon_oid, new_value=stripped,
                ))
                for oid, el, parent in dirty_members[1:]:
                    parent.remove(el)
                    _update_refs(oid, stripped)
                    result.changes.append(Change(
                        status=ChangeStatus.APPLIED, rule="RAVE-011",
                        description=(
                            f"<CodeList OID='{oid}'> merged into '{stripped}' "
                            "(byte-identical content; @suffix stripped)."
                        ),
                        location=loc(canon_el),
                        old_value=oid, new_value=stripped,
                        note="All CodeListRef elements updated to the canonical OID.",
                    ))
            # else: content differs — leave all for _sanitise_oid (@ → _) fallback


# ── OID sanitisation ─────────────────────────────────────────────────────────

def _sanitise_oid(oid: str) -> str:
    clean = re.sub(r'[^A-Za-z0-9_]', '_', oid)
    if clean and not clean[0].isalpha():
        clean = "X" + clean
    return clean or "X"


def _plan_oid_renames(root: etree._Element, nsmap: dict, registry: OIDRegistry) -> None:
    for tag in ALL_OID_ELEMENTS:
        max_len = RAVE_OID_LIMITS.get(tag, 128)
        for el in root.iter(q(nsmap, tag)):
            oid = el.get("OID", "")
            if not oid:
                continue
            desired = _sanitise_oid(oid) if not RAVE_OID_PATTERN.match(oid) else oid
            registry.rename(oid, desired, max_len)


def _apply_oid_renames(
    root: etree._Element, nsmap: dict,
    registry: OIDRegistry, result: TransformResult,
) -> None:
    for tag in ALL_OID_ELEMENTS:
        max_len = RAVE_OID_LIMITS.get(tag, 128)
        for el in root.iter(q(nsmap, tag)):
            old = el.get("OID", "")
            if not old or not registry.has_rename(old):
                continue
            new = registry.resolve(old)
            el.set("OID", new)

            char_changed = re.sub(r'[^A-Za-z0-9_]', '_', old) != old
            len_changed  = len(old) > max_len
            reasons = []
            if char_changed:
                reasons.append("invalid characters replaced with '_'")
            if len_changed:
                reasons.append(f"truncated to {max_len} characters")

            rule         = "RAVE-010" if char_changed else "RAVE-020"
            desired_naive = _sanitise_oid(old)[:max_len]
            note         = "Suffix added to avoid collision." if desired_naive != new else None

            result.changes.append(Change(
                status=ChangeStatus.APPLIED, rule=rule,
                description=f"<{tag}> OID renamed: {'; '.join(reasons)}.",
                location=loc(el), old_value=old, new_value=new, note=note,
            ))

    for ref_tag, (ref_attr, _) in REF_ATTRS.items():
        for el in root.iter(q(nsmap, ref_tag)):
            old_ref = el.get(ref_attr, "")
            if old_ref and registry.has_rename(old_ref):
                el.set(ref_attr, registry.resolve(old_ref))


# ── DataType fixes (RAVE-030, ItemDef only) ──────────────────────────────────

_ITEM_DATATYPE_MAP = {"string": "text", "double": "float", "URI": "text"}


def _fix_datatypes(root: etree._Element, nsmap: dict, result: TransformResult) -> None:
    for el in root.iter(q(nsmap, "ItemDef")):
        dt = el.get("DataType", "")
        if dt in _ITEM_DATATYPE_MAP:
            new_dt = _ITEM_DATATYPE_MAP[dt]
            el.set("DataType", new_dt)
            result.changes.append(Change(
                status=ChangeStatus.APPLIED, rule="RAVE-030",
                description="<ItemDef> DataType changed (CodeList DataType left unchanged).",
                location=loc(el), old_value=dt, new_value=new_dt,
            ))


# ── Label item remodel (RAVE-114) ────────────────────────────────────────────

def _remodel_label_items(
    root: etree._Element, nsmap: dict, result: TransformResult
) -> None:
    """
    Detect ItemDefs marked as labels (osb:hideInput="TRUE" on ItemRef or ItemDef)
    and remodel them as mdsol:LabelDef + mdsol:LabelRef.

    Must run before foreign-namespace stripping so osb: attributes are intact.
    """
    osb_hide = None  # lazy: we'll detect the hideInput attr from any namespace

    # Build ItemOID → ItemDef map
    item_def_map: dict[str, etree._Element] = {}
    item_def_parent: dict[str, etree._Element] = {}
    for mdv in root.iter(q(nsmap, "MetaDataVersion")):
        for el in list(mdv):
            if not isinstance(el.tag, str):
                continue
            local = etree.QName(el.tag).localname
            if local == "ItemDef":
                oid = el.get("OID", "")
                if oid:
                    item_def_map[oid]    = el
                    item_def_parent[oid] = mdv

    for mdv in root.iter(q(nsmap, "MetaDataVersion")):
        for ig in list(mdv):
            if not isinstance(ig.tag, str):
                continue
            if etree.QName(ig.tag).localname != "ItemGroupDef":
                continue

            refs_to_process = []
            for item_ref in list(ig):
                if not isinstance(item_ref.tag, str):
                    continue
                if etree.QName(item_ref.tag).localname != "ItemRef":
                    continue
                item_oid = item_ref.get("ItemOID", "")
                if not item_oid:
                    continue

                # Check for label flag on either ItemRef or ItemDef
                is_label = _has_hide_input(item_ref) or (
                    item_oid in item_def_map and _has_hide_input(item_def_map[item_oid])
                )
                if is_label:
                    refs_to_process.append((item_ref, item_oid))

            for item_ref, item_oid in refs_to_process:
                item_def = item_def_map.get(item_oid)
                order_no = item_ref.get("OrderNumber", "")

                # Extract question text from the ItemDef
                label_text = ""
                if item_def is not None:
                    q_el = item_def.find(q(nsmap, "Question"))
                    if q_el is not None:
                        for tt in q_el.iter(q(nsmap, "TranslatedText")):
                            label_text = (tt.text or "").strip()
                            break

                # Replace ItemRef with mdsol:LabelRef in the same position,
                # preserving the tail whitespace for clean serialization.
                ref_idx = list(ig).index(item_ref)
                saved_tail = item_ref.tail
                ig.remove(item_ref)
                label_ref = etree.Element(f"{{{_MDSOL_NS}}}LabelRef")
                label_ref.set("LabelOID", item_oid)
                if order_no:
                    label_ref.set("OrderNumber", order_no)
                label_ref.tail = saved_tail
                ig.insert(ref_idx, label_ref)

                # Add mdsol:LabelDef at the end of MetaDataVersion
                label_def = etree.SubElement(mdv, f"{{{_MDSOL_NS}}}LabelDef")
                label_def.set("OID", item_oid)
                label_def.set("Name", item_oid)
                if label_text:
                    tt_el = etree.SubElement(
                        label_def,
                        f"{{{nsmap['odm']}}}TranslatedText",
                    )
                    tt_el.set(f"{{{_XML_NS}}}lang", "en")
                    tt_el.text = label_text

                # Remove the original ItemDef
                if item_def is not None:
                    parent = item_def_parent.get(item_oid)
                    if parent is not None:
                        parent.remove(item_def)

                result.changes.append(Change(
                    status=ChangeStatus.APPLIED, rule="RAVE-114",
                    description=(
                        f"<ItemDef OID='{item_oid}'> remodeled as "
                        f"mdsol:LabelDef + mdsol:LabelRef "
                        f"(label text: '{label_text[:60]}')."
                    ),
                    location=loc(ig),
                    old_value=f"ItemDef OID='{item_oid}'",
                    new_value=f"mdsol:LabelDef OID='{item_oid}'",
                    note=(
                        "Rave uses mdsol:LabelDef for header/label rows. "
                        "The original ItemDef has been removed."
                    ),
                ))


def _has_hide_input(el: etree._Element) -> bool:
    """Return True if any attribute named 'hideInput' (any namespace) is 'TRUE' or 'Yes'."""
    for attr, val in el.attrib.items():
        if not (isinstance(attr, str) and "{" in attr):
            continue
        _, _, local = attr[1:].partition("}")
        if local.lower() == "hideinput" and val.upper() in ("TRUE", "YES"):
            return True
    return False


# ── Foreign-namespace attribute strip (RAVE-003) ─────────────────────────────

def _fix_foreign_ns_attrs(root: etree._Element, result: TransformResult) -> None:
    """Remove all vendor-namespaced attributes except mdsol: and xml:."""
    _ALLOWED = {_MDSOL_NS, _XML_NS}
    removed: list[tuple[str, str, str]] = []
    for el in root.iter():
        for attr in list(el.attrib):
            if not (isinstance(attr, str) and "{" in attr):
                continue
            ns, _, local = attr[1:].partition("}")
            if ns in _ALLOWED:
                continue
            val = el.attrib.pop(attr)
            tag = etree.QName(el.tag).localname if isinstance(el.tag, str) else "?"
            removed.append((tag, local, val[:40]))

    if removed:
        result.changes.append(Change(
            status=ChangeStatus.APPLIED, rule="RAVE-003",
            description=(
                f"Removed {len(removed)} foreign-namespace attribute(s) "
                "(e.g. osb:*). Rave only accepts the mdsol: vendor namespace."
            ),
            location="/ODM",
            old_value=f"{len(removed)} attribute(s): " + ", ".join(
                f"{tag}/{local}" for tag, local, _ in removed[:5]
            ) + ("…" if len(removed) > 5 else ""),
            new_value=None,
        ))


def _strip_foreign_ns_elements(
    root: etree._Element, nsmap: dict, result: TransformResult
) -> None:
    """Remove all vendor-namespaced elements except mdsol: ones."""
    allowed = {nsmap["odm"], _MDSOL_NS}
    # Only the top-most foreign element of each subtree: its children go with it
    foreign = [
        el for el in root.iter()
        if isinstance(el.tag, str)
        and etree.QName(el.tag).namespace not in allowed
        and el.getparent() is not None
        and etree.QName(el.getparent().tag).namespace in allowed
    ]
    removed: list[str] = []
    for el in foreign:
        parent = el.getparent()
        if el.tail:  # keep the surrounding whitespace / text
            prev = el.getprevious()
            if prev is not None:
                prev.tail = (prev.tail or "") + el.tail
            else:
                parent.text = (parent.text or "") + el.tail
        removed.append(
            f"{etree.QName(parent.tag).localname}/"
            f"{el.prefix}:{etree.QName(el.tag).localname}"
        )
        parent.remove(el)

    if removed:
        result.changes.append(Change(
            status=ChangeStatus.APPLIED, rule="RAVE-003",
            description=(
                f"Removed {len(removed)} foreign-namespace element(s) "
                "(e.g. osb:DesignNotes). Rave only accepts the mdsol: vendor namespace."
            ),
            location="/ODM",
            old_value=f"{len(removed)} element(s): " + ", ".join(removed[:5])
            + ("…" if len(removed) > 5 else ""),
            new_value=None,
        ))


# ── OrderNumber on CodeListItem / EnumeratedItem (RAVE-101) ──────────────────

def _remove_codelist_item_order_number(
    root: etree._Element, nsmap: dict, result: TransformResult
) -> None:
    item_tags = {q(nsmap, "CodeListItem"), q(nsmap, "EnumeratedItem")}
    for cl in root.iter(q(nsmap, "CodeList")):
        children = [c for c in cl if c.tag in item_tags and c.get("OrderNumber") is not None]
        if not children:
            continue
        # Sort by OrderNumber before stripping so Rave reads items in correct order
        try:
            children.sort(key=lambda c: int(c.get("OrderNumber")))
        except ValueError:
            children.sort(key=lambda c: c.get("OrderNumber", ""))
        # Re-insert in sorted order (preserve non-item siblings in-place)
        first_pos = next(
            (i for i, c in enumerate(list(cl)) if c.tag in item_tags), len(cl)
        )
        for child in children:
            cl.remove(child)
        for offset, child in enumerate(children):
            cl.insert(first_pos + offset, child)
        # Now strip the attribute
        cl_oid = cl.get("OID", "?")
        for child in children:
            val = child.get("OrderNumber")
            tag_local = etree.QName(child.tag).localname
            del child.attrib["OrderNumber"]
            result.changes.append(Change(
                status=ChangeStatus.APPLIED, rule="RAVE-101",
                description=f"Removed OrderNumber from <{tag_local}> in CodeList '{cl_oid}'.",
                location=loc(child), old_value=val, new_value=None,
                note="Rave's ODM 1.3.0 schema does not declare this attribute.",
            ))


# ── SASFormatName on CodeList (RAVE-134) ─────────────────────────────────────

def _drop_codelist_sas_format_name(
    root: etree._Element, nsmap: dict, result: TransformResult
) -> None:
    for cl in root.iter(q(nsmap, "CodeList")):
        val = cl.get("SASFormatName")
        if val is not None:
            del cl.attrib["SASFormatName"]
            result.changes.append(Change(
                status=ChangeStatus.APPLIED, rule="RAVE-134",
                description=f"Removed SASFormatName from <CodeList OID='{cl.get('OID', '?')}'>.",
                location=loc(cl), old_value=val, new_value=None,
                note="Rave's ODM 1.3.0 schema does not declare SASFormatName on CodeList.",
            ))


# ── Unique ItemRef OrderNumbers within each ItemGroupDef (RAVE-103) ──────────

def _fix_itemref_order_numbers(
    root: etree._Element, nsmap: dict, result: TransformResult
) -> None:
    """
    Renumber all ItemRef OrderNumbers within a given ItemGroupDef when duplicates
    are detected. Renumbers in document order starting from 1.
    Does NOT touch ItemGroupRef, FormRef, or StudyEventRef OrderNumbers.
    """
    _label_ref_tag = f"{{{_MDSOL_NS}}}LabelRef"
    for ig in root.iter(q(nsmap, "ItemGroupDef")):
        # Collect both ItemRef and mdsol:LabelRef — they share the OrderNumber space
        ordering_els = [
            c for c in ig
            if isinstance(c.tag, str) and (
                etree.QName(c.tag).localname == "ItemRef"
                or c.tag == _label_ref_tag
            )
        ]
        if len(ordering_els) < 2:
            continue
        order_nums = [c.get("OrderNumber") for c in ordering_els]
        defined    = [n for n in order_nums if n is not None]
        if len(defined) == len(set(defined)):
            continue  # all unique — nothing to do

        old_nums = order_nums[:]
        for i, ref in enumerate(ordering_els, start=1):
            ref.set("OrderNumber", str(i))

        result.changes.append(Change(
            status=ChangeStatus.APPLIED, rule="RAVE-103",
            description=(
                f"<ItemGroupDef OID='{ig.get('OID', '?')}'> "
                f"ItemRef/LabelRef OrderNumbers renumbered 1–{len(ordering_els)} "
                "(duplicate values detected)."
            ),
            location=loc(ig),
            old_value=", ".join(str(n) for n in old_nums),
            new_value=", ".join(str(i) for i in range(1, len(ordering_els) + 1)),
        ))


# ── Move mdsol:IsVisible from ItemRef → mdsol:Visible on ItemDef (RAVE-111) ──

def _move_visible_to_itemdef(
    root: etree._Element, nsmap: dict, result: TransformResult
) -> None:
    """
    Per Rave doc p. 48, field visibility is controlled by mdsol:Visible on ItemDef
    (values Yes / No, default Yes). mdsol:IsVisible on ItemRef is not valid.

    Algorithm:
      1. For each ItemRef carrying mdsol:IsVisible:
         a. Convert value: TRUE/Yes → "Yes" (omit as default); FALSE/No → "No" (emit).
         b. Find the corresponding ItemDef and set mdsol:Visible="No" there.
         c. Remove mdsol:IsVisible from the ItemRef.
    """
    is_visible_attr = f"{{{_MDSOL_NS}}}IsVisible"
    visible_attr    = f"{{{_MDSOL_NS}}}Visible"

    # Build OID → ItemDef map
    item_def_map: dict[str, etree._Element] = {
        el.get("OID"): el
        for el in root.iter(q(nsmap, "ItemDef"))
        if el.get("OID")
    }

    for item_ref in root.iter(q(nsmap, "ItemRef")):
        raw = item_ref.get(is_visible_attr)
        if raw is None:
            continue

        del item_ref.attrib[is_visible_attr]
        item_oid = item_ref.get("ItemOID", "?")

        # Normalise to Yes/No
        yes_no = "No" if raw.upper() in ("FALSE", "NO") else "Yes"

        result.changes.append(Change(
            status=ChangeStatus.APPLIED, rule="RAVE-111",
            description=(
                f"<ItemRef ItemOID='{item_oid}'> mdsol:IsVisible removed "
                f"(not valid on ItemRef)."
            ),
            location=loc(item_ref),
            old_value=f"mdsol:IsVisible={raw}",
            new_value=None,
            note="Visibility is set on ItemDef as mdsol:Visible.",
        ))

        # Only set on ItemDef when value is non-default (No)
        if yes_no == "No":
            item_def = item_def_map.get(item_oid)
            if item_def is not None and item_def.get(visible_attr) is None:
                item_def.set(visible_attr, "No")
                result.changes.append(Change(
                    status=ChangeStatus.APPLIED, rule="RAVE-111",
                    description=(
                        f"<ItemDef OID='{item_oid}'> mdsol:Visible set to 'No' "
                        "(propagated from ItemRef mdsol:IsVisible='FALSE')."
                    ),
                    location=loc(item_def),
                    old_value=None,
                    new_value="mdsol:Visible=No",
                ))


# ── SDSVarName cleanup (ODM-055) ──────────────────────────────────────────────

def _strip_invalid_sds_var_names(
    root: etree._Element, nsmap: dict, result: TransformResult
) -> None:
    for el in root.iter(q(nsmap, "ItemDef")):
        sds = el.get("SDSVarName")
        if sds is None or _SASNAME_PATTERN.match(sds):
            continue
        del el.attrib["SDSVarName"]
        result.changes.append(Change(
            status=ChangeStatus.APPLIED, rule="ODM-055",
            description=(
                f"<ItemDef OID='{el.get('OID', '?')}'> "
                "SDSVarName removed (invalid sasName)."
            ),
            location=loc(el),
            old_value=sds[:60] + ("…" if len(sds) > 60 else ""),
            new_value=None,
            note="Set a valid 8-character SAS variable name manually if needed.",
        ))


# ── mdsol:SASLabel from SASFieldName (RAVE-133) ──────────────────────────────

def _populate_sas_label(
    root: etree._Element, nsmap: dict, result: TransformResult
) -> None:
    sl_attr = f"{{{_MDSOL_NS}}}SASLabel"
    for el in root.iter(q(nsmap, "ItemDef")):
        if el.get(sl_attr) is not None:
            continue
        sas_fn = el.get("SASFieldName")
        if not sas_fn:
            continue
        el.set(sl_attr, sas_fn)
        result.changes.append(Change(
            status=ChangeStatus.APPLIED, rule="RAVE-133",
            description=(
                f"<ItemDef OID='{el.get('OID', '?')}'> "
                "mdsol:SASLabel populated from SASFieldName."
            ),
            location=loc(el), old_value=None, new_value=sas_fn,
            note=(
                "Rave uses mdsol:SASLabel as the SAS variable label; "
                "the ODM SASFieldName attribute is not shown in the Rave UI."
            ),
        ))


# ── SASFieldName / SASDatasetName truncation (ODM-055) ───────────────────────

def _fix_sas_names(
    root: etree._Element, nsmap: dict, result: TransformResult
) -> None:
    sas_reg = SASNameRegistry()

    for tag, attr in (("ItemDef", "SASFieldName"), ("ItemGroupDef", "SASDatasetName")):
        for el in root.iter(q(nsmap, tag)):
            val = el.get(attr, "")
            if val and len(val) <= 8:
                sas_reg.register(attr, val)

    for tag, attr in (("ItemDef", "SASFieldName"), ("ItemGroupDef", "SASDatasetName")):
        for el in root.iter(q(nsmap, tag)):
            val = el.get(attr, "")
            if not val or len(val) <= 8:
                continue
            new_val = sas_reg.truncate(attr, val, max_len=8)
            el.set(attr, new_val)
            note = "Suffix added to avoid collision." if val[:8] != new_val else None
            result.changes.append(Change(
                status=ChangeStatus.APPLIED, rule="ODM-055",
                description=f"<{tag}> {attr} truncated to 8 characters.",
                location=loc(el), old_value=val, new_value=new_val, note=note,
            ))


# ── StudyEventDef Type="Common" (RAVE-080) ───────────────────────────────────

def _fix_study_event_type(
    root: etree._Element, nsmap: dict, result: TransformResult
) -> None:
    for el in root.iter(q(nsmap, "StudyEventDef")):
        if el.get("Type") == "Common":
            el.set("Type", "Scheduled")
            result.changes.append(Change(
                status=ChangeStatus.APPLIED, rule="RAVE-080",
                description=f"<StudyEventDef OID='{el.get('OID', '?')}'> Type changed.",
                location=loc(el), old_value="Common", new_value="Scheduled",
                note="Defaulted to 'Scheduled'. Change to 'Unscheduled' if appropriate.",
            ))


# ── mdsol: attribute casing (RAVE-110) ───────────────────────────────────────

def _fix_mdsol_casing(root: etree._Element, result: TransformResult) -> None:
    for el in root.iter():
        renames = [
            (attr, f"{{{_MDSOL_NS}}}{local[0].upper() + local[1:]}", local)
            for attr in list(el.attrib)
            if isinstance(attr, str) and "{" in attr
            for ns, _, local in [attr[1:].partition("}")]
            if ns == _MDSOL_NS and local and local[0].islower()
        ]
        for old_attr, new_attr, local in renames:
            val = el.attrib.pop(old_attr)
            el.attrib[new_attr] = val
            pascal = local[0].upper() + local[1:]
            result.changes.append(Change(
                status=ChangeStatus.APPLIED, rule="RAVE-110",
                description=f"mdsol:{local} renamed to mdsol:{pascal} (PascalCase).",
                location=loc(el),
                old_value=f"mdsol:{local}", new_value=f"mdsol:{pascal}",
            ))


# ── Semantic mdsol: renames (RAVE-115) ───────────────────────────────────────

def _rename_mdsol_attrs(root: etree._Element, result: TransformResult) -> None:
    """
    Apply the canonical-name rename map from MDSOL_RENAMES.
    Runs AFTER RAVE-110 PascalCase conversion so keys are guaranteed PascalCase.
    None-valued entries drop the attribute entirely.
    """
    for el in root.iter():
        for attr in list(el.attrib):
            if not (isinstance(attr, str) and "{" in attr):
                continue
            ns, _, local = attr[1:].partition("}")
            if ns != _MDSOL_NS:
                continue
            if local not in MDSOL_RENAMES:
                continue
            val      = el.attrib.pop(attr)
            new_name = MDSOL_RENAMES[local]
            if new_name is None:
                result.changes.append(Change(
                    status=ChangeStatus.APPLIED, rule="RAVE-115",
                    description=f"mdsol:{local} dropped (not in Rave RWS schema).",
                    location=loc(el),
                    old_value=f"mdsol:{local}={val[:40]}",
                    new_value=None,
                ))
            else:
                el.attrib[f"{{{_MDSOL_NS}}}{new_name}"] = val
                result.changes.append(Change(
                    status=ChangeStatus.APPLIED, rule="RAVE-115",
                    description=(
                        f"mdsol:{local} renamed to mdsol:{new_name} "
                        "(canonical Rave doc name)."
                    ),
                    location=loc(el),
                    old_value=f"mdsol:{local}", new_value=f"mdsol:{new_name}",
                ))


# ── EntryRestriction(s) / ViewRestriction(s) removal from FormDef (RAVE-113) ─

def _fix_entry_restrictions(
    root: etree._Element, nsmap: dict, result: TransformResult
) -> None:
    """
    Remove mdsol:EntryRestriction(s) and mdsol:ViewRestriction(s) from FormDef.
    Neither form is a valid attribute on FormDef in the Rave RWS schema.
    """
    _to_strip = {
        "EntryRestriction", "EntryRestrictions",
        "ViewRestriction", "ViewRestrictions",
    }
    for el in root.iter(q(nsmap, "FormDef")):
        for attr in list(el.attrib):
            if not (isinstance(attr, str) and "{" in attr):
                continue
            ns, _, local = attr[1:].partition("}")
            if ns != _MDSOL_NS:
                continue
            canonical = local[0].upper() + local[1:] if local else local
            if canonical not in _to_strip:
                continue
            val = el.attrib.pop(attr)
            result.changes.append(Change(
                status=ChangeStatus.APPLIED, rule="RAVE-113",
                description=(
                    f"<FormDef OID='{el.get('OID', '?')}'> mdsol:{local} removed — "
                    "not a valid attribute on FormDef in the Rave RWS ODM schema."
                ),
                location=loc(el),
                old_value=f"mdsol:{local}={val}",
                new_value=None,
            ))


# ── TRUE/FALSE → Yes/No on all mdsol: boolean attrs (RAVE-112) ───────────────

def _fix_mdsol_boolean_values(root: etree._Element, result: TransformResult) -> None:
    """Normalise any mdsol: boolean attribute to the exact canonical form 'Yes'/'No'."""
    for el in root.iter():
        for attr in list(el.attrib):
            if not (isinstance(attr, str) and "{" in attr):
                continue
            ns, _, local = attr[1:].partition("}")
            if ns != _MDSOL_NS:
                continue
            val = el.attrib[attr]
            upper = val.upper()
            if upper in ("TRUE", "YES"):
                new_val = "Yes"
            elif upper in ("FALSE", "NO"):
                new_val = "No"
            else:
                continue
            if new_val == val:
                continue
            el.attrib[attr] = new_val
            result.changes.append(Change(
                status=ChangeStatus.APPLIED, rule="RAVE-112",
                description=(
                    f"mdsol:{local} value normalised from '{val}' to '{new_val}' "
                    "(Rave schema requires exactly Yes/No)."
                ),
                location=loc(el),
                old_value=val, new_value=new_val,
            ))


# ── Skip-when-default: remove mdsol: attrs matching Rave defaults (Fix 10) ───

def _drop_mdsol_defaults(root: etree._Element, result: TransformResult) -> None:
    """
    Remove mdsol: attributes whose value equals the Rave-documented default.
    Runs after boolean value conversion (so values are already Yes/No).
    """
    _variable_oid_attr = f"{{{_MDSOL_NS}}}VariableOID"
    for el in root.iter():
        for attr in list(el.attrib):
            if not (isinstance(attr, str) and "{" in attr):
                continue
            ns, _, local = attr[1:].partition("}")
            if ns != _MDSOL_NS:
                continue
            val = el.attrib[attr]
            # Drop mdsol:VariableOID when it redundantly echoes the element's OID
            if attr == _variable_oid_attr:
                if val == el.get("OID"):
                    del el.attrib[attr]
                    result.changes.append(Change(
                        status=ChangeStatus.APPLIED, rule="RAVE-112",
                        description=(
                            f"mdsol:VariableOID='{val}' removed — equals element OID "
                            "(redundant attribute)."
                        ),
                        location=loc(el),
                        old_value=f"mdsol:VariableOID={val}",
                        new_value=None,
                    ))
                continue
            default = MDSOL_BOOLEAN_DEFAULTS.get(local)
            if default is None:
                continue
            if val != default:
                continue
            del el.attrib[attr]
            result.changes.append(Change(
                status=ChangeStatus.APPLIED, rule="RAVE-112",
                description=(
                    f"mdsol:{local}='{val}' removed — matches Rave default "
                    "(redundant attribute omitted to reduce schema-validation surface)."
                ),
                location=loc(el),
                old_value=f"mdsol:{local}={val}",
                new_value=None,
            ))


# ── Promote ItemDef mdsol: attrs to child elements (RAVE-116) ────────────────

def _promote_itemdef_child_attrs(
    root: etree._Element, nsmap: dict, result: TransformResult
) -> None:
    """
    For each ItemDef, convert mdsol: attributes that Rave requires as child
    elements into the corresponding <mdsol:ElementName> child elements.
    The full attribute value (including any commas) becomes the element text.
    The attribute name is preserved as the element name.
    The attribute is removed after conversion.
    """
    for el in root.iter(q(nsmap, "ItemDef")):
        for attr in list(el.attrib):
            if not (isinstance(attr, str) and "{" in attr):
                continue
            ns, _, local = attr[1:].partition("}")
            if ns != _MDSOL_NS:
                continue
            canonical = local[0].upper() + local[1:] if local else local
            if canonical not in ITEMDEF_MDSOL_ATTR_TO_CHILD:
                continue
            el_local = ITEMDEF_MDSOL_ATTR_TO_CHILD[canonical]
            val = el.attrib.pop(attr)
            child = etree.SubElement(el, f"{{{_MDSOL_NS}}}{el_local}")
            child.text = val
            result.changes.append(Change(
                status=ChangeStatus.APPLIED, rule="RAVE-116",
                description=(
                    f"<ItemDef OID='{el.get('OID', '?')}'> mdsol:{local} promoted "
                    f"to <mdsol:{el_local}> child element."
                ),
                location=loc(el),
                old_value=f"mdsol:{local}={val}",
                new_value=f"<mdsol:{el_local}>{val}</>",
            ))


# ── Split plural restriction/group child elements into singular (RAVE-116) ────

_PLURAL_CHILD_SPLIT: dict[str, str] = {
    # plural element local name → singular element local name
    "EntryRestrictions": "EntryRestriction",
    "ViewRestrictions":  "ViewRestriction",
    "ReviewGroups":      "ReviewGroup",
}


def _split_plural_child_elements(root: etree._Element, result: TransformResult) -> None:
    """
    Replace each mdsol:EntryRestrictions / ViewRestrictions / ReviewGroups child
    element with one or more singular elements, splitting the text on commas.
    Both forms are valid per Rave; this normalises to singular for consistency.
    """
    for plural_local, singular_local in _PLURAL_CHILD_SPLIT.items():
        plural_tag   = f"{{{_MDSOL_NS}}}{plural_local}"
        singular_tag = f"{{{_MDSOL_NS}}}{singular_local}"
        for plural_el in list(root.iter(plural_tag)):
            parent = plural_el.getparent()
            if parent is None:
                continue
            text   = plural_el.text or ""
            values = [v.strip() for v in text.split(",") if v.strip()]
            if not values:
                continue
            pos  = list(parent).index(plural_el)
            tail = plural_el.tail
            parent.remove(plural_el)
            for offset, v in enumerate(values):
                new_el      = etree.Element(singular_tag)
                new_el.text = v
                new_el.tail = tail if offset == len(values) - 1 else "\n"
                parent.insert(pos + offset, new_el)
            result.changes.append(Change(
                status=ChangeStatus.APPLIED, rule="RAVE-116",
                description=(
                    f"<mdsol:{plural_local}> split into "
                    f"{len(values)} <mdsol:{singular_local}> element(s)."
                ),
                location=loc(parent),
                old_value=f"<mdsol:{plural_local}>{text}</>",
                new_value=f"{len(values)}× <mdsol:{singular_local}>",
            ))


# ── Reorder ItemGroupDef: ItemRef first, mdsol:LabelRef at end (RAVE-117) ────

def _sort_itemgroup_label_refs(
    root: etree._Element, nsmap: dict, result: TransformResult
) -> None:
    """Move all mdsol:LabelRef elements to after the last ItemRef in each ItemGroupDef."""
    label_ref_tag = f"{{{_MDSOL_NS}}}LabelRef"
    for ig in root.iter(q(nsmap, "ItemGroupDef")):
        children = list(ig)
        item_ref_positions = [
            i for i, c in enumerate(children)
            if isinstance(c.tag, str) and etree.QName(c.tag).localname == "ItemRef"
        ]
        label_ref_positions = [
            i for i, c in enumerate(children)
            if c.tag == label_ref_tag
        ]
        if not item_ref_positions or not label_ref_positions:
            continue
        last_item_ref = max(item_ref_positions)
        first_label_ref = min(label_ref_positions)
        if first_label_ref >= last_item_ref:
            continue  # already in order
        label_refs = [c for c in children if c.tag == label_ref_tag]
        for lr in label_refs:
            ig.remove(lr)
        remaining = list(ig)
        last_ir_pos = max(
            i for i, c in enumerate(remaining)
            if isinstance(c.tag, str) and etree.QName(c.tag).localname == "ItemRef"
        )
        for offset, lr in enumerate(label_refs):
            ig.insert(last_ir_pos + 1 + offset, lr)
        result.changes.append(Change(
            status=ChangeStatus.APPLIED, rule="RAVE-117",
            description=(
                f"<ItemGroupDef OID='{ig.get('OID', '?')}'> {len(label_refs)} "
                "mdsol:LabelRef element(s) moved to after the last ItemRef."
            ),
            location=loc(ig),
            old_value=f"LabelRef at positions {label_ref_positions}",
            new_value="LabelRef(s) after all ItemRef elements",
        ))


# ── Reorder FormDef / ItemGroupDef children per ODM 1.3 (ODM-059, ODM-060) ───

def _reorder_def_children(
    root: etree._Element, nsmap: dict, result: TransformResult
) -> None:
    """
    Stable-sort FormDef and ItemGroupDef children into the ODM 1.3 sequence.

    ODM elements come first in canonical order, then mdsol: elements in their
    original relative order (vendor extensions close the ODM content model),
    then anything else. OSB writes Alias before ItemGroupRef / ItemRef.
    """
    odm_uri = nsmap.get("odm", "")

    def label(el: etree._Element) -> str:
        if not isinstance(el.tag, str):
            return "#comment"
        local = etree.QName(el.tag).localname
        return f"mdsol:{local}" if el.tag.startswith(f"{{{_MDSOL_NS}}}") else local

    def sequence(children: list[etree._Element]) -> str:
        # Collapse runs so a group of 40 ItemRef reads "ItemRef×40"
        runs: list[list] = []
        for c in children:
            name = label(c)
            if runs and runs[-1][0] == name:
                runs[-1][1] += 1
            else:
                runs.append([name, 1])
        return " → ".join(n if k == 1 else f"{n}×{k}" for n, k in runs)

    for tag, order, rule in _DEF_CHILD_ORDERS:
        rank = {local: i for i, local in enumerate(order)}
        n = len(order)

        def child_key(el: etree._Element) -> int:
            if not isinstance(el.tag, str):
                return n + 2
            ns_uri, _, local = el.tag[1:].partition("}")
            if ns_uri == odm_uri:
                return rank.get(local, n)  # unknown ODM element: after known ones
            if ns_uri == _MDSOL_NS:
                return n + 1
            return n + 2

        for parent in root.iter(q(nsmap, tag)):
            children = list(parent)
            if len(children) < 2:
                continue
            keys = [child_key(c) for c in children]
            if keys == sorted(keys):
                continue  # already in order
            sorted_children = sorted(children, key=child_key)  # stable
            # Tails stay with their position so the indentation is unchanged
            tails = [c.tail for c in children]
            for c in children:
                parent.remove(c)
            for c, tail in zip(sorted_children, tails):
                c.tail = tail
                parent.append(c)
            result.changes.append(Change(
                status=ChangeStatus.APPLIED, rule=rule,
                description=(
                    f"<{tag} OID='{parent.get('OID', '?')}'> child elements "
                    f"reordered to the ODM 1.3 sequence ({' → '.join(order)}, "
                    "then mdsol: elements)."
                ),
                location=loc(parent),
                old_value=sequence(children),
                new_value=sequence(sorted_children),
            ))


# ── Reorder ItemDef body children per Rave required sequence (RAVE-118) ──────

def _reorder_itemdef_body(
    root: etree._Element, nsmap: dict, result: TransformResult
) -> None:
    """Sort ItemDef child elements into the canonical Rave-required sequence."""
    odm_uri = nsmap.get("odm", "")
    order_map = {
        (ns_key, ln): i
        for i, (ns_key, ln) in enumerate(_ITEMDEF_BODY_ORDER)
    }

    def child_key(el: etree._Element) -> int:
        if not isinstance(el.tag, str):
            return len(_ITEMDEF_BODY_ORDER)
        ns_uri, _, local = el.tag[1:].partition("}")
        if ns_uri == odm_uri:
            ns_key = "odm"
        elif ns_uri == _MDSOL_NS:
            ns_key = "mdsol"
        else:
            return len(_ITEMDEF_BODY_ORDER)
        return order_map.get((ns_key, local), len(_ITEMDEF_BODY_ORDER))

    for item_def in root.iter(q(nsmap, "ItemDef")):
        children = list(item_def)
        if len(children) < 2:
            continue
        keys = [child_key(c) for c in children]
        if keys == sorted(keys):
            continue  # already in order
        sorted_children = sorted(children, key=child_key)
        tails = {id(c): c.tail for c in children}
        for c in children:
            item_def.remove(c)
        for c in sorted_children:
            c.tail = tails[id(c)]
            item_def.append(c)
        before_locals = [etree.QName(c.tag).localname for c in children]
        after_locals  = [etree.QName(c.tag).localname for c in sorted_children]
        result.changes.append(Change(
            status=ChangeStatus.APPLIED, rule="RAVE-118",
            description=(
                f"<ItemDef OID='{item_def.get('OID', '?')}'> child elements "
                "reordered to match Rave required sequence."
            ),
            location=loc(item_def),
            old_value=" → ".join(before_locals),
            new_value=" → ".join(after_locals),
        ))


# ── Unknown mdsol: attribute strip (RAVE-130) ────────────────────────────────

def _strip_unknown_mdsol_attrs(root: etree._Element, result: TransformResult) -> None:
    """
    Remove mdsol: attributes not in the Rave schema whitelist.
    Must run after _fix_mdsol_casing and _rename_mdsol_attrs so attrs are
    already in their canonical PascalCase form before the lookup.
    """
    for el in root.iter():
        tag = etree.QName(el.tag).localname if isinstance(el.tag, str) else None
        if tag is None:
            continue
        known = MDSOL_KNOWN_ATTRS.get(tag)
        if known is None:
            continue
        to_remove = [
            (attr, local)
            for attr in list(el.attrib)
            if isinstance(attr, str) and "{" in attr
            for ns, _, local in [attr[1:].partition("}")]
            if ns == _MDSOL_NS and local not in known
        ]
        for attr_name, local in to_remove:
            val = el.attrib.pop(attr_name)
            oid = el.get("OID", "?")
            result.changes.append(Change(
                status=ChangeStatus.APPLIED, rule="RAVE-130",
                description=f"<{tag} OID='{oid}'> mdsol:{local} removed (not in Rave schema).",
                location=loc(el),
                old_value=f"mdsol:{local}={val[:60]}",
                new_value=None,
                note="Rave rejects unknown vendor attributes, aborting the import.",
            ))


# ── Unique ItemGroupRef OrderNumbers within each FormDef (RAVE-105) ──────────

def _fix_itemgroupref_order_numbers(
    root: etree._Element, nsmap: dict, result: TransformResult
) -> None:
    """Renumber ItemGroupRef OrderNumbers within a FormDef when duplicates detected."""
    for fd in root.iter(q(nsmap, "FormDef")):
        ordering_els = [
            c for c in fd
            if isinstance(c.tag, str) and etree.QName(c.tag).localname == "ItemGroupRef"
        ]
        if len(ordering_els) < 2:
            continue
        order_nums = [c.get("OrderNumber") for c in ordering_els]
        defined    = [n for n in order_nums if n is not None]
        if len(defined) == len(set(defined)):
            continue  # all unique — nothing to do
        old_nums = order_nums[:]
        for i, ref in enumerate(ordering_els, start=1):
            ref.set("OrderNumber", str(i))
        result.changes.append(Change(
            status=ChangeStatus.APPLIED, rule="RAVE-105",
            description=(
                f"<FormDef OID='{fd.get('OID', '?')}'> "
                f"ItemGroupRef OrderNumbers renumbered 1–{len(ordering_els)} "
                "(duplicate values detected)."
            ),
            location=loc(fd),
            old_value=", ".join(str(n) for n in old_nums),
            new_value=", ".join(str(i) for i in range(1, len(ordering_els) + 1)),
        ))


# ── Unique FormRef OrderNumbers within each StudyEventDef (RAVE-106) ─────────

def _fix_formref_order_numbers(
    root: etree._Element, nsmap: dict, result: TransformResult
) -> None:
    """Renumber FormRef OrderNumbers within a StudyEventDef when duplicates detected."""
    for se in root.iter(q(nsmap, "StudyEventDef")):
        refs = [
            c for c in se
            if isinstance(c.tag, str) and etree.QName(c.tag).localname == "FormRef"
        ]
        if len(refs) < 2:
            continue
        order_nums = [c.get("OrderNumber") for c in refs]
        defined = [n for n in order_nums if n is not None]
        if len(defined) == len(set(defined)):
            continue
        old_nums = order_nums[:]
        for i, ref in enumerate(refs, start=1):
            ref.set("OrderNumber", str(i))
        result.changes.append(Change(
            status=ChangeStatus.APPLIED, rule="RAVE-106",
            description=(
                f"<StudyEventDef OID='{se.get('OID', '?')}'> "
                f"FormRef OrderNumbers renumbered 1–{len(refs)} "
                "(duplicate values detected)."
            ),
            location=loc(se),
            old_value=", ".join(str(n) for n in old_nums),
            new_value=", ".join(str(i) for i in range(1, len(refs) + 1)),
        ))


# ── Globally unique ItemRef / LabelRef OrderNumbers within each FormDef (RAVE-104)

def _fix_itemref_order_numbers_across_form(
    root: etree._Element, nsmap: dict, result: TransformResult
) -> None:
    """
    For each FormDef, collect all ItemRef and mdsol:LabelRef across all referenced
    ItemGroupDefs (in FormDef's ItemGroupRef document order). When the combined
    OrderNumbers have cross-group duplicates, renumber globally from 1 through N —
    continuing the count from one ItemGroupDef to the next.
    """
    _label_ref_tag = f"{{{_MDSOL_NS}}}LabelRef"

    ig_map: dict[str, etree._Element] = {
        ig.get("OID"): ig
        for ig in root.iter(q(nsmap, "ItemGroupDef"))
        if ig.get("OID")
    }

    for fd in root.iter(q(nsmap, "FormDef")):
        groups: list[tuple[str, list[etree._Element]]] = []
        seen_igs: set[str] = set()
        for ig_ref in fd:
            if not (isinstance(ig_ref.tag, str)
                    and etree.QName(ig_ref.tag).localname == "ItemGroupRef"):
                continue
            ig_oid = ig_ref.get("ItemGroupOID", "")
            if ig_oid in seen_igs:
                continue
            seen_igs.add(ig_oid)
            ig = ig_map.get(ig_oid)
            if ig is None:
                continue
            ordering_els = [
                c for c in ig
                if isinstance(c.tag, str) and (
                    etree.QName(c.tag).localname == "ItemRef"
                    or c.tag == _label_ref_tag
                )
            ]
            if ordering_els:
                groups.append((ig_oid, ordering_els))

        if not groups:
            continue

        all_nums = [el.get("OrderNumber") for _, els in groups for el in els]
        defined  = [n for n in all_nums if n is not None]
        if len(defined) == len(set(defined)):
            continue  # all unique across all groups — nothing to do

        fd_oid = fd.get("OID", "?")
        global_counter = 1
        for ig_oid, ordering_els in groups:
            old_nums = [el.get("OrderNumber") for el in ordering_els]
            for el in ordering_els:
                el.set("OrderNumber", str(global_counter))
                global_counter += 1
            new_nums = [el.get("OrderNumber") for el in ordering_els]
            result.changes.append(Change(
                status=ChangeStatus.APPLIED, rule="RAVE-104",
                description=(
                    f"<ItemGroupDef OID='{ig_oid}'> ItemRef/LabelRef OrderNumbers "
                    f"renumbered globally within <FormDef OID='{fd_oid}'> "
                    "(cross-group duplicate values detected)."
                ),
                location=loc(ig_map[ig_oid]),
                old_value=", ".join(str(n) for n in old_nums),
                new_value=", ".join(new_nums),
            ))


# ── Record deliberate skips ───────────────────────────────────────────────────

def _record_skips(
    root: etree._Element, nsmap: dict, result: TransformResult
) -> None:
    # boolean DataType
    for tag in ("ItemDef", "CodeList"):
        for el in root.iter(q(nsmap, tag)):
            if el.get("DataType") == "boolean":
                result.changes.append(Change(
                    status=ChangeStatus.SKIPPED, rule="RAVE-030",
                    description=f"<{tag} OID='{el.get('OID', '?')}'> uses DataType='boolean'.",
                    location=loc(el), old_value="boolean",
                    note=(
                        "Cannot auto-convert: replacing boolean with integer requires "
                        "adding a 0/1 codelist and updating the ItemDef. Fix manually."
                    ),
                ))

    # Missing Length on text fields
    for el in root.iter(q(nsmap, "ItemDef")):
        if el.get("DataType") == "text" and el.get("Length") is None:
            result.changes.append(Change(
                status=ChangeStatus.SKIPPED, rule="RAVE-060",
                description=f"<ItemDef OID='{el.get('OID', '?')}'> has no Length attribute.",
                location=loc(el),
                note="Cannot auto-set Length: the correct value depends on your data. Add Length='N' manually.",
            ))

    # CodeList / ItemDef DataType mismatch
    cl_types: dict[str, str] = {}
    for cl in root.iter(q(nsmap, "CodeList")):
        oid = cl.get("OID")
        if oid and oid not in cl_types:
            cl_types[oid] = cl.get("DataType", "")

    def _norm(dt: str) -> str:
        return "text" if dt == "string" else dt

    for item in root.iter(q(nsmap, "ItemDef")):
        item_dt = item.get("DataType", "")
        cl_ref  = item.find(q(nsmap, "CodeListRef"))
        if cl_ref is None:
            continue
        cl_oid = cl_ref.get("CodeListOID", "")
        cl_dt  = cl_types.get(cl_oid, "")
        if cl_dt and item_dt and _norm(cl_dt) != _norm(item_dt):
            result.changes.append(Change(
                status=ChangeStatus.SKIPPED, rule="RAVE-031",
                description=(
                    f"<ItemDef OID='{item.get('OID', '?')}'> DataType='{item_dt}' "
                    f"does not match CodeList '{cl_oid}' DataType='{cl_dt}'."
                ),
                location=loc(item),
                note=(
                    "Cannot auto-fix: it is unclear which side is correct. "
                    "Align DataType manually on either the ItemDef or the CodeList."
                ),
            ))

    # Float missing SignificantDigits
    for el in root.iter(q(nsmap, "ItemDef")):
        if el.get("DataType") == "float" and el.get("SignificantDigits") is None:
            result.changes.append(Change(
                status=ChangeStatus.SKIPPED, rule="RAVE-063",
                description=f"<ItemDef OID='{el.get('OID', '?')}'> float is missing SignificantDigits.",
                location=loc(el),
                note="Cannot auto-set SignificantDigits: decimal precision depends on your data.",
            ))

    # Question text > 255
    for item in root.iter(q(nsmap, "ItemDef")):
        question = item.find(q(nsmap, "Question"))
        if question is None:
            continue
        for trans in question.iter(q(nsmap, "TranslatedText")):
            if len((trans.text or "").strip()) > 255:
                result.changes.append(Change(
                    status=ChangeStatus.SKIPPED, rule="RAVE-040",
                    description=(
                        f"<ItemDef OID='{item.get('OID', '?')}'> Question text "
                        "exceeds 255 characters."
                    ),
                    location=loc(trans),
                    note="Cannot auto-truncate: shortening question text may change its meaning.",
                ))
