"""Version admission shared by Windows inspection and capture receipts."""
import re


def is_windows_42_version(value):
    """Accept complete 4.2.x.x file versions; this does not prove compatibility."""
    return isinstance(value, str) and re.fullmatch(r'4\.2\.[0-9]+\.[0-9]+', value) is not None
