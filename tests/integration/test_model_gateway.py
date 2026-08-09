from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx

import waysplit.model_gateway as gateway_module
from waysplit.errors import ModelResponseError
from waysplit.ingest import DocumentContent, DocumentPage
from waysplit.model_gateway import (
    ModelGateway,
    ModelProvider,
    discover_models,
    probe_model,
)


class ChunkedResponse(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self.chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk


def _document(statement_text: str, *, with_image: bool = False) -> DocumentContent:
    return DocumentContent(
        media_type="application/pdf",
        pages=(
            DocumentPage(
                number=1,
                text=statement_text,
                image_data_uri=(
                    "data:image/png;base64,c3ludGhldGljLWltYWdl" if with_image else None
                ),
            ),
        ),
    )


@pytest.mark.asyncio
@respx.mock
async def test_discovers_ollama_and_openai_compatible_models_without_network() -> None:
    ollama_tags = respx.get("http://127.0.0.1:11434/api/tags").mock(
        return_value=httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "zeta-text:latest",
                        "size": 1200,
                        "digest": "digest-zeta",
                        "details": {
                            "family": "example",
                            "parameter_size": "3B",
                            "quantization_level": "Q4",
                        },
                    },
                    {
                        "name": "alpha-vision:latest",
                        "size": 2400,
                        "digest": "digest-alpha",
                        "details": {"family": "example-vl"},
                    },
                ]
            },
        )
    )
    openai_tags = respx.get("http://127.0.0.1:9090/api/tags").mock(return_value=httpx.Response(404))
    openai_models = respx.get("http://127.0.0.1:9090/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"id": "zeta-local"}, {"id": "alpha-vl-local"}]},
        )
    )

    results = await discover_models(
        ("http://127.0.0.1:11434/", "http://127.0.0.1:9090/v1"),
        allow_remote=False,
        api_key="synthetic-discovery-key",
    )

    assert [result.provider for result in results] == [
        ModelProvider.OLLAMA,
        ModelProvider.OPENAI_COMPATIBLE,
    ]
    assert [model.name for model in results[0].models] == [
        "alpha-vision:latest",
        "zeta-text:latest",
    ]
    assert results[0].models[0].vision_hint is True
    assert results[0].models[1].parameter_size == "3B"
    assert [model.name for model in results[1].models] == [
        "alpha-vl-local",
        "zeta-local",
    ]
    assert all(route.called for route in (ollama_tags, openai_tags, openai_models))
    assert "authorization" not in ollama_tags.calls.last.request.headers
    assert "authorization" not in openai_tags.calls.last.request.headers
    assert (
        openai_models.calls.last.request.headers["authorization"]
        == "Bearer synthetic-discovery-key"
    )


@pytest.mark.asyncio
@respx.mock
async def test_discovery_rejects_remote_endpoint_before_any_http_request() -> None:
    results = await discover_models(
        ("https://models.example.invalid",),
        allow_remote=False,
        api_key="must-not-leave-the-process",
    )

    assert results[0].provider is None
    assert results[0].models == ()
    assert "Remote model endpoints are disabled" in (results[0].error or "")
    assert len(respx.calls) == 0


@pytest.mark.asyncio
@respx.mock
async def test_ollama_extraction_sends_schema_and_image_then_validates_bill(
    normalized_bill_payload: dict[str, object], statement_text: str
) -> None:
    route = respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={"message": {"content": json.dumps(normalized_bill_payload)}},
        )
    )
    gateway = ModelGateway(
        endpoint="http://127.0.0.1:11434",
        provider=ModelProvider.OLLAMA,
        model="example-vision:latest",
        allow_remote=False,
        timeout_seconds=30,
    )

    bill = await gateway.extract_bill(_document(statement_text, with_image=True))

    request_payload = json.loads(route.calls.last.request.content)
    assert bill.issuer.name == "Example Mobile"
    assert request_payload["model"] == "example-vision:latest"
    assert request_payload["stream"] is False
    assert request_payload["think"] is False
    assert request_payload["options"] == {"temperature": 0}
    assert request_payload["format"]["title"] == "NormalizedBill"
    assert request_payload["messages"][1]["images"] == ["c3ludGhldGljLWltYWdl"]
    assert "UNTRUSTED STATEMENT CONTENT BEGINS" in request_payload["messages"][1]["content"]


@pytest.mark.asyncio
@respx.mock
async def test_openai_compatible_extraction_falls_back_to_json_mode_once(
    normalized_bill_payload: dict[str, object], statement_text: str
) -> None:
    response_modes: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body: dict[str, Any] = json.loads(request.content)
        response_modes.append(body["response_format"]["type"])
        if len(response_modes) == 1:
            return httpx.Response(400, json={"error": "json_schema unsupported"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(normalized_bill_payload)}}]},
        )

    route = respx.post("http://127.0.0.1:9090/v1/chat/completions").mock(side_effect=respond)
    gateway = ModelGateway(
        endpoint="http://127.0.0.1:9090/v1",
        provider=ModelProvider.OPENAI_COMPATIBLE,
        model="example-local",
        allow_remote=False,
        timeout_seconds=30,
        api_key="synthetic-api-key",
    )

    bill = await gateway.extract_bill(_document(statement_text))

    assert bill.statement.statement_identifier == "example-statement-2026-08"
    assert response_modes == ["json_schema", "json_object"]
    assert route.call_count == 2
    assert all(
        call.request.headers["authorization"] == "Bearer synthetic-api-key" for call in route.calls
    )


@pytest.mark.asyncio
@respx.mock
async def test_schema_invalid_model_output_is_rejected_without_echoing_output(
    statement_text: str,
) -> None:
    respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={"message": {"content": '{"private_statement_text":"do not echo"}'}},
        )
    )
    gateway = ModelGateway(
        endpoint="http://127.0.0.1:11434",
        provider=ModelProvider.OLLAMA,
        model="example-text",
        allow_remote=False,
        timeout_seconds=30,
    )

    with pytest.raises(ModelResponseError) as captured:
        await gateway.extract_bill(_document(statement_text))

    assert "private_statement_text" not in str(captured.value)
    assert "did not match the bill schema" in str(captured.value)


@pytest.mark.asyncio
@respx.mock
async def test_model_probe_combines_ollama_metadata_with_structured_reconciliation(
    normalized_bill_payload: dict[str, object],
) -> None:
    respx.post("http://127.0.0.1:11434/api/show").mock(
        return_value=httpx.Response(
            200,
            json={
                "capabilities": ["completion", "vision"],
                "digest": "synthetic-digest",
                "license": "Example permissive license\nAdditional text",
            },
        )
    )
    respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={"message": {"content": json.dumps(normalized_bill_payload)}},
        )
    )

    result = await probe_model(
        endpoint="http://127.0.0.1:11434",
        provider=ModelProvider.OLLAMA,
        model="example-vision",
        allow_remote=False,
        timeout_seconds=30,
    )

    assert result.ready is True
    assert result.structured_output is True
    assert result.vision is True
    assert result.capabilities == ("completion", "vision")
    assert result.digest == "synthetic-digest"
    assert result.license_excerpt == "Example permissive license"


@pytest.mark.asyncio
@respx.mock
async def test_model_probe_observes_ollama_digest_server_side_from_tags(
    normalized_bill_payload: dict[str, object],
) -> None:
    respx.post("http://127.0.0.1:11434/api/show").mock(
        return_value=httpx.Response(200, json={"capabilities": ["completion"]})
    )
    tags = respx.get("http://127.0.0.1:11434/api/tags").mock(
        return_value=httpx.Response(
            200,
            json={"models": [{"name": "example-local", "digest": "server-observed-digest"}]},
        )
    )
    respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={"message": {"content": json.dumps(normalized_bill_payload)}},
        )
    )

    result = await probe_model(
        endpoint="http://127.0.0.1:11434",
        provider=ModelProvider.OLLAMA,
        model="example-local",
        allow_remote=False,
        timeout_seconds=30,
    )

    assert result.ready is True
    assert result.digest == "server-observed-digest"
    assert tags.called


@pytest.mark.asyncio
@respx.mock
async def test_extraction_response_is_streamed_under_hard_byte_cap(
    statement_text: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway_module, "MAX_EXTRACTION_RESPONSE_BYTES", 32)
    respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            stream=ChunkedResponse(b'{"message":{"content":"', b"x" * 40, b'"}}'),
        )
    )
    gateway = ModelGateway(
        endpoint="http://127.0.0.1:11434",
        provider=ModelProvider.OLLAMA,
        model="example-local",
        allow_remote=False,
        timeout_seconds=30,
    )

    with pytest.raises(ModelResponseError, match="exceeded the safety limit"):
        await gateway.extract_bill(_document(statement_text))


@pytest.mark.asyncio
@respx.mock
async def test_compressed_model_response_is_rejected_without_decompression(
    statement_text: str,
) -> None:
    respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            stream=ChunkedResponse(b"not-decompressed-in-process"),
        )
    )
    gateway = ModelGateway(
        endpoint="http://127.0.0.1:11434",
        provider=ModelProvider.OLLAMA,
        model="example-local",
        allow_remote=False,
        timeout_seconds=30,
    )

    with pytest.raises(ModelResponseError, match="compressed"):
        await gateway.extract_bill(_document(statement_text))


@pytest.mark.asyncio
@respx.mock
async def test_discovery_rejects_oversized_model_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway_module, "MAX_DISCOVERED_MODELS", 1)
    respx.get("http://127.0.0.1:11434/api/tags").mock(
        return_value=httpx.Response(
            200,
            json={"models": [{"name": "model-one"}, {"name": "model-two"}]},
        )
    )
    respx.get("http://127.0.0.1:11434/v1/models").mock(return_value=httpx.Response(404))

    result = (await discover_models(("http://127.0.0.1:11434",), allow_remote=False))[0]

    assert result.provider is None
    assert result.models == ()


@pytest.mark.asyncio
@respx.mock
async def test_discovery_skips_unbounded_model_identifier() -> None:
    respx.get("http://127.0.0.1:11434/api/tags").mock(
        return_value=httpx.Response(
            200,
            json={
                "models": [
                    {"name": "x" * 301},
                    {"name": "bounded-model"},
                ]
            },
        )
    )

    result = (await discover_models(("http://127.0.0.1:11434",), allow_remote=False))[0]

    assert [model.name for model in result.models] == ["bounded-model"]
