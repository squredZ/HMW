"""Bounded, project-scoped read tools for the analysis subprocess.

Filesystem offsets are byte offsets; knowledge offsets retain ProjectLibrary's
character semantics. Roots and the single log file are trusted server settings,
never values supplied by the agent. On platforms lacking descriptor-relative
no-follow operations, filesystem access fails closed.
"""
import base64
import ctypes.util
import gzip
import json
import subprocess
import sys
import io
import os
import tarfile
import zipfile
import re
import stat
from contextlib import contextmanager
from pathlib import Path


class AnalysisReadTools:
    def __init__(self, *, code_root=None, log_path=None, log_filename=None, skill_root=None, library=None,
                 page_size=30, read_size=3000):
        if not 1 <= page_size <= 100 or not 1 <= read_size <= 12000:
            raise ValueError('invalid limits')
        self.roots = {'code': code_root, 'skill': skill_root}
        self.log_path = Path(log_path) if log_path is not None else None
        self.log_filename = log_filename if log_filename is not None else (self.log_path.name if self.log_path else None)
        if self.log_filename is not None:
            try:
                if self._parts(self.log_filename) != [self.log_filename]:
                    raise ValueError('invalid log filename')
            except ValueError:
                lower = self.log_filename.lower()
                suffix = next((ext for ext in ('.tar.gz', '.zip', '.tar', '.tgz', '.rar') if lower.endswith(ext)), '.log')
                self.log_filename = 'uploaded-log' + suffix
        self.library = library
        self.page_size, self.read_size = page_size, read_size

    @staticmethod
    def _offset(offset):
        if type(offset) is not int or not 0 <= offset <= 2147483647:
            raise ValueError('invalid offset')

    @staticmethod
    def _parts(path):
        if not isinstance(path, str) or len(path) > 4096 or '\\' in path or '\x00' in path:
            raise ValueError('invalid path')
        if not path:
            return []
        parts = path.split('/')
        if any(not part or part in ('.', '..') or ':' in part or AnalysisReadTools._sensitive(part) for part in parts):
            raise ValueError('invalid path')
        return parts

    @staticmethod
    def _sensitive(name):
        lower = name.lower()
        return (lower.startswith('.') or lower in {'node_modules', '__pycache__', 'credentials', 'secrets'}
                or lower.endswith(('.pem', '.key', '.p12', '.pfx', '.jks', '.keystore'))
                or bool(re.search(r'(^|[._-])(secret|token|password|credential)([._-]|$)', lower)))

    @contextmanager
    def _open(self, root, parts, directory):
        if (root is None or not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY')
                or os.open not in os.supports_dir_fd):
            raise ValueError('resource unavailable')
        fds = []
        try:
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            fds.append(fd)
            for index, part in enumerate(parts):
                flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0)
                if index < len(parts) - 1 or directory:
                    flags |= os.O_DIRECTORY
                fd = os.open(part, flags, dir_fd=fd)
                fds.append(fd)
            mode = os.fstat(fd).st_mode
            if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
                raise ValueError('resource unavailable')
            yield fd
        except OSError as exc:
            raise ValueError('resource unavailable') from exc
        finally:
            for fd in reversed(fds):
                os.close(fd)

    def _target(self, kind, path):
        parts = self._parts(path)
        if kind == 'log':
            if self.log_path is None or (parts and parts != [self.log_filename]):
                raise ValueError('resource unavailable')
            return self.log_path.parent, [self.log_path.name] if parts else []
        if kind not in self.roots or self.roots[kind] is None:
            raise ValueError('resource unavailable')
        return self.roots[kind], parts

    def list_resources(self, kind, path='', offset=0):
        self._offset(offset)
        if kind == 'log' and isinstance(path, str) and '!/' in path:
            return self._archive(path, offset, listing=True)
        if kind == 'knowledge':
            if path or self.library is None:
                raise ValueError('resource unavailable')
            return self.library.list_documents(offset)
        root, parts = self._target(kind, path)
        if kind == 'log':
            if parts:
                raise ValueError('resource unavailable')
            # Verify that the configured log is a regular file before advertising it.
            name = self.log_filename
            with self._open(root, [self.log_path.name], False):
                pass
            entries = [{'name': name, 'type': 'file'}]
        else:
            entries = []
            with self._open(root, parts, True) as fd:
                with os.scandir(fd) as it:
                    for entry in it:
                        if self._sensitive(entry.name):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            kind_name = 'directory'
                        elif entry.is_file(follow_symlinks=False):
                            kind_name = 'file'
                        else:
                            continue
                        entries.append({'name': entry.name, 'type': kind_name})
                        if len(entries) > 10000:
                            raise ValueError('directory too large')
            entries.sort(key=lambda entry: entry['name'])
        end = offset + self.page_size
        return {'entries': entries[offset:end], 'next_offset': end if end < len(entries) else None}

    def read_resource(self, kind, path, offset=0):
        self._offset(offset)
        if kind == 'log' and isinstance(path, str) and '!/' in path:
            return self._archive(path, offset, listing=False)
        if kind == 'knowledge':
            if self.library is None:
                raise ValueError('resource unavailable')
            return self.library.read_document(path, offset)
        root, parts = self._target(kind, path)
        if not parts:
            raise ValueError('resource unavailable')
        with self._open(root, parts, False) as fd:
            os.lseek(fd, offset, os.SEEK_SET)
            content = os.read(fd, self.read_size + 1)
        return {'text': content[:self.read_size].decode('utf-8', errors='replace'),
                'next_offset': offset + self.read_size if len(content) > self.read_size else None}

    def _archive(self, path, offset, *, listing):
        archive_name, member_path = path.split('!/', 1)
        if self.log_path is None or archive_name != self.log_filename:
            raise ValueError('resource unavailable')
        self._parts(archive_name)
        parts = self._parts(member_path)
        if not listing and not parts:
            raise ValueError('resource unavailable')
        lower = archive_name.lower()
        if not lower.endswith(('.zip', '.tar', '.tar.gz', '.tgz', '.rar')):
            raise ValueError('unsupported archive')
        limit = 20 * 1024 * 1024
        with self._open(self.log_path.parent, [self.log_path.name], False) as fd:
            with os.fdopen(os.dup(fd), 'rb') as source:
                data = source.read(limit + 1)
        if len(data) > limit:
            raise ValueError('archive too large')
        if lower.endswith('.rar'):
            return self._rar(data, parts, offset, listing=listing)
        try:
            if lower.endswith(('.tar.gz', '.tgz')):
                with gzip.GzipFile(fileobj=io.BytesIO(data)) as source:
                    data = source.read(limit + 1)
                if len(data) > limit:
                    raise ValueError('archive expansion limit')
            with (zipfile.ZipFile(io.BytesIO(data)) if lower.endswith('.zip') else
                  tarfile.open(fileobj=io.BytesIO(data), mode='r:')) as archive:
                is_zip = isinstance(archive, zipfile.ZipFile)
                members = archive.infolist() if is_zip else archive.getmembers()
                if len(members) > 1000:
                    raise ValueError('archive entry limit')
                available = {}
                total = 0
                for member in members:
                    name = member.filename if is_zip else member.name
                    name = name.rstrip('/')
                    try:
                        safe_parts = self._parts(name)
                    except ValueError:
                        continue
                    if not safe_parts:
                        continue
                    if is_zip:
                        mode = (member.external_attr >> 16) & 0xffff
                        regular = not member.is_dir() and (stat.S_IFMT(mode) in (0, stat.S_IFREG))
                        size = member.file_size
                    else:
                        regular = member.isfile()
                        size = member.size
                    if not regular:
                        continue
                    total += size
                    if size > 1024 * 1024 or total > limit:
                        raise ValueError('archive expansion limit')
                    # Duplicate paths are ambiguous and must not select attacker-controlled variants.
                    key = '/'.join(safe_parts)
                    if key in available:
                        raise ValueError('duplicate archive entry')
                    available[key] = member
                prefix = '/'.join(parts)
                if listing:
                    prefix = prefix + '/' if prefix else ''
                    entries = {}
                    for name in available:
                        if name.startswith(prefix):
                            suffix = name[len(prefix):]
                            head, separator, _ = suffix.partition('/')
                            if head:
                                entries[head] = 'directory' if separator else 'file'
                    result = [{'name': name, 'type': entries[name]} for name in sorted(entries)]
                    end = offset + self.page_size
                    return {'entries': result[offset:end], 'next_offset': end if end < len(result) else None}
                member = available.get(prefix)
                if member is None:
                    raise ValueError('resource unavailable')
                with (archive.open(member) if is_zip else archive.extractfile(member)) as source:
                    content = source.read(1024 * 1024 + 1)
                if len(content) > 1024 * 1024:
                    raise ValueError('archive expansion limit')
                chunk = content[offset:offset + self.read_size]
                end = offset + len(chunk)
                return {'text': chunk.decode('utf-8', errors='replace'),
                        'next_offset': end if end < len(content) else None}
        except (OSError, EOFError, tarfile.TarError, zipfile.BadZipFile, RuntimeError) as exc:
            raise ValueError('invalid archive') from exc

    def run_command(self, command, args):
        """Execute a small in-process command vocabulary; no shell is involved."""
        if not isinstance(args, list) or len(args) > 2 or any(not isinstance(a, str) or len(a) > 4096 for a in args):
            raise ValueError('invalid arguments')
        if command == 'pwd' and not args:
            return {'output': '/'}
        if command == 'ls' and len(args) <= 1:
            return {'output': '\n'.join(item['name'] for item in self.list_resources('code', args[0] if args else '', 0)['entries'])}
        if command == 'cat' and len(args) == 1:
            result = self.read_resource('code', args[0], 0)
            return {'output': result['text'], 'next_offset': result['next_offset']}
        if command == 'rg' and 1 <= len(args) <= 2:
            needle = args[0]
            if not needle or len(needle) > 256 or '\n' in needle or '\r' in needle:
                raise ValueError('invalid search')
            base = args[1] if len(args) == 2 else ''
            self._parts(base)
            pending = [base]
            seen, byte_count, matches = 0, 0, []
            while pending:
                directory = pending.pop()
                page = 0
                while page is not None:
                    result = self.list_resources('code', directory, page)
                    for entry in result['entries']:
                        name = f'{directory}/{entry["name"]}' if directory else entry['name']
                        seen += 1
                        if seen > 500:
                            raise ValueError('search entry limit')
                        if entry['type'] == 'directory':
                            pending.append(name)
                            continue
                        root, parts = self._target('code', name)
                        with self._open(root, parts, False) as fd:
                            data = os.read(fd, min(65537, 2 * 1024 * 1024 - byte_count + 1))
                        byte_count += len(data)
                        if byte_count > 2 * 1024 * 1024:
                            raise ValueError('search byte limit')
                        if len(data) > 65536 or b'\0' in data:
                            continue
                        for number, line in enumerate(data.decode('utf-8', errors='replace').splitlines(), 1):
                            if needle in line:
                                matches.append(f'{name}:{number}:{line[:500]}')
                                if len(matches) >= 100 or sum(map(len, matches)) >= 12000:
                                    return {'output': '\n'.join(matches)[:12000], 'truncated': True}
                    page = result['next_offset']
            return {'output': '\n'.join(matches), 'truncated': False}
        raise ValueError('unsupported command or arguments')

    def _rar(self, data, parts, offset, *, listing):
        library = ctypes.util.find_library('archive')
        if not library:
            raise ValueError('RAR archive decoder unavailable (libarchive required)')
        try:
            result = subprocess.run([sys.executable, '-I', '-c', _RAR_WORKER, library,
                                     '' if listing else '/'.join(parts)], input=data,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    timeout=5, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('RAR decoder timeout') from exc
        except OSError as exc:
            raise ValueError('RAR archive decoder unavailable') from exc
        if result.returncode != 0 or len(result.stdout) > 1600000:
            raise ValueError('invalid or unsupported RAR archive')
        try:
            decoded = json.loads(result.stdout)
            names = decoded['entries']
            if not isinstance(names, list) or len(names) > 1000:
                raise ValueError('invalid archive')
            safe = []
            for name in names:
                try:
                    if self._parts(name):
                        safe.append(name)
                except ValueError:
                    continue
            if len(safe) != len(set(safe)):
                raise ValueError('duplicate archive entry')
            prefix = '/'.join(parts)
            if listing:
                prefix = prefix + '/' if prefix else ''
                entries = {}
                for name in safe:
                    if name.startswith(prefix):
                        head, separator, _ = name[len(prefix):].partition('/')
                        if head:
                            entries[head] = 'directory' if separator else 'file'
                items = [{'name': name, 'type': entries[name]} for name in sorted(entries)]
                end = offset + self.page_size
                return {'entries': items[offset:end], 'next_offset': end if end < len(items) else None}
            if prefix not in safe or decoded.get('data') is None:
                raise ValueError('resource unavailable')
            content = base64.b64decode(decoded['data'], validate=True)
            if len(content) > 1024 * 1024:
                raise ValueError('archive expansion limit')
            chunk = content[offset:offset + self.read_size]
            end = offset + len(chunk)
            return {'text': chunk.decode('utf-8', errors='replace'),
                    'next_offset': end if end < len(content) else None}
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError('invalid RAR decoder response') from exc


# libarchive runs in a separate, time-limited process and only reads memory; it
# never extracts paths or invokes an external unpacker. The host revalidates
# names and applies its normal path and sensitive-name policy.
_RAR_WORKER = r'''
import base64, ctypes, json, sys
lib = ctypes.CDLL(sys.argv[1])
def bind(name, restype, *argtypes):
    f = getattr(lib, name); f.restype = restype; f.argtypes = argtypes; return f
ptr, integer, size = ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t
new = bind('archive_read_new', ptr)
support = bind('archive_read_support_format_rar', integer, ptr)
support5 = bind('archive_read_support_format_rar5', integer, ptr)
open_mem = bind('archive_read_open_memory', integer, ptr, ptr, size)
next_header = bind('archive_read_next_header', integer, ptr, ctypes.POINTER(ptr))
pathname = bind('archive_entry_pathname', ctypes.c_char_p, ptr)
filetype = bind('archive_entry_filetype', integer, ptr)
entry_size = bind('archive_entry_size', ctypes.c_longlong, ptr)
symlink = bind('archive_entry_symlink', ctypes.c_char_p, ptr)
hardlink = bind('archive_entry_hardlink', ctypes.c_char_p, ptr)
read_data = bind('archive_read_data', ctypes.c_ssize_t, ptr, ptr, size)
free = bind('archive_read_free', integer, ptr)
data = sys.stdin.buffer.read(20 * 1024 * 1024 + 1)
if len(data) > 20 * 1024 * 1024: raise ValueError('archive too large')
raw = ctypes.create_string_buffer(data); reader = new()
if not reader: raise ValueError('reader unavailable')
try:
    support(reader); support5(reader)
    if open_mem(reader, raw, len(data)) != 0: raise ValueError('invalid archive')
    names, total, name_bytes, found, count = [], 0, 0, None, 0
    while True:
        entry = ptr(); status = next_header(reader, ctypes.byref(entry))
        if status == 1: break
        if status != 0: raise ValueError('invalid archive')
        count += 1
        if count > 1000: raise ValueError('entry limit')
        name = pathname(entry)
        if not name or len(name) > 4096: raise ValueError('invalid name')
        name = name.decode('utf-8', 'strict').rstrip('/')
        name_bytes += len(name)
        if name_bytes > 200000: raise ValueError('name limit')
        if filetype(entry) != 0o100000 or symlink(entry) or hardlink(entry): continue
        length = entry_size(entry)
        if length < 0 or length > 1024 * 1024: raise ValueError('entry size limit')
        total += length
        if total > 20 * 1024 * 1024: raise ValueError('expansion limit')
        names.append(name)
        if name == sys.argv[2]:
            chunks, used = [], 0
            while True:
                buf = ctypes.create_string_buffer(65536)
                amount = read_data(reader, buf, len(buf))
                if amount < 0: raise ValueError('invalid entry')
                if not amount: break
                used += amount
                if used > 1024 * 1024: raise ValueError('expansion limit')
                chunks.append(buf.raw[:amount])
            found = base64.b64encode(b''.join(chunks)).decode('ascii')
    print(json.dumps({'entries':names, 'data':found}))
finally:
    free(reader)
'''
