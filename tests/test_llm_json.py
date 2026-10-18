import json

import pytest

from app.llm.client import LLMError, extract_json


@pytest.mark.parametrize("wrapper", ["{}", "说明：{}\n完毕", "```json\n{}\n```"])
@pytest.mark.parametrize("value", [
    {"text": "a } closing brace"},
    {"text": "an { opening brace"},
    {"nested": [{"text": 'escaped \\" quote " and { brace }'}]},
])
def test_extract_object_respects_json_strings(wrapper, value):
    assert extract_json(wrapper.format(json.dumps(value))) == value


@pytest.mark.parametrize("preamble", ["字段 {scene_id} 的结果：", "[说明] 以下是结果："])
@pytest.mark.parametrize("language", ["json", ""])
def test_fenced_object_takes_priority_over_brackets_in_prose(preamble, language):
    value = {"scene_id": 2, "text": 'braces { } and escaped " quotes'}
    text = f"{preamble}\n```{language}\n{json.dumps(value)}\n```\n完毕"
    assert extract_json(text) == value


@pytest.mark.parametrize("payload", ['[{"scene_id": 2}]', '{"scene_id":', '[{"scene_id": 2}'])
def test_invalid_fenced_payload_does_not_fall_back_to_prose_object(payload):
    text = '{"example": true}\n```json\n' + payload + '\n```'
    with pytest.raises(LLMError):
        extract_json(text)


@pytest.mark.parametrize("text", [
    "", "no JSON here", '{"a":', '{"a": "unterminated}',
    '[{"a": 1}]', 'prose [{"a": 1}] trailing',
    '```json\n[{"a": 1}]\n```', '[]', '[{"a": 1}',
])
def test_extract_json_rejects_malformed_output_and_non_objects(text):
    with pytest.raises(LLMError):
        extract_json(text)
