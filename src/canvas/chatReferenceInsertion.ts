import {
  assetKind,
  fingerprintMatches,
  referenceIds,
  referencePart,
  validContent,
  type ReferenceAsset,
  type ReferenceContent,
} from "./chatReferenceContent.ts";
import { editContent, type Caret } from "./chatReferenceDOM.ts";
import { referenceByteLimit } from "../contracts/ruleFacts.generated.ts";
import type { Translate } from "../i18n/I18nProvider.tsx";
import { defaultTranslate } from "./assistantTranslate.ts";

export interface ReferenceRules {
  availableIds?: string[];
  allowedKinds: string[];
  maxImages: number;
  imageBytes: number;
}
export function referenceSizeLimit(asset: ReferenceAsset, imageBytes: number) {
  return referenceByteLimit({ kind: assetKind(asset), imageBytes });
}

// Prepare the whole edit before publishing; never silently drop invalid items.
export function referenceInsertion(
  content: ReferenceContent,
  caret: Caret,
  added: ReferenceAsset[],
  catalog: ReferenceAsset[],
  rules: ReferenceRules,
  deduplicate = false,
  t: Translate = defaultTranslate,
) {
  for (const asset of added) {
    if (asset.fileAvailable === false)
      throw new Error(t("assistant.error.fileMissing", { name: asset.name }));
    if (asset.available === false && asset.binding !== "asset")
      throw new Error(
        t("assistant.error.assetUnavailable", { name: asset.name }),
      );
    if (!rules.allowedKinds.includes(assetKind(asset)))
      throw new Error(
        t("assistant.error.unsupportedKind", { name: asset.name }),
      );
    if (rules.availableIds && !rules.availableIds.includes(asset.id))
      throw new Error(
        t("assistant.error.invalidVersion", { name: asset.name }),
      );
    if (asset.size > referenceSizeLimit(asset, rules.imageBytes))
      throw new Error(t("assistant.error.tooLarge", { name: asset.name }));
    if (
      content.some(
        (part) =>
          part.type === "reference" &&
          part.assetId === asset.id &&
          part.fingerprint &&
          !fingerprintMatches(asset, part.fingerprint),
      )
    )
      throw new Error(
        t("assistant.error.versionMismatch", { name: asset.name }),
      );
  }
  const ids = new Set(referenceIds(content));
  const chosen = deduplicate
    ? added.filter((asset) => {
        if (ids.has(asset.id)) return false;
        ids.add(asset.id);
        return true;
      })
    : added;
  const parts = chosen.map(referencePart);
  const next = parts.length ? editContent(content, caret, parts) : content;
  const images = referenceIds(next).filter((id) =>
    [...catalog, ...added].some(
      (asset) => asset.id === id && assetKind(asset) === "image",
    ),
  );
  if (!validContent(next) || images.length > rules.maxImages)
    throw new Error(
      t("assistant.error.limitExceeded", { max: rules.maxImages }),
    );
  const end = caret.start + parts.length;
  return { content: next, caret: parts.length ? { start: end, end } : caret };
}
