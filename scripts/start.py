"""Run the local development API, import worker and web server together."""
from __future__ import annotations

import os
import re
from pathlib import Path
import shutil
import socket
import signal
import subprocess
import sys
import time
import tomllib
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / 'apps' / 'api'
WEB_ROOT = ROOT / 'apps' / 'web'


def read_config(path: Path) -> dict:
    try:
        with path.open('rb') as stream:
            config = tomllib.load(stream)
    except FileNotFoundError as exc:
        raise ValueError('配置文件不存在；请复制 config.example.toml 为 .hmw.toml 并填写账号密码。') from exc
    except tomllib.TOMLDecodeError as exc:
        # Use only numeric location information; parser text may contain private values.
        location = re.search(r'\(at line (\d+), column (\d+)\)', str(exc))
        detail = f'（第 {location.group(1)} 行，第 {location.group(2)} 列）' if location else ''
        raise ValueError(f'配置文件格式无效{detail}，请检查 TOML 语法。') from exc
    llm = config.get('llm', {})
    if not isinstance(llm, dict) or type(llm.get('enabled', False)) is not bool:
        raise ValueError('llm.enabled 必须是布尔值 true 或 false，不要加引号。')
    owner = config.get('owner')
    if not isinstance(owner, dict) or any(not isinstance(owner.get(key), str) or not owner[key].strip() for key in ('username', 'password')):
        raise ValueError('请设置非空的 owner.username 和 owner.password。')
    server = config.get('server', {})
    if not isinstance(server, dict):
        raise ValueError('server 必须是配置表。')
    ports = [server.get('api_port', 8000), server.get('web_port', 5173)]
    if any(type(port) is not int or not 1 <= port <= 65535 for port in ports) or ports[0] == ports[1]:
        raise ValueError('api_port 和 web_port 必须是不同的 1–65535 整数。')
    return {'owner':owner, 'server':{'api_port':ports[0], 'web_port':ports[1]}}


def stop_children(children: list[tuple[str, subprocess.Popen]]) -> None:
    # Each child starts in its own process group; do not signal unrelated services.
    for _, process in children:
        try:
            if os.name == 'nt':
                if process.poll() is None:
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=5)
            else:
                os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            process.kill()
    for _, process in children:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name != 'nt':
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait()


def check_ports(ports: list[int]) -> None:
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name != 'nt':
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(('127.0.0.1', port))
            except OSError as exc:
                raise ValueError(f'本地端口 {port} 不可用，请检查占用或修改配置。') from exc


def wait_ready(name: str, url: str, process: subprocess.Popen, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    opener = build_opener(ProxyHandler({}))
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'{name} 在启动期间退出（状态 {process.returncode}）。')
        try:
            with opener.open(url, timeout=0.5) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            time.sleep(0.2)
    raise RuntimeError(f'{name} 未能在规定时间内启动，请检查端口和日志。')


def run(config: dict, config_path: Path | None = None) -> int:
    node = shutil.which('node')
    vite = WEB_ROOT / 'node_modules' / 'vite' / 'bin' / 'vite.js'
    if node is None or not vite.is_file():
        raise ValueError('未找到 Node.js 或前端依赖，请先运行 setup 脚本。')
    server, owner = config['server'], config['owner']
    check_ports([server['api_port'], server['web_port']])
    shared_env = os.environ.copy()
    for name in ('HMW_DEV_USERS_JSON', 'HMW_DEV_USERNAME', 'HMW_DEV_PASSWORD'):
        shared_env.pop(name, None)
    shared_env['HMW_DATABASE_URL'] = f"sqlite:///{(API_ROOT / 'workbench-dev.db').as_posix()}"
    shared_env['HMW_STORAGE_ROOT'] = str(API_ROOT / 'workbench-objects')
    shared_env.pop('HMW_CONFIG_PATH', None)
    api_env = {**shared_env, 'HMW_DEV_USERNAME':owner['username'], 'HMW_DEV_PASSWORD':owner['password']}
    if config_path is not None:
        api_env['HMW_CONFIG_PATH'] = str(config_path.resolve())
    web_env = {**shared_env, 'HMW_API_PORT':str(server['api_port'])}
    commands = [
        ('API', [sys.executable, '-m', 'uvicorn', 'workbench.dev:app', '--host', '127.0.0.1', '--port', str(server['api_port']), '--no-access-log'], API_ROOT, api_env),
        ('Worker', [sys.executable, '-m', 'workbench.jobs.worker'], API_ROOT, shared_env),
        ('Web', [node, str(vite), '--host', '127.0.0.1', '--port', str(server['web_port']), '--strictPort'], WEB_ROOT, web_env),
    ]
    children: list[tuple[str, subprocess.Popen]] = []
    try:
        for name, command, cwd, env in commands:
            kwargs = {'cwd':cwd, 'env':env}
            if os.name == 'nt':
                kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs['start_new_session'] = True
            process = subprocess.Popen(command, **kwargs)
            children.append((name, process))
            if name == 'API':
                wait_ready(name, f"http://127.0.0.1:{server['api_port']}/openapi.json", process)
                if process.poll() is not None:
                    raise RuntimeError('API 已退出，无法启动 Worker。')
            elif name == 'Web':
                wait_ready(name, f"http://127.0.0.1:{server['web_port']}/", process)
        print(f"工作台已启动：http://127.0.0.1:{server['web_port']} （Ctrl+C 停止）", flush=True)
        while True:
            for name, process in children:
                code = process.poll()
                if code is not None:
                    print(f'{name} 已退出（状态 {code}），正在停止其他服务。', file=sys.stderr, flush=True)
                    return 1
            time.sleep(0.25)
    except KeyboardInterrupt:
        print('\n正在停止服务。', flush=True)
        return 0
    finally:
        stop_children(children)


def main() -> int:
    try:
        path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / '.hmw.toml'
        return run(read_config(path), path)
    except (ValueError, OSError, RuntimeError) as exc:
        print(f'启动失败：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    def terminate(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    raise SystemExit(main())
