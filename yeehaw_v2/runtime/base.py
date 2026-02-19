from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import RuntimeKind, SessionHandle, SessionSpec


class RuntimeErrorBase(RuntimeError):
    pass


class RuntimeAdapter(ABC):
    @property
    @abstractmethod
    def kind(self) -> RuntimeKind:
        raise NotImplementedError

    @abstractmethod
    def start_session(self, spec: SessionSpec) -> SessionHandle:
        raise NotImplementedError

    @abstractmethod
    def send_user_input(self, handle: SessionHandle, text: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def capture_output(self, handle: SessionHandle, lines: int = 400) -> str:
        raise NotImplementedError

    @abstractmethod
    def is_session_alive(self, handle: SessionHandle) -> bool:
        raise NotImplementedError

    @abstractmethod
    def terminate_session(self, handle: SessionHandle) -> None:
        raise NotImplementedError


class RuntimeRegistry:
    def __init__(self) -> None:
        self._adapters: dict[RuntimeKind, RuntimeAdapter] = {}

    def register(self, adapter: RuntimeAdapter) -> None:
        self._adapters[adapter.kind] = adapter

    def get(self, kind: RuntimeKind) -> RuntimeAdapter:
        adapter = self._adapters.get(kind)
        if adapter is None:
            raise RuntimeErrorBase(f"runtime adapter not registered for kind={kind}")
        return adapter
