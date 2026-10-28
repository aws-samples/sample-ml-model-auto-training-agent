import { describe, expect, it } from "vitest";

import {
  canTransitionFlowStage,
  canTransitionToPreview,
  nextStageAfterPreviewSubmit,
  transitionFlowStage,
} from "../lib/flow";

describe("flow helpers", () => {
  it("allows preview from draft edit", () => {
    expect(canTransitionToPreview("draft-edit")).toBe(true);
  });

  it("rejects invalid direct jump to submitting", () => {
    expect(canTransitionFlowStage("welcome", "submitting")).toBe(false);
    expect(transitionFlowStage("welcome", "submitting")).toBe("welcome");
  });

  it("maps submit result to terminal stage", () => {
    expect(nextStageAfterPreviewSubmit(true)).toBe("submit-success");
    expect(nextStageAfterPreviewSubmit(false)).toBe("submit-failure");
  });
});
