from vmctl_core.paths import safe_segment


def validate_vm_identifier(name: str, *, field: str) -> None:
    """VM and snapshot names can become both path segments and virsh arguments."""
    if not isinstance(name, str):
        raise ValueError(f"{field} must be a string")
    safe_segment(name, field=field)
    if name.startswith("-"):
        raise ValueError(f"{field} must not start with '-'")
