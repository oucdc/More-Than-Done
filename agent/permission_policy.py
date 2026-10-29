"""User-selected approval presets and conservative automatic checks."""

import re


PERMISSION_PRESETS = {
    "request": {"allow_read": True, "allow_write": True, "allow_shell": True, "confirm_write": True, "confirm_shell": True},
    "auto": {"allow_read": True, "allow_write": True, "allow_shell": True, "confirm_write": False, "confirm_shell": True},
    "full": {"allow_read": True, "allow_write": True, "allow_shell": True, "confirm_write": False, "confirm_shell": False},
}
PERMISSION_LABELS = {"request": "请求批准", "auto": "帮我批准", "full": "完全访问权限", "custom": "自定义权限"}


def read_only_check(command: object) -> str | None:
    """Recognize only exact, argument-free checks executed by Python, never a shell.

    Everything else, including pipelines, redirections, substitutions, scripts,
    network access and command arguments, needs approval in automatic mode.
    """
    if not isinstance(command, str):
        return None
    text = command.strip().casefold()
    wrapper = re.fullmatch(r'powershell(?:\.exe)?\s+-noprofile\s+-command\s+(?:"([^"\r\n]+)"|\x27([^\x27\r\n]+)\x27|([^\s"\x27]+))', text)
    if wrapper:
        text = next(group for group in wrapper.groups() if group is not None)
    if text in {"pwd", "get-location"}:
        return "location"
    if text in {"dir", "ls", "get-childitem"}:
        return "directory"
    return None
