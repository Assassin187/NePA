"""LLM 抽象层单元测试（设计文档 8.4、5.5）。

全部用 httpx.MockTransport，无真实网络/LLM 调用。
覆盖：成功路径、JSON 修复路径、重试路径、缓存命中、trace 落盘。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from nepa.llm.cache import ResponseCache
from nepa.llm.client import (
    LLMClient,
    LLMRequest,
    LLMResponse,
    ProviderHTTPError,
    ProviderResponseError,
    RetryExhaustedError,
    StructuredOutputError,
    extract_first_json,
)
from nepa.llm.providers.anthropic import AnthropicProvider
from nepa.llm.providers.openai_compat import OpenAICompatProvider
from nepa.llm.telemetry import ModelPricing, TraceWriter, compute_cost

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def openai_body(
    content: str, *, tin: int = 10, tout: int = 5, model: str = "test-model"
) -> dict[str, Any]:
    return {
        "id": "resp-test",
        "model": model,
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": tin, "completion_tokens": tout},
    }


def make_provider(
    handler: Callable[[httpx.Request], httpx.Response], **kwargs: Any
) -> OpenAICompatProvider:
    kwargs.setdefault("retry_base_delay_s", 0.0)
    return OpenAICompatProvider(
        base_url="https://api.example.com",
        api_key="sk-test",
        model="test-model",
        name="testprov",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def make_request(**kwargs: Any) -> LLMRequest:
    kwargs.setdefault("role", "coder")
    kwargs.setdefault("system", "You are a test assistant.")
    kwargs.setdefault("user", "Answer the question.")
    kwargs.setdefault("temperature", 0.1)
    kwargs.setdefault("max_tokens", 256)
    return LLMRequest(**kwargs)


# ---------------------------------------------------------------- 容错剥壳


class TestExtractFirstJson:
    def test_bare_object(self) -> None:
        assert extract_first_json('{"a": 1}') == {"a": 1}

    def test_markdown_fenced(self) -> None:
        text = 'Here you go:\n```json\n{"a": [1, 2]}\n```\nDone.'
        assert extract_first_json(text) == {"a": [1, 2]}

    def test_leading_prose_and_trailing_text(self) -> None:
        text = 'Sure { not json } wait, the result is {"answer": "x{y}"} thanks'
        assert extract_first_json(text) == {"answer": "x{y}"}

    def test_nested_object(self) -> None:
        assert extract_first_json('{"a": {"b": {"c": 3}}}') == {"a": {"b": {"c": 3}}}

    def test_no_json_raises(self) -> None:
        with pytest.raises(ValueError):
            extract_first_json("no json here")


# ---------------------------------------------------------------- 成功路径


class TestSuccessPath:
    def test_plain_text_no_schema(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=openai_body("hello world"))

        provider = make_provider(handler)
        resp = provider.complete(make_request(json_schema=None))
        assert resp.text == "hello world"
        assert resp.parsed is None
        assert resp.validation is None
        assert (resp.tokens_in, resp.tokens_out) == (10, 5)
        assert resp.parameter_support == {
            "temperature": "unknown",
            "max_tokens": "unknown",
        }
        assert resp.provider_metadata == {
            "finish_reason": "stop",
            "response_id": "resp-test",
        }
        payload = json.loads(requests[0].content)
        assert "response_format" not in payload  # 无 schema 不开 JSON 模式
        assert payload["messages"][0]["role"] == "system"

    def test_structured_pass(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=openai_body('{"answer": "42"}'))

        provider = make_provider(handler)
        resp = provider.complete(make_request(json_schema=SCHEMA))
        assert resp.parsed == {"answer": "42"}
        assert resp.validation == "pass"
        assert resp.model == "test-model"
        assert not resp.cached
        assert len(resp.provider_calls) == 1
        assert resp.provider_calls[0].provider_call_index == 1
        assert resp.provider_calls[0].call_kind == "initial"
        payload = json.loads(requests[0].content)
        # 原生 JSON 模式 + schema 内嵌提示词（8.4 要点 2）
        assert payload["response_format"] == {"type": "json_object"}
        assert "JSON Schema" in payload["messages"][1]["content"]
        assert '"answer"' in payload["messages"][1]["content"]

    def test_structured_pass_with_fenced_output(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=openai_body('```json\n{"answer": "ok"}\n```'))

        provider = make_provider(handler)
        resp = provider.complete(make_request(json_schema=SCHEMA))
        assert resp.parsed == {"answer": "ok"}
        assert resp.validation == "pass"

    def test_native_json_mode_disabled(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=openai_body('{"answer": "x"}'))

        provider = make_provider(handler, native_json_mode=False)
        resp = provider.complete(make_request(json_schema=SCHEMA))
        assert resp.validation == "pass"
        assert "response_format" not in json.loads(requests[0].content)

    def test_parameter_support_rejects_unrecognized_state(self) -> None:
        with pytest.raises(ValidationError):
            LLMResponse(
                text="x",
                parameter_support={"temperature": "assumed_applied"},  # type: ignore[dict-item]
            )


# ---------------------------------------------------------------- JSON 修复路径


class TestRepairPath:
    def test_repair_succeeds(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(200, json=openai_body('{"wrong_key": 1}'))
            return httpx.Response(200, json=openai_body('{"answer": "fixed"}'))

        provider = make_provider(handler)
        resp = provider.complete(make_request(json_schema=SCHEMA))
        assert resp.parsed == {"answer": "fixed"}
        assert resp.validation == "repaired"
        # tokens 累加初次 + 修复调用
        assert (resp.tokens_in, resp.tokens_out) == (20, 10)
        assert resp.parameter_support == {
            "temperature": "unknown",
            "max_tokens": "unknown",
        }
        assert len(resp.provider_metadata["calls"]) == 2
        assert resp.provider_metadata["finish_reason"] == "stop"
        assert [call.provider_call_index for call in resp.provider_calls] == [1, 2]
        assert [call.call_kind for call in resp.provider_calls] == [
            "initial",
            "format_repair",
        ]
        assert [call.validation for call in resp.provider_calls] == ["fail", "repaired"]
        # 修复提示词包含上次输出与错误清单
        repair_user = json.loads(requests[1].content)["messages"][1]["content"]
        assert "wrong_key" in repair_user
        assert "Validation errors" in repair_user

    def test_repair_after_non_json_output(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(200, json=openai_body("sorry, I cannot"))
            return httpx.Response(200, json=openai_body('{"answer": "ok"}'))

        provider = make_provider(handler)
        resp = provider.complete(make_request(json_schema=SCHEMA))
        assert resp.validation == "repaired"
        assert resp.parsed == {"answer": "ok"}

    def test_repair_fails_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=openai_body('{"still": "wrong"}'))

        provider = make_provider(handler)
        with pytest.raises(StructuredOutputError) as exc_info:
            provider.complete(make_request(json_schema=SCHEMA))
        err = exc_info.value
        assert err.response is not None
        assert err.response.validation == "fail"
        assert err.response.parsed is None
        assert err.errors


# ---------------------------------------------------------------- 重试路径（8.4 要点 3）


class TestRetryPath:
    def test_retry_on_5xx_then_success(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] <= 2:
                return httpx.Response(500, text="server error")
            return httpx.Response(200, json=openai_body("recovered"))

        provider = make_provider(handler)
        resp = provider.complete(make_request(json_schema=None))
        assert resp.text == "recovered"
        assert calls["n"] == 3

    def test_retry_on_429_then_success(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, text="rate limited")
            return httpx.Response(200, json=openai_body("ok"))

        provider = make_provider(handler)
        assert provider.complete(make_request(json_schema=None)).text == "ok"
        assert calls["n"] == 2

    def test_retry_on_transport_error_then_success(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("connection refused", request=request)
            return httpx.Response(200, json=openai_body("ok"))

        provider = make_provider(handler)
        assert provider.complete(make_request(json_schema=None)).text == "ok"
        assert calls["n"] == 2

    def test_retry_exhausted(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(500, text="down")

        provider = make_provider(handler)
        with pytest.raises(RetryExhaustedError):
            provider.complete(make_request(json_schema=None))
        assert calls["n"] == 4  # 首次 + 3 次重试

    def test_backoff_delays_are_exponential(self) -> None:
        delays: list[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="down")

        provider = make_provider(handler, retry_base_delay_s=0.5, sleep=delays.append)
        with pytest.raises(RetryExhaustedError):
            provider.complete(make_request(json_schema=None))
        assert delays == [0.5, 1.0, 2.0]

    def test_4xx_not_retried(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(401, text="bad key")

        provider = make_provider(handler)
        with pytest.raises(ProviderHTTPError) as exc_info:
            provider.complete(make_request(json_schema=None))
        assert exc_info.value.status_code == 401
        assert calls["n"] == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>upstream error</html>"),
        httpx.Response(200, json={"error": {"message": "model unavailable"}}),
        httpx.Response(200, json={"choices": []}),
    ],
)
def test_openai_malformed_http_200_is_llm_error(response: httpx.Response) -> None:
    provider = make_provider(lambda request: response)

    with pytest.raises(ProviderResponseError):
        provider.complete(make_request(json_schema=None))


# ---------------------------------------------------------------- Anthropic provider


class TestAnthropicProvider:
    def test_structured_via_prompt_fallback(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "id": "msg-test",
                    "model": "claude-test",
                    "content": [{"type": "text", "text": '{"answer": "hi"}'}],
                    "usage": {"input_tokens": 7, "output_tokens": 3},
                    "stop_reason": "end_turn",
                },
            )

        provider = AnthropicProvider(
            api_key="sk-ant",
            model="claude-test",
            transport=httpx.MockTransport(handler),
            retry_base_delay_s=0.0,
        )
        resp = provider.complete(make_request(json_schema=SCHEMA))
        assert resp.parsed == {"answer": "hi"}
        assert resp.validation == "pass"
        assert (resp.tokens_in, resp.tokens_out) == (7, 3)
        assert resp.parameter_support == {
            "temperature": "unknown",
            "max_tokens": "unknown",
        }
        assert resp.provider_metadata == {
            "finish_reason": "end_turn",
            "response_id": "msg-test",
        }
        req = requests[0]
        assert req.url.path == "/v1/messages"
        assert req.headers["x-api-key"] == "sk-ant"
        assert req.headers["anthropic-version"] == "2023-06-01"
        payload = json.loads(req.content)
        assert payload["system"] == "You are a test assistant."
        assert "response_format" not in payload  # 无原生 JSON 模式
        assert "JSON Schema" in payload["messages"][0]["content"]  # schema 内嵌提示词

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(200, text="not-json"),
            httpx.Response(200, json={"error": {"message": "overloaded"}}),
            httpx.Response(200, json={"content": [None]}),
        ],
    )
    def test_malformed_http_200_is_llm_error(self, response: httpx.Response) -> None:
        provider = AnthropicProvider(
            api_key="sk-ant",
            model="claude-test",
            transport=httpx.MockTransport(lambda request: response),
            retry_base_delay_s=0.0,
        )

        with pytest.raises(ProviderResponseError):
            provider.complete(make_request(json_schema=None))


# ---------------------------------------------------------------- 缓存命中（8.4 要点 4）


class TestCache:
    def test_cache_hit_zero_cost_single_http_call(self, tmp_path: Path) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json=openai_body('{"answer": "cached"}'))

        provider = make_provider(handler)
        client = LLMClient(
            provider,
            provider_name="testprov",
            cache=ResponseCache(tmp_path / "cache"),
        )
        req = make_request(json_schema=SCHEMA)

        first = client.complete(req)
        second = client.complete(req)
        assert calls["n"] == 1  # 第二次命中缓存，不发 HTTP
        assert not first.cached
        assert second.cached
        assert second.cost_usd == 0.0
        assert second.parsed == {"answer": "cached"}
        assert second.validation == "pass"
        assert second.provider_calls == []
        cache_files = list((tmp_path / "cache").glob("*.json"))
        assert len(cache_files) == 1
        assert "provider_calls" not in json.loads(cache_files[0].read_text(encoding="utf-8"))

    def test_different_request_misses(self, tmp_path: Path) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json=openai_body('{"answer": "x"}'))

        provider = make_provider(handler)
        client = LLMClient(
            provider, provider_name="testprov", cache=ResponseCache(tmp_path / "cache")
        )
        client.complete(make_request(json_schema=SCHEMA, user="q1"))
        client.complete(make_request(json_schema=SCHEMA, user="q2"))
        assert calls["n"] == 2

    def test_cache_can_be_explicitly_bypassed(self, tmp_path: Path) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json=openai_body("OK"))

        provider = make_provider(handler)
        client = LLMClient(
            provider,
            provider_name="testprov",
            cache=ResponseCache(tmp_path / "cache"),
        )
        req = make_request(json_schema=None)

        first = client.complete(req, use_cache=False)
        second = client.complete(req, use_cache=False)

        assert calls["n"] == 2
        assert not first.cached and not second.cached
        assert list((tmp_path / "cache").glob("*.json")) == []

    def test_key_includes_provider_model_params_prompts(self) -> None:
        req = make_request()
        base = ResponseCache.make_key("p1", "m1", req)
        assert ResponseCache.make_key("p2", "m1", req) != base
        assert ResponseCache.make_key("p1", "m2", req) != base
        assert ResponseCache.make_key("p1", "m1", make_request(temperature=0.9)) != base
        assert ResponseCache.make_key("p1", "m1", make_request(user="other")) != base
        assert ResponseCache.make_key("p1", "m1", req) == base


# ---------------------------------------------------------------- trace 落盘（5.5）


class TestTelemetry:
    def test_compute_cost(self) -> None:
        pricing = ModelPricing(input_usd_per_mtok=1.0, output_usd_per_mtok=2.0)
        assert compute_cost(pricing, 1_000_000, 500_000) == pytest.approx(2.0)

    def test_trace_line_and_files(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=openai_body('{"answer": "42"}', tin=100, tout=50))

        provider = make_provider(handler)
        trace_dir = tmp_path / "trace"
        writer = TraceWriter(
            trace_dir,
            run_id="run-001",
            pricing={"test-model": ModelPricing(1.0, 2.0)},
        )
        client = LLMClient(provider, provider_name="testprov", trace=writer)
        resp = client.complete(
            make_request(json_schema=SCHEMA, tier="T2"),
            stage="S6",
            task_id="T-012",
            attempt=1,
        )

        # 成本按价格表折算（8.4 要点 5）
        expected_cost = 100 / 1e6 * 1.0 + 50 / 1e6 * 2.0
        assert resp.cost_usd == pytest.approx(expected_cost)

        lines = (trace_dir / "llm_calls.ndjson").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        # 5.5 行格式关键字段
        assert rec["run_id"] == "run-001"
        assert rec["stage"] == "S6"
        assert rec["agent_role"] == "coder"
        assert rec["tier"] == "T2"
        assert rec["task_id"] == "T-012"
        assert rec["attempt"] == 1
        assert rec["provider_call_index"] == 1
        assert rec["call_kind"] == "initial"
        assert rec["model"] == "testprov/test-model"
        assert rec["params_requested"] == {"temperature": 0.1, "max_tokens": 256}
        assert rec["parameter_support"] == {
            "temperature": "unknown",
            "max_tokens": "unknown",
        }
        assert rec["finish_reason"] == "stop"
        assert rec["provider_metadata"]["response_id"] == "resp-test"
        assert rec["tokens_in"] == 100 and rec["tokens_out"] == 50
        assert rec["cost_usd"] == pytest.approx(expected_cost)
        assert rec["validation"] == "pass"
        assert len(rec["prompt_sha256"]) == 64

        # 提示词与输出全文落盘（5.5）
        prompt_file = trace_dir / "prompts" / Path(rec["prompt_path"]).name
        output_file = trace_dir / "outputs" / Path(rec["output_path"]).name
        assert "Answer the question." in prompt_file.read_text(encoding="utf-8")
        assert json.loads(output_file.read_text(encoding="utf-8")) == {"answer": "42"}

    def test_stage_specific_trace_extra_is_recorded(self, tmp_path: Path) -> None:
        """5.5：S4 调用额外记录 compiler_phase/work_package_id/父哈希/修复预算。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=openai_body('{"answer": "42"}'))

        trace_dir = tmp_path / "trace"
        writer = TraceWriter(trace_dir, run_id="r", pricing={})
        client = LLMClient(make_provider(handler), provider_name="testprov", trace=writer)
        client.complete(
            make_request(json_schema=SCHEMA),
            stage="S4",
            trace_extra={
                "compiler_phase": "EXPAND_WORK_PACKAGES",
                "work_package_id": "wp-codec",
                "parent_artifact_sha256": "ab" * 32,
                "repair_budget_used": {"architecture": 1},
            },
        )

        rec = json.loads((trace_dir / "llm_calls.ndjson").read_text(encoding="utf-8"))
        assert rec["compiler_phase"] == "EXPAND_WORK_PACKAGES"
        assert rec["work_package_id"] == "wp-codec"
        assert rec["parent_artifact_sha256"] == "ab" * 32
        assert rec["repair_budget_used"] == {"architecture": 1}

    def test_trace_extra_cannot_shadow_a_common_field(self, tmp_path: Path) -> None:
        writer = TraceWriter(tmp_path / "trace", run_id="r", pricing={})

        with pytest.raises(ValueError, match="stage"):
            writer.record(
                req=make_request(json_schema=None),
                resp=LLMResponse(text="ok"),
                provider_name="testprov",
                stage="S4",
                extra={"stage": "S6"},
            )

    def test_cached_call_traced_with_zero_cost(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=openai_body('{"answer": "x"}', tin=100, tout=50))

        provider = make_provider(handler)
        trace_dir = tmp_path / "trace"
        writer = TraceWriter(trace_dir, run_id="r", pricing={"test-model": ModelPricing(1.0, 2.0)})
        client = LLMClient(
            provider,
            provider_name="testprov",
            cache=ResponseCache(tmp_path / "cache"),
            trace=writer,
        )
        req = make_request(json_schema=SCHEMA)
        client.complete(req, stage="S2")
        resp2 = client.complete(req, stage="S2")

        assert resp2.cached and resp2.cost_usd == 0.0
        lines = (trace_dir / "llm_calls.ndjson").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        rec2 = json.loads(lines[1])
        assert rec2["cached"] is True
        assert rec2["cost_usd"] == 0.0
        assert rec2["provider_call_index"] is None
        assert rec2["call_kind"] == "cache_replay"

    def test_repair_calls_are_traced_separately(self, tmp_path: Path) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            content = '{"wrong_key": 1}' if calls["n"] == 1 else '{"answer": "fixed"}'
            return httpx.Response(200, json=openai_body(content, tin=100, tout=50))

        provider = make_provider(handler)
        trace_dir = tmp_path / "trace"
        writer = TraceWriter(
            trace_dir,
            run_id="r",
            pricing={"test-model": ModelPricing(1.0, 2.0)},
        )
        client = LLMClient(provider, provider_name="testprov", trace=writer)

        resp = client.complete(make_request(json_schema=SCHEMA), stage="S4")

        assert resp.validation == "repaired"
        assert (resp.tokens_in, resp.tokens_out) == (200, 100)
        expected_call_cost = 100 / 1e6 * 1.0 + 50 / 1e6 * 2.0
        assert resp.cost_usd == pytest.approx(expected_call_cost * 2)

        lines = [
            json.loads(line)
            for line in (trace_dir / "llm_calls.ndjson").read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        assert len(lines) == 2
        assert [line["provider_call_index"] for line in lines] == [1, 2]
        assert [line["call_kind"] for line in lines] == ["initial", "format_repair"]
        assert [line["validation"] for line in lines] == ["fail", "repaired"]
        assert [(line["tokens_in"], line["tokens_out"]) for line in lines] == [
            (100, 50),
            (100, 50),
        ]
        assert all(line["cost_usd"] == pytest.approx(expected_call_cost) for line in lines)

        first_prompt = trace_dir / "prompts" / Path(lines[0]["prompt_path"]).name
        repair_prompt = trace_dir / "prompts" / Path(lines[1]["prompt_path"]).name
        first_output = trace_dir / "outputs" / Path(lines[0]["output_path"]).name
        repaired_output = trace_dir / "outputs" / Path(lines[1]["output_path"]).name
        assert "JSON Schema" in first_prompt.read_text(encoding="utf-8")
        assert "Validation errors" in repair_prompt.read_text(encoding="utf-8")
        assert '{"wrong_key": 1}' in first_output.read_text(encoding="utf-8")
        assert json.loads(repaired_output.read_text(encoding="utf-8")) == {
            "answer": "fixed"
        }

    def test_failed_validation_traced_then_raises(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=openai_body('{"bad": 1}'))

        provider = make_provider(handler)
        trace_dir = tmp_path / "trace"
        writer = TraceWriter(trace_dir, run_id="r")
        client = LLMClient(provider, provider_name="testprov", trace=writer)
        with pytest.raises(StructuredOutputError):
            client.complete(make_request(json_schema=SCHEMA), stage="S2")
        records = [
            json.loads(line)
            for line in (trace_dir / "llm_calls.ndjson").read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        assert len(records) == 2
        assert [rec["provider_call_index"] for rec in records] == [1, 2]
        assert [rec["call_kind"] for rec in records] == ["initial", "format_repair"]
        assert [rec["validation"] for rec in records] == ["fail", "fail"]

    def test_unknown_model_pricing_costs_zero(self, tmp_path: Path) -> None:
        writer = TraceWriter(tmp_path / "trace", run_id="r", pricing={})
        assert writer.cost_for("p", "unknown-model", 1000, 1000) == 0.0
