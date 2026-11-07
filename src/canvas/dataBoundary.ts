import { RULE_FACTS } from "../contracts/ruleFacts.generated.ts";
import { parseImageCrop } from "./imageCrop.ts";
import { tNow } from "../i18n/messages.ts";
import { parseCanvasToolTaskValue, sameCanvasToolTask } from "./toolCatalog.ts";
import { parseLocalStageValue, sameLocalStage } from "./stageDocument.ts";
import type {
  CanvasNodeData,
  CanvasNodeKind,
  CanvasResource,
  ExternalData,
  GenerationSettings,
  StackData,
  SubjectData,
  TimelineClip,
  TimelineData,
  ToolData,
} from "./model.ts";
import { parseLayerDocumentValue, sameLayerDocument } from "./layerDocument.ts";
import {
  normalizeContent,
  validContent,
  type ReferenceContent,
} from "./chatReferenceContent.ts";

const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const nonempty = (value: unknown): value is string =>
  typeof value === "string" && value.trim().length > 0;
const optionalString = (value: unknown) =>
  value === undefined || typeof value === "string";
const uniqueIds = (items: readonly { id: string }[]) =>
  new Set(items.map((item) => item.id)).size === items.length;
const SETTINGS_KEYS = [
  "mode",
  "model",
  "aspectRatio",
  "durationSeconds",
  "resolution",
  "prompt",
  "count",
  "audioEnabled",
  "videoMode",
  "firstFrameNodeId",
  "lastFrameNodeId",
  "frameSlotsInitialized",
  "audioCreationType",
  "audioMode",
  "voiceName",
  "lyrics",
  "scoreMode",
  "durationFromReference",
  "imagePreset",
  "modelConfigId",
] as const;

/** Where a node's media came from, as recorded by a canvas archive.
 * Unknown sources are rejected so a crafted file cannot claim a provenance
 * the importer would then hand to the backend. */
export function isGenerationProvenance(
  value: unknown,
): value is NonNullable<CanvasNodeData["generationProvenance"]> {
  if (!record(value)) return false;
  if (
    ![
      "generated",
      "edited",
      "rendered",
      "upload",
      "manual",
      "agent_written",
      "imported",
    ].includes(String(value.source))
  )
    return false;
  return (
    optionalString(value.jobId) &&
    optionalString(value.inputFingerprint) &&
    optionalString(value.editorDocumentId) &&
    (value.editorRevision === undefined ||
      Number.isSafeInteger(value.editorRevision))
  );
}

export function isGenerationSettings(
  value: unknown,
): value is GenerationSettings {
  return (
    record(value) &&
    typeof value.mode === "string" &&
    ["image", "video", "audio"].includes(value.mode) &&
    typeof value.model === "string" &&
    typeof value.aspectRatio === "string" &&
    finite(value.durationSeconds) &&
    value.durationSeconds > 0 &&
    typeof value.resolution === "string" &&
    typeof value.prompt === "string" &&
    (value.count === undefined ||
      (Number.isInteger(value.count) &&
        Number(value.count) >= 1 &&
        Number(value.count) <= 8)) &&
    (value.audioEnabled === undefined ||
      typeof value.audioEnabled === "boolean") &&
    (value.videoMode === undefined ||
      value.videoMode === "reference" ||
      value.videoMode === "first-last" ||
      value.videoMode === "continuation") &&
    (value.firstFrameNodeId === undefined ||
      nonempty(value.firstFrameNodeId)) &&
    (value.lastFrameNodeId === undefined || nonempty(value.lastFrameNodeId)) &&
    (value.frameSlotsInitialized === undefined ||
      typeof value.frameSlotsInitialized === "boolean") &&
    (value.audioCreationType === undefined ||
      value.audioCreationType === "audio" ||
      value.audioCreationType === "music") &&
    (value.audioMode === undefined ||
      value.audioMode === "unified" ||
      value.audioMode === "tts") &&
    optionalString(value.voiceName) &&
    (value.lyrics === undefined || typeof value.lyrics === "string") &&
    (value.scoreMode === undefined ||
      value.scoreMode === "melody" ||
      value.scoreMode === "full") &&
    (value.durationFromReference === undefined ||
      typeof value.durationFromReference === "boolean") &&
    (value.imagePreset === undefined ||
      value.imagePreset === "none" ||
      value.imagePreset === "product-views") &&
    (value.modelConfigId === undefined || nonempty(value.modelConfigId))
  );
}

export function copyGenerationSettings(
  value: GenerationSettings,
): GenerationSettings {
  const next = Object.fromEntries(
    SETTINGS_KEYS.filter((key) => value[key] !== undefined).map((key) => [
      key,
      value[key],
    ]),
  ) as unknown as GenerationSettings;
  // Documents written before the locale split stored the rendered "自动" label.
  if (next.aspectRatio === "自动") next.aspectRatio = "auto";
  if (next.resolution === "自动") next.resolution = "auto";
  return next;
}

export function isResource(value: unknown): value is CanvasResource {
  return (
    record(value) &&
    nonempty(value.id) &&
    typeof value.kind === "string" &&
    ["image", "video", "audio", "text", "file"].includes(value.kind) &&
    typeof value.kind === "string" &&
    typeof value.title === "string" &&
    optionalString(value.src) &&
    optionalString(value.mimeType) &&
    optionalString(value.text) &&
    (value.assetId === undefined || nonempty(value.assetId)) &&
    (value.byteSize === undefined ||
      (finite(value.byteSize) && value.byteSize >= 0)) &&
    (value.nodeId === undefined || nonempty(value.nodeId)) &&
    (value.durationSeconds === undefined ||
      (finite(value.durationSeconds) && value.durationSeconds > 0)) &&
    (value.width === undefined || (finite(value.width) && value.width > 0)) &&
    (value.height === undefined || (finite(value.height) && value.height > 0))
  );
}

export function copyResource(value: CanvasResource): CanvasResource {
  return {
    id: value.id,
    kind: value.kind,
    title: value.title,
    ...(value.text !== undefined ? { text: value.text } : {}),
    ...(value.assetId !== undefined ? { assetId: value.assetId } : {}),
    ...(value.byteSize !== undefined ? { byteSize: value.byteSize } : {}),
    ...(value.src !== undefined ? { src: value.src } : {}),
    ...(value.mimeType !== undefined ? { mimeType: value.mimeType } : {}),
    ...(value.nodeId !== undefined ? { nodeId: value.nodeId } : {}),
    ...(value.width !== undefined ? { width: value.width } : {}),
    ...(value.height !== undefined ? { height: value.height } : {}),
    ...(value.durationSeconds !== undefined
      ? { durationSeconds: value.durationSeconds }
      : {}),
  };
}

export function timelineError(value: unknown): string | null {
  if (
    !record(value) ||
    !Number.isSafeInteger(value.fps) ||
    Number(value.fps) <= 0 ||
    !Number.isSafeInteger(value.width) ||
    Number(value.width) <= 0 ||
    !Number.isSafeInteger(value.height) ||
    Number(value.height) <= 0 ||
    !Array.isArray(value.tracks) ||
    !value.tracks.length
  )
    return tNow("errors.data.timelineShape");
  if (
    value.importedAssets !== undefined &&
    (!Array.isArray(value.importedAssets) ||
      !value.importedAssets.every(
        (asset) => isResource(asset) && asset.kind !== "file",
      ))
  )
    return tNow("errors.data.timelineAssets");
  const trackIds = new Set<string>(),
    clipIds = new Set<string>();
  let visualCount = 0;
  for (const track of value.tracks) {
    if (
      !record(track) ||
      !nonempty(track.id) ||
      trackIds.has(track.id) ||
      typeof track.kind !== "string" ||
      !["visual", "audio"].includes(track.kind) ||
      typeof track.title !== "string" ||
      typeof track.muted !== "boolean" ||
      !Array.isArray(track.clips)
    )
      return tNow("errors.data.timelineTrack");
    trackIds.add(track.id);
    if (track.kind === "visual") visualCount++;
    const audioIntervals: [number, number][] = [];
    for (const clip of track.clips) {
      if (
        !record(clip) ||
        !nonempty(clip.id) ||
        clipIds.has(clip.id) ||
        !isResource(clip.resource) ||
        !finite(clip.inPoint) ||
        clip.inPoint < 0 ||
        !finite(clip.outPoint) ||
        clip.outPoint <= clip.inPoint ||
        !finite(clip.speed) ||
        clip.speed <= 0 ||
        !finite(clip.volume) ||
        clip.volume < 0 ||
        clip.volume > 1 ||
        typeof clip.muted !== "boolean"
      )
        return tNow("errors.data.timelineClip");
      clipIds.add(clip.id);
      if (
        clip.resource.durationSeconds !== undefined &&
        clip.resource.kind !== "image" &&
        clip.outPoint > clip.resource.durationSeconds + 0.000001
      )
        return tNow("errors.data.timelineOutPoint");
      if (track.kind === "visual") {
        if (
          !["image", "video"].includes(clip.resource.kind) ||
          clip.start !== undefined
        )
          return tNow("errors.data.timelineVisualKind");
        if (
          clip.resource.kind === "image" &&
          (clip.inPoint !== 0 || clip.speed !== 1)
        )
          return tNow("errors.data.timelineStill");
      } else {
        if (
          clip.resource.kind !== "audio" ||
          !finite(clip.start) ||
          clip.start < 0
        )
          return tNow("errors.data.timelineAudio");
        audioIntervals.push([
          clip.start,
          clip.start + (clip.outPoint - clip.inPoint) / clip.speed,
        ]);
      }
    }
    audioIntervals.sort((a, b) => a[0] - b[0]);
    if (
      audioIntervals.some(
        (interval, index) =>
          index > 0 && interval[0] < audioIntervals[index - 1]![1] - 0.000001,
      )
    )
      return tNow("errors.data.timelineOverlap");
  }
  return visualCount === 1 ? null : tNow("errors.data.timelineVisualTrack");
}

/** Legacy documents only kept clip snapshots. Migrate once when copying at the boundary. */
export function timelineImportedAssets(
  value: TimelineData,
): readonly CanvasResource[] {
  if (value.importedAssets !== undefined) return value.importedAssets;
  const assets = new Map<string, CanvasResource>();
  for (const track of value.tracks)
    for (const { resource } of track.clips)
      assets.set(`${resource.kind}:${resource.src ?? resource.id}`, resource);
  return [...assets.values()];
}

export function copyTimeline(value: TimelineData): TimelineData {
  return {
    fps: value.fps,
    width: value.width,
    height: value.height,
    importedAssets: timelineImportedAssets(value).map(copyResource),
    tracks: value.tracks.map((track) => ({
      id: track.id,
      kind: track.kind,
      title: track.title,
      muted: track.muted,
      clips: track.clips.map((clip) => ({
        id: clip.id,
        resource: copyResource(clip.resource),
        inPoint: clip.inPoint,
        outPoint: clip.outPoint,
        speed: clip.speed,
        volume: clip.volume,
        muted: clip.muted,
        ...(clip.start !== undefined ? { start: clip.start } : {}),
      })),
    })),
  };
}

export function subjectError(value: unknown): string | null {
  if (
    !record(value) ||
    typeof value.description !== "string" ||
    typeof value.category !== "string" ||
    !Array.isArray(value.auxiliaries) ||
    value.auxiliaries.length > 3
  )
    return tNow("errors.data.subject");
  if (
    value.main !== undefined &&
    (!isResource(value.main) || !["image", "video"].includes(value.main.kind))
  )
    return tNow("errors.data.subjectMain");
  if (
    value.voice !== undefined &&
    (!isResource(value.voice) || value.voice.kind !== "audio")
  )
    return tNow("errors.data.subjectVoice");
  if (
    !value.auxiliaries.every(
      (item) => isResource(item) && item.kind === "image",
    )
  )
    return tNow("errors.data.subjectAuxiliary");
  if (!value.main && (value.voice || value.auxiliaries.length))
    return tNow("errors.data.subjectOrder");
  if (
    value.main &&
    (value.main as CanvasResource).kind === "video" &&
    (value.voice || value.auxiliaries.length)
  )
    return tNow("errors.data.subjectVideoExtras");
  if (
    value.librarySource !== undefined &&
    (!record(value.librarySource) ||
      !nonempty(value.librarySource.itemId) ||
      !nonempty(value.librarySource.importId) ||
      !Number.isInteger(value.librarySource.revision) ||
      Number(value.librarySource.revision) < 1)
  )
    return tNow("errors.data.subjectLibrary");
  const resources = [value.main, ...value.auxiliaries, value.voice].filter(
    Boolean,
  ) as CanvasResource[];
  if (!uniqueIds(resources))
    return tNow("errors.data.subjectDuplicateAsset");
  const nodeIds = resources.flatMap((resource) =>
    resource.nodeId ? [resource.nodeId] : [],
  );
  if (new Set(nodeIds).size !== nodeIds.length)
    return tNow("errors.data.subjectDuplicateNode");
  const main = value.main as CanvasResource | undefined,
    voice = value.voice as CanvasResource | undefined;
  if (
    main?.kind === "video" &&
    main.durationSeconds !== undefined &&
    main.durationSeconds > 15
  )
    return tNow("errors.data.subjectVideoDuration");
  if (
    voice?.durationSeconds !== undefined &&
    (voice.durationSeconds <= 0 || voice.durationSeconds > 15)
  )
    return tNow("errors.data.subjectVoiceDuration");
  return null;
}

export function copySubject(value: SubjectData): SubjectData {
  return {
    description: value.description,
    category: value.category,
    auxiliaries: value.auxiliaries.map(copyResource),
    ...(value.main ? { main: copyResource(value.main) } : {}),
    ...(value.voice ? { voice: copyResource(value.voice) } : {}),
    ...(value.librarySource
      ? { librarySource: { ...value.librarySource } }
      : {}),
  };
}

export function stackError(value: unknown): string | null {
  if (
    !record(value) ||
    !Array.isArray(value.nodeIds) ||
    !value.nodeIds.every(nonempty) ||
    value.nodeIds.length > 50 ||
    new Set(value.nodeIds).size !== value.nodeIds.length ||
    !Number.isSafeInteger(value.activeIndex) ||
    Number(value.activeIndex) < 0 ||
    Number(value.activeIndex) >= Math.max(1, value.nodeIds.length)
  )
    return tNow("errors.data.stack");
  return null;
}

export function toolError(value: unknown): string | null {
  if (
    !record(value) ||
    typeof value.kind !== "string" ||
    !["camera", "style", "palette", "camera_motion"].includes(value.kind) ||
    !Array.isArray(value.fields) ||
    value.fields.length > 16
  )
    return tNow("errors.data.tool");
  const stringValue = (item: unknown) =>
    typeof item === "string" && [...item].length <= 256;
  if (
    !value.fields.every(
      (field) =>
        record(field) &&
        nonempty(field.name) &&
        stringValue(field.name) &&
        Array.isArray(field.values) &&
        field.values.length <= 32 &&
        field.values.every(stringValue),
    )
  )
    return tNow("errors.data.toolFields");
  if (
    value.provenance !== undefined &&
    (!record(value.provenance) ||
      !nonempty(value.provenance.paletteId) ||
      !Number.isSafeInteger(value.provenance.version) ||
      Number(value.provenance.version) < 0)
  )
    return tNow("errors.data.toolProvenance");
  if (value.palette !== undefined) {
    if (
      value.kind !== "palette" ||
      !record(value.palette) ||
      (value.palette.name === undefined &&
        value.palette.sourceImageUri === undefined)
    )
      return tNow("errors.data.paletteKind");
    if (
      value.palette.name !== undefined &&
      (!nonempty(value.palette.name) ||
        /[\x00-\x1f\x7f]/.test(value.palette.name) ||
        [...value.palette.name].length > 128 ||
        new TextEncoder().encode(value.palette.name).length > 512)
    )
      return tNow("errors.data.paletteName");
    if (
      value.palette.sourceImageUri !== undefined &&
      (!nonempty(value.palette.sourceImageUri) ||
        /[\s\x00-\x1f\x7f\\?#:]/.test(value.palette.sourceImageUri) ||
        new TextEncoder().encode(value.palette.sourceImageUri).length > 1024)
    )
      return tNow("errors.data.paletteSource");
  }
  return null;
}

function copyTool(value: ToolData): ToolData {
  return {
    kind: value.kind,
    fields: value.fields.map((field) => ({
      name: field.name,
      values: [...field.values],
    })),
    ...(value.provenance
      ? {
          provenance: {
            paletteId: value.provenance.paletteId,
            version: value.provenance.version,
          },
        }
      : {}),
    ...(value.palette
      ? {
          palette: {
            ...(value.palette.name !== undefined
              ? { name: value.palette.name }
              : {}),
            ...(value.palette.sourceImageUri !== undefined
              ? { sourceImageUri: value.palette.sourceImageUri }
              : {}),
          },
        }
      : {}),
  };
}

export function externalError(value: unknown): string | null {
  if (!record(value) || value.externalType !== "director-stage")
    return tNow("errors.data.externalType");
  if (
    value.current !== undefined &&
    (!record(value.current) ||
      typeof value.current.versionId !== "string" ||
      !/^(0|[1-9]\d*)$/.test(value.current.versionId) ||
      value.current.versionId.length > 20 ||
      BigInt(value.current.versionId) > 18446744073709551615n)
  )
    return tNow("errors.data.externalVersion");
  return null;
}

export function parseNodeData(
  kind: CanvasNodeKind,
  value: unknown,
): { ok: true; value: CanvasNodeData } | { ok: false; error: string } {
  if (
    !record(value) ||
    value.kind !== kind ||
    typeof value.title !== "string" ||
    typeof value.prompt !== "string" ||
    !optionalString(value.src) ||
    !optionalString(value.mimeType) ||
    !optionalString(value.text) ||
    (value.assetState !== undefined &&
      value.assetState !== "ready" &&
      value.assetState !== "missing") ||
    (value.mediaRole !== undefined &&
      value.mediaRole !== "asset" &&
      value.mediaRole !== "generation") ||
    (value.generationProvenance !== undefined &&
      !isGenerationProvenance(value.generationProvenance)) ||
    (value.settings !== undefined && !isGenerationSettings(value.settings))
  )
    return { ok: false, error: tNow("errors.data.node") };
  const result: CanvasNodeData = {
    kind,
    title: value.title,
    prompt: value.prompt,
    ...(typeof value.backendAssetId === "string" && value.backendAssetId
      ? { backendAssetId: value.backendAssetId }
      : {}),
    ...(value.byteSize !== undefined &&
    typeof value.byteSize === "number" &&
    Number.isFinite(value.byteSize) &&
    value.byteSize >= 0
      ? { byteSize: value.byteSize }
      : {}),
  };
  if (value.promptContent !== undefined) {
    if (
      !Array.isArray(value.promptContent) ||
      value.promptContent.length > 2000 ||
      !value.promptContent.every(
        (part) =>
          record(part) &&
          (part.type === "text"
            ? typeof part.text === "string"
            : part.type === "reference" &&
              nonempty(part.assetId) &&
              nonempty(part.name) &&
              part.name.length <= 255 &&
              (part.nodeId == null || nonempty(part.nodeId)) &&
              (part.sourceRole === undefined ||
                (typeof part.sourceRole === "string" &&
                  part.sourceRole.length <= 100)) &&
              (part.referenceId === undefined || nonempty(part.referenceId)) &&
              (part.binding === undefined || part.binding === "asset") &&
              (part.fingerprint == null ||
                typeof part.fingerprint === "string")),
      )
    )
      return { ok: false, error: tNow("errors.data.contentReference") };
    const content = normalizeContent(value.promptContent as ReferenceContent);
    if (!validContent(content))
      return { ok: false, error: tNow("errors.data.contentLimit") };
    result.promptContent = content;
  }
  if (value.promptMentions !== undefined) {
    if (
      !Array.isArray(value.promptMentions) ||
      !value.promptMentions.every(
        (mention) =>
          record(mention) &&
          (nonempty(mention.sourceNodeId) || nonempty(mention.referenceId)) &&
          (mention.sourceNodeId === undefined ||
            nonempty(mention.sourceNodeId)) &&
          (mention.referenceId === undefined ||
            nonempty(mention.referenceId)) &&
          Number.isInteger(mention.start) &&
          Number.isInteger(mention.end) &&
          Number(mention.start) >= 0 &&
          Number(mention.end) > Number(mention.start) &&
          Number(mention.end) <= String(value.prompt).length,
      )
    )
      return { ok: false, error: tNow("errors.data.mentionRange") };
    result.promptMentions = value.promptMentions.map((mention) => ({
      ...(mention.sourceNodeId
        ? { sourceNodeId: mention.sourceNodeId as string }
        : {}),
      ...(mention.referenceId
        ? { referenceId: mention.referenceId as string }
        : {}),
      start: mention.start as number,
      end: mention.end as number,
    }));
  }
  for (const key of ["src", "mimeType", "text"] as const)
    if (typeof value[key] === "string") result[key] = value[key];
  if (value.assetState) result.assetState = value.assetState;
  if (value.mediaRole) result.mediaRole = value.mediaRole;
  // Dropping this would lose the only evidence that an imported generation
  // node is one, and the importer falls back to a plain material.
  if (value.generationProvenance !== undefined)
    result.generationProvenance = {
      ...(value.generationProvenance as NonNullable<
        CanvasNodeData["generationProvenance"]
      >),
    };
  if (value.agentSettings !== undefined) {
    const agent = value.agentSettings;
    if (
      kind !== "text" ||
      !record(agent) ||
      !optionalString(agent.modelConfigId) ||
      !["auto", "low", "medium", "high"].includes(String(agent.thinking)) ||
      !Number.isInteger(agent.maxTokens) ||
      Number(agent.maxTokens) < 1 ||
      Number(agent.maxTokens) > RULE_FACTS.textMaxTokens
    )
      return { ok: false, error: tNow("errors.data.agent") };
    result.agentSettings = {
      ...(agent.modelConfigId
        ? { modelConfigId: agent.modelConfigId as string }
        : {}),
      thinking: agent.thinking as "auto" | "low" | "medium" | "high",
      maxTokens: Number(agent.maxTokens),
    };
  }
  if (value.generationEvidence !== undefined) {
    const evidence = value.generationEvidence;
    if (
      !record(evidence) ||
      !nonempty(evidence.output) ||
      !record(evidence.references) ||
      !Object.entries(evidence.references).every(
        ([id, material]) => nonempty(id) && typeof material === "string",
      )
    )
      return { ok: false, error: tNow("errors.data.evidence") };
    result.generationEvidence = {
      output: evidence.output,
      references: { ...evidence.references } as Record<string, string>,
    };
  }
  if (value.settings)
    result.settings = copyGenerationSettings(
      value.settings as unknown as GenerationSettings,
    );
  for (const [key, validate] of [
    ["timeline", timelineError],
    ["subject", subjectError],
    ["stack", stackError],
    ["tool", toolError],
    ["external", externalError],
  ] as const) {
    if (value[key] === undefined) continue;
    const error = validate(value[key]);
    if (error) return { ok: false, error };
  }
  if (value.timeline)
    result.timeline = copyTimeline(value.timeline as unknown as TimelineData);
  if (value.subject)
    result.subject = copySubject(value.subject as unknown as SubjectData);
  if (value.stack) {
    const stack = value.stack as unknown as StackData;
    result.stack = {
      nodeIds: [...stack.nodeIds],
      activeIndex: stack.activeIndex,
    };
  }
  if (value.tool) result.tool = copyTool(value.tool as unknown as ToolData);
  if (value.external) {
    const external = value.external as unknown as ExternalData;
    result.external = {
      externalType: external.externalType,
      ...(external.current
        ? { current: { versionId: external.current.versionId } }
        : {}),
    };
  }
  if (value.media !== undefined) {
    if (
      !record(value.media) ||
      ["width", "height", "durationSeconds"].some(
        (key) =>
          value.media &&
          record(value.media) &&
          value.media[key] !== undefined &&
          (!finite(value.media[key]) || Number(value.media[key]) <= 0),
      )
    )
      return { ok: false, error: tNow("errors.data.media") };
    result.media = Object.fromEntries(
      ["width", "height", "durationSeconds"]
        .filter(
          (key) =>
            value.media &&
            record(value.media) &&
            value.media[key] !== undefined,
        )
        .map((key) => [key, (value.media as Record<string, number>)[key]]),
    );
  }
  if (value.taskDraft !== undefined) {
    try {
      result.taskDraft = parseCanvasToolTaskValue(value.taskDraft);
    } catch (error) {
      return {
        ok: false,
        error:
          error instanceof Error
            ? error.message
            : tNow("errors.data.taskDraft"),
      };
    }
  }
  if (value.localStage !== undefined) {
    try {
      result.localStage = parseLocalStageValue(value.localStage);
    } catch (error) {
      return {
        ok: false,
        error:
          error instanceof Error
            ? error.message
            : tNow("errors.data.localStage"),
      };
    }
  }
  if (typeof value.copiedSourceId === "string")
    result.copiedSourceId = value.copiedSourceId;
  if (value.imageCrop !== undefined) {
    try {
      result.imageCrop = parseImageCrop(value.imageCrop);
    } catch (error) {
      return {
        ok: false,
        error:
          error instanceof Error
            ? error.message
            : tNow("errors.data.imageCrop"),
      };
    }
  }
  if (value.layers !== undefined) {
    try {
      result.layers = parseLayerDocumentValue(value.layers);
    } catch (error) {
      return {
        ok: false,
        error:
          error instanceof Error
            ? error.message
            : tNow("errors.data.layers"),
      };
    }
  }
  return { ok: true, value: result };
}

export function nodeResources(data: CanvasNodeData): CanvasResource[] {
  return [
    ...(data.timeline ? timelineImportedAssets(data.timeline) : []),
    ...(data.timeline?.tracks.flatMap((track) =>
      track.clips.map((clip) => clip.resource),
    ) ?? []),
    ...(data.subject
      ? ([
          data.subject.main,
          ...data.subject.auxiliaries,
          data.subject.voice,
        ].filter(Boolean) as CanvasResource[])
      : []),
  ];
}

const sameArray = <T>(
  a: readonly T[],
  b: readonly T[],
  equal: (a: T, b: T) => boolean,
) =>
  a === b ||
  (a.length === b.length && a.every((item, index) => equal(item, b[index]!)));
const sameOptional = <T>(
  a: T | undefined,
  b: T | undefined,
  equal: (a: T, b: T) => boolean,
) => a === b || (a !== undefined && b !== undefined && equal(a, b));
const sameResource = (a: CanvasResource, b: CanvasResource) =>
  (a.text === b.text &&
    a.byteSize === b.byteSize &&
    a.assetId === b.assetId &&
    a === b) ||
  (a.id === b.id &&
    a.kind === b.kind &&
    a.title === b.title &&
    a.src === b.src &&
    a.mimeType === b.mimeType &&
    a.nodeId === b.nodeId &&
    a.durationSeconds === b.durationSeconds &&
    a.width === b.width &&
    a.height === b.height);
export const sameGenerationSettings = (
  a: GenerationSettings | undefined,
  b: GenerationSettings | undefined,
) =>
  sameOptional(a, b, (a, b) => SETTINGS_KEYS.every((key) => a[key] === b[key]));
const sameClip = (a: TimelineClip, b: TimelineClip) =>
  a === b ||
  (a.id === b.id &&
    sameResource(a.resource, b.resource) &&
    a.start === b.start &&
    a.inPoint === b.inPoint &&
    a.outPoint === b.outPoint &&
    a.speed === b.speed &&
    a.volume === b.volume &&
    a.muted === b.muted);

/** Known domain facts are compared directly; media is never JSON-serialized during dragging. */
export function sameNodeData(a: CanvasNodeData, b: CanvasNodeData): boolean {
  return (
    a === b ||
    (a.kind === b.kind &&
      a.title === b.title &&
      a.prompt === b.prompt &&
      a.backendAssetId === b.backendAssetId &&
      a.byteSize === b.byteSize &&
      JSON.stringify(a.promptContent) === JSON.stringify(b.promptContent) &&
      JSON.stringify(a.promptMentions ?? []) ===
        JSON.stringify(b.promptMentions ?? []) &&
      a.src === b.src &&
      a.mimeType === b.mimeType &&
      a.text === b.text &&
      a.assetState === b.assetState &&
      a.mediaRole === b.mediaRole &&
      sameOptional(
        a.agentSettings,
        b.agentSettings,
        (a, b) =>
          a.modelConfigId === b.modelConfigId &&
          a.thinking === b.thinking &&
          a.maxTokens === b.maxTokens,
      ) &&
      sameOptional(
        a.generationEvidence,
        b.generationEvidence,
        (a, b) =>
          a.output === b.output &&
          Object.keys(a.references).length ===
            Object.keys(b.references).length &&
          Object.entries(a.references).every(
            ([id, value]) => b.references[id] === value,
          ),
      ) &&
      sameGenerationSettings(a.settings, b.settings) &&
      sameOptional(
        a.timeline,
        b.timeline,
        (a, b) =>
          a.fps === b.fps &&
          a.width === b.width &&
          a.height === b.height &&
          sameArray(
            timelineImportedAssets(a),
            timelineImportedAssets(b),
            sameResource,
          ) &&
          sameArray(
            a.tracks,
            b.tracks,
            (a, b) =>
              a.id === b.id &&
              a.kind === b.kind &&
              a.title === b.title &&
              a.muted === b.muted &&
              sameArray(a.clips, b.clips, sameClip),
          ),
      ) &&
      sameOptional(
        a.subject,
        b.subject,
        (a, b) =>
          a.description === b.description &&
          a.category === b.category &&
          sameOptional(
            a.librarySource,
            b.librarySource,
            (a, b) =>
              a.itemId === b.itemId &&
              a.revision === b.revision &&
              a.importId === b.importId,
          ) &&
          sameOptional(a.main, b.main, sameResource) &&
          sameArray(a.auxiliaries, b.auxiliaries, sameResource) &&
          sameOptional(a.voice, b.voice, sameResource),
      ) &&
      sameOptional(
        a.stack,
        b.stack,
        (a, b) =>
          a.activeIndex === b.activeIndex &&
          sameArray(a.nodeIds, b.nodeIds, (a, b) => a === b),
      ) &&
      sameOptional(
        a.tool,
        b.tool,
        (a, b) =>
          a.kind === b.kind &&
          sameArray(
            a.fields,
            b.fields,
            (a, b) =>
              a.name === b.name &&
              sameArray(a.values, b.values, (a, b) => a === b),
          ) &&
          sameOptional(
            a.provenance,
            b.provenance,
            (a, b) => a.paletteId === b.paletteId && a.version === b.version,
          ) &&
          sameOptional(
            a.palette,
            b.palette,
            (a, b) =>
              a.name === b.name && a.sourceImageUri === b.sourceImageUri,
          ),
      ) &&
      sameOptional(
        a.external,
        b.external,
        (a, b) =>
          a.externalType === b.externalType &&
          a.current?.versionId === b.current?.versionId,
      ) &&
      sameLayerDocument(a.layers, b.layers) &&
      JSON.stringify(a.imageCrop) === JSON.stringify(b.imageCrop) &&
      sameLocalStage(a.localStage, b.localStage) &&
      sameCanvasToolTask(a.taskDraft, b.taskDraft) &&
      sameOptional(
        a.media,
        b.media,
        (a, b) =>
          a.width === b.width &&
          a.height === b.height &&
          a.durationSeconds === b.durationSeconds,
      ))
  );
}
