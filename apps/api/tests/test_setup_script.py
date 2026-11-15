from pathlib import Path
import os
import subprocess

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX shell wrapper")


ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("shell", ["sh", "bash"])
def test_setup_uses_locked_dependencies_from_any_directory(tmp_path, shell):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    calls = tmp_path / 'calls'
    for name in ('uv', 'pnpm', 'node', 'npm'):
        executable = bindir / name
        executable.write_text('#!/bin/sh\nprintf "%s\\n" "' + name + ' $*" >> "$HMW_TEST_CALLS"\n')
        executable.chmod(0o755)
    env = {**os.environ, 'PATH':f'{bindir}:/usr/bin:/bin', 'HMW_TEST_CALLS':str(calls)}
    result = subprocess.run([shell, str(ROOT / 'scripts' / 'setup.sh')], cwd=tmp_path,
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    commands = calls.read_text().splitlines()
    assert f'uv sync --locked --project {ROOT / "apps/api"}' in commands
    assert f'npm ci --prefix {ROOT / "apps/agent"} --ignore-scripts' in commands
    assert f'pnpm --dir {ROOT / "apps/web"} install --frozen-lockfile' in commands


def test_setup_stops_when_dependency_install_fails(tmp_path):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    for name in ('uv', 'pnpm', 'node', 'npm'):
        executable = bindir / name
        executable.write_text('#!/bin/sh\n' + ('exit 42\n' if name == 'uv' else 'exit 0\n'))
        executable.chmod(0o755)
    env = {**os.environ, 'PATH':f'{bindir}:/usr/bin:/bin'}
    result = subprocess.run(['bash', str(ROOT / 'scripts' / 'setup.sh')], cwd=tmp_path,
                            env=env, capture_output=True, text=True)
    assert result.returncode == 42
    assert '环境准备完成' not in result.stdout
