"""Generation intent, references and fixed execution facts, independent of nodes."""
import math
from typing import Annotated, Literal
from datetime import datetime
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from opendirector.domain.minimax_models import MINIMAX_MODELS
from opendirector.domain.generation import GenerationFailure, ResultCheck
from opendirector.domain.rule_facts import (
    FASTH3_MEDIA_REFERENCE_LIMIT, FRAME_IMAGE_LIMIT, LYRICS_MAX_LENGTH, MAX_REFERENCES,
    MUSIC_SECONDS_INITIAL, MUSIC_SECONDS_MAX, MUSIC_SECONDS_MIN, PROMPT_MAX_LENGTH,
    REFERENCE_MEDIA_SECONDS, STORED_VOICE_PATTERN, TEXT_DEFAULT_MAX_TOKENS, TEXT_MAX_TOKENS,
    effective_video_mode, generate_audio_options,
)
from .assets import AssetRef, MediaMetadata
from .base import CanvasItemVersion, Contract, Digest, GenerationKind, NonNegative, ObjectVersion, PositiveInt, ProjectEntity, Revision, digest, unique
from .models import ModelConfigPublic, ModelSnapshot, VideoMode


class TextParameters(Contract):
    thinking: Literal['auto', 'low', 'medium', 'high'] = Field(default='auto', description='Requested reasoning effort.')
    maxTokens: Annotated[int, Field(strict=True, ge=1, le=TEXT_MAX_TOKENS)] = Field(default=TEXT_DEFAULT_MAX_TOKENS, description='Maximum generated text tokens.')


class ImageParameters(Contract):
    size: str = Field(default='auto', pattern=r'^(?:auto|2K|[1-9]\d{2,3}x[1-9]\d{2,3})$', description='Requested image dimensions or size preset.')
    aspectRatio: str | None = Field(default=None, pattern=r'^[1-9]\d?:[1-9]\d?$', description='Output width-to-height ratio.')
    preset: Literal['none', 'product-views'] = Field(default='none', description='Selected generation or motion preset.')
    responseFormat: Literal['url'] = Field(default='url', description='Provider response representation.')
    outputFormat: Literal['png'] = Field(default='png', description='Generated image encoding.')


class VideoParameters(Contract):
    generateAudio: bool = Field(default=True, description='Include audio; value must be allowed by model inputRules.generateAudioOptions.')
    videoMode: VideoMode = Field(default='first_frame', description='Video generation input mode.')
    durationSeconds: Annotated[float, Field(strict=True, ge=1, le=60)] | None = Field(default=None, description='Duration in seconds.')
    aspectRatio: str | None = Field(default=None, pattern=r'^[1-9]\d?:[1-9]\d?$', description='Output width-to-height ratio.')
    resolution: str = Field(default='auto', pattern=r'^(?:auto|[A-Z0-9][A-Z0-9_.-]{0,23})$', description='Requested output resolution.')


# Music models count audio frames at 25 per second, between 200 and 9000 frames.
MUSIC_SECONDS = (MUSIC_SECONDS_MIN, MUSIC_SECONDS_MAX)
DEFAULT_MUSIC_SECONDS = MUSIC_SECONDS_INITIAL
# A cover can run a little longer than its reference (tempo, held endings);
# the cap leaves this much room so the model ends the piece itself.
REFERENCE_LENGTH_HEADROOM = 1.1


def reference_music_seconds(reference_seconds):
    """The length cap for a piece that follows a reference recording."""
    return min(MUSIC_SECONDS[1], max(MUSIC_SECONDS[0], math.ceil(reference_seconds * REFERENCE_LENGTH_HEADROOM)))


class AudioParameters(Contract):
    creationType: Literal['audio', 'music'] = Field(default='audio', description='Speech or music creation mode.')
    mode: Literal['tts', 'unified'] = Field(default='tts', description='Operation mode within this contract variant.')
    voice: str | None = Field(default=None, pattern=STORED_VOICE_PATTERN, description='Provider voice identifier or name.')
    # Legacy: a release stored an explicit instrumental choice. It stays readable
    # so those specs and job fingerprints still load; generation ignores it.
    instrumental: bool = Field(default=False, exclude_if=lambda value: value is False, description='Legacy explicit instrumental choice retained for stored records.')
    # Music only: the prompt is the style description; empty lyrics ask for an instrumental piece.
    # Unset music fields stay out of the dump, so specs and job fingerprints
    # recorded before music support keep their identity.
    lyrics: str = Field(default='', max_length=LYRICS_MAX_LENGTH, exclude_if=lambda value: value == '', description='Song lyrics; empty lyrics request instrumental music.')
    durationSeconds: Annotated[float, Field(strict=True, ge=MUSIC_SECONDS[0], le=MUSIC_SECONDS[1])] | None = Field(
        default=None, exclude_if=lambda value: value is None, description='Maximum music duration in seconds.')
    # Music with a reference recording: follow its melody only, or its melody and chords.
    scoreMode: Literal['melody', 'full'] = Field(
        default='melody', exclude_if=lambda value: value == 'melody',
        description='Music with one audio reference: follow its transcribed melody, or melody and chords')
    durationFromReference: bool | None = Field(
        default=None, exclude_if=lambda value: value is None,
        description='Music with one audio reference: cap the length at the reference length (unset follows it) '
                    'instead of durationSeconds')


class PromptText(Contract):
    type: Literal['text'] = Field(description='Operation or variant discriminator.')
    text: str = Field(max_length=PROMPT_MAX_LENGTH, description='UTF-8 text content or search query.')


class PromptReference(Contract):
    type: Literal['reference'] = Field(description='Operation or variant discriminator.')
    referenceId: UUID = Field(description='Generation reference ID.')


PromptPart = Annotated[PromptText | PromptReference, Field(discriminator='type')]


class GenerationSpecBase(ProjectEntity):
    purpose: Literal['draft', 'generation'] = Field(default='draft', description='Draft settings or executable generation configuration.')
    modelConfigId: UUID | None = Field(default=None, description='Model configuration ID from the model catalog or user model selections.')  # Drafts can exist before choosing a model.
    prompt: str = Field(default='', max_length=PROMPT_MAX_LENGTH, description='Generation prompt text.')
    promptContent: tuple[PromptPart, ...] | None = Field(default=None, description='Structured prompt text and reference tokens.')
    currentResultId: UUID | None = Field(default=None, description='Currently adopted result ID.')


class TextGenerationSpec(GenerationSpecBase):
    kind: Literal['text'] = Field(description='Business object or media type.')
    parameters: TextParameters = Field(default_factory=TextParameters, description='Typed generation or editing parameters.')


class ImageGenerationSpec(GenerationSpecBase):
    kind: Literal['image'] = Field(description='Business object or media type.')
    parameters: ImageParameters = Field(default_factory=ImageParameters, description='Typed generation or editing parameters.')


class VideoGenerationSpec(GenerationSpecBase):
    kind: Literal['video'] = Field(description='Business object or media type.')
    parameters: VideoParameters = Field(default_factory=VideoParameters, description='Typed generation or editing parameters.')


class AudioGenerationSpec(GenerationSpecBase):
    kind: Literal['audio'] = Field(description='Business object or media type.')
    parameters: AudioParameters = Field(default_factory=AudioParameters, description='Typed generation or editing parameters.')


GenerationSpec = Annotated[TextGenerationSpec | ImageGenerationSpec | VideoGenerationSpec | AudioGenerationSpec,
                           Field(discriminator='kind')]


class FollowingSource(Contract):
    objectType: Literal['canvas_item'] = Field(default='canvas_item', description='Referenced business object type.')
    id: UUID = Field(description='Source canvas item ID.')


class ReferenceBase(ProjectEntity):
    targetSpecId: UUID = Field(description='Target generation configuration ID.')
    role: Literal['reference', 'first_frame', 'last_frame', 'continuation'] = Field(default='reference', description='Business role of this binding or reference.')
    order: int = Field(default=0, ge=0, strict=True, description='Reference ordering within the target configuration.')
    active: bool = Field(default=True, description='Whether this reference participates in generation.')  # Retained tombstone prevents revision ABA on undo.


class PinnedReference(ReferenceBase):
    binding: Literal['pinned'] = Field(description='Use a fixed asset version, retaining canvas provenance when supplied.')
    source: AssetRef = Field(description='Fixed asset version used by generation.')
    # Historical provenance only; execution always uses the pinned asset.
    sourceVersion: CanvasItemVersion | None = Field(default=None, exclude_if=lambda value: value is None,
        description='Source canvas item identity and revision. Required for canvas-derived references to retain their connection.')
    subjectRole: Literal['main', 'auxiliary', 'voice'] | None = Field(default=None, exclude_if=lambda value: value is None,
        description='Subject media slot provenance only; omit for ordinary image references and explicit first_frame/last_frame assets. main does not mean first frame.')


class FollowingReference(ReferenceBase):
    # A stable logical source item survives hiding; its current Asset/Spec
    # binding may change. It never points to a transient React Flow instance.
    binding: Literal['follow_current'] = Field(description="Follow the source canvas item's current asset.")
    source: FollowingSource = Field(description='Source canvas_item identity.')


Reference = Annotated[PinnedReference | FollowingReference, Field(discriminator='binding',
    description='Generation input reference. Preserve source canvas identity for canvas-derived references, whether following or pinned.')]


class ResolvedReference(Contract):
    referenceId: UUID = Field(description='Generation reference ID.')
    role: Literal['reference', 'first_frame', 'last_frame', 'continuation'] = Field(description='Business role of this binding or reference.')
    asset: AssetRef = Field(description='Immutable asset version.')
    metadata: MediaMetadata = Field(description='Verified media properties.')
    sourceVersion: ObjectVersion | None = Field(default=None, description='Captured source object version.')
    documentVersion: ObjectVersion | None = Field(default=None, description='Version of the source editable document.')
    subjectRole: Literal['main', 'auxiliary', 'voice'] | None = Field(default=None, description='Subject media slot used by this reference.')
    name: str = Field(default='', max_length=255, description='User-visible name.')
    sourceTitle: str = Field(default='', max_length=255, description='Source canvas title captured for this reference.')
    transmission: Literal['content', 'metadata', 'context_only', 'latent'] = Field(default='content', description='How reference content is sent to the provider.')


class InputSnapshot(Contract):
    """Only creative inputs; layout, selection and credentials are excluded."""
    spec: GenerationSpec = Field(description='Generation configuration for this operation.')
    model: ModelSnapshot = Field(description='Fixed model configuration for this execution.')
    references: tuple[ResolvedReference, ...] = Field(default=(), description='Ordered generation reference bindings.')

    @model_validator(mode='after')
    def identity(self):
        expected_kind = 'agent' if self.spec.kind == 'text' else self.spec.kind
        if self.model.kind != expected_kind or self.spec.modelConfigId != self.model.modelConfigId:
            raise ValueError('Snapshot model must match the generation configuration')
        if self.spec.purpose != 'generation':
            raise ValueError('Accepted execution requires a committed generation purpose')
        if self.spec.currentResultId is not None:
            raise ValueError('Current display selection is not a generation input')
        unique([(ref.referenceId, ref.asset.assetId) for ref in self.references], 'resolved reference assets')
        for reference_id in {ref.referenceId for ref in self.references}:
            expanded = [ref for ref in self.references if ref.referenceId == reference_id]
            if len(expanded) > 1 and (any(ref.documentVersion is None or ref.subjectRole is None for ref in expanded)
                    or len({(ref.documentVersion.id, ref.documentVersion.revision) for ref in expanded}) != 1):
                raise ValueError('Multiple assets per reference require one frozen subject document')
        if any(ref.asset.kind == 'file' for ref in self.references):
            raise ValueError('File nodes are not generation media references')
        reference_ids = {ref.referenceId for ref in self.references}
        if self.spec.promptContent and any(isinstance(part, PromptReference) and part.referenceId not in reference_ids
                                           for part in self.spec.promptContent):
            raise ValueError('Prompt points to an unresolved reference')
        return self

    def fingerprint(self):
        # Retain exact source revisions for audit, but a display-selection edit
        # can increment the spec revision without changing creative inputs.
        return digest(self, exclude={'spec': {'revision', 'currentResultId'},
            'references': {'__all__': {'name': True, 'sourceTitle': True, 'asset': {'revision'},
                'sourceVersion': {'revision'}, 'documentVersion': {'revision'}}}})


class VideoExecution(Contract):
    """Resolved adapter parameters, distinct from user intent and source layout."""
    kind: Literal['video'] = Field(description='Business object or media type.')
    durationSeconds: Annotated[float, Field(strict=True, ge=1, le=60)] | None = Field(default=None, description='Duration in seconds.')
    size: str | None = Field(default=None, pattern=r'^\d+x\d+$', description='Effective video dimensions sent to the provider.')
    resolution: str | None = Field(default=None, description='Requested output resolution.')
    fps: PositiveInt | None = Field(default=None, description='Frames per second.')
    numFrames: PositiveInt | None = Field(default=None, description='Requested video frame count.')
    numInferenceSteps: PositiveInt | None = Field(default=None, description='Diffusion sampling step count.')
    guidanceScale: NonNegative | None = Field(default=None, description='Diffusion guidance scale.')
    seed: int | None = Field(default=None, strict=True, description='Random seed, when specified.')
    watermark: bool | None = Field(default=None, description='Whether provider watermarking is enabled.')
    generateAudio: bool | None = Field(default=None, description='Whether video generation includes audio.')


class ImageExecution(Contract):
    kind: Literal['image'] = Field(description='Business object or media type.')
    size: str = Field(description='Effective image dimensions sent to the provider.')
    responseFormat: Literal['url', 'b64_json'] | None = Field(default=None, description='Provider response representation.')
    outputFormat: Literal['png'] = Field(default='png', description='Generated image encoding.')
    watermark: bool | None = Field(default=None, description='Whether provider watermarking is enabled.')


class AudioExecution(Contract):
    kind: Literal['audio'] = Field(description='Business object or media type.')
    voice: str | None = Field(default=None, description='Provider voice identifier or name.')
    speaker: str | None = Field(default=None, description='Provider speaker identifier.')
    format: Literal['mp3', 'wav'] = Field(description='Audio container or codec format.')
    sampleRate: PositiveInt | None = Field(default=None, description='Audio samples per second.')
    creationType: Literal['audio', 'music'] = Field(default='audio', exclude_if=lambda value: value == 'audio', description='Speech or music creation mode.')
    # Legacy, see AudioParameters.instrumental.
    instrumental: bool = Field(default=False, exclude_if=lambda value: value is False, description='Legacy explicit instrumental choice retained for stored records.')
    # A music model may end the piece earlier than this cap.
    maxDurationSeconds: Annotated[float, Field(strict=True, ge=MUSIC_SECONDS[0], le=MUSIC_SECONDS[1])] | None = Field(
        default=None, exclude_if=lambda value: value is None, description='Maximum generated audio duration in seconds.')
    # Set when the piece follows a reference recording's transcribed score.
    scoreMode: Literal['melody', 'full'] | None = Field(default=None, exclude_if=lambda value: value is None, description='Whether reference music supplies melody or full accompaniment.')


class TextExecution(TextParameters):
    kind: Literal['text'] = Field(description='Business object or media type.')


ExecutionParameters = Annotated[TextExecution | ImageExecution | VideoExecution | AudioExecution, Field(discriminator='kind')]


class Job(ProjectEntity):
    schemaVersion: Literal[1] = Field(default=1, description='Record schema version.')
    createdAt: datetime | None = Field(default=None, description='Execution acceptance timestamp.')
    inputSnapshot: InputSnapshot = Field(description='Fixed generation inputs accepted for this execution.')
    inputFingerprint: Digest = Field(description='Fingerprint of the fixed execution input.')
    execution: ExecutionParameters = Field(description='Fixed effective parameters submitted to the provider.')
    status: Literal['queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted'] = Field(default='queued', description='Current operation lifecycle state.')
    phase: Literal['queued', 'prepare', 'submit', 'poll', 'download', 'check', 'save', 'adopt', 'complete'] = Field(default='queued', description='Current execution phase.')
    externalTaskId: str | None = Field(default=None, min_length=1, max_length=200, description='Provider task ID for polling or recovery.')
    asset: AssetRef | None = Field(default=None, description='Immutable asset version.')
    errorDetails: GenerationFailure | None = Field(default=None, description='Structured execution failure, if any.')
    recovery: Literal['submit', 'remote', 'download', 'local', 'review', 'retry', 'done'] = Field(default='submit', description='Available recovery strategy for this execution.')
    resumeAllowed: bool = Field(default=False, description='Whether the existing execution can be resumed.')
    cancelRequested: bool = Field(default=False, description='Whether cancellation was requested locally.')
    remoteCancellation: Literal['not_requested', 'requested', 'confirmed', 'unknown'] = Field(default='not_requested', description='Provider cancellation confirmation state.')
    originItemId: UUID | None = Field(default=None, description='Originating canvas item ID.')  # Optional display provenance, not execution ownership.
    previousJobId: UUID | None = Field(default=None, description='Previous generation attempt ID.')
    technicalCheck: ResultCheck | None = Field(default=None, description='Technical validation outcome for the generated asset.')
    adoptionPolicy: Literal['if_current', 'candidate_only'] = Field(default='if_current', description='Policy for adopting the completed result.')
    batchId: UUID | None = Field(default=None, description='Generation batch ID.')
    showAdditionalResult: bool = Field(default=False, description='Whether to display an additional result item.')
    adoptionResultId: UUID | None = Field(default=None, description='Result ID reserved for adoption.')
    adoptionMaterial: AssetRef | None = Field(default=None, description='Material version reserved for adoption.')
    adopted: bool = Field(default=False, description='Whether the generated result was adopted.')
    adapterParameters: dict[str, JsonValue] = Field(default_factory=dict, description='Fixed provider-specific execution parameters.')

    @model_validator(mode='after')
    def execution_identity(self):
        if self.showAdditionalResult and (self.originItemId is None or self.adoptionPolicy != 'candidate_only'):
            raise ValueError('Additional result display requires an origin and candidate-only adoption')
        if self.inputSnapshot.spec.projectId != self.projectId:
            raise ValueError('Job and generation snapshot belong to different projects')
        if self.inputFingerprint != self.inputSnapshot.fingerprint():
            raise ValueError('Job input fingerprint differs from its snapshot')
        if self.execution.kind != self.inputSnapshot.spec.kind or self.asset and self.asset.kind != self.execution.kind:
            raise ValueError('Job execution/output kind differs from its inputs')
        if self.status == 'succeeded' and self.asset is None:
            raise ValueError('Successful jobs require an actual output asset')
        if self.errorDetails and self.errorDetails.category == 'submission_unknown':
            if self.recovery != 'review' or self.resumeAllowed or self.status in {'queued', 'running', 'succeeded'}:
                raise ValueError('Unknown submissions require review, never automatic resubmission')
        if self.previousJobId == self.id:
            raise ValueError('A new attempt cannot refer to itself')
        if self.recovery == 'remote' and self.externalTaskId is None:
            raise ValueError('Remote recovery requires the original task ID')
        if self.resumeAllowed and self.recovery not in {'submit', 'remote', 'download', 'local'}:
            raise ValueError('Recovery mode cannot resume execution')
        if self.remoteCancellation != 'not_requested' and not self.cancelRequested:
            raise ValueError('Remote cancellation facts require a cancellation request')
        return self


class ResultVersion(ProjectEntity):
    specId: UUID = Field(description='Generation configuration ID.')
    jobId: UUID = Field(description='Generation job ID.')
    asset: AssetRef = Field(description='Immutable asset version.')
    inputFingerprint: Digest = Field(description='Fingerprint of the fixed execution input.')


def execution_spec(spec, references, rules):
    """Resolve effective video mode from transmitted inputs, preserving intent.

    Use the same resolution when freezing a job and comparing current inputs
    for automatic adoption. Reference layout without media is text-to-video.
    """
    if spec.kind != 'video':
        return spec
    mode = effective_video_mode({
        'mode': spec.parameters.videoMode,
        'allowedModes': rules.videoModes,
        'hasNonTextMedia': any(ref.transmission != 'context_only' and ref.asset.kind != 'text' for ref in references),
        'configured': True,
    })
    if mode == spec.parameters.videoMode:
        return spec
    return spec.model_copy(update={'parameters': spec.parameters.model_copy(update={'videoMode': mode})})


def validate_generation(spec: GenerationSpec, model: ModelConfigPublic, references: tuple[ResolvedReference, ...] = ()):
    """Pure shared validation; it never submits, uploads or changes a project."""
    if spec.modelConfigId != model.id or model.kind != ('agent' if spec.kind == 'text' else spec.kind):
        raise ValueError('所选模型与生成类型不匹配。')
    if model.inputRules.profile == 'vllm-omni-segmentation':
        raise ValueError('视觉分割模型仅用于选区、分割和抠像，请选择生成模型。')
    if model.inputRules.profile == 'vllm-omni-depth':
        raise ValueError('视频深度模型仅用于深度动作捕捉，请选择生成模型。')
    if model.inputRules.profile == 'vllm-omni-score':
        raise ValueError('扒谱模型仅用于参照音乐生成，请选择音乐生成模型。')
    if (spec.promptContent is not None and not any(isinstance(part, PromptReference) or part.text.strip() for part in spec.promptContent)) or (spec.promptContent is None and not spec.prompt.strip()):
        raise ValueError('请填写生成提示词。')
    rules = model.inputRules
    if len(references) > MAX_REFERENCES:
        raise ValueError('参考素材数量超过上限。')
    unique([(ref.referenceId, ref.asset.assetId) for ref in references], 'resolved reference assets')
    if spec.promptContent and any(isinstance(part, PromptReference) and part.referenceId not in {ref.referenceId for ref in references} for part in spec.promptContent):
        raise ValueError('提示词引用了未解析的参考素材。')
    transmitted = [ref for ref in references if ref.role != 'continuation' and ref.transmission != 'context_only']
    if sum(ref.metadata.size for ref in transmitted) > rules.requestBytes:
        raise ValueError('参考素材总大小超过模型请求上限。')
    for ref in transmitted:
        limit = rules.imageBytes if ref.asset.kind == 'image' else rules.videoBytes if ref.asset.kind == 'video' else rules.requestBytes
        if ref.metadata.size > limit:
            raise ValueError('参考文件超过模型的单文件大小限制。')
    kinds = [ref.asset.kind for ref in references if ref.transmission != 'context_only']
    if spec.kind == 'audio' and rules.profile == 'vllm-omni-music':
        # Count connected recordings even where the policy would leave them
        # unsent: a music piece silently ignoring its reference would mislead.
        recordings = sum(ref.asset.kind == 'audio' for ref in references)
        if recordings and 'audio' not in rules.allowedKinds:
            raise ValueError('所选音乐模型不支持参照音乐，请换用 YuE2 或断开参照。')
        if recordings > rules.maxAudios:
            raise ValueError('参照音乐最多连接 1 个音频节点。')
    if any(kind not in rules.allowedKinds for kind in kinds):
        raise ValueError('当前模型不支持这种参考素材类型。')
    for kind, limit in [('image', rules.maxImages), ('video', rules.maxVideos), ('audio', rules.maxAudios)] if spec.kind != 'text' else []:
        count = sum(ref.asset.kind == kind for ref in transmitted)
        if count > limit:
            label = {'image': '图片', 'video': '视频', 'audio': '音频'}[kind]
            raise ValueError(f'{label}参考素材数量超过上限。')
    if spec.kind == 'image':
        from opendirector.domain.image_output import resolve_image_output
        from opendirector.domain.projects import ProjectError
        if spec.parameters.preset != 'none':
            raise ValueError('当前服务不支持产品三视图预设，请使用自由创作。')
        # One stale size must not make the whole canvas unreadable: validation
        # reports a ValueError so the node stays visible but cannot generate.
        try:
            resolve_image_output(model.model_dump(), rules.model_dump(), spec.parameters.model_dump())
        except ProjectError as error:
            raise ValueError(str(error)) from error
    if spec.kind == 'audio' and rules.profile == 'vllm-omni-music':
        if spec.parameters.creationType != 'music':
            raise ValueError('所选模型用于音乐生成，请切换为音乐生成。')
    elif spec.kind == 'audio' and (spec.parameters.creationType != 'audio' or spec.parameters.mode != 'tts'):
        raise ValueError('当前语音服务仅支持文本朗读。')
    if spec.kind == 'audio' and spec.promptContent is not None and not any(part.type == 'text' and part.text.strip() for part in spec.promptContent):
        raise ValueError('语音生成需要实际文本，不能只使用参考标签。')
    if spec.kind != 'video':
        if any(ref.role != 'reference' for ref in references):
            raise ValueError('首尾帧和续写参考只能用于视频生成。')
        return
    params = spec.parameters
    if rules.profile == 'minimax-h3-official':
        if model.modelId not in MINIMAX_MODELS:
            raise ValueError('当前官方协议不支持所选型号。')
        if params.aspectRatio is not None and params.aspectRatio not in rules.aspectRatios:
            raise ValueError('MiniMax-H3 不支持所选画幅比例。')
    audio_options = generate_audio_options({
        'published': None if rules.generateAudioOptions is None else list(rules.generateAudioOptions),
        'protocol': model.protocol,
    })
    if params.generateAudio not in audio_options:
        raise ValueError('当前视频适配器不支持关闭生成音频。')
    if params.videoMode == 'first_frame' and len({ref.referenceId for ref in references if ref.asset.kind == 'image' and ref.subjectRole is None}) > FRAME_IMAGE_LIMIT:
        raise ValueError(f'首尾帧模式最多连接 {FRAME_IMAGE_LIMIT} 个图片节点，请先断开多余连线。')
    if params.videoMode not in rules.videoModes:
        raise ValueError(f"当前模型不支持视频模式 {params.videoMode}；允许值：{', '.join(rules.videoModes)}。")
    if params.videoMode == 'continuation':
        continuation = [ref for ref in references if ref.role == 'continuation']
        if len(continuation) != 1 or continuation[0].asset.kind != 'video' or kinds.count('video') != 1:
            raise ValueError('视频续写需要且只能连接一个原视频。')
        if params.resolution != 'auto':
            raise ValueError('视频续写会沿用原视频尺寸，不能修改分辨率。')
    elif any(ref.role == 'continuation' for ref in references):
        raise ValueError('续写参考必须使用续写模式。')
    if params.videoMode == 'first_frame' and (kinds.count('image') > (FRAME_IMAGE_LIMIT if rules.frameRoles and 'last_frame' in rules.frameRoles else 1) or any(kind in {'audio', 'video'} for kind in kinds)):
        raise ValueError('首帧模式最多只能连接一个图片参考和文本。')
    for role in ('first_frame', 'last_frame'):
        frames = [ref for ref in transmitted if ref.role == role]
        if len(frames) > 1 or any(ref.asset.kind != 'image' for ref in frames):
            raise ValueError('每个首尾帧槽位只能使用一张图片。')
        if role == 'last_frame' and any(ref.role == role for ref in references) and (not rules.frameRoles or role not in rules.frameRoles):
            raise ValueError('当前模型不支持尾帧。')
    if params.videoMode != 'first_frame' and any(ref.role in {'first_frame', 'last_frame'} for ref in references):
        raise ValueError('首帧参考必须使用首帧模式。')
    if kinds.count('audio') and not rules.audioReferenceStandalone and not (kinds.count('image') or kinds.count('video')):
        raise ValueError('音频参考必须同时连接图片或视频参考。')
    for kind in ('audio', 'video'):
        seconds = [ref.metadata.durationSeconds for ref in transmitted if ref.asset.kind == kind]
        minimum = 2 if kind == 'audio' or rules.profile == 'minimax-h3-official' else 0
        if any(value is None or value < minimum or value <= 0 for value in seconds) or sum(seconds) > REFERENCE_MEDIA_SECONDS:
            label = '视频' if kind == 'video' else '音频'
            raise ValueError(f'{label}参考的时长必须已知，且总时长不能超过 {REFERENCE_MEDIA_SECONDS} 秒。')
    if rules.profile == 'fasth3-omni' and sum(kind in {'image', 'video', 'audio'} for kind in kinds) > FASTH3_MEDIA_REFERENCE_LIMIT:
        raise ValueError(f'FastH3 最多支持 {FASTH3_MEDIA_REFERENCE_LIMIT} 个媒体参考。')
    if params.resolution != 'auto':
        choices = rules.referenceImageResolutions if params.videoMode == 'reference' and 'image' in kinds and rules.referenceImageResolutions is not None else rules.outputResolutions
        if params.resolution not in choices:
            raise ValueError('当前参考模式不支持该输出分辨率。')
    caps = rules.videoCapabilities
    if caps and params.durationSeconds is not None:
        choices = [output for output in caps.outputs if params.resolution == 'auto' or output.resolution == params.resolution]
        if not any(params.durationSeconds in output.durations for output in choices):
            raise ValueError('当前时长与分辨率组合不受支持。')
