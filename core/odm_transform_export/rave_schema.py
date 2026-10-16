"""
Medidata Rave schema constants used by the Rave transform.

Copied from the Rave rule set of the ODM XML Validator, which uses the same
constants for its RAVE-1xx checks. Keep both copies in sync when a rule changes.

Source: Medidata RWS ODM Study Metadata Schema Support (official PDF).
"""
from __future__ import annotations

# ── mdsol: attribute whitelist (PascalCase, canonical Rave names, per element) ─
# Sources:
#   - Medidata RWS ODM Study Metadata Schema Support PDF
#   - ALS (Architect Loader Specification) → OSB attribute mapping, canonicalised
# Any mdsol: attribute not in this map (after casing and semantic renaming) is
# flagged by RAVE-130 and stripped automatically by the transformer.
# Exported as a public constant so the transformer can import it directly.
MDSOL_KNOWN_ATTRS: dict[str, frozenset[str]] = {
    "ODM": frozenset(),
    "Study": frozenset(),
    "MetaDataVersion": frozenset({
        "PrimaryFormOID", "DefaultMatrixOID", "SignaturePrompt",
    }),
    "StudyEventDef": frozenset({
        "StudyEventRepeatKey", "AllowRemoveRepeat", "IsVisible",
        "ChronologicalOrder",
    }),
    "StudyEventRef": frozenset({
        "BlockSubjectDate",
    }),
    "FormDef": frozenset({
        "OrderNumber", "Active", "Template", "SignatureRequired",
        "LogDirection", "DoubleDataEntry", "ConfirmationStyle",
        "LinkStudyEventOID", "LinkFormOID",
    }),
    "FormRef": frozenset({
        "Active",
    }),
    "ItemGroupDef": frozenset({
        "Grid", "IsLog", "IsVisible",
        "ViewRestriction", "EntryRestriction",
        "LabelType", "IndentLevel", "FieldNumber", "LineNumber",
    }),
    "ItemGroupRef": frozenset({
        "Active", "IsVisible",
    }),
    "ItemDef": frozenset({
        # Valid as attributes (Rave schema)
        "Active", "ControlType", "AcceptableFileExtensions", "IndentLevel",
        "SourceDocument", "DefaultValue", "SASFormat", "SASLabel",
        "QueryFutureDate", "Visible", "TranslationRequired", "QueryNonConformance",
        "OtherVisits", "CanSetItemGroupDate", "CanSetFormDate", "CanSetStudyEventDate",
        "CanSetSubjectDate", "VisualVerify", "DoesNotBreakSignature", "DateTimeFormat",
        "FieldNumber", "VariableOID",
        # These attrs must be expressed as child elements; RAVE-116 converts them
        "HeaderText", "HelpText",
        "ViewRestriction", "ViewRestrictions",
        "EntryRestriction", "EntryRestrictions",
        "ReviewGroups", "ReviewGroup",
    }),
    "ItemRef": frozenset({
        "Active",
        # mdsol:Visible belongs on ItemDef (not ItemRef) per Rave doc p.48;
        # the transformer moves it there. IsVisible on ItemRef is NOT valid.
    }),
    "CodeList": frozenset({
        "ExternalDictionaryType",
    }),
    "CodeListItem": frozenset({
        "UserNote", "TranslationComment",
    }),
    "EnumeratedItem": frozenset({
        "UserNote", "TranslationComment",
    }),
    "MethodDef": frozenset(),
    "ConditionDef": frozenset(),
    "MeasurementUnit": frozenset(),
}

# ── Semantic rename map (applied *after* PascalCase conversion) ──────────────
# Maps from the wrong name (ALS column name, promoted through OSB) to the
# canonical Rave doc name. None = drop entirely (not in the Rave doc).
# The transformer imports this to apply the renames; the validator uses it
# for RAVE-115 checks.
MDSOL_RENAMES: dict[str, str | None] = {
    "SasLabel":             "SASLabel",
    "SasFormat":            "SASFormat",
    "IsSignatureRequired":  "SignatureRequired",
    "IsTranslationRequired": "TranslationRequired",
    "IsVisualVerify":       "VisualVerify",
    "IsClinicalSignificance": None,       # not in Rave doc
    "IsEproForm":           None,         # not in Rave doc
    "CanSetRecordDate":     "CanSetItemGroupDate",
    "CanSetDataPageDate":   "CanSetFormDate",
    "CanSetInstanceDate":   "CanSetStudyEventDate",
    # IsVisible on ItemRef → moved to ItemDef as Visible (see Fix 5 transformer)
    "IsVisible":            "Visible",             # on ItemRef only (then moved)
}

# ── Default values for boolean mdsol: attributes (canonical names) ────────────
# If an attribute's value equals its Rave default, it is redundant and the
# transformer removes it (Fix 10 / skip-when-default rule).
# Values are in the Rave Yes/No form (after Fix 7 conversion).
MDSOL_BOOLEAN_DEFAULTS: dict[str, str] = {
    # Default Yes
    "Active":                "Yes",
    "Visible":               "Yes",
    # Default No
    "Template":              "No",
    "SignatureRequired":     "No",
    "SourceDocument":        "No",
    "QueryFutureDate":       "No",
    "QueryFutureDates":      "No",
    "QueryNonConformance":   "No",
    "TranslationRequired":   "No",
    "OtherVisits":           "No",
    "CanSetItemGroupDate":   "No",
    "CanSetFormDate":        "No",
    "CanSetStudyEventDate":  "No",
    "CanSetSubjectDate":     "No",
    "VisualVerify":          "No",
    "DoesNotBreakSignature": "No",
    "Specify":               "No",
}

# mdsol: attrs on ItemDef that must be expressed as child elements.
# Key = attribute local name; value = (child element local name, split_by_comma).
ITEMDEF_MDSOL_ATTR_TO_CHILD: dict[str, str] = {
    # attr local name → child element local name (same value preserved as text content)
    "HeaderText":        "HeaderText",
    "HelpText":          "HelpText",
    "ViewRestriction":   "ViewRestriction",
    "ViewRestrictions":  "ViewRestrictions",
    "EntryRestriction":  "EntryRestriction",
    "EntryRestrictions": "EntryRestrictions",
    "ReviewGroups":      "ReviewGroups",
    "ReviewGroup":       "ReviewGroup",
}

# Canonical child-element order within an <ItemDef> body.
# Each entry is (ns_key, localname); ns_key is "odm" or "mdsol".
_ITEMDEF_BODY_ORDER: list[tuple[str, str]] = [
    ("odm",   "Description"),
    ("odm",   "Question"),
    ("odm",   "ExternalQuestion"),
    ("odm",   "MeasurementUnitRef"),
    ("odm",   "RangeCheck"),
    ("odm",   "CodeListRef"),
    ("odm",   "Role"),
    ("odm",   "Alias"),
    ("mdsol", "HeaderText"),
    ("mdsol", "HelpText"),
    ("mdsol", "ViewRestriction"),
    ("mdsol", "ViewRestrictions"),
    ("mdsol", "EntryRestriction"),
    ("mdsol", "EntryRestrictions"),
    ("mdsol", "ReviewGroup"),
    ("mdsol", "ReviewGroups"),
    ("mdsol", "Label"),
]
