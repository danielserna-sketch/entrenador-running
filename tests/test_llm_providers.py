"""Tests de contrato: ambos proveedores deben comportarse igual frente al agente."""

import json
from types import SimpleNamespace as NS

import pytest

from coach.llm.anthropic_client import AnthropicLLM
from coach.llm.base import ChatMessage, SystemPrompt, ToolResult, ToolSpec
from coach.llm.openai_client import OpenAILLM

TOOL = ToolSpec("get_profile", "Perfil", {"type": "object", "properties": {}})
SYSTEM = SystemPrompt(static="Eres un entrenador.", dynamic="hoy: 2026-10-17")


class FakeAnthropic:
    def __init__(self):
        self.calls = []
        self.responses = [
            NS(stop_reason="tool_use", usage=NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0),
               content=[NS(type="thinking", thinking=""),
                        NS(type="tool_use", id="tu_1", name="get_profile", input={})]),
            NS(stop_reason="end_turn", usage=NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=8),
               content=[NS(type="text", text="¡Listo, Daniel!")]),
        ]
        self.beta = NS(messages=NS(create=self._create))
        self.messages = NS(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeOpenAIMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls
        self.refusal = None

    def model_dump(self, exclude_none=True):
        d = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [{"id": t.id, "type": "function",
                                "function": {"name": t.function.name, "arguments": t.function.arguments}}
                               for t in self.tool_calls]
        return {k: v for k, v in d.items() if v is not None}


class FakeOpenAI:
    def __init__(self):
        self.calls = []
        call = NS(id="call_1", function=NS(name="get_profile", arguments="{}"))
        self.responses = [
            NS(choices=[NS(message=FakeOpenAIMessage(tool_calls=[call]), finish_reason="tool_calls")],
               usage=NS(prompt_tokens=10, completion_tokens=5)),
            NS(choices=[NS(message=FakeOpenAIMessage(content="¡Listo, Daniel!"), finish_reason="stop")],
               usage=NS(prompt_tokens=10, completion_tokens=5)),
        ]
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(json.loads(json.dumps(kwargs, default=str)))
        return self.responses.pop(0)


@pytest.fixture(params=["anthropic", "openai"])
def llm_and_fake(request):
    if request.param == "anthropic":
        fake = FakeAnthropic()
        return AnthropicLLM("k", client=fake), fake
    fake = FakeOpenAI()
    return OpenAILLM("k", client=fake), fake


def test_tool_loop_contract(llm_and_fake):
    llm, fake = llm_and_fake
    history = [ChatMessage("assistant", "hola previa"), ChatMessage("user", "¿cómo voy?")]
    session = llm.session(SYSTEM, history, [TOOL])
    step = session.step()
    assert step.stop_reason == "tool_use"
    assert step.tool_calls[0].name == "get_profile" and step.tool_calls[0].arguments == {}
    session.add_tool_results([ToolResult(step.tool_calls[0].id, '{"perfil": {}}')])
    final = session.step()
    assert final.stop_reason == "end" and final.text == "¡Listo, Daniel!" and not final.tool_calls
    assert len(fake.calls) == 2


def test_anthropic_caching_and_fallback_params():
    fake = FakeAnthropic()
    llm = AnthropicLLM("k", client=fake)
    session = llm.session(SYSTEM, [ChatMessage("user", "hola")], [TOOL])
    session.step()
    call = fake.calls[0]
    assert call["model"] == "claude-sonnet-5-5"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["system"][1]["text"] == "hoy: 2026-10-17"
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    # El historial empieza por user y alterna roles.
    assert call["messages"][0]["role"] == "user"


def test_anthropic_haiku_has_no_fallbacks():
    fake = FakeAnthropic()
    llm = AnthropicLLM("k", client=fake)
    llm.session(SYSTEM, [ChatMessage("user", "hola")], [TOOL], tier="fast").step()
    assert fake.calls[0]["model"] == "claude-haiku-5-5" and "fallbacks" not in fake.calls[0]


def test_anthropic_refusal():
    fake = FakeAnthropic()
    fake.responses = [NS(stop_reason="refusal", usage=NS(input_tokens=1, output_tokens=0), content=[])]
    step = AnthropicLLM("k", client=fake).session(SYSTEM, [ChatMessage("user", "x")], []).step()
    assert step.stop_reason == "refusal"


def test_openai_tool_message_format():
    fake = FakeOpenAI()
    llm = OpenAILLM("k", client=fake)
    session = llm.session(SYSTEM, [ChatMessage("user", "hola")], [TOOL])
    step = session.step()
    session.add_tool_results([ToolResult(step.tool_calls[0].id, "{}")])
    session.step()
    msgs = fake.calls[1]["messages"]
    assert msgs[0]["role"] == "system" and msgs[0]["content"].startswith("Eres un entrenador.")
    assert msgs[-1] == {"role": "tool", "tool_call_id": "call_1", "content": "{}"}
    assert fake.calls[0]["tools"][0]["function"]["name"] == "get_profile"
