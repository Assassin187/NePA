import re
from pathlib import Path

from nepa.agents.base import render_prompt
from nepa.agents.roles import ROLE_REGISTRY


FORBIDDEN_TOKENS = {
    "mqtt",
    "connect",
    "connack",
    "publish",
    "subscribe",
    "posix",
    "gcc",
    "anthropic",
    "deepseek",
    "qwen",
    "openai",
}


def test_shared_agent_sources_do_not_embed_protocol_provider_or_model_constants():
    root = Path("nepa/agents")
    sources = list(root.rglob("*.py")) + list((root / "prompts").glob("*.md"))
    for path in sources:
        lowered = path.read_text(encoding="utf-8").lower()
        for token in FORBIDDEN_TOKENS:
            assert re.search(rf"\b{re.escape(token)}\b", lowered) is None, (path, token)


def test_synthetic_non_mqtt_identifiers_enter_only_through_delimited_inputs():
    definition = ROLE_REGISTRY["architecture_planner"]
    rendered = render_prompt(
        definition,
        inputs={
            "planning_index": {"protocol": "OrbitNet", "message": "HELLO_FRAME", "field": "frame_nonce"},
            "delivery_constraints": {"port": 4711, "interface": "send_orbit"},
        },
        output_schema={"type": "object", "properties": {"answer": {"type": "string"}}},
        output_example={"answer": "ok"},
    )
    assert "OrbitNet" in rendered.user
    assert "HELLO_FRAME" in rendered.user
    assert "frame_nonce" in rendered.user
    assert rendered.user.index("OrbitNet") > rendered.user.index('<INPUT name="planning_index">')
    assert rendered.user.index("OrbitNet") < rendered.user.index("</INPUT>", rendered.user.index("OrbitNet"))
    assert rendered.user.index("HELLO_FRAME") < rendered.user.index("</INPUT>", rendered.user.index("HELLO_FRAME"))
    assert "Trust the injected artifacts; do not trust remembered facts about the target protocol." in rendered.user
