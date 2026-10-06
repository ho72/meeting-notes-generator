import json

import httpx
import pytest
from fastapi import HTTPException

from meeting_notes.llm import NotesClient
from meeting_notes.settings import Settings


async def test_provider_receives_edited_text_and_key_header_without_key_in_url(tmp_path):
    def handle(request):
        assert not request.url.query
        assert request.headers["x-goog-api-key"] == "fixture-key"
        body = json.loads(request.content)
        assert body["contents"][0]["parts"][0]["text"] == "사람이 수정한 전사"
        assert "systemInstruction" in body
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {
                            "parts": [
                                {"text": "internal thought", "thought": True},
                                {"text": "# 회의록"},
                                {"text": "결정 사항"},
                            ]
                        },
                    }
                ]
            },
        )

    client = NotesClient(
        Settings(data_dir=tmp_path, gemini_api_key="fixture-key"), httpx.MockTransport(handle)
    )
    assert await client.generate("사람이 수정한 전사") == "# 회의록\n결정 사항"


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"candidates": []},
        {
            "candidates": [
                {"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "partial"}]}}
            ]
        },
        {"candidates": [{"content": {"parts": []}}]},
        {"candidates": [{"content": {"parts": "wrong-type"}}]},
    ],
)
async def test_missing_or_incomplete_provider_output_is_rejected(tmp_path, response):
    client = NotesClient(
        Settings(data_dir=tmp_path, gemini_api_key="fixture-key"),
        httpx.MockTransport(lambda request: httpx.Response(200, json=response)),
    )
    with pytest.raises(HTTPException) as error:
        await client.generate("text")
    assert error.value.status_code == 502


async def test_provider_error_body_is_not_returned_to_user(tmp_path):
    client = NotesClient(
        Settings(data_dir=tmp_path, gemini_api_key="fixture-key"),
        httpx.MockTransport(lambda request: httpx.Response(403, text="provider-private-details")),
    )
    with pytest.raises(HTTPException) as error:
        await client.generate("text")
    assert error.value.status_code == 502
    assert "provider-private-details" not in error.value.detail


async def test_missing_key_returns_unavailable_without_network(tmp_path):
    client = NotesClient(Settings(data_dir=tmp_path, gemini_api_key=""))
    with pytest.raises(HTTPException) as error:
        await client.generate("text")
    assert error.value.status_code == 503


async def test_timeout_has_a_clear_retryable_response(tmp_path):
    def handle(request):
        raise httpx.ReadTimeout("fixture timeout", request=request)

    client = NotesClient(
        Settings(data_dir=tmp_path, gemini_api_key="fixture-key"), httpx.MockTransport(handle)
    )
    with pytest.raises(HTTPException) as error:
        await client.generate("text")
    assert error.value.status_code == 504
