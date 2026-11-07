import assert from "node:assert/strict";
import test from "node:test";
import { canvasReferenceRole, MUSIC_SECONDS } from "../src/backend/rules.ts";
import { CONTENT_LIMIT, REFERENCE_LIMIT } from "../src/canvas/chatReferenceContent.ts";
import { FRAME_IMAGE_LIMIT } from "../src/canvas/frameReferences.ts";
import { GENERATION_RATIOS } from "../src/canvas/generationCatalog.ts";
import {
  RULE_DERIVATION_VECTORS,
  RULE_FACTS,
  aspectRatioChoices,
  effectiveVideoMode,
  generateAudioOptions,
  referenceByteLimit,
  resolutionChoices,
} from "../src/contracts/ruleFacts.generated.ts";
import { assistant as enAssistant } from "../src/i18n/messages/en-US/assistant.ts";
import { errors as enErrors } from "../src/i18n/messages/en-US/errors.ts";
import { assistant as zhAssistant } from "../src/i18n/messages/zh-CN/assistant.ts";
import { errors as zhErrors } from "../src/i18n/messages/zh-CN/errors.ts";

const derivations = {
  aspectRatioChoices,
  resolutionChoices,
  effectiveVideoMode,
  generateAudioOptions,
  referenceByteLimit,
};

function mentions(text: string, value: number) {
  const digits = String(value);
  const grouped = digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return text.includes(digits) || text.includes(grouped);
}

for (const vector of RULE_DERIVATION_VECTORS) {
  test(`generated derivation matches ${vector.name}`, () => {
    const derive = derivations[vector.fn] as (input: never) => unknown;
    assert.deepEqual(derive(vector.input as never), vector.expected);
  });
}

test("frontend modules read shared limits instead of declaring them", () => {
  assert.equal(MUSIC_SECONDS.min, RULE_FACTS.musicSecondsMin);
  assert.equal(MUSIC_SECONDS.max, RULE_FACTS.musicSecondsMax);
  assert.equal(MUSIC_SECONDS.initial, RULE_FACTS.musicSecondsInitial);
  assert.equal(FRAME_IMAGE_LIMIT, RULE_FACTS.frameImageLimit);
  assert.deepEqual([...GENERATION_RATIOS], [...RULE_FACTS.videoAspectRatios]);
  assert.equal(CONTENT_LIMIT, RULE_FACTS.promptMaxLength);
  assert.equal(REFERENCE_LIMIT, RULE_FACTS.maxReferences);
  assert.equal(new RegExp(RULE_FACTS.serviceVoicePattern).test("Voice.1"), true);
  assert.equal(new RegExp(RULE_FACTS.serviceVoicePattern).test("动漫海绵"), false);
});

test("visible copy mentions the shared limits", () => {
  for (const text of [
    zhAssistant["assistant.reference.limit"],
    enAssistant["assistant.reference.limit"],
  ]) {
    assert.equal(mentions(text, RULE_FACTS.promptMaxLength), true);
    assert.equal(mentions(text, RULE_FACTS.maxReferences), true);
  }
  assert.equal(
    mentions(zhErrors["errors.rules.lyricsLimit"], RULE_FACTS.lyricsMaxLength),
    true,
  );
  assert.equal(
    mentions(enErrors["errors.rules.lyricsLimit"], RULE_FACTS.lyricsMaxLength),
    true,
  );
  assert.equal(
    mentions(
      zhErrors["errors.connection.frameCapacity"],
      RULE_FACTS.frameImageLimit,
    ),
    true,
  );
  assert.equal(
    mentions(
      enErrors["errors.connection.frameCapacity"],
      RULE_FACTS.frameImageLimit,
    ),
    true,
  );
});

test("one function assigns persisted reference roles from canvas slots", () => {
  const settings = {
    videoMode: "first-last" as const,
    firstFrameNodeId: "first",
    lastFrameNodeId: "last",
  };
  assert.equal(
    canvasReferenceRole(
      { videoMode: "continuation" },
      "video",
      { binding: "follow_current", source: { id: "clip" } },
    ),
    "continuation",
  );
  assert.equal(
    canvasReferenceRole(
      { videoMode: "continuation" },
      "image",
      { binding: "follow_current", source: { id: "still" } },
    ),
    "reference",
  );
  assert.equal(
    canvasReferenceRole(settings, "image", {
      binding: "follow_current",
      source: { id: "first" },
    }),
    "first_frame",
  );
  assert.equal(
    canvasReferenceRole(
      { videoMode: "first-last" },
      "image",
      { binding: "pinned", role: "last_frame", source: { assetId: "asset" } },
    ),
    "last_frame",
  );
  assert.equal(
    canvasReferenceRole(settings, "text", {
      binding: "follow_current",
      source: { id: "note" },
    }),
    "reference",
  );
});
