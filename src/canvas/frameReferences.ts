import type {
  CanvasDocument,
  CanvasNode,
  GenerationSettings,
} from "./model.ts";
import { tNow } from "../i18n/messages.ts";
import type { ModelConfig } from "../backend/rules.ts";
import { RULE_FACTS } from "../contracts/ruleFacts.generated.ts";

export const FRAME_IMAGE_LIMIT = RULE_FACTS.frameImageLimit;
export const frameImageLimitMessage = () =>
  tNow("errors.connection.frameCapacity");

export function frameImageLimitIssue(
  settings: GenerationSettings,
  incoming: readonly CanvasNode[],
) {
  return settings.mode === "video" &&
    settings.videoMode === "first-last" &&
    new Set(
      incoming.filter((node) => node.type === "image").map((node) => node.id),
    ).size > FRAME_IMAGE_LIMIT
    ? frameImageLimitMessage()
    : null;
}

/**
 * What the canvas command layer needs to know about the selected model. The
 * document model has no access to the configured service catalog, so the
 * workspace supplies it. Without it, a configured service model is treated as
 * first-frame only: filling a slot the model rejects would block generation.
 */
export interface FrameCapabilities {
  supportsLastFrame(settings: GenerationSettings): boolean;
}

/** Read last-frame support from the configured model's input rules. */
export function modelFrameCapabilities(
  models: readonly Pick<ModelConfig, "id" | "inputRules">[],
): FrameCapabilities {
  return {
    supportsLastFrame: (settings) => {
      if (!settings.modelConfigId) return true;
      const config = models.find(
        (model) => model.id === settings.modelConfigId,
      );
      return Boolean(config?.inputRules.frameRoles?.includes("last_frame"));
    },
  };
}

function supportsLastFrame(
  settings: GenerationSettings,
  capabilities?: FrameCapabilities,
) {
  if (capabilities) return capabilities.supportsLastFrame(settings);
  return !settings.modelConfigId;
}

/** Connection/mode-change default only; rendering and manual clearing never
 * refill a slot. Keep explicit selections and the order of incoming edges. */
export function fillConnectedFrames(
  settings: GenerationSettings,
  incoming: readonly CanvasNode[],
  capabilities?: FrameCapabilities,
): GenerationSettings {
  if (
    settings.mode !== "video" ||
    settings.videoMode !== "first-last" ||
    frameImageLimitIssue(settings, incoming)
  )
    return settings;
  const images = incoming.filter(
    (node) =>
      node.type === "image" &&
      node.data.src &&
      node.data.assetState !== "missing",
  );
  let next = settings.frameSlotsInitialized
    ? settings
    : { ...settings, frameSlotsInitialized: true };
  for (const field of ["firstFrameNodeId", "lastFrameNodeId"] as const) {
    if (
      field === "lastFrameNodeId" &&
      !supportsLastFrame(settings, capabilities)
    )
      continue;
    if (next[field]) continue;
    const image = images.find(
      (node) =>
        node.id !== next.firstFrameNodeId && node.id !== next.lastFrameNodeId,
    );
    if (image) next = { ...next, [field]: image.id };
  }
  return next;
}

/** Upgrade older local canvas documents once. Subsequent reloads must preserve
 * intentionally empty slots instead of assigning a frame the user cleared. */
export function initializeConnectedFrames(
  document: CanvasDocument,
): CanvasDocument {
  let changed = false;
  const nodes = document.nodes.map((node) => {
    const settings = node.data.settings;
    if (
      node.type !== "video" ||
      !settings ||
      settings.frameSlotsInitialized ||
      settings.videoMode !== "first-last"
    )
      return node;
    const sources = [
      ...new Set(
        document.edges
          .filter(
            (edge) =>
              edge.target === node.id && edge.intent !== "post_edit_result",
          )
          .map((edge) => edge.source),
      ),
    ];
    const incoming = sources.flatMap(
      (id) => document.nodes.find((item) => item.id === id) ?? [],
    );
    const initialized = fillConnectedFrames(settings, incoming);
    if (initialized === settings) return node;
    changed = true;
    return { ...node, data: { ...node.data, settings: initialized } };
  });
  return changed ? { ...document, nodes } : document;
}
