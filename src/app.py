#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import os

from aws_cdk import App, Environment

from infra.infra_stack import AutogluonServerlessStack


app = App()
# Bind to a concrete account/region so the VPC construct can look up the real
# availability zones (an env-agnostic stack falls back to dummy AZs and fails
# Fn::Select on multi-AZ subnets at deploy time).
env = Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION"),
)
AutogluonServerlessStack(app, "auto-training-agent", env=env)
app.synth()
