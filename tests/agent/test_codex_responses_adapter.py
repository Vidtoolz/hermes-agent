from types import SimpleNamespace

import pytest

from agent.codex_responses_adapter import (
    _chat_messages_to_responses_input,
    _format_responses_error,
    _normalize_codex_response,
    _preflight_codex_api_kwargs,
    _preflight_codex_input_items,
)




def test_normalize_codex_response_treats_summary_only_reasoning_as_incomplete():
    """Summary-only reasoning keeps the continuation path for Codex backends.

    Since #64434, an unrecognized issuer with ``response.status="completed"``
    trusts the provider and returns ``stop`` — so this test pins the Codex
    backend explicitly, where reasoning-only still means "still thinking".
    """
    response = SimpleNamespace(
        status="completed",
        output=[
            SimpleNamespace(
                type="reasoning",
                id="rs_tmp_789",
                encrypted_content="opaque-transient",
                summary=[SimpleNamespace(text="still thinking")],
            )
        ],
    )

    assistant_message, finish_reason = _normalize_codex_response(
        response, issuer_kind="codex_backend"
    )

    assert finish_reason == "incomplete"
    assert assistant_message.content == ""
    assert assistant_message.reasoning == "still thinking"
    assert assistant_message.codex_reasoning_items is None




# ---------------------------------------------------------------------------
# Server-side built-in tool calls (xAI native web_search, code interpreter,
# etc.) come back as discrete ``*_call`` output items that xAI's
# /v1/responses surface routinely leaves at ``status="in_progress"`` even
# when the overall ``response.status == "completed"``.  These must NOT mark
# the turn incomplete — otherwise grok-composer-2.5-fast research queries
# (which invoke server-side web_search) get misclassified as
# ``finish_reason="incomplete"`` and burn 3 fruitless continuation retries
# before failing with "Codex response remained incomplete after 3
# continuation attempts".  Observed live against grok-composer-2.5-fast on
# SuperGrok OAuth (2026-06).
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# Replayed assistant message items with an oversized server-assigned ``id``
# (Codex issues 400+ char base64 blobs) must never reach the API — the
# Responses endpoint caps input[].id at 64 chars and rejects the whole
# request with a non-retryable HTTP 400, permanently bricking the session
# (every subsequent turn replays the same bad id). Short ids (msg_...) are
# still worth keeping for prefix-cache hits, so this is a length guard, not
# a blanket strip.
# ---------------------------------------------------------------------------

_OVERSIZED_ITEM_ID = "x" * 408
_VALID_ITEM_ID = "msg_abc123"






# The codex app-server overflows the Responses 64-char call_id limit for
# MCP-routed tools, e.g. codex_mcp__hermes-tools__web_search_exec-<uuid> (#73492).
_OVERSIZED_CALL_ID = "codex_mcp__hermes-tools__web_search_exec-" + "0" * 43


def test_chat_messages_to_responses_input_clamps_oversized_call_id():
    """An oversized call_id must be clamped to <=64 chars on BOTH the
    function_call and its matching function_call_output, to the same surrogate,
    so the pairing survives (#73492)."""
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "call_id": _OVERSIZED_CALL_ID,
                    "function": {"name": "web_search", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": _OVERSIZED_CALL_ID,
            "content": "some result",
        },
    ]

    items = _chat_messages_to_responses_input(messages)

    call = next(i for i in items if i.get("type") == "function_call")
    output = next(i for i in items if i.get("type") == "function_call_output")

    assert len(call["call_id"]) <= 64
    assert call["call_id"] != _OVERSIZED_CALL_ID
    # Deterministic surrogate — the pair must still reference the same id.
    assert call["call_id"] == output["call_id"]


def test_chat_messages_to_responses_input_keeps_short_call_id():
    """A call_id already within the limit passes through unchanged (#73492)."""
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "call_id": "call_abc123",
                    "function": {"name": "web_search", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_abc123",
            "content": "some result",
        },
    ]

    items = _chat_messages_to_responses_input(messages)

    call = next(i for i in items if i.get("type") == "function_call")
    output = next(i for i in items if i.get("type") == "function_call_output")
    assert call["call_id"] == "call_abc123"
    assert output["call_id"] == "call_abc123"






def test_preflight_codex_input_items_drops_short_id_for_github_responses():
    items = _preflight_codex_input_items(
        [
            {
                "type": "message",
                "role": "assistant",
                "status": "in_progress",
                "content": [{"type": "output_text", "text": "pong"}],
                "id": _VALID_ITEM_ID,
                "phase": "final_answer",
            }
        ],
        is_github_responses=True,
    )

    assert "id" not in items[0]
    assert items[0]["status"] == "in_progress"
    assert items[0]["phase"] == "final_answer"
    assert items[0]["content"] == [{"type": "output_text", "text": "pong"}]


def test_preflight_codex_api_kwargs_drops_oversized_message_id_end_to_end():
    kwargs = _preflight_codex_api_kwargs(
        {
            "model": "gpt-5.5",
            "instructions": "You are Hermes.",
            "input": [
                {"role": "user", "content": "ping"},
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "pong"}],
                    "id": _OVERSIZED_ITEM_ID,
                    "phase": "final_answer",
                },
            ],
            "tools": [],
            "store": False,
        }
    )

    message_item = next(item for item in kwargs["input"] if item.get("type") == "message")
    assert "id" not in message_item


# ---------------------------------------------------------------------------
# _preflight_codex_api_kwargs — built-in (provider-executed) tools must pass
# through validation.  Regression guard for the xAI native web_search
# injection: the preflight validator previously rejected any tool whose
# ``type != "function"`` with "unsupported type", which would 400 every xAI
# turn once the native web_search tool is declared.
# ---------------------------------------------------------------------------


def test_preflight_passes_native_web_search_tool_through():
    kwargs = {
        "model": "grok-composer-2.5-fast",
        "instructions": "You are helpful.",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "store": False,
        "tools": [
            {"type": "function", "name": "read_file", "description": "Read.",
             "parameters": {"type": "object", "properties": {}}},
            {"type": "web_search"},
        ],
    }
    out = _preflight_codex_api_kwargs(kwargs, allow_stream=True)
    tools = out["tools"]
    assert {"type": "web_search"} in tools
    assert any(t.get("type") == "function" and t.get("name") == "read_file" for t in tools)




# ---------------------------------------------------------------------------
# _format_responses_error — adapted from anomalyco/opencode#28757.
# Provider failures should surface BOTH the code (rate_limit_exceeded /
# context_length_exceeded / internal_error / server_error) and the message,
# so consumers can tell rate limits apart from context-length failures and
# both apart from generic stream drops.
# ---------------------------------------------------------------------------




def test_format_responses_error_message_only():
    err = {"message": "Upstream model unavailable"}
    assert _format_responses_error(err, "failed") == "Upstream model unavailable"














def test_normalize_codex_response_failed_includes_code_in_error():
    """Regression: response_status == 'failed' should surface the error
    code, not just the message. Used to leak a bare 'Slow down' string
    that was indistinguishable from a generic stream truncation."""
    # ``output`` non-empty so we don't trip the "no output items" guard
    # before reaching the failed-status branch. Real failed responses
    # often DO carry a partial message item alongside the error.
    response = SimpleNamespace(
        status="failed",
        output=[
            SimpleNamespace(
                type="message",
                role="assistant",
                status="incomplete",
                content=[SimpleNamespace(type="output_text", text="partial")],
            ),
        ],
        error={"code": "rate_limit_exceeded", "message": "Slow down"},
    )
    with pytest.raises(RuntimeError, match=r"^rate_limit_exceeded: Slow down$"):
        _normalize_codex_response(response)




# ---------------------------------------------------------------------------
# Reasoning-channel answer salvage (xAI grok) — grok-4.x on the xAI
# /v1/responses surface sometimes emits its final answer inside the
# reasoning item, delimited by grok's internal "<response>" tag, with no
# ``message`` output item at all.  Because those reasoning items carry no
# encrypted_content, the interim message replays as nothing and every
# continuation request is byte-identical — the turn burns 3 retries and
# fails even though the answer was produced.  Observed live with grok-4.20
# on xai-oauth (2026-07-13).
# ---------------------------------------------------------------------------


def _xai_reasoning_only_response(reasoning_text):
    return SimpleNamespace(
        status="completed",
        output=[
            SimpleNamespace(
                type="reasoning",
                id="rs_1",
                encrypted_content=None,
                summary=[SimpleNamespace(text=reasoning_text)],
            )
        ],
    )










# ---------------------------------------------------------------------------
# _responses_tools — fail fast on malformed Chat Completions tool definitions
# instead of silently skipping them.  A silently dropped tool removes an
# agent capability with no diagnostic, and the surviving partial list would
# be transmitted as if it were the whole request; the downstream
# _preflight_codex_api_kwargs validator only sees what survives conversion,
# so it cannot catch the omission.  Recovered intent from stash 741ccc58
# (recovery/hermes-codex-adapter-stash-741ccc58, 29d422f0), adapted to pass
# provider-executed built-in declarations through like the preflight does.
# ---------------------------------------------------------------------------


def _valid_function_tool(name="read_file", **overrides):
    fn = {
        "name": name,
        "description": "Read a file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
    }
    fn.update(overrides)
    return {"type": "function", "function": fn}


def test_responses_tools_converts_valid_function_tool_deterministically():
    from agent.codex_responses_adapter import _responses_tools

    tools = [_valid_function_tool(), _valid_function_tool(name="write_file")]
    out = _responses_tools(tools)
    assert [t["name"] for t in out] == ["read_file", "write_file"]  # order preserved
    assert out[0] == {
        "type": "function",
        "name": "read_file",
        "description": "Read a file.",
        "strict": False,
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
    }
    assert _responses_tools(tools) == out  # deterministic


def test_responses_tools_does_not_mutate_caller_input():
    from agent.codex_responses_adapter import _responses_tools
    import copy

    tools = [_valid_function_tool(name="  padded  ")]
    snapshot = copy.deepcopy(tools)
    out = _responses_tools(tools)
    assert tools == snapshot  # caller input untouched
    assert out[0]["name"] == "padded"  # converted name is stripped


def test_responses_tools_empty_and_none_input():
    from agent.codex_responses_adapter import _responses_tools

    assert _responses_tools(None) is None
    assert _responses_tools([]) is None


def test_responses_tools_rejects_non_object_tool_with_index():
    from agent.codex_responses_adapter import _responses_tools

    with pytest.raises(ValueError, match=r"tool\[1\] must be an object"):
        _responses_tools([_valid_function_tool(), "not-a-tool"])


def test_responses_tools_rejects_unsupported_type_with_index_and_no_payload():
    from agent.codex_responses_adapter import _responses_tools

    secret_payload = {"type": "banana", "function": {"name": "sk-SECRET-VALUE"}}
    with pytest.raises(ValueError) as excinfo:
        _responses_tools([secret_payload])
    message = str(excinfo.value)
    assert "tool[0]" in message and "'banana'" in message
    assert "sk-SECRET-VALUE" not in message  # no payload values leaked


def test_responses_tools_rejects_missing_function_definition():
    from agent.codex_responses_adapter import _responses_tools

    with pytest.raises(ValueError, match=r"tool\[0\] is missing its function definition"):
        _responses_tools([{"type": "function"}])
    with pytest.raises(ValueError, match=r"tool\[0\] is missing its function definition"):
        _responses_tools([{"type": "function", "function": "nope"}])


def test_responses_tools_rejects_missing_or_empty_name():
    from agent.codex_responses_adapter import _responses_tools

    with pytest.raises(ValueError, match=r"tool\[0\] is missing function\.name"):
        _responses_tools([{"type": "function", "function": {"parameters": {}}}])
    with pytest.raises(ValueError, match=r"tool\[0\] is missing function\.name"):
        _responses_tools([_valid_function_tool(name="   ")])


def test_responses_tools_mixed_list_fails_whole_request_no_partial_output():
    """Regression control: under the old silent-skip behavior this returned a
    1-tool partial list; the corrected behavior refuses the whole conversion
    so no partial tool list can ever be transmitted."""
    from agent.codex_responses_adapter import _responses_tools

    mixed = [_valid_function_tool(), {"type": "function", "function": {"parameters": {}}}]
    with pytest.raises(ValueError, match=r"tool\[1\]"):
        _responses_tools(mixed)


def test_responses_tools_passes_builtin_declarations_through_verbatim():
    """A ``{"type": "web_search"}`` built-in declaration was previously
    silently dropped (no function schema -> no name -> skip); it must pass
    through verbatim like _preflight_codex_api_kwargs does."""
    from agent.codex_responses_adapter import _responses_tools

    tools = [{"type": "web_search"}, _valid_function_tool()]
    out = _responses_tools(tools)
    assert out[0] == {"type": "web_search"}
    assert out[0] is not tools[0]  # copy, not the caller's object
    assert out[1]["name"] == "read_file"


def test_responses_tools_default_parameters_schema_preserved():
    from agent.codex_responses_adapter import _responses_tools

    out = _responses_tools([{"type": "function", "function": {"name": "bare"}}])
    assert out[0]["parameters"] == {"type": "object", "properties": {}}


def test_responses_tools_duplicate_names_preserved_in_order():
    from agent.codex_responses_adapter import _responses_tools

    out = _responses_tools([_valid_function_tool(name="dup"), _valid_function_tool(name="dup")])
    assert [t["name"] for t in out] == ["dup", "dup"]
