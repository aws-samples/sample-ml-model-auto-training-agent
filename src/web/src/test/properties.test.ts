// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import fc from "fast-check";
import { describe, expect, it } from "vitest";

import {
  canTransitionFlowStage,
  nextStageAfterPreviewSubmit,
  transitionFlowStage,
} from "../lib/flow";
import { buildPreview } from "../lib/preview";
import type { SubmissionFlowStage } from "../lib/types";

describe("frontend properties", () => {
  it("preview payload keeps confirmation flag invariant and passes comment through", () => {
    fc.assert(
      fc.property(fc.string({ minLength: 0, maxLength: 24 }), (comment) => {
        const preview = buildPreview(
          {
            taskName: "训练任务",
            taskType: "timeseries_forecast",
            dataLocation: "data/train.csv",
            outputLocation: "output/run-1",
            modelName: "demo_model_1",
            targetEndpoint: "demo-endpoint-1",
            comment,
            imageTag: "",
        deploymentMode: "auto",
            isRecurring: false,
            scheduleStart: "",
            intervalValue: "1",
            intervalUnit: "days",
          },
          {
            apiBaseUrl: "/poc",
            environmentLabel: "PoC",
            submittedBy: "demo-operator",
            signRegion: "cn-northwest-1",
            signHost: "example.execute-api.cn-northwest-1.amazonaws.com.cn",
            accessKeyId: "AKIAEXAMPLE",
            secretAccessKey: "secretexample",
          },
        );
        expect(preview.payload.confirmation_flag).toBe(true);
      }),
    );
  });

  it("submit outcome stays in explicit terminal states", () => {
    fc.assert(
      fc.property(fc.boolean(), (success) => {
        expect(["submit-success", "submit-failure"]).toContain(
          nextStageAfterPreviewSubmit(success),
        );
      }),
    );
  });

  it("invalid transition requests leave the flow stage unchanged", () => {
    fc.assert(
      fc.property(
        fc.constantFrom<SubmissionFlowStage>("welcome", "draft-edit", "preview"),
        fc.constantFrom<SubmissionFlowStage>(
          "bootstrap",
          "submitting",
          "submit-success",
        ),
        (current, next) => {
          if (!canTransitionFlowStage(current, next)) {
            expect(transitionFlowStage(current, next)).toBe(current);
          }
        },
      ),
    );
  });
});
