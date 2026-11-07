"""Portable inline references. Plain inputs are projections, never a second draft."""

from typing import Annotated, Literal
from opendirector.domain.input_rules import MAX_REFERENCES
from opendirector.domain.rule_facts import PROMPT_MAX_LENGTH
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class TextPart(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: Literal['text']
    text: str = Field(max_length=PROMPT_MAX_LENGTH)


class ReferencePart(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: Literal['reference']
    assetId: UUID
    binding: Literal["asset"] | None = Field(default=None, exclude_if=lambda value: value is None)
    referenceId: UUID | None = Field(default=None, exclude_if=lambda value: value is None)
    transmission: Literal["context_only"] | None = Field(default=None, exclude_if=lambda value: value is None)
    sourceRole: str | None = Field(default=None, max_length=100, exclude_if=lambda value: value is None)
    nodeId: UUID | None = Field(default=None, exclude_if=lambda value: value is None)
    name: str = Field(min_length=1, max_length=255)
    fingerprint: str | None = Field(default=None, max_length=1000)


ReferenceContent = Annotated[list[Annotated[TextPart | ReferencePart, Field(discriminator='type')]], Field(max_length=2000)]
content_adapter = TypeAdapter(ReferenceContent)


def project_content(content, *, speech=False):
    """Accept JSON or validated parts; retain first-mention ordering for providers."""
    parts = content_adapter.validate_python(content)
    references, text = [], []
    for part in parts:
        if part.type == 'text':
            text.append(part.text)
        else:
            if part.transmission == 'context_only':
                continue
            asset_id = str(part.assetId)
            if asset_id not in references:
                references.append(asset_id)
            if not speech:
                text.append(f'［参考{references.index(asset_id) + 1}：{part.name}］')
    prompt = ''.join(text)
    if speech:
        prefix, has_reference = '', False
        for part in parts:
            if part.type == 'reference':
                has_reference = True
                continue
            line, newline, _ = part.text.partition('\n')
            prefix += line
            if newline:
                if has_reference and not prefix.strip():
                    prompt = prompt[len(prefix) + 1:]
                break
    if len(references) > MAX_REFERENCES or len(prompt) > 20000:
        raise ValueError(f'正文最多 20000 字、{MAX_REFERENCES} 份不同参考')
    return prompt, references
