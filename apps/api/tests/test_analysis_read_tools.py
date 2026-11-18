import os

import pytest

from workbench.issues.read_tools import AnalysisReadTools

pytestmark = pytest.mark.skipif(os.name == "nt", reason="descriptor-relative no-follow access is unavailable on Windows")


class Library:
    def list_documents(self, offset):
        return {'documents': [{'id': 'doc'}], 'next_offset': None}

    def read_document(self, path, offset):
        return {'text': path, 'next_offset': None}


def test_files_are_bounded_and_paginated(tmp_path):
    (tmp_path / 'a.txt').write_text('abcdef')
    (tmp_path / 'b.txt').write_text('b')
    tools = AnalysisReadTools(code_root=tmp_path, library=Library(), page_size=1, read_size=3)
    assert tools.list_resources('code', '', 0) == {'entries': [{'name': 'a.txt', 'type': 'file'}], 'next_offset': 1}
    assert tools.read_resource('code', 'a.txt', 0) == {'text': 'abc', 'next_offset': 3}
    assert tools.read_resource('code', 'a.txt', 3) == {'text': 'def', 'next_offset': None}
    assert tools.list_resources('knowledge', '', 0)['documents'][0]['id'] == 'doc'
    assert tools.read_resource('knowledge', 'doc', 0)['text'] == 'doc'


@pytest.mark.parametrize('path', ['../secret', '/etc/passwd', 'C:\\secret', 'dir/../a', '.env', '.git/config', 'secret.pem'])
def test_denies_unsafe_paths(tmp_path, path):
    tools = AnalysisReadTools(code_root=tmp_path)
    with pytest.raises(ValueError):
        tools.read_resource('code', path, 0)


def test_symlinks_and_sensitive_entries_are_hidden(tmp_path):
    (tmp_path / 'safe').write_text('ok')
    (tmp_path / '.env').write_text('private')
    (tmp_path / 'link').symlink_to(tmp_path / 'safe')
    tools = AnalysisReadTools(code_root=tmp_path)
    assert tools.list_resources('code', '', 0)['entries'] == [{'name': 'safe', 'type': 'file'}]
    with pytest.raises(ValueError):
        tools.read_resource('code', 'link', 0)


def test_log_is_a_server_selected_file(tmp_path):
    log = tmp_path / 'service.log'
    log.write_text('hello')
    tools = AnalysisReadTools(log_path=log)
    assert tools.list_resources('log', '', 0)['entries'] == [{'name': 'service.log', 'type': 'file'}]
    assert tools.read_resource('log', 'service.log', 0)['text'] == 'hello'
    with pytest.raises(ValueError):
        tools.read_resource('log', '../service.log', 0)


def test_archive_browsing_rejects_traversal_and_symlinks(tmp_path):
    import zipfile
    archive = tmp_path / 'logs.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('server/run.log', 'hello world')
        output.writestr('../outside', 'bad')
        link = zipfile.ZipInfo('link')
        link.create_system = 3
        link.external_attr = (0o120777 << 16)
        output.writestr(link, 'server/run.log')
    tools = AnalysisReadTools(log_path=archive, read_size=5)
    assert tools.list_resources('log', 'logs.zip!/', 0)['entries'] == [{'name': 'server', 'type': 'directory'}]
    assert tools.read_resource('log', 'logs.zip!/server/run.log', 0) == {'text': 'hello', 'next_offset': 5}
    with pytest.raises(ValueError):
        tools.read_resource('log', 'logs.zip!/../outside', 0)
    with pytest.raises(ValueError):
        tools.read_resource('log', 'logs.zip!/link', 0)


def test_rar_without_decoder_explains_unavailability(tmp_path, monkeypatch):
    monkeypatch.setattr('workbench.issues.read_tools.ctypes.util.find_library', lambda name: None)
    archive = tmp_path / 'logs.rar'
    archive.write_bytes(b'not a rar')
    tools = AnalysisReadTools(log_path=archive)
    with pytest.raises(ValueError, match='RAR'):
        tools.list_resources('log', 'logs.rar!/', 0)


def test_read_only_command_search_and_rejects_shell(tmp_path):
    (tmp_path / 'source.txt').write_text('first\nneedle here\nlast')
    (tmp_path / '.env').write_text('needle secret')
    tools = AnalysisReadTools(code_root=tmp_path)
    assert tools.run_command('pwd', []) == {'output': '/'}
    assert 'source.txt:2:needle here' in tools.run_command('rg', ['needle'])['output']
    with pytest.raises(ValueError):
        tools.run_command('sh', ['-c', 'whoami'])
    with pytest.raises(ValueError):
        tools.run_command('rg', ['needle', '../outside'])
    with pytest.raises(ValueError, match='unsupported'):
        tools.run_command('git', ['status'])


@pytest.mark.parametrize('extension,mode', [('tar', 'w'), ('tar.gz', 'w:gz')])
def test_tar_archive_regular_files_only(tmp_path, extension, mode):
    import io
    import tarfile
    archive = tmp_path / f'logs.{extension}'
    with tarfile.open(archive, mode) as output:
        entry = tarfile.TarInfo('run.log')
        entry.size = 4
        output.addfile(entry, io.BytesIO(b'data'))
        link = tarfile.TarInfo('link')
        link.type = tarfile.SYMTYPE
        link.linkname = 'run.log'
        output.addfile(link)
    tools = AnalysisReadTools(log_path=archive)
    assert tools.list_resources('log', f'{archive.name}!/', 0)['entries'] == [{'name': 'run.log', 'type': 'file'}]
    assert tools.read_resource('log', f'{archive.name}!/run.log', 0)['text'] == 'data'
    with pytest.raises(ValueError):
        tools.read_resource('log', f'{archive.name}!/link', 0)


def test_log_display_name_is_independent_from_storage_key(tmp_path):
    import zipfile
    archive = tmp_path / 'random-storage-key'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('run.log', 'result')
    tools = AnalysisReadTools(log_path=archive, log_filename='logs.zip')
    assert tools.list_resources('log', '', 0)['entries'][0]['name'] == 'logs.zip'
    assert tools.read_resource('log', 'logs.zip!/run.log', 0)['text'] == 'result'


def test_rar_uses_bounded_decoder_and_filters_entries(tmp_path, monkeypatch):
    import json
    import subprocess
    archive = tmp_path / 'logs.rar'
    archive.write_bytes(b'rar')
    monkeypatch.setattr('workbench.issues.read_tools.ctypes.util.find_library', lambda name: 'libarchive.so')
    def run(*args, **kwargs):
        assert kwargs['input'] == b'rar'
        assert kwargs['timeout'] == 5
        return subprocess.CompletedProcess(args[0], 0, stdout=json.dumps({'entries': ['dir/log', '../secret', '.env'], 'data': 'aGVsbG8='}).encode())
    monkeypatch.setattr('workbench.issues.read_tools.subprocess.run', run)
    tools = AnalysisReadTools(log_path=archive, read_size=3)
    assert tools.list_resources('log', 'logs.rar!/', 0)['entries'] == [{'name': 'dir', 'type': 'directory'}]
    assert tools.read_resource('log', 'logs.rar!/dir/log', 0) == {'text': 'hel', 'next_offset': 3}


def test_rar_decoder_timeout_is_reported(tmp_path, monkeypatch):
    import subprocess
    archive = tmp_path / 'logs.rar'
    archive.write_bytes(b'rar')
    monkeypatch.setattr('workbench.issues.read_tools.ctypes.util.find_library', lambda name: 'libarchive.so')
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 5)
    monkeypatch.setattr('workbench.issues.read_tools.subprocess.run', run)
    with pytest.raises(ValueError, match='timeout'):
        AnalysisReadTools(log_path=archive).list_resources('log', 'logs.rar!/', 0)


@pytest.mark.parametrize('filename', ['secret.log', '.log', 'server:1.log', 'security-token.log'])
def test_uploaded_log_with_sensitive_name_uses_safe_alias(tmp_path, filename):
    source = tmp_path / 'storage-key'
    source.write_text('failure details')
    tools = AnalysisReadTools(log_path=source, log_filename=filename)
    assert tools.list_resources('log')['entries'][0]['name'] == 'uploaded-log.log'
    assert tools.read_resource('log', 'uploaded-log.log')['text'] == 'failure details'
