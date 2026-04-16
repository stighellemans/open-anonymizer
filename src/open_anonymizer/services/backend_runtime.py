from __future__ import annotations

import atexit
from collections import deque
from dataclasses import dataclass, field
from multiprocessing import get_context
from multiprocessing.connection import Connection
import threading
from typing import Any, Callable

from open_anonymizer.models import ProcessBatchRequest


_BACKEND_POLL_INTERVAL_SECONDS = 0.05


def _backend_service_main(connection: Connection) -> None:
    from open_anonymizer.services.deduce_backend import warm_backend_for_settings
    from open_anonymizer.services.deidentifier import deidentify_document

    while True:
        try:
            message = connection.recv()
        except EOFError:
            break

        kind = message.get("kind")
        if kind == "shutdown":
            break

        try:
            if kind == "warmup":
                request = message["request"]
                warm_backend_for_settings(request["settings"])
                response = {
                    "status": "completed",
                    "kind": kind,
                    "flags_key": request["flags_key"],
                }
            elif kind == "batch":
                request: ProcessBatchRequest = message["request"]
                processed_documents = {}
                errors = {}
                for document in request.documents:
                    try:
                        processed_documents[document.id] = deidentify_document(
                            document,
                            request.anonymization_settings,
                        )
                    except Exception as exc:
                        errors[document.id] = str(exc)

                response = {
                    "status": "completed",
                    "kind": kind,
                    "flags_key": request.anonymization_settings.recognition_flags.as_key(),
                    "processed_documents": processed_documents,
                    "errors": errors,
                    "document_ids": [document.id for document in request.documents],
                    "context_generation": request.context_generation,
                }
            else:
                response = {
                    "status": "failed",
                    "kind": kind,
                    "message": f"Unsupported backend job: {kind}",
                }
        except Exception as exc:
            response = {
                "status": "failed",
                "kind": kind,
                "message": str(exc) or exc.__class__.__name__,
            }

        try:
            connection.send(response)
        except (BrokenPipeError, EOFError):
            break

    connection.close()


@dataclass
class _BackendJob:
    kind: str
    request: Any
    completed: Callable[[dict[str, Any]], None]
    failed: Callable[[str], None]
    cancelled: threading.Event = field(default_factory=threading.Event)


class BackendJobHandle:
    def __init__(self, manager: "_BackendRuntimeManager", job: _BackendJob) -> None:
        self._manager = manager
        self._job = job

    def cancel(self) -> None:
        self._manager.cancel(self._job)


class _BackendRuntimeManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._pending_jobs: deque[_BackendJob] = deque()
        self._current_job: _BackendJob | None = None
        self._process = None
        self._connection: Connection | None = None
        self._ready_flags: set[tuple[bool, ...]] = set()
        self._stopping = False
        self._worker_thread = threading.Thread(
            target=self._run,
            name="open-anonymizer-backend-runtime",
            daemon=True,
        )
        self._worker_thread.start()

    def submit(
        self,
        *,
        kind: str,
        request: Any,
        completed: Callable[[dict[str, Any]], None],
        failed: Callable[[str], None],
    ) -> BackendJobHandle:
        job = _BackendJob(
            kind=kind,
            request=request,
            completed=completed,
            failed=failed,
        )
        with self._condition:
            if self._stopping:
                raise RuntimeError("Backend runtime is shutting down.")
            self._pending_jobs.append(job)
            self._condition.notify_all()
        return BackendJobHandle(self, job)

    def cancel(self, job: _BackendJob) -> None:
        job.cancelled.set()
        with self._condition:
            self._condition.notify_all()

    def is_ready(self, flags_key: tuple[bool, ...]) -> bool:
        with self._lock:
            self._clear_dead_process_locked()
            return flags_key in self._ready_flags

    def shutdown(self) -> None:
        with self._condition:
            self._stopping = True
            for job in self._pending_jobs:
                job.cancelled.set()
            self._pending_jobs.clear()
            if self._current_job is not None:
                self._current_job.cancelled.set()
            self._condition.notify_all()

        if self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2)

        with self._lock:
            self._shutdown_process_locked()

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._pending_jobs and not self._stopping:
                    self._condition.wait()

                if self._stopping and not self._pending_jobs:
                    break

                job = self._pending_jobs.popleft()
                while job.cancelled.is_set():
                    if self._stopping and not self._pending_jobs:
                        return
                    if not self._pending_jobs:
                        job = None
                        break
                    job = self._pending_jobs.popleft()

                if job is None:
                    continue

                self._current_job = job

            try:
                self._ensure_process()
                response = self._run_job(job)
                if response is None or job.cancelled.is_set():
                    continue

                if response.get("status") == "completed":
                    flags_key = response.get("flags_key")
                    if isinstance(flags_key, tuple):
                        with self._lock:
                            self._ready_flags.add(flags_key)
                    job.completed(response)
                else:
                    job.failed(response.get("message", "Backend job failed."))
            except Exception as exc:
                if not job.cancelled.is_set():
                    job.failed(str(exc) or exc.__class__.__name__)
            finally:
                with self._condition:
                    if self._current_job is job:
                        self._current_job = None

        with self._lock:
            self._shutdown_process_locked()

    def _run_job(self, job: _BackendJob) -> dict[str, Any] | None:
        assert self._connection is not None

        self._connection.send(
            {
                "kind": job.kind,
                "request": job.request,
            }
        )

        while True:
            if job.cancelled.is_set():
                with self._lock:
                    self._restart_process_locked()
                return None

            if self._connection.poll(_BACKEND_POLL_INTERVAL_SECONDS):
                return self._connection.recv()

            with self._lock:
                if self._process is None or not self._process.is_alive():
                    self._clear_dead_process_locked()
                    raise RuntimeError("Backend runtime stopped unexpectedly.")

    def _ensure_process(self) -> None:
        with self._lock:
            self._clear_dead_process_locked()
            if self._process is not None and self._connection is not None:
                return

            context = get_context("spawn")
            parent_connection, child_connection = context.Pipe()
            process = context.Process(
                target=_backend_service_main,
                args=(child_connection,),
                name="open-anonymizer-backend-runtime",
                daemon=True,
            )
            process.start()
            child_connection.close()
            self._process = process
            self._connection = parent_connection

    def _clear_dead_process_locked(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        self._shutdown_process_locked()

    def _restart_process_locked(self) -> None:
        self._shutdown_process_locked()

    def _shutdown_process_locked(self) -> None:
        connection = self._connection
        process = self._process
        self._connection = None
        self._process = None
        self._ready_flags.clear()

        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

        if process is not None and process.is_alive():
            process.terminate()
            process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)


_backend_runtime_manager: _BackendRuntimeManager | None = None
_backend_runtime_manager_lock = threading.Lock()


def _manager() -> _BackendRuntimeManager:
    global _backend_runtime_manager

    manager = _backend_runtime_manager
    if manager is not None:
        return manager

    with _backend_runtime_manager_lock:
        manager = _backend_runtime_manager
        if manager is None:
            manager = _BackendRuntimeManager()
            _backend_runtime_manager = manager
        return manager


def submit_batch_job(
    request: ProcessBatchRequest,
    *,
    completed: Callable[[dict[str, Any]], None],
    failed: Callable[[str], None],
) -> BackendJobHandle:
    return _manager().submit(
        kind="batch",
        request=request,
        completed=completed,
        failed=failed,
    )


def submit_warmup_job(
    *,
    settings: Any,
    flags_key: tuple[bool, ...],
    completed: Callable[[dict[str, Any]], None],
    failed: Callable[[str], None],
) -> BackendJobHandle:
    return _manager().submit(
        kind="warmup",
        request={
            "settings": settings,
            "flags_key": flags_key,
        },
        completed=completed,
        failed=failed,
    )


def backend_is_ready(flags_key: tuple[bool, ...]) -> bool:
    manager = _backend_runtime_manager
    if manager is None:
        return False
    return manager.is_ready(flags_key)


def shutdown_backend_runtime() -> None:
    global _backend_runtime_manager

    manager = _backend_runtime_manager
    if manager is None:
        return

    _backend_runtime_manager = None
    manager.shutdown()


atexit.register(shutdown_backend_runtime)
