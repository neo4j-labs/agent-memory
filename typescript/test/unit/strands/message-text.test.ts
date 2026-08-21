/**
 * Content-block flattening. Both the snapshot path (Message instances, which
 * carry a `type` discriminator) and the extraction path (MessageData, whose
 * non-text blocks are bare `{ toolUse: … }` records) land here.
 */

import { describe, it, expect } from "vitest";
import {
  strandsMessageToText,
  toStrandsMessage,
} from "../../../src/integrations/strands/messages.js";

describe("strandsMessageToText", () => {
  it("joins text blocks with newlines", () => {
    expect(
      strandsMessageToText({ content: [{ text: "one" }, { text: "two" }] }),
    ).toBe("one\ntwo");
  });

  it("tags a block that declares a type but carries no text", () => {
    expect(strandsMessageToText({ content: [{ type: "toolUseBlock" }] })).toBe(
      "[toolUseBlock]",
    );
  });

  it("drops a data-form tool block, which has neither text nor type", () => {
    // MessageData content blocks are plain records: { toolUse: { … } }. With no
    // text and no discriminator there is nothing to store, so the message is
    // empty and the store skips it.
    expect(
      strandsMessageToText({ content: [{ toolUse: { name: "x", input: {} } }] }),
    ).toBe("");
  });

  it("returns empty for a missing or non-array content field", () => {
    expect(strandsMessageToText({})).toBe("");
    expect(strandsMessageToText({ content: "not an array" })).toBe("");
  });
});

describe("toStrandsMessage", () => {
  it("wraps stored content in a single text block", () => {
    const message = toStrandsMessage({ role: "user", content: "hello" });
    expect(message.role).toBe("user");
    expect(message.content).toEqual([{ text: "hello" }]);
  });
});
