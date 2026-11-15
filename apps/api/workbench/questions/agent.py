"""Read-only Pi adapter; only bounded, authorized evidence crosses this boundary."""
import json
import os
import re
import shutil
import subprocess
import threading
import tomllib
from pathlib import Path
from urllib.parse import urlsplit


class AgentError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def validate_answer(value: object, count: int) -> dict:
    if not isinstance(value, dict) or set(value) != {'answer', 'citations'}:
        raise AgentError('invalid_response')
    answer, citations = value['answer'], value['citations']
    if not isinstance(answer, str) or not answer.strip() or len(answer) > 4000:
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
                if total > 32000:
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
    def __init__(self, settings: dict, script: Path):
        if not isinstance(settings, dict) or not all(isinstance(settings.get(key), str) and settings[key].strip() for key in ('api_key', 'api_url', 'model')):
            raise ValueError('Enabled LLM requires api_key, api_url and model')
        parsed = urlsplit(settings['api_url'])
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('LLM api_url must be an HTTPS base URL without credentials or query')
        effort = settings.get('reasoning_effort', '') or 'off'
        if effort not in ('off', 'low', 'high'):
            raise ValueError('reasoning_effort must be off, low or high')
        self._settings = {key:settings[key] for key in ('api_key', 'api_url', 'model')}
        self._settings['reasoning_effort'] = effort
        self._script = script
        self._slots = threading.BoundedSemaphore(2)

    def answer(self, question: str, evidence: list[dict], history: list[dict] | None = None) -> dict:
        if not self._slots.acquire(blocking=False):
            raise AgentError('busy')
        try:
            node = shutil.which('node')
            if not node:
                raise AgentError('runtime_unavailable')
            payload = {'question':question, 'evidence':[{'excerpt':item['excerpt']} for item in evidence], 'history':history or [], 'settings':self._settings}
            # Pi receives no owner credentials, database settings or config path.
            env = {key:os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP') if key in os.environ}
            data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            if len(data) > 200000:
                raise AgentError('input_limit')
            code, output = run_bounded_process([node, '--max-old-space-size=128', str(self._script)], data, env)
            if code != 0:
                raise AgentError('provider_error')
            try:
                return validate_answer(json.loads(output), len(evidence))
            except (ValueError, TypeError) as exc:
                raise AgentError('invalid_response') from exc
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
    return ReadonlyPiAgent(settings, script)
