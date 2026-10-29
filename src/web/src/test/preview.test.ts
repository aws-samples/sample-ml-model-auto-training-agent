// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { describe, expect, it } from "vitest";

import { buildPreview } from "../lib/preview";

describe("buildPreview", () => {
  it("defaults image tag to stable", () => {
    const preview = buildPreview(
      {
        taskName: "训练任务",
        taskType: "timeseries_forecast",
        dataLocation: "data/train.csv",
        outputLocation: "output/run-1",
        modelName: "demo_model_1",
        targetEndpoint: "demo-endpoint-1",
        comment: "预测未来 7 天目标指标",
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

    expect(preview.resolvedImageTag).toBe("stable");
    expect(preview.payload.confirmation_flag).toBe(true);
  });
});
