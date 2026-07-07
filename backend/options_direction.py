VALID_OPTION_DIRECTIONS = {"calls_only", "puts_only"}
LEGACY_BOTH_DIRECTION = "both"


def normalize_options_type(value, default="calls_only"):
    clean = str(value or default or "calls_only").strip().lower()
    if clean in {"call", "calls", "call_only", "calls_only"}:
        return "calls_only"
    if clean in {"put", "puts", "put_only", "puts_only"}:
        return "puts_only"
    if clean in {"both", "calls_and_puts", "call_put"}:
        return LEGACY_BOTH_DIRECTION
    return clean


def validate_options_type(value, *, default="calls_only", field_name="options_type"):
    clean = normalize_options_type(value, default=default)
    if clean not in VALID_OPTION_DIRECTIONS:
        raise ValueError(f"{field_name} must be CALL or PUT. Legacy Both is disabled.")
    return clean


def is_legacy_or_invalid_options_type(value):
    return normalize_options_type(value, default="") not in VALID_OPTION_DIRECTIONS


def options_type_to_contract_type(value):
    clean = normalize_options_type(value)
    if clean == "calls_only":
        return "CALL"
    if clean == "puts_only":
        return "PUT"
    return None


def options_type_label(value):
    clean = normalize_options_type(value)
    if clean == "calls_only":
        return "CALL"
    if clean == "puts_only":
        return "PUT"
    return "Legacy Both Disabled"
