"""Feature-module names are project-scoped labels, never filesystem paths."""
import unicodedata

PUBLIC_MODULE = '公共知识'


def module_name(value: str) -> str:
    value = unicodedata.normalize('NFKC', value).strip()
    if not value or len(value) > 200 or len(value.casefold()) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('invalid module')
    return value


def module_key(value: str) -> str:
    return module_name(value).casefold()
