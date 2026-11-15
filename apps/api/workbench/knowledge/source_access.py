"""Verified, bounded source downloads shared by owner and guest routes."""
import hashlib
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, Response


def verified_download(root: Path, key: str, filename: str, digest: str, max_bytes: int) -> Response:
    try:
        with (root / key).open('rb') as source_file:
            content = source_file.read(max_bytes + 1)
    except OSError:
        raise HTTPException(404, 'Source unavailable') from None
    if len(content) > max_bytes or hashlib.sha256(content).hexdigest() != digest:
        raise HTTPException(409, 'Source integrity check failed')
    return Response(content=content, media_type='application/octet-stream', headers={
        'Content-Disposition': f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
        'X-Content-Type-Options':'nosniff', 'Cache-Control':'no-store',
    })
