"""The shared creation-rule module owns menu derivation and the contract limits."""
import re

from pydantic import ValidationError
import pytest

from opendirector.domain.creation.generation import AudioParameters, PromptText, TextParameters
from opendirector.domain.rule_facts import (
    AGENT_IMAGE_BYTES, DEFAULT_VIDEO_BYTES, LYRICS_MAX_LENGTH, MUSIC_SECONDS_MAX,
    MUSIC_SECONDS_MIN, PROMPT_MAX_LENGTH, SERVICE_VOICE_PATTERN, STORED_VOICE_PATTERN,
    TEXT_DEFAULT_MAX_TOKENS, TEXT_MAX_TOKENS, TEXT_REFERENCE_BYTES, VIDEO_ASPECT_RATIOS,
    derivation_vectors,
)


def test_voice_precheck_is_stricter_than_the_stored_contract_pattern():
    assert re.fullmatch(SERVICE_VOICE_PATTERN, 'Voice.1')
    assert re.fullmatch(SERVICE_VOICE_PATTERN, 'a' * 64)
    assert re.fullmatch(SERVICE_VOICE_PATTERN, '动漫海绵') is None
    assert re.fullmatch(SERVICE_VOICE_PATTERN, 'has space') is None
    assert re.fullmatch(STORED_VOICE_PATTERN, '动漫海绵')
    assert re.fullmatch(STORED_VOICE_PATTERN, 'voice name') is None


def test_contract_fields_use_the_shared_limits():
    PromptText(type='text', text='x' * PROMPT_MAX_LENGTH)
    with pytest.raises(ValidationError):
        PromptText(type='text', text='x' * (PROMPT_MAX_LENGTH + 1))
    assert TextParameters().maxTokens == TEXT_DEFAULT_MAX_TOKENS
    TextParameters(maxTokens=TEXT_MAX_TOKENS)
    with pytest.raises(ValidationError):
        TextParameters(maxTokens=TEXT_MAX_TOKENS + 1)
    AudioParameters(creationType='music', lyrics='a' * LYRICS_MAX_LENGTH, durationSeconds=MUSIC_SECONDS_MIN)
    AudioParameters(creationType='music', durationSeconds=MUSIC_SECONDS_MAX)
    with pytest.raises(ValidationError):
        AudioParameters(creationType='music', lyrics='a' * (LYRICS_MAX_LENGTH + 1))
    with pytest.raises(ValidationError):
        AudioParameters(creationType='music', durationSeconds=MUSIC_SECONDS_MIN - 1)


def test_menu_derivations_follow_published_rules_then_the_catalog():
    rows = {row['name']: row['expected'] for row in derivation_vectors()}
    catalog = ['auto', *VIDEO_ASPECT_RATIOS]
    assert rows == {
        'image-ratios-drop-auto-and-duplicates': ['auto', '16:9', '1:1'],
        'video-published-first-last-is-auto-only': ['auto'],
        'video-published-ratios-keep-order': ['auto', '9:16', '9:16'],
        'video-empty-published-ratios-are-present': ['auto'],
        'volcengine-uses-catalog': catalog,
        'dimensioned-outputs-use-catalog': catalog,
        'continuation-without-published-ratios-is-auto': ['auto'],
        'unframed-video-is-auto': ['auto'],
        'audio-ratios-are-auto': ['auto'],
        'image-sizes-follow-aspect-and-dedupe': ['auto', '1024x1024'],
        'image-sizes-keep-every-size-for-auto': ['auto', '1024x1024', '1536x1024'],
        'continuation-resolution-is-auto': ['auto'],
        'audio-resolution-is-auto': ['auto'],
        'reference-image-uses-reference-resolutions': ['auto', '480P'],
        'missing-reference-resolutions-fall-back': ['auto', '720P', '1080P'],
        'output-resolutions-when-no-image-reference': ['auto', '720P'],
        'duration-filters-labels': ['auto', '720P'],
        'unset-duration-keeps-labels': ['auto', '720P', '1080P'],
        'missing-capabilities-keep-labels': ['auto', '720P'],
        'empty-capabilities-drop-labels': ['auto'],
        'continuation-mode': 'continuation',
        'first-last-mode': 'first_frame',
        'stored-first-frame-mode': 'first_frame',
        'reference-without-first-frame-support': 'reference',
        'text-only-reference-becomes-first-frame': 'first_frame',
        'media-reference-stays-reference': 'reference',
        'unconfigured-text-only-becomes-first-frame': 'first_frame',
        'published-audio-options-win': [True],
        'volcengine-audio-fallback': [True, False],
        'default-audio-is-on-only': [True],
        'empty-published-audio-options-stay-empty': [],
        'text-byte-limit': TEXT_REFERENCE_BYTES,
        'image-byte-fallback': AGENT_IMAGE_BYTES,
        'explicit-image-bytes': 10,
        'video-byte-fallback': DEFAULT_VIDEO_BYTES,
        'other-kinds-use-video-fallback': DEFAULT_VIDEO_BYTES,
    }


def test_first_frame_image_cap_uses_the_shared_limit():
    from uuid import UUID
    from opendirector.domain.creation import validate_generation
    from opendirector.domain.creation.assets import Asset, AssetRef
    from opendirector.domain.creation.generation import ResolvedReference, VideoGenerationSpec
    from opendirector.domain.creation.models import ModelConfigPublic
    from opendirector.domain.input_rules import input_rules
    from opendirector.domain.rule_facts import FRAME_IMAGE_LIMIT, MAX_REFERENCES

    project = UUID(int=1)

    def uid(number):
        return UUID(int=number)

    config = dict(kind='video', url='https://api.minimax.cn/v2', modelId='MiniMax-H3',
                  connectionType='api', protocol='minimax_video_v2', authMode='bearer', videoAdapter='auto')
    public = ModelConfigPublic(id=uid(20), inputRules=input_rules(config), **config)
    spec = VideoGenerationSpec(id=uid(65), projectId=project, kind='video', purpose='generation',
                               modelConfigId=public.id, prompt='Animate',
                               parameters=dict(videoMode='first_frame', resolution='auto'))

    def image_ref(number):
        asset_id = uid(number)
        asset = Asset(id=asset_id, projectId=project, kind='image', name='frame',
                      fingerprint='sha256:' + f'{number:064x}', path=f'assets/{asset_id}',
                      metadata=dict(mimeType='image/png', size=100, width=128, height=128),
                      provenance=dict(source='upload'))
        return ResolvedReference(
            referenceId=uid(1000 + number), role='reference',
            asset=AssetRef(assetId=asset.id, revision=asset.revision, fingerprint=asset.fingerprint, kind=asset.kind),
            metadata=asset.metadata)

    validate_generation(spec, public, tuple(image_ref(number) for number in range(FRAME_IMAGE_LIMIT)))
    with pytest.raises(ValueError, match=str(FRAME_IMAGE_LIMIT)):
        validate_generation(spec, public, tuple(image_ref(number) for number in range(FRAME_IMAGE_LIMIT + 1)))
    with pytest.raises(ValueError, match='参考素材数量超过上限'):
        validate_generation(spec, public, tuple(image_ref(number) for number in range(MAX_REFERENCES + 1)))
