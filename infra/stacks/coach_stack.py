"""Infraestructura AWS del entrenador (CDK).

API Gateway (webhook WhatsApp) → Lambda webhook → SQS FIFO → Lambda agent_worker
EventBridge Scheduler → Lambda garmin_sync / proactive_jobs
DynamoDB (datos), S3 (JSON crudo y reportes), Secrets Manager, SES, alarmas → SNS email.
"""

from __future__ import annotations

import json
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    SecretValue,
    Stack,
)
from aws_cdk import (
    aws_apigatewayv2 as apigw,
)
from aws_cdk import (
    aws_apigatewayv2_integrations as integrations,
)
from aws_cdk import (
    aws_budgets as budgets,
)
from aws_cdk import (
    aws_cloudwatch as cw,
)
from aws_cdk import (
    aws_cloudwatch_actions as cw_actions,
)
from aws_cdk import (
    aws_dynamodb as ddb,
)
from aws_cdk import (
    aws_ecr_assets as ecr_assets,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_lambda_event_sources as sources,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_scheduler as scheduler,
)
from aws_cdk import (
    aws_secretsmanager as sm,
)
from aws_cdk import (
    aws_ses as ses,
)
from aws_cdk import (
    aws_sns as sns,
)
from aws_cdk import (
    aws_sns_subscriptions as subs,
)
from aws_cdk import (
    aws_sqs as sqs,
)
from constructs import Construct

ROOT = Path(__file__).resolve().parents[2]


class CoachStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, cfg: dict, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        tz = cfg.get("timezone", "America/Bogota")

        # ---- datos ------------------------------------------------------------
        table = ddb.Table(
            self, "Table",
            partition_key=ddb.Attribute(name="PK", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="SK", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True),
            removal_policy=RemovalPolicy.RETAIN,
        )
        bucket = s3.Bucket(
            self, "Data",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # ---- secretos (se crean con valores de ejemplo; reemplázalos tras el deploy) ----
        def placeholder(name: str, keys: list[str]) -> sm.Secret:
            return sm.Secret(self, name.replace("/", "-"), secret_name=name,
                             secret_object_value={k: SecretValue.unsafe_plain_text("REEMPLAZAR") for k in keys})

        garmin_secret = placeholder("coach/garmin-tokens", ["tokens"])
        whatsapp_secret = placeholder("coach/whatsapp", ["access_token", "app_secret", "verify_token",
                                                          "phone_number_id"])
        llm_secret = placeholder("coach/llm", ["anthropic_api_key", "openai_api_key"])

        # ---- cola ---------------------------------------------------------------
        dlq = sqs.Queue(self, "IncomingDLQ", fifo=True, retention_period=Duration.days(14))
        queue = sqs.Queue(
            self, "Incoming", fifo=True, visibility_timeout=Duration.seconds(960),
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=2, queue=dlq),
        )

        # ---- Lambdas (una imagen, distintos handlers) ---------------------------------
        env = {
            "TABLE_NAME": table.table_name,
            "BUCKET_NAME": bucket.bucket_name,
            "QUEUE_URL": queue.queue_url,
            "SECRET_GARMIN_TOKENS": garmin_secret.secret_name,
            "SECRET_WHATSAPP": whatsapp_secret.secret_name,
            "SECRET_LLM": llm_secret.secret_name,
            "ALLOWED_PHONE": cfg["allowed_phone"],
            "COACH_TIMEZONE": tz,
            "LLM_PROVIDER": cfg.get("llm_provider", "anthropic"),
            "LLM_PROVIDER_PROACTIVE": cfg.get("llm_provider_proactive", ""),
            "LLM_MODEL_MAIN": cfg.get("llm_model_main", ""),
            "LLM_MODEL_FAST": cfg.get("llm_model_fast", ""),
            "REPORT_EMAIL": cfg["report_email"],
            "SENDER_EMAIL": cfg["sender_email"],
            "WHATSAPP_TEMPLATE": cfg.get("whatsapp_template", "coach_update"),
            "WHATSAPP_TEMPLATE_LANG": cfg.get("whatsapp_template_lang", "es"),
        }

        def fn(name: str, handler: str, timeout: int, memory: int = 1024) -> lambda_.DockerImageFunction:
            f = lambda_.DockerImageFunction(
                self, name,
                code=lambda_.DockerImageCode.from_image_asset(
                    str(ROOT), cmd=[handler], platform=ecr_assets.Platform.LINUX_ARM64),
                architecture=lambda_.Architecture.ARM_64,
                timeout=Duration.seconds(timeout),
                memory_size=memory,
                environment=env,
                log_group=logs.LogGroup(self, f"{name}Logs", retention=logs.RetentionDays.ONE_MONTH,
                                        removal_policy=RemovalPolicy.DESTROY),
            )
            table.grant_read_write_data(f)
            bucket.grant_read_write(f)
            for secret in (garmin_secret, whatsapp_secret, llm_secret):
                secret.grant_read(f)
            garmin_secret.grant_write(f)  # el refresh token de Garmin rota
            return f

        webhook = fn("Webhook", "coach.handlers.webhook.handler", 10, 512)
        worker = fn("AgentWorker", "coach.handlers.agent_worker.handler", 900)
        sync_fn = fn("GarminSync", "coach.handlers.garmin_sync.handler", 600)
        jobs = fn("ProactiveJobs", "coach.handlers.proactive_jobs.handler", 900)

        queue.grant_send_messages(webhook)
        worker.add_event_source(sources.SqsEventSource(queue, batch_size=1, report_batch_item_failures=True))

        ses_identity = ses.EmailIdentity(self, "Sender", identity=ses.Identity.email(cfg["sender_email"]))
        for f in (worker, sync_fn, jobs):
            f.add_to_role_policy(iam.PolicyStatement(actions=["ses:SendRawEmail", "ses:SendEmail"], resources=["*"]))

        # ---- API del webhook ---------------------------------------------------------
        api = apigw.HttpApi(self, "WebhookApi")
        api.add_routes(path="/webhook", methods=[apigw.HttpMethod.GET, apigw.HttpMethod.POST],
                       integration=integrations.HttpLambdaIntegration("WebhookIntegration", webhook))

        # ---- programación (hora local) ------------------------------------------------
        sched_role = iam.Role(self, "SchedulerRole", assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"))
        sync_fn.grant_invoke(sched_role)
        jobs.grant_invoke(sched_role)

        def schedule(name: str, expr: str, target: lambda_.IFunction, payload: dict) -> None:
            scheduler.CfnSchedule(
                self, name,
                schedule_expression=expr,
                schedule_expression_timezone=tz,
                flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
                target=scheduler.CfnSchedule.TargetProperty(
                    arn=target.function_arn, role_arn=sched_role.role_arn, input=json.dumps(payload),
                    retry_policy=scheduler.CfnSchedule.RetryPolicyProperty(maximum_retry_attempts=1)),
            )

        schedule("SyncEvery30Min", "cron(0/30 5-22 * * ? *)", sync_fn, {})
        schedule("MorningBrief", cfg.get("morning_cron", "cron(15 6 * * ? *)"), jobs, {"job": "morning_brief"})
        schedule("WeeklyReview", cfg.get("weekly_cron", "cron(0 18 ? * SUN *)"), jobs, {"job": "weekly_review"})
        schedule("EveningAlerts", "cron(0 20 * * ? *)", jobs, {"job": "alerts"})

        # ---- alarmas -----------------------------------------------------------------
        topic = sns.Topic(self, "Alarms")
        topic.add_subscription(subs.EmailSubscription(cfg["report_email"]))
        action = cw_actions.SnsAction(topic)
        for f in (webhook, worker, sync_fn, jobs):
            f.metric_errors(period=Duration.hours(1)).create_alarm(
                self, f"{f.node.id}Errors", threshold=3, evaluation_periods=1,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING).add_alarm_action(action)
        dlq.metric_approximate_number_of_messages_visible().create_alarm(
            self, "DLQNotEmpty", threshold=1, evaluation_periods=1).add_alarm_action(action)
        for f in (sync_fn, jobs):
            mf = logs.MetricFilter(
                self, f"{f.node.id}GarminAuth", log_group=f.log_group,
                filter_pattern=logs.FilterPattern.literal("GARMIN_AUTH_FAILED"),
                metric_namespace="Coach", metric_name="GarminAuthFailed", metric_value="1")
            mf.metric(period=Duration.hours(6)).create_alarm(
                self, f"{f.node.id}GarminAuthAlarm", threshold=1, evaluation_periods=1,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING).add_alarm_action(action)

        if cfg.get("monthly_budget_usd"):
            budgets.CfnBudget(
                self, "Budget",
                budget=budgets.CfnBudget.BudgetDataProperty(
                    budget_type="COST", time_unit="MONTHLY",
                    budget_limit=budgets.CfnBudget.SpendProperty(amount=float(cfg["monthly_budget_usd"]), unit="USD")),
                notifications_with_subscribers=[budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        comparison_operator="GREATER_THAN", notification_type="ACTUAL", threshold=80),
                    subscribers=[budgets.CfnBudget.SubscriberProperty(
                        address=cfg["report_email"], subscription_type="EMAIL")])],
            )

        CfnOutput(self, "WebhookUrl", value=f"{api.api_endpoint}/webhook")
        CfnOutput(self, "TableName", value=table.table_name)
        CfnOutput(self, "BucketName", value=bucket.bucket_name)
        CfnOutput(self, "SesIdentity", value=ses_identity.email_identity_name)
