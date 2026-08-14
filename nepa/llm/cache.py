"""Deterministic immutable response cache for logical LLM completions."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..run_store import ArtifactConflict, RunStore, RunStoreError, RunValidationError
from ..speclib.lint import canonical_json_bytes
from .client import EvidenceStorageError, LLMRequest, LLMResponse, ValidationState


def cache_key(
    *,
    provider_name: str,
    provider_kind: str,
    model: str,
    request: LLMRequest,
) -> str:
    material = {
        "provider": provider_name,
        "provider_kind": provider_kind,
        "model": model,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "json_schema": request.json_schema,
        "system": request.system,
        "user": request.user,
    }
    return hashlib.sha256(canonical_json_bytes(material)).hexdigest()


class LLMCache:
    def __init__(self, store: RunStore) -> None:
        self.store = store

    @staticmethod
    def _path(key: str) -> str:
        return f"cache/llm/{key}.json"

    def publish(
        self,
        key: str,
        *,
        provider_name: str,
        provider_kind: str,
        model: str,
        request: LLMRequest,
        response: LLMResponse,
    ) -> None:
        if response.validation == ValidationState.FAIL:
            raise EvidenceStorageError("failed LLM responses cannot enter the cache")
        entry = {
            "cache_key": key,
            "provider": provider_name,
            "provider_kind": provider_kind,
            "model": model,
            "parameters": {"temperature": request.temperature, "max_tokens": request.max_tokens},
            "response": response.model_dump(mode="json"),
        }
        try:
            self.store.publish_immutable_json(self._path(key), entry)
        except RunStoreError as exc:
            raise EvidenceStorageError(str(exc)) from exc

    def load(self, key: str) -> LLMResponse | None:
        try:
            data = self.store.read_verified_bytes(self._path(key))
        except RunValidationError as exc:
            if str(exc).startswith("missing artifact"):
                return None
            raise EvidenceStorageError(str(exc)) from exc
        try:
            entry = json.loads(data.decode("utf-8"))
            if entry.get("cache_key") != key:
                raise ValueError("cache key binding mismatch")
            return LLMResponse.model_validate(entry["response"])
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise EvidenceStorageError(f"invalid cache entry for {key}") from exc
