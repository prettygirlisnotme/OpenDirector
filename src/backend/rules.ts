import { nodeByteSize } from "../canvas/uploadPolicy.ts";
import type { CanvasNode, GenerationSettings } from "../canvas/model.ts";
import {
  RULE_FACTS,
  aspectRatioChoices,
  effectiveVideoMode,
  generateAudioOptions,
  referenceByteLimit,
  resolutionChoices,
} from "../contracts/ruleFacts.generated.ts";
import { subjectReferenceNodes } from "../canvas/subjectReferences.ts";
export { subjectReferenceNodes } from "../canvas/subjectReferences.ts";
import { frameImageLimitIssue } from "../canvas/frameReferences.ts";
import { nodeDownstreamKinds } from "../canvas/connectionPolicy.ts";
import { tNow } from "../i18n/messages.ts";
import type { MessageKey } from "../i18n/types.ts";

/** Model configurations and input rules published by the local API. */
export type ModelKind = "agent" | "image" | "video" | "audio" | "vision";
export type BackendVideoMode = "first_frame" | "reference" | "continuation";
export type VideoAdapter = "auto" | "fasth3" | "minimax_h3" | "standard";
export interface VideoCapabilities {
  outputs: {
    resolution: string;
    durations: number[];
    width?: number;
    height?: number;
  }[];
  defaultResolution: string;
  defaultDuration: number;
  dimensionStep: 1 | 2 | 8 | 16 | 32 | 64;
}

export interface InputRules {
  profile: string;
  allowedKinds: string[];
  maxImages: number;
  imageBytes?: number;
  videoBytes?: number;
  requestBytes?: number;
  maxVideos: number;
  maxAudios: number;
  videoModes: BackendVideoMode[];
  aspectRatios?: string[] | null;
  frameRoles?: ("first_frame" | "last_frame")[] | null;
  audioReferenceStandalone?: boolean | null;
  generateAudioOptions?: boolean[] | null;
  imageOutputs?: { aspectRatio: string; size: string }[];
  outputResolutions?: string[];
  referenceImageResolutions?: string[];
  videoCapabilities?: VideoCapabilities | null;
}

export interface ModelConfig {
  providerId?: string | null;
  id: string;
  kind: ModelKind;
  url: string;
  modelId: string;
  connectionType: "api" | "self_hosted";
  protocol:
    | "openai"
    | "volcengine"
    | "minimax_video_v2"
    | "vllm_omni"
    | "vllm_omni_segmentation"
    | "vllm_omni_music"
    | "vllm_omni_depth"
    | "vllm_omni_score"
    | null;
  authMode: "none" | "bearer" | null;
  proxyMode?: "system" | "direct";
  inputRules: InputRules;
  inputModalities?: ("text" | "image")[] | null;
  videoAdapter?: VideoAdapter;
  videoCapabilities?: VideoCapabilities | null;
}

export interface BackendReference {
  node: CanvasNode;
  role: "reference" | "firstFrame" | "lastFrame" | "continuation";
}

/** SAM configurations serve editing tools, including legacy vision entries. */
export function isSegmentationModel(
  model: Pick<ModelConfig, "kind" | "protocol">,
) {
  return model.kind === "vision" || model.protocol === "vllm_omni_segmentation";
}

/** Video Depth Anything configurations serve the depth capture tool only. */
export function isDepthModel(model: Pick<ModelConfig, "kind" | "protocol">) {
  return model.protocol === "vllm_omni_depth";
}

/** SheetSage2 configurations transcribe reference music into scores only. */
export function isScoreModel(model: Pick<ModelConfig, "kind" | "protocol">) {
  return model.protocol === "vllm_omni_score";
}

/** vLLM-Omni music models read a style description and lyrics instead of text to speak. */
export function isMusicModel(model: Pick<ModelConfig, "kind" | "protocol">) {
  return model.kind === "audio" && model.protocol === "vllm_omni_music";
}

/** Music models count audio frames at 25 per second, between 200 and 9000 frames. */
export const MUSIC_SECONDS = {
  min: RULE_FACTS.musicSecondsMin,
  max: RULE_FACTS.musicSecondsMax,
  initial: RULE_FACTS.musicSecondsInitial,
} as const;

const serviceVoicePattern = new RegExp(RULE_FACTS.serviceVoicePattern);

function isServiceVoice(value: string | undefined) {
  return serviceVoicePattern.test(value ?? "");
}

/** The audio node's role follows its model: a music model composes, any other reads aloud. */
export function serviceAudioSettings(
  settings: GenerationSettings,
  config: ModelConfig,
): GenerationSettings {
  if (settings.mode !== "audio") return settings;
  const music = isMusicModel(config);
  const seconds = settings.durationSeconds;
  return {
    ...settings,
    audioCreationType: music ? "music" : "audio",
    audioMode: "tts",
    ...(music && !(seconds >= MUSIC_SECONDS.min && seconds <= MUSIC_SECONDS.max)
      ? { durationSeconds: MUSIC_SECONDS.initial }
      : {}),
  };
}

/** A music node follows a connected recording once its model accepts audio references. */
export function hasMusicReference(
  settings: GenerationSettings,
  incoming: readonly CanvasNode[],
  config: ModelConfig,
) {
  return (
    settings.mode === "audio" &&
    isMusicModel(config) &&
    backendReferences(settings, incoming, config).some(
      (item) => item.node.data.kind === "audio",
    )
  );
}

export function isGenerationModel(
  model: Pick<ModelConfig, "kind" | "protocol">,
) {
  return (
    !isSegmentationModel(model) && !isDepthModel(model) && !isScoreModel(model)
  );
}

const LABELS: Record<string, MessageKey> = {
  image: "errors.rules.kindImage",
  video: "errors.rules.kindVideo",
  audio: "errors.rules.kindAudio",
  text: "errors.rules.kindText",
  subject: "errors.rules.kindSubject",
};

/** Speaker names a speech service accepts; others fall back to its default voice. */
export function serviceVoice(settings: Pick<GenerationSettings, "voiceName">) {
  return isServiceVoice(settings.voiceName) ? settings.voiceName : undefined;
}

export function modelLabel(config: Pick<ModelConfig, "modelId">) {
  return config.modelId.split("/").at(-1) || config.modelId;
}

export function backendModel(
  settings: Pick<GenerationSettings, "modelConfigId">,
  models: readonly ModelConfig[],
) {
  return settings.modelConfigId
    ? models.find((model) => model.id === settings.modelConfigId)
    : undefined;
}

export function backendReferenceIssue(
  node: CanvasNode,
  settings: GenerationSettings,
  config: ModelConfig,
): string | null {
  const kind = node.data.kind;
  const rules = config.inputRules;
  if (
    settings.mode === "video" &&
    settings.videoMode === "first-last" &&
    ["video", "audio"].includes(kind)
  )
    return tNow("errors.rules.firstLastReference");
  if (kind === "subject") {
    if (!nodeDownstreamKinds(node).includes(settings.mode))
      return tNow("errors.rules.videoSubject");
    if (settings.mode === "video" && settings.videoMode === "first-last")
      return tNow("errors.rules.firstLastImage");
    const resources = subjectReferenceNodes(node);
    if (!resources.length) return tNow("errors.rules.subjectEmpty");
    return resources.some(
      (resource) =>
        !(resource.type === "video" && settings.videoMode === "continuation") &&
        !backendReferenceIssue(resource, settings, config),
    )
      ? null
      : tNow("errors.rules.subjectReference");
  }
  if (!["image", "video", "audio", "text"].includes(kind))
    return tNow("errors.rules.nodeKind");
  if (kind === "text")
    return rules.allowedKinds.includes("text")
      ? node.data.text?.trim()
        ? null
        : tNow("errors.rules.textEmpty")
      : tNow("errors.rules.textReference");
  const continuation =
    settings.mode === "video" && settings.videoMode === "continuation";
  if (kind === "video" && !continuation && !rules.maxVideos)
    return rules.videoModes.includes("continuation")
      ? tNow("errors.rules.videoContinuation")
      : tNow("errors.rules.videoReference");
  if (!rules.allowedKinds.includes(kind))
    return tNow("errors.rules.kindReference", {
      kind: tNow(LABELS[kind] ?? "errors.rules.kindImage"),
    });
  return node.data.src && node.data.assetState !== "missing"
    ? null
    : tNow("errors.rules.mediaMissing");
}

/** First-frame mode submits only the chosen frame; other modes use every usable input. */
export function backendReferences(
  settings: GenerationSettings,
  incoming: readonly CanvasNode[],
  config: ModelConfig,
): BackendReference[] {
  const usable = incoming
    .filter((node) => !backendReferenceIssue(node, settings, config))
    .flatMap((node) =>
      node.type === "subject"
        ? subjectReferenceNodes(node).filter(
            (resource) =>
              !(
                resource.type === "video" &&
                settings.videoMode === "continuation"
              ) && !backendReferenceIssue(resource, settings, config),
          )
        : [node],
    );
  if (settings.mode === "video" && settings.videoMode === "first-last") {
    const frame = usable.find(
      (node) =>
        node.id === settings.firstFrameNodeId && node.data.kind === "image",
    );
    return [
      ...(frame ? [{ node: frame, role: "firstFrame" as const }] : []),
      ...(config.inputRules.frameRoles?.includes("last_frame")
        ? usable
            .filter(
              (node) =>
                node.id === settings.lastFrameNodeId &&
                node.data.kind === "image",
            )
            .map((node) => ({ node, role: "lastFrame" as const }))
        : []),
      ...usable
        .filter((node) => node.data.kind === "text")
        .map((node) => ({ node, role: "reference" as const })),
    ];
  }
  return usable.map((node) => ({
    node,
    role:
      settings.videoMode === "continuation" && node.data.kind === "video"
        ? "continuation"
        : "reference",
  }));
}

/** Canvas slot assignment for a saved reference. The backend stores this role. */
export function canvasReferenceRole(
  settings: Pick<
    GenerationSettings,
    "videoMode" | "firstFrameNodeId" | "lastFrameNodeId"
  > | undefined,
  sourceKind: string | undefined,
  ref: {
    binding: string;
    role?: string | null;
    source: { id?: string } | { assetId: string };
  },
): "reference" | "first_frame" | "last_frame" | "continuation" {
  const sourceId = "id" in ref.source ? ref.source.id : undefined;
  if (settings?.videoMode === "continuation" && sourceKind === "video") {
    return "continuation";
  }
  if (
    settings?.videoMode === "first-last" &&
    ((ref.binding === "follow_current" &&
      sourceId === settings.firstFrameNodeId) ||
      (ref.binding === "pinned" &&
        ref.role === "first_frame" &&
        !settings.firstFrameNodeId))
  ) {
    return "first_frame";
  }
  if (
    settings?.videoMode === "first-last" &&
    ((ref.binding === "follow_current" &&
      sourceId === settings.lastFrameNodeId) ||
      (ref.binding === "pinned" &&
        ref.role === "last_frame" &&
        !settings.lastFrameNodeId))
  ) {
    return "last_frame";
  }
  return "reference";
}

export function backendVideoMode(
  settings: GenerationSettings,
  references: readonly BackendReference[],
  config?: ModelConfig,
): BackendVideoMode {
  // Without media inputs a reference request is plain text-to-video.
  return effectiveVideoMode({
    mode: settings.videoMode,
    allowedModes: config?.inputRules.videoModes ?? [],
    hasNonTextMedia: references.some((item) => item.node.data.kind !== "text"),
    configured: config != null,
  });
}

/** The backend publishes adapter capabilities; unknown services retain automatic sizing. */
export function backendResolutions(
  settings: Pick<GenerationSettings, "mode" | "videoMode"> &
    Partial<Pick<GenerationSettings, "durationSeconds" | "aspectRatio">>,
  config: ModelConfig | undefined,
  incoming: readonly CanvasNode[] = [],
): string[] {
  const rules = config?.inputRules;
  return resolutionChoices({
    kind: settings.mode,
    videoMode: settings.videoMode,
    aspectRatio: settings.aspectRatio,
    imageOutputs: rules?.imageOutputs ?? [],
    hasImageReference: incoming.some(
      (node) =>
        node.data.kind === "image" ||
        subjectReferenceNodes(node).some(
          (resource) => resource.type === "image",
        ),
    ),
    outputResolutions: rules?.outputResolutions ?? null,
    referenceImageResolutions: rules?.referenceImageResolutions ?? null,
    durationSeconds: settings.durationSeconds ?? null,
    capabilityOutputs: rules?.videoCapabilities?.outputs ?? null,
  });
}

export function backendDurations(config: ModelConfig | undefined): number[] {
  return [
    ...new Set(
      config?.inputRules.videoCapabilities?.outputs.flatMap(
        (item) => item.durations,
      ) ?? [],
    ),
  ].sort((a, b) => a - b);
}

export function backendAspectRatios(
  settings: Pick<GenerationSettings, "mode" | "videoMode">,
  config?: ModelConfig,
): string[] {
  return aspectRatioChoices({
    kind: settings.mode,
    videoMode: settings.videoMode,
    publishedRatios: config?.inputRules.aspectRatios ?? null,
    protocol: config?.protocol ?? null,
    dimensioned: Boolean(
      config?.inputRules.videoCapabilities?.outputs.some(
        (item) => item.width && item.height,
      ),
    ),
    imageOutputRatios: (config?.inputRules.imageOutputs ?? []).map(
      (item) => item.aspectRatio,
    ),
  });
}

export function serviceGenerationSettings(
  settings: GenerationSettings,
  config: ModelConfig,
  incoming: readonly CanvasNode[] = [],
  modelChanged = false,
): GenerationSettings {
  let next =
    settings.mode === "video"
      ? serviceVideoSettings(settings, config, incoming, modelChanged)
      : { ...settings };
  if (next.mode !== "audio") {
    const ratios = backendAspectRatios(next, config);
    if (!ratios.includes(next.aspectRatio)) next.aspectRatio = "auto";
    const resolutions = backendResolutions(next, config, incoming);
    if (
      (modelChanged && next.mode === "image") ||
      !resolutions.includes(next.resolution)
    )
      next.resolution = "auto";
  }
  next = serviceAudioSettings(next, config);
  if (modelChanged) {
    next.imagePreset = "none";
    if (!isServiceVoice(next.voiceName)) next.voiceName = undefined;
    if (
      !generateAudioOptions({
        published: config.inputRules.generateAudioOptions ?? null,
        protocol: config.protocol,
      }).includes(false)
    )
      next.audioEnabled = true;
  }
  return next;
}

/** Normalize a deliberate model/time change before persisting it to the canvas. */
export function serviceVideoSettings(
  settings: GenerationSettings,
  config: ModelConfig,
  incoming: readonly CanvasNode[] = [],
  modelChanged = false,
): GenerationSettings {
  const next = { ...settings };
  const caps = config.inputRules.videoCapabilities;
  const durations = backendDurations(config);
  if (durations.length && !durations.includes(next.durationSeconds))
    next.durationSeconds = caps!.defaultDuration;
  if (
    next.videoMode === "continuation" &&
    !config.inputRules.videoModes.includes("continuation")
  )
    next.videoMode = "reference";
  const resolutions = backendResolutions(next, config, incoming);
  if (modelChanged || !resolutions.includes(next.resolution)) {
    const preferred =
      caps?.defaultResolution === "auto" ? "auto" : caps?.defaultResolution;
    next.resolution =
      !modelChanged && resolutions.includes(next.resolution)
        ? next.resolution
        : preferred && resolutions.includes(preferred)
          ? preferred
          : "auto";
  }
  return next;
}

export function backendBlocker(
  settings: GenerationSettings,
  incoming: readonly CanvasNode[],
  models: readonly ModelConfig[],
): string | null {
  const frameLimit = frameImageLimitIssue(settings, incoming);
  if (frameLimit) return frameLimit;
  const config = backendModel(settings, models);
  if (!config) return tNow("errors.rules.modelMissing");
  if (config.kind !== settings.mode)
    return tNow("errors.rules.modelKind");
  if (isDepthModel(config))
    return tNow("errors.rules.depthModel");
  if (isScoreModel(config))
    return tNow("errors.rules.transcriptionModel");
  if (!isGenerationModel(config))
    return tNow("errors.rules.segmentationModel");
  if (!settings.prompt.trim())
    return settings.mode !== "audio"
      ? tNow("errors.rules.promptRequired")
      : isMusicModel(config)
        ? tNow("errors.rules.musicStyle")
        : tNow("errors.rules.speechText");
  const rules = config.inputRules;
  if (settings.mode === "image") {
    if (settings.imagePreset && settings.imagePreset !== "none")
      return tNow("errors.rules.productViews");
    if (
      !backendAspectRatios(settings, config).includes(settings.aspectRatio) ||
      !backendResolutions(settings, config).includes(settings.resolution)
    )
      return tNow("errors.rules.imageSize");
  }
  if (settings.mode === "audio" && isMusicModel(config)) {
    if (settings.audioCreationType !== "music")
      return tNow("errors.rules.musicModel");
    if (
      !(
        settings.durationSeconds >= MUSIC_SECONDS.min &&
        settings.durationSeconds <= MUSIC_SECONDS.max
      )
    )
      return tNow("errors.rules.musicDuration", {
        min: MUSIC_SECONDS.min,
        max: MUSIC_SECONDS.max,
      });
    if ((settings.lyrics ?? "").length > RULE_FACTS.lyricsMaxLength)
      return tNow("errors.rules.lyricsLimit");
    if (
      !config.inputRules.allowedKinds.includes("audio") &&
      incoming.some((node) => node.data.kind === "audio")
    )
      return tNow("errors.rules.referenceMusic");
    if (
      hasMusicReference(settings, incoming, config) &&
      !models.some(isScoreModel)
    )
      return tNow("errors.rules.transcriptionRequired");
  } else if (
    settings.mode === "audio" &&
    ((settings.audioCreationType && settings.audioCreationType !== "audio") ||
      (settings.audioMode && settings.audioMode !== "tts"))
  )
    return tNow("errors.rules.speechOnly");
  if (
    settings.mode === "audio" &&
    settings.voiceName &&
    !isServiceVoice(settings.voiceName)
  )
    return tNow("errors.rules.voiceId");
  if (
    settings.mode === "video" &&
    settings.audioEnabled === false &&
    !generateAudioOptions({
      published: rules.generateAudioOptions ?? null,
      protocol: config.protocol,
    }).includes(false)
  )
    return tNow("errors.rules.audioDisable");
  const references = backendReferences(settings, incoming, config);
  if (settings.mode === "video") {
    const durations = backendDurations(config);
    if (durations.length && !durations.includes(settings.durationSeconds))
      return tNow("errors.rules.duration");
    if (
      rules.outputResolutions &&
      !backendResolutions(settings, config, incoming).includes(
        settings.resolution,
      )
    )
      return tNow("errors.rules.resolution");
    if (
      !rules.videoModes.includes(backendVideoMode(settings, references, config))
    )
      return tNow("errors.rules.videoMode");
    if (settings.videoMode === "first-last") {
      if (settings.lastFrameNodeId && !rules.frameRoles?.includes("last_frame"))
        return tNow("errors.rules.lastFrame");
      if (
        settings.lastFrameNodeId &&
        !references.some((item) => item.role === "lastFrame")
      )
        return tNow("errors.rules.lastFrameMissing");
      if (
        settings.firstFrameNodeId &&
        !references.some((item) => item.role === "firstFrame")
      )
        return tNow("errors.rules.firstFrameMissing");
    }
    if (
      settings.videoMode === "continuation" &&
      references.filter((item) => item.role === "continuation").length !== 1
    )
      return tNow("errors.rules.continuationSource");
  }
  const count = (kind: string) =>
    references.filter(
      (item) => item.role !== "continuation" && item.node.data.kind === kind,
    ).length;
  for (const [kind, limit] of [
    ["image", rules.maxImages],
    ["video", rules.maxVideos],
    ["audio", rules.maxAudios],
  ] as const)
    if (count(kind) > limit)
      return tNow("errors.rules.referenceLimit", {
        kind: tNow(LABELS[kind] ?? "errors.rules.kindImage"),
        limit,
      });
  return null;
}

/** The same budget applies to following nodes, pinned assets and expanded subjects. */
export function referenceBudgetIssue(
  sources: readonly CanvasNode[],
  config: ModelConfig,
  settings?: GenerationSettings,
) {
  const usable = settings
    ? backendReferences(settings, sources, config)
        .filter((ref) => ref.role !== "continuation")
        .map((ref) => ref.node)
    : sources.flatMap((node) =>
        node.type === "subject" ? subjectReferenceNodes(node) : [node],
      );
  let total = 0;
  const seen = new Set<string>();
  for (const source of usable) {
    const key = `${source.id}:${source.type}:${source.data.src ?? source.data.text ?? ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const size = nodeByteSize(source);
    const limit = referenceByteLimit({
      kind: source.type,
      imageBytes: config.inputRules.imageBytes,
      videoBytes: config.inputRules.videoBytes,
    });
    if (size > limit) return tNow("errors.rules.referenceFileSize");
    total += size;
  }
  if (total > (config.inputRules.requestBytes ?? Infinity))
    return tNow("errors.rules.referenceBudget");
  for (const [kind, maximum] of [
    ["image", config.inputRules.maxImages],
    ["video", config.inputRules.maxVideos],
    ["audio", config.inputRules.maxAudios],
  ] as const)
    if (
      !(config.kind === "agent" && kind !== "image") &&
      usable.filter((source) => source.type === kind).length > maximum
    )
      return tNow("errors.rules.referenceCount");
  return null;
}
