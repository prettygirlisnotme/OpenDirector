import type { ModelConfig } from "../backend/rules.ts";
import { RULE_FACTS } from "../contracts/ruleFacts.generated.ts";
import type { CanvasDocument, CanvasNode, AgentNodeSettings } from "./model.ts";
import { referenceMaterial } from "./connectionPolicy.ts";
import type { ResultSource } from "./linkedResults.ts";
import { applyCommand } from "./model.ts";
import { lockedNodeIds } from "./hierarchy.ts";
import { tNow } from "../i18n/messages.ts";

export function agentNodeSettings(node: CanvasNode): AgentNodeSettings {
  return {
    thinking: "auto",
    maxTokens: RULE_FACTS.textDefaultMaxTokens,
    ...node.data.agentSettings,
  };
}
export function agentGenerationBlocker(
  node: CanvasNode,
  incoming: readonly CanvasNode[],
  model?: ModelConfig,
) {
  if (!model || model.kind !== "agent")
    return tNow("errors.text.noAgent");
  if (!node.data.prompt.trim()) return tNow("errors.text.promptRequired");
  const settings = agentNodeSettings(node);
  if (
    !Number.isInteger(settings.maxTokens) ||
    settings.maxTokens < 1 ||
    settings.maxTokens > RULE_FACTS.textMaxTokens
  )
    return tNow("errors.text.maxTokens");
  for (const reference of incoming) {
    if (!model.inputRules.allowedKinds.includes(reference.type))
      return tNow("errors.text.unsupportedKind", {
        title: reference.data.title,
      });
    if (!referenceMaterial(reference))
      return tNow("errors.text.referenceUnavailable", {
        title: reference.data.title,
      });
  }
  for (const [kind, max] of [
    ["image", model.inputRules.maxImages],
    ["video", model.inputRules.maxVideos],
    ["audio", model.inputRules.maxAudios],
  ] as const)
    if (incoming.filter((n) => n.type === kind).length > max)
      return tNow("errors.text.referenceLimit");
  return null;
}
export function applyTextGenerationResult(
  document: CanvasDocument,
  source: ResultSource,
  output: { kind: "text"; title: string; text: string; assetId?: string },
) {
  const target = document.nodes.find((n) => n.id === source.nodeId);
  const error =
    document.id !== source.documentId
      ? tNow("errors.text.projectChanged")
      : !target || target.type !== "text" || source.kind !== "text"
        ? tNow("errors.text.sourceChanged")
        : lockedNodeIds(document.nodes).has(target.id)
          ? tNow("errors.text.locked")
          : target.data.text !== source.text
            ? tNow("errors.text.bodyChanged")
            : null;
  if (error || !target)
    return {
      document,
      ids: [] as string[],
      error: error ?? tNow("errors.text.nodeMissing"),
    };
  return {
    document: applyCommand(document, {
      type: "update-node",
      id: target.id,
      patch: {
        data: {
          text: output.text,
          backendAssetId: output.assetId,
          mediaRole: "generation",
          generationEvidence: source.referenceInputs
            ? { output: output.text, references: { ...source.referenceInputs } }
            : undefined,
        },
      },
    }),
    ids: [target.id],
  };
}
