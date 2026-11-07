"""Shared creation-rule facts and the menu derivations the browser evaluates.

`scripts/generate_contracts.py` writes `src/contracts/ruleFacts.generated.ts`
from this module. Edit the facts and the Python functions here, keep the
TypeScript twins in `typescript_module` aligned, then regenerate. The checked-in
vectors are produced by these functions; the generated TypeScript is rejected
when it disagrees with them.

Browser-only checks are described in `docs/architecture/unified-project.md`.
"""
import json

# Storage/structure guard. Provider budgets are checked separately.
MAX_REFERENCES = 64
TEXT_REFERENCE_BYTES = 64 * 1024
# Chat and agent image references, and the browser fallback when a payload
# omits imageBytes. Adapter profiles publish their own imageBytes.
AGENT_IMAGE_BYTES = 32 * 1024 * 1024
DEFAULT_VIDEO_BYTES = 50 * 1024 * 1024
FRAME_IMAGE_LIMIT = 2
PROMPT_MAX_LENGTH = 20000
TEXT_MAX_TOKENS = 32768
TEXT_DEFAULT_MAX_TOKENS = 8192
LYRICS_MAX_LENGTH = 5000
# Music models count audio frames at 25 per second, between 200 and 9000 frames.
MUSIC_SECONDS_MIN = 8
MUSIC_SECONDS_MAX = 360
MUSIC_SECONDS_INITIAL = 30
REFERENCE_MEDIA_SECONDS = 15
FASTH3_MEDIA_REFERENCE_LIMIT = 12
# Identifiers sent to a speech adapter. Stored specs may also keep a Unicode
# display name; that contract pattern is STORED_VOICE_PATTERN and is not a
# JavaScript \\w check.
SERVICE_VOICE_PATTERN = r'^[A-Za-z0-9_.-]{1,64}$'
STORED_VOICE_PATTERN = r'^[\w.-]{1,64}$'
VIDEO_ASPECT_RATIOS = (
    '21:9', '16:9', '3:2', '4:3', '1:1', '3:4', '2:3', '9:16', '5:4', '4:5', '2:1',
)


def facts_payload():
    return {
        'maxReferences': MAX_REFERENCES,
        'textReferenceBytes': TEXT_REFERENCE_BYTES,
        'agentImageBytes': AGENT_IMAGE_BYTES,
        'defaultVideoBytes': DEFAULT_VIDEO_BYTES,
        'frameImageLimit': FRAME_IMAGE_LIMIT,
        'promptMaxLength': PROMPT_MAX_LENGTH,
        'textMaxTokens': TEXT_MAX_TOKENS,
        'textDefaultMaxTokens': TEXT_DEFAULT_MAX_TOKENS,
        'lyricsMaxLength': LYRICS_MAX_LENGTH,
        'musicSecondsMin': MUSIC_SECONDS_MIN,
        'musicSecondsMax': MUSIC_SECONDS_MAX,
        'musicSecondsInitial': MUSIC_SECONDS_INITIAL,
        'referenceMediaSeconds': REFERENCE_MEDIA_SECONDS,
        'fasth3MediaReferenceLimit': FASTH3_MEDIA_REFERENCE_LIMIT,
        'serviceVoicePattern': SERVICE_VOICE_PATTERN,
        'storedVoicePattern': STORED_VOICE_PATTERN,
        'videoAspectRatios': list(VIDEO_ASPECT_RATIOS),
    }


def _unique(values):
    return list(dict.fromkeys(values))


def aspect_ratio_choices(spec):
    """Menus offered before submit. Published ratios win; the catalog is the fallback."""
    kind = spec['kind']
    video_mode = spec.get('videoMode')
    published = spec.get('publishedRatios')
    if kind == 'image':
        return ['auto', *_unique(ratio for ratio in spec.get('imageOutputRatios') or [] if ratio != 'auto')]
    if kind == 'video' and published is not None:
        if video_mode == 'first-last':
            return ['auto']
        return ['auto', *list(published)]
    framed = spec.get('protocol') == 'volcengine' or bool(spec.get('dimensioned'))
    if kind == 'video' and video_mode != 'continuation' and framed:
        return ['auto', *VIDEO_ASPECT_RATIOS]
    return ['auto']


def resolution_choices(spec):
    """Sizes offered before submit. Callers report whether an image reference is connected."""
    kind = spec['kind']
    video_mode = spec.get('videoMode')
    if kind == 'image':
        aspect = spec.get('aspectRatio')
        sizes = [item['size'] for item in spec.get('imageOutputs') or []
                 if not aspect or aspect == 'auto' or item['aspectRatio'] == aspect]
        return ['auto', *_unique(sizes)]
    if kind != 'video' or video_mode == 'continuation':
        return ['auto']
    reference_resolutions = spec.get('referenceImageResolutions')
    if video_mode == 'reference' and spec.get('hasImageReference') and reference_resolutions is not None:
        choices = list(reference_resolutions)
    else:
        choices = list(spec.get('outputResolutions') or [])
    outputs = spec.get('capabilityOutputs')
    duration = spec.get('durationSeconds')
    if outputs is not None and duration is not None:
        choices = [label for label in choices
                   if any(item['resolution'] == label and duration in item['durations'] for item in outputs)]
    return ['auto', *choices]


def effective_video_mode(spec):
    """Backend video mode for a canvas mode and the media that would actually be sent.

    Reference layout with no non-text media is text-to-video (`first_frame`) when
    the model allows it. The same function freezes a job and fills the menu.
    """
    mode = spec.get('mode')
    if mode == 'continuation':
        return 'continuation'
    if mode in {'first-last', 'first_frame'}:
        return 'first_frame'
    if spec.get('configured') and 'first_frame' not in (spec.get('allowedModes') or []):
        return 'reference'
    return 'reference' if spec.get('hasNonTextMedia') else 'first_frame'


def generate_audio_options(spec):
    """Allowed generateAudio values. A published list wins, including an empty one."""
    published = spec.get('published')
    if published is not None:
        return list(published)
    return [True, False] if spec.get('protocol') == 'volcengine' else [True]


def reference_byte_limit(spec):
    """Per-file budget. Text uses the shared cap; image and video fall back when omitted."""
    kind = spec['kind']
    if kind == 'text':
        return TEXT_REFERENCE_BYTES
    if kind == 'image':
        return AGENT_IMAGE_BYTES if spec.get('imageBytes') is None else spec['imageBytes']
    if kind == 'video':
        return DEFAULT_VIDEO_BYTES if spec.get('videoBytes') is None else spec['videoBytes']
    return DEFAULT_VIDEO_BYTES


DERIVATIONS = {
    'aspectRatioChoices': aspect_ratio_choices,
    'resolutionChoices': resolution_choices,
    'effectiveVideoMode': effective_video_mode,
    'generateAudioOptions': generate_audio_options,
    'referenceByteLimit': reference_byte_limit,
}

_CASES = (
    ('image-ratios-drop-auto-and-duplicates', 'aspectRatioChoices', {
        'kind': 'image', 'videoMode': None, 'publishedRatios': None, 'protocol': None,
        'dimensioned': False, 'imageOutputRatios': ['16:9', 'auto', '16:9', '1:1']}),
    ('video-published-first-last-is-auto-only', 'aspectRatioChoices', {
        'kind': 'video', 'videoMode': 'first-last', 'publishedRatios': ['9:16', '16:9'],
        'protocol': 'minimax_video_v2', 'dimensioned': False, 'imageOutputRatios': []}),
    ('video-published-ratios-keep-order', 'aspectRatioChoices', {
        'kind': 'video', 'videoMode': 'reference', 'publishedRatios': ['9:16', '9:16'],
        'protocol': None, 'dimensioned': False, 'imageOutputRatios': []}),
    ('video-empty-published-ratios-are-present', 'aspectRatioChoices', {
        'kind': 'video', 'videoMode': 'reference', 'publishedRatios': [],
        'protocol': 'volcengine', 'dimensioned': True, 'imageOutputRatios': []}),
    ('volcengine-uses-catalog', 'aspectRatioChoices', {
        'kind': 'video', 'videoMode': 'reference', 'publishedRatios': None,
        'protocol': 'volcengine', 'dimensioned': False, 'imageOutputRatios': []}),
    ('dimensioned-outputs-use-catalog', 'aspectRatioChoices', {
        'kind': 'video', 'videoMode': 'reference', 'publishedRatios': None,
        'protocol': 'openai', 'dimensioned': True, 'imageOutputRatios': []}),
    ('continuation-without-published-ratios-is-auto', 'aspectRatioChoices', {
        'kind': 'video', 'videoMode': 'continuation', 'publishedRatios': None,
        'protocol': 'openai', 'dimensioned': True, 'imageOutputRatios': []}),
    ('unframed-video-is-auto', 'aspectRatioChoices', {
        'kind': 'video', 'videoMode': 'reference', 'publishedRatios': None,
        'protocol': 'openai', 'dimensioned': False, 'imageOutputRatios': []}),
    ('audio-ratios-are-auto', 'aspectRatioChoices', {
        'kind': 'audio', 'videoMode': None, 'publishedRatios': None, 'protocol': None,
        'dimensioned': False, 'imageOutputRatios': []}),
    ('image-sizes-follow-aspect-and-dedupe', 'resolutionChoices', {
        'kind': 'image', 'videoMode': None, 'aspectRatio': '1:1',
        'imageOutputs': [
            {'aspectRatio': '1:1', 'size': '1024x1024'},
            {'aspectRatio': '1:1', 'size': '1024x1024'},
            {'aspectRatio': '3:2', 'size': '1536x1024'}],
        'hasImageReference': False, 'outputResolutions': None, 'referenceImageResolutions': None,
        'durationSeconds': None, 'capabilityOutputs': None}),
    ('image-sizes-keep-every-size-for-auto', 'resolutionChoices', {
        'kind': 'image', 'videoMode': None, 'aspectRatio': None,
        'imageOutputs': [
            {'aspectRatio': '1:1', 'size': '1024x1024'},
            {'aspectRatio': '3:2', 'size': '1536x1024'}],
        'hasImageReference': False, 'outputResolutions': None, 'referenceImageResolutions': None,
        'durationSeconds': None, 'capabilityOutputs': None}),
    ('continuation-resolution-is-auto', 'resolutionChoices', {
        'kind': 'video', 'videoMode': 'continuation', 'aspectRatio': '16:9', 'imageOutputs': [],
        'hasImageReference': True, 'outputResolutions': ['720P'], 'referenceImageResolutions': ['480P'],
        'durationSeconds': 5, 'capabilityOutputs': None}),
    ('audio-resolution-is-auto', 'resolutionChoices', {
        'kind': 'audio', 'videoMode': None, 'aspectRatio': None, 'imageOutputs': [],
        'hasImageReference': False, 'outputResolutions': ['720P'], 'referenceImageResolutions': None,
        'durationSeconds': None, 'capabilityOutputs': None}),
    ('reference-image-uses-reference-resolutions', 'resolutionChoices', {
        'kind': 'video', 'videoMode': 'reference', 'aspectRatio': 'auto', 'imageOutputs': [],
        'hasImageReference': True, 'outputResolutions': ['720P'], 'referenceImageResolutions': ['480P'],
        'durationSeconds': None, 'capabilityOutputs': None}),
    ('missing-reference-resolutions-fall-back', 'resolutionChoices', {
        'kind': 'video', 'videoMode': 'reference', 'aspectRatio': 'auto', 'imageOutputs': [],
        'hasImageReference': True, 'outputResolutions': ['720P', '1080P'], 'referenceImageResolutions': None,
        'durationSeconds': None, 'capabilityOutputs': None}),
    ('output-resolutions-when-no-image-reference', 'resolutionChoices', {
        'kind': 'video', 'videoMode': 'reference', 'aspectRatio': 'auto', 'imageOutputs': [],
        'hasImageReference': False, 'outputResolutions': ['720P'], 'referenceImageResolutions': ['480P'],
        'durationSeconds': None, 'capabilityOutputs': None}),
    ('duration-filters-labels', 'resolutionChoices', {
        'kind': 'video', 'videoMode': 'first-last', 'aspectRatio': 'auto', 'imageOutputs': [],
        'hasImageReference': False, 'outputResolutions': ['720P', '1080P'], 'referenceImageResolutions': None,
        'durationSeconds': 5, 'capabilityOutputs': [
            {'resolution': '720P', 'durations': [4, 5]},
            {'resolution': '1080P', 'durations': [4]}]}),
    ('unset-duration-keeps-labels', 'resolutionChoices', {
        'kind': 'video', 'videoMode': 'first-last', 'aspectRatio': 'auto', 'imageOutputs': [],
        'hasImageReference': False, 'outputResolutions': ['720P', '1080P'], 'referenceImageResolutions': None,
        'durationSeconds': None, 'capabilityOutputs': [
            {'resolution': '720P', 'durations': [4, 5]},
            {'resolution': '1080P', 'durations': [4]}]}),
    ('missing-capabilities-keep-labels', 'resolutionChoices', {
        'kind': 'video', 'videoMode': 'reference', 'aspectRatio': 'auto', 'imageOutputs': [],
        'hasImageReference': False, 'outputResolutions': ['720P'], 'referenceImageResolutions': None,
        'durationSeconds': 5, 'capabilityOutputs': None}),
    ('empty-capabilities-drop-labels', 'resolutionChoices', {
        'kind': 'video', 'videoMode': 'reference', 'aspectRatio': 'auto', 'imageOutputs': [],
        'hasImageReference': False, 'outputResolutions': ['720P'], 'referenceImageResolutions': None,
        'durationSeconds': 5, 'capabilityOutputs': []}),
    ('continuation-mode', 'effectiveVideoMode', {
        'mode': 'continuation', 'allowedModes': ['first_frame'], 'hasNonTextMedia': False, 'configured': True}),
    ('first-last-mode', 'effectiveVideoMode', {
        'mode': 'first-last', 'allowedModes': ['reference'], 'hasNonTextMedia': True, 'configured': True}),
    ('stored-first-frame-mode', 'effectiveVideoMode', {
        'mode': 'first_frame', 'allowedModes': ['first_frame', 'reference'], 'hasNonTextMedia': False, 'configured': True}),
    ('reference-without-first-frame-support', 'effectiveVideoMode', {
        'mode': 'reference', 'allowedModes': ['reference'], 'hasNonTextMedia': False, 'configured': True}),
    ('text-only-reference-becomes-first-frame', 'effectiveVideoMode', {
        'mode': 'reference', 'allowedModes': ['first_frame', 'reference'], 'hasNonTextMedia': False, 'configured': True}),
    ('media-reference-stays-reference', 'effectiveVideoMode', {
        'mode': 'reference', 'allowedModes': ['first_frame', 'reference'], 'hasNonTextMedia': True, 'configured': True}),
    ('unconfigured-text-only-becomes-first-frame', 'effectiveVideoMode', {
        'mode': 'reference', 'allowedModes': [], 'hasNonTextMedia': False, 'configured': False}),
    ('published-audio-options-win', 'generateAudioOptions', {
        'published': [True], 'protocol': 'volcengine'}),
    ('volcengine-audio-fallback', 'generateAudioOptions', {
        'published': None, 'protocol': 'volcengine'}),
    ('default-audio-is-on-only', 'generateAudioOptions', {
        'published': None, 'protocol': 'openai'}),
    ('empty-published-audio-options-stay-empty', 'generateAudioOptions', {
        'published': [], 'protocol': 'volcengine'}),
    ('text-byte-limit', 'referenceByteLimit', {
        'kind': 'text', 'imageBytes': 1, 'videoBytes': 1}),
    ('image-byte-fallback', 'referenceByteLimit', {
        'kind': 'image', 'imageBytes': None, 'videoBytes': None}),
    ('explicit-image-bytes', 'referenceByteLimit', {
        'kind': 'image', 'imageBytes': 10, 'videoBytes': None}),
    ('video-byte-fallback', 'referenceByteLimit', {
        'kind': 'video', 'imageBytes': None, 'videoBytes': None}),
    ('other-kinds-use-video-fallback', 'referenceByteLimit', {
        'kind': 'audio', 'imageBytes': None, 'videoBytes': 3}),
)


def derivation_vectors():
    return [
        {'name': name, 'fn': fn, 'input': payload, 'expected': DERIVATIONS[fn](payload)}
        for name, fn, payload in _CASES
    ]


_TYPESCRIPT = """// Generated by scripts/generate_contracts.py. Do not edit.
// Source of truth: opendirector.domain.rule_facts.
// Shared limits and menu derivations. Do not copy them into frontend modules.
// storedVoicePattern is the Python contract (\\\\w is Unicode there). The speech
// pre-check uses serviceVoicePattern and must not compile storedVoicePattern.

export const RULE_FACTS = __FACTS__ as const;

function unique(values: readonly string[]): string[] {
  return [...new Set(values)];
}

export function aspectRatioChoices(input: {
  readonly kind: string;
  readonly videoMode?: string | null;
  readonly publishedRatios: readonly string[] | null;
  readonly protocol?: string | null;
  readonly dimensioned: boolean;
  readonly imageOutputRatios: readonly string[];
}): string[] {
  if (input.kind === "image") {
    return [
      "auto",
      ...unique(input.imageOutputRatios.filter((ratio) => ratio !== "auto")),
    ];
  }
  if (input.kind === "video" && input.publishedRatios != null) {
    if (input.videoMode === "first-last") return ["auto"];
    return ["auto", ...input.publishedRatios];
  }
  const framed = input.protocol === "volcengine" || input.dimensioned;
  if (
    input.kind === "video" &&
    input.videoMode !== "continuation" &&
    framed
  ) {
    return ["auto", ...RULE_FACTS.videoAspectRatios];
  }
  return ["auto"];
}

export function resolutionChoices(input: {
  readonly kind: string;
  readonly videoMode?: string | null;
  readonly aspectRatio?: string | null;
  readonly imageOutputs: readonly {
    readonly aspectRatio: string;
    readonly size: string;
  }[];
  readonly hasImageReference: boolean;
  readonly outputResolutions: readonly string[] | null;
  readonly referenceImageResolutions: readonly string[] | null;
  readonly durationSeconds?: number | null;
  readonly capabilityOutputs: readonly {
    readonly resolution: string;
    readonly durations: readonly number[];
  }[] | null;
}): string[] {
  if (input.kind === "image") {
    const sizes = input.imageOutputs
      .filter(
        (item) =>
          !input.aspectRatio ||
          input.aspectRatio === "auto" ||
          item.aspectRatio === input.aspectRatio,
      )
      .map((item) => item.size);
    return ["auto", ...unique(sizes)];
  }
  if (input.kind !== "video" || input.videoMode === "continuation") {
    return ["auto"];
  }
  const choices =
    input.videoMode === "reference" &&
    input.hasImageReference &&
    input.referenceImageResolutions != null
      ? [...input.referenceImageResolutions]
      : [...(input.outputResolutions ?? [])];
  const outputs = input.capabilityOutputs;
  const duration = input.durationSeconds;
  const filtered =
    outputs != null && duration != null
      ? choices.filter((label) =>
          outputs.some(
            (item) =>
              item.resolution === label && item.durations.includes(duration),
          ),
        )
      : choices;
  return ["auto", ...filtered];
}

export function effectiveVideoMode(input: {
  readonly mode?: string | null;
  readonly allowedModes: readonly string[];
  readonly hasNonTextMedia: boolean;
  readonly configured: boolean;
}): "continuation" | "first_frame" | "reference" {
  if (input.mode === "continuation") return "continuation";
  if (input.mode === "first-last" || input.mode === "first_frame") {
    return "first_frame";
  }
  if (input.configured && !input.allowedModes.includes("first_frame")) {
    return "reference";
  }
  return input.hasNonTextMedia ? "reference" : "first_frame";
}

export function generateAudioOptions(input: {
  readonly published: readonly boolean[] | null;
  readonly protocol?: string | null;
}): boolean[] {
  if (input.published != null) return [...input.published];
  return input.protocol === "volcengine" ? [true, false] : [true];
}

export function referenceByteLimit(input: {
  readonly kind: string;
  readonly imageBytes?: number | null;
  readonly videoBytes?: number | null;
}): number {
  if (input.kind === "text") return RULE_FACTS.textReferenceBytes;
  if (input.kind === "image") {
    return input.imageBytes ?? RULE_FACTS.agentImageBytes;
  }
  if (input.kind === "video") {
    return input.videoBytes ?? RULE_FACTS.defaultVideoBytes;
  }
  return RULE_FACTS.defaultVideoBytes;
}

export const RULE_DERIVATION_VECTORS = __VECTORS__ as const;
"""


def typescript_module():
    facts = json.dumps(facts_payload(), ensure_ascii=False, indent=2)
    vectors = json.dumps(derivation_vectors(), ensure_ascii=False, indent=2)
    return _TYPESCRIPT.replace('__FACTS__', facts).replace('__VECTORS__', vectors)
