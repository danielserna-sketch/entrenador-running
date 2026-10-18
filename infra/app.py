#!/usr/bin/env python3
"""Punto de entrada de CDK. La configuración sale de `cdk.json` → context.coach."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aws_cdk as cdk

from infra.stacks.coach_stack import CoachStack

app = cdk.App()
cfg = app.node.try_get_context("coach") or {}
missing = [k for k in ("allowed_phone", "report_email", "sender_email") if not cfg.get(k)]
if missing:
    raise SystemExit(f"Completa en cdk.json → context.coach: {', '.join(missing)}")

CoachStack(
    app, "EntrenadorRunning", cfg=cfg,
    env=cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
                        region=cfg.get("region") or os.environ.get("CDK_DEFAULT_REGION", "us-east-1")),
)
app.synth()
