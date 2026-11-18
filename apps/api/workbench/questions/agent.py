"""Read-only Pi adapter; only bounded, authorized evidence crosses this boundary."""
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from workbench.logging_utils import log_event

tool_logger = logging.getLogger('workbench.agent_tools')


class AgentError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def validate_answer(value: object, count: int, max_answer_chars: int = 4000) -> dict:
    if not isinstance(value, dict) or set(value) != {'answer', 'citations'}:
        raise AgentError('invalid_response')
    answer, citations = value['answer'], value['citations']
    if not isinstance(answer, str) or not answer.strip() or len(answer) > max_answer_chars:
        raise AgentError('invalid_response')
    if not isinstance(citations, list) or len(citations) > count:
        raise AgentError('invalid_citations')
    if any(type(index) is not int or index < 1 or index > count for index in citations) or len(set(citations)) != len(citations):
        raise AgentError('invalid_citations')
    markers = [int(number) for number in re.findall(r'\[(\d+)\]', answer)]
    if set(markers) != set(citations):
        raise AgentError('invalid_citations')
    return {'answer':answer.strip(), 'citations':citations}


def run_bounded_process(command: list[str], payload: bytes, env: dict, *, timeout: float = 45) -> tuple[int, str]:
    """Drain both pipes concurrently with a fixed combined budget and deadline."""
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    except OSError as exc:
        raise AgentError('runtime_unavailable') from exc
    output = bytearray()
    exceeded = threading.Event()
    lock = threading.Lock()
    total = 0

    def drain(stream, keep: bool) -> None:
        nonlocal total
        while chunk := stream.read(4096):
            with lock:
                total += len(chunk)
                if total > 524288:
                    exceeded.set()
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    break
                if keep:
                    output.extend(chunk)

    def write_input() -> None:
        try:
            process.stdin.write(payload)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    threads = [threading.Thread(target=drain, args=(process.stdout, True)),
               threading.Thread(target=drain, args=(process.stderr, False)),
               threading.Thread(target=write_input)]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait()
    finally:
        for thread in threads:
            thread.join()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
    if exceeded.is_set():
        raise AgentError('output_limit')
    if timed_out:
        raise AgentError('timeout')
    try:
        return process.returncode, output.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise AgentError('invalid_response') from exc


class ReadonlyPiAgent:
    def __init__(self, settings: dict, script: Path, network: dict | None = None):
        if not isinstance(settings, dict) or not all(isinstance(settings.get(key), str) and settings[key].strip() for key in ('api_key', 'api_url', 'model')):
            raise ValueError('Enabled LLM requires api_key, api_url and model')
        parsed = urlsplit(settings['api_url'])
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('LLM api_url must be an HTTPS base URL without credentials or query')
        effort = settings.get('reasoning_effort', '') or 'off'
        if effort not in ('off', 'low', 'high', 'max'):
            raise ValueError('reasoning_effort must be off, low, high or max')
        self._settings = {key:settings[key] for key in ('api_key', 'api_url', 'model')}
        self._settings['reasoning_effort'] = effort
        limits = {
            'max_output_tokens': (1024, 1, 32768),
            'context_window_tokens': (131072, 2, 1048576),
            'max_context_bytes': (200000, 1024, 1000000),
            'max_answer_chars': (4000, 1, 65536),
            'timeout_seconds': (90, 1, 900),
        }
        for name, (default, minimum, maximum) in limits.items():
            value = settings.get(name, default)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f'llm.{name} must be an integer between {minimum} and {maximum}')
            self._settings[name] = value
        if self._settings['max_output_tokens'] >= self._settings['context_window_tokens']:
            raise ValueError('llm.max_output_tokens must be smaller than context_window_tokens')
        network = {} if network is None else network
        if not isinstance(network, dict) or network.get('proxy_mode', 'direct') not in ('direct', 'environment'):
            raise ValueError('network.proxy_mode must be direct or environment')
        for key in ('http_proxy', 'https_proxy', 'no_proxy', 'ca_file'):
            if key in network and not isinstance(network[key], str):
                raise ValueError(f'network.{key} must be a string')
        self._network = {key:network[key] for key in ('http_proxy', 'https_proxy', 'no_proxy', 'ca_file') if key in network}
        self._network['proxy_mode'] = network.get('proxy_mode', 'direct')
        self._script = script
        self._slots = threading.BoundedSemaphore(2)

    def _agent_environment(self) -> dict:
        env = {key:os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP') if key in os.environ}
        if self._network['proxy_mode'] == 'environment':
            for name in ('http_proxy', 'https_proxy', 'no_proxy'):
                if name in self._network:
                    env[name] = env[name.upper()] = self._network[name]
                else:
                    for variant in (name, name.upper()):
                        if variant in os.environ:
                            env[variant] = os.environ[variant]
            ca_file = self._network.get('ca_file', os.environ.get('NODE_EXTRA_CA_CERTS', ''))
            if ca_file:
                env['NODE_EXTRA_CA_CERTS'] = ca_file
            env['NODE_USE_ENV_PROXY'] = '1'
            env['HMW_PROXY_REQUIRED'] = '1'
        return env

    @property
    def max_answer_chars(self) -> int:
        return self._settings['max_answer_chars']

    def answer(self, question: str, evidence: list[dict], history: list[dict] | None = None) -> dict:
        if not self._slots.acquire(blocking=False):
            raise AgentError('busy')
        try:
            node = shutil.which('node')
            if not node:
                raise AgentError('runtime_unavailable')
            payload = {'question':question, 'evidence':[{'excerpt':item['excerpt']} for item in evidence], 'history':history or [], 'settings':self._settings}
            # Pi receives no owner credentials, database settings or config path.
            env = self._agent_environment()
            data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            if len(data) > 200000:
                raise AgentError('input_limit')
            code, output = run_bounded_process([node, '--max-old-space-size=128', str(self._script)], data, env,
                                               timeout=self._settings['timeout_seconds'])
            if code != 0:
                raise AgentError('provider_error')
            try:
                return validate_answer(json.loads(output), len(evidence), self.max_answer_chars)
            except (ValueError, TypeError) as exc:
                raise AgentError('invalid_response') from exc
        finally:
            self._slots.release()

    def explore(self, question: str, history: list[dict], library) -> dict:
        if not self._slots.acquire(blocking=False):
            raise AgentError('busy')
        try:
            node = shutil.which('node')
            if not node:
                raise AgentError('runtime_unavailable')
            env = self._agent_environment()
            payload = {'question':question, 'history':history, 'settings':self._settings}
            return run_tool_process([node, '--max-old-space-size=128', str(self._script.with_name('explore.mjs'))], payload, env, library, timeout=self._settings['timeout_seconds'])
        finally:
            self._slots.release()

    def analyze(self, question: str, library) -> dict:
        if not self._slots.acquire(blocking=False):
            raise AgentError('busy')
        try:
            node = shutil.which('node')
            if not node:
                raise AgentError('runtime_unavailable')
            payload = {'question':question, 'settings':self._settings}
            return run_tool_process([node, '--max-old-space-size=128', str(self._script.with_name('analysis.mjs'))],
                                    payload, self._agent_environment(), library,
                                    timeout=self._settings['timeout_seconds'],
                                    lock_fd=getattr(library, 'lock_fd', None))
        finally:
            self._slots.release()


def load_agent(config_path: Path | None) -> ReadonlyPiAgent | None:
    if config_path is None:
        return None
    with config_path.open('rb') as stream:
        config = tomllib.load(stream)
    settings = config.get('llm', {})
    if not isinstance(settings, dict) or type(settings.get('enabled', False)) is not bool:
        raise ValueError('llm.enabled must be a boolean')
    if not settings.get('enabled', False):
        return None
    script = Path(__file__).resolve().parents[3] / 'agent' / 'readonly.mjs'
    return ReadonlyPiAgent(settings, script, config.get('network'))


def run_tool_process(command: list[str], payload: dict, env: dict, library, *, timeout: float = 90, lock_fd: int | None = None) -> dict:
    """Exchange bounded JSON lines with the agent; kill it on timeout or protocol errors."""
    import queue
    import time

    try:
        options = {'pass_fds': (lock_fd,)} if lock_fd is not None and os.name != 'nt' else {}
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, **options)
    except OSError as exc:
        raise AgentError('runtime_unavailable') from exc
    messages = queue.Queue(maxsize=32)
    deadline = time.monotonic() + timeout
    exceeded = threading.Event()

    def read_lines():
        try:
            while True:
                line = process.stdout.readline(524289)
                if len(line) > 524288 or (line and not line.endswith(b'\n')):
                    exceeded.set()
                    process.kill()
                    return
                if not line:
                    messages.put(None, timeout=1)
                    return
                messages.put(line, timeout=1)
        except (OSError, queue.Full):
            exceeded.set()
            try:
                process.kill()
            except ProcessLookupError:
                pass

    def drain_errors():
        count = 0
        while chunk := process.stderr.read(4096):
            count += len(chunk)
            if count > 32000:
                exceeded.set()
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                return

    threads = [threading.Thread(target=read_lines), threading.Thread(target=drain_errors)]
    for thread in threads:
        thread.start()
    expired = threading.Event()

    def stop_at_deadline():
        expired.set()
        try:
            process.kill()
        except ProcessLookupError:
            pass

    timer = threading.Timer(timeout, stop_at_deadline)
    timer.start()
    try:
        initial = json.dumps(payload, ensure_ascii=False).encode('utf-8') + b'\n'
        if len(initial) > 200000:
            raise AgentError('input_limit')
        process.stdin.write(initial)
        process.stdin.flush()
        calls = 0
        while True:
            try:
                line = messages.get(timeout=max(0.001, deadline - time.monotonic()))
            except queue.Empty as exc:
                raise AgentError('timeout') from exc
            if expired.is_set() or time.monotonic() >= deadline:
                raise AgentError('timeout')
            if exceeded.is_set():
                raise AgentError('output_limit')
            if line is None:
                raise AgentError('provider_error')
            tool_started = time.monotonic()
            is_tool, name, args, failure_reason = False, None, {}, None
            try:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError()
                if message.get('type') == 'error':
                    reason = message.get('reason')
                    raise AgentError(reason if reason in {'context_limit', 'output_limit', 'invalid_response', 'provider_auth', 'provider_rate_limited', 'provider_unavailable', 'provider_network'} else 'provider_error')
                if message.get('type') == 'answer':
                    evidence = library.library.evidence if hasattr(library, 'list_resources') and library.library else library.evidence
                    return validate_answer(message.get('answer'), len(evidence), payload['settings'].get('max_answer_chars', 4000))
                if message.get('type') != 'tool' or not isinstance(message.get('args'), dict):
                    raise ValueError()
                is_tool = True
                calls += 1
                if calls > 30:
                    raise ValueError()
                name, args = message.get('name'), message['args']
                if name in ('list_resources', 'read_resource') and hasattr(library, name) and set(args) == {'kind', 'path', 'offset'}:
                    result = getattr(library, name)(args['kind'], args['path'], args['offset'])
                elif name == 'run_command' and hasattr(library, name) and set(args) == {'command', 'args'}:
                    result = library.run_command(args['command'], args['args'])
                elif name == 'list_knowledge' and set(args) == {'offset'}:
                    result = library.list_documents(args['offset'])
                elif name == 'read_knowledge' and set(args) == {'id', 'offset'}:
                    result = library.read_document(args['id'], args['offset'])
                else:
                    raise ValueError()
                response = {'result':result}
            except ValueError as exc:
                # Only stable, non-sensitive decoder errors may reach the model.
                allowed = {'RAR archive decoder unavailable (libarchive required)', 'RAR decoder timeout',
                           'invalid or unsupported RAR archive'}
                reasons = {'RAR archive decoder unavailable (libarchive required)':'rar_decoder_unavailable',
                           'RAR decoder timeout':'rar_decoder_timeout', 'invalid or unsupported RAR archive':'invalid_rar',
                           'archive expansion limit':'archive_limit', 'resource unavailable':'resource_unavailable',
                           'invalid path':'invalid_path', 'search entry limit':'search_limit'}
                failure_reason = reasons.get(str(exc), 'invalid_request')
                if hasattr(library, 'list_resources'):
                    response = {'result':{'error':str(exc) if str(exc) in allowed else 'resource unavailable'}}
                else:
                    response = {'error':'invalid or unavailable tool request'}
            except (TypeError, KeyError, UnicodeError):
                failure_reason = 'invalid_request'
                response = {'error':'invalid or unavailable tool request'}
            if is_tool:
                known_tools = ('list_resources', 'read_resource', 'run_command', 'list_knowledge', 'read_knowledge')
                safe_name = name if isinstance(name, str) and name in known_tools else 'unknown'
                resource = args.get('kind') if isinstance(args, dict) else None
                resource = resource if isinstance(resource, str) and resource in ('code', 'log', 'knowledge', 'skill') else None
                command_name = args.get('command') if isinstance(args, dict) else None
                command_name = command_name if isinstance(command_name, str) and command_name in ('pwd', 'ls', 'cat', 'rg') else None
                failed = 'error' in response or (isinstance(response.get('result'), dict) and 'error' in response['result'])
                log_event(tool_logger, logging.WARNING if failed else logging.INFO, 'agent_tools', 'tool_finished',
                          **getattr(library, 'log_context', {}), tool=safe_name, resource_type=resource,
                          command=command_name, result='failed' if failed else 'completed', reason=failure_reason,
                          duration_ms=round((time.monotonic() - tool_started) * 1000, 2))
            process.stdin.write(json.dumps(response, ensure_ascii=False).encode('utf-8') + b'\n')
            process.stdin.flush()
    except (BrokenPipeError, OSError) as exc:
        raise AgentError('timeout' if expired.is_set() else 'provider_error') from exc
    finally:
        timer.cancel()
        timer.join()
        if process.poll() is None:
            process.kill()
        process.wait()
        for thread in threads:
            thread.join(timeout=2)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
