import os
from datetime import date, timedelta

import boto3
import pytest
from moto import mock_aws

os.environ.update({
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_ACCESS_KEY_ID": "test",
    "AWS_SECRET_ACCESS_KEY": "test",
    "TABLE_NAME": "coach-test",
    "BUCKET_NAME": "coach-test-bucket",
    "ALLOWED_PHONE": "573001112233",
    "REPORT_EMAIL": "atleta@example.com",
    "SENDER_EMAIL": "coach@example.com",
    "MPLBACKEND": "Agg",
})


@pytest.fixture
def aws():
    with mock_aws():
        ddb = boto3.resource("dynamodb")
        ddb.create_table(
            TableName="coach-test",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                                  {"AttributeName": "SK", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        boto3.client("s3").create_bucket(Bucket="coach-test-bucket")
        ses = boto3.client("ses")
        ses.verify_email_identity(EmailAddress="coach@example.com")
        yield


@pytest.fixture
def repo(aws):
    from coach.storage.repo import Repo

    return Repo()


@pytest.fixture
def zones():
    from coach.analytics.zones import build_zones

    # Test: 4:40/km y 170 ppm → umbral 4:54 y 161.5
    return build_zones(294.0, 161.5, source="test", test_date=date(2026, 10, 1))


def make_activity(day: date, minutes: float = 50, pace_s: float = 360, hr: float = 140, cadence: float = 164,
                  activity_id: str | None = None, tss: float | None = None) -> dict:
    return {
        "activity_id": activity_id or f"a{day.isoformat()}",
        "name": "Carrera",
        "date": day.isoformat(),
        "sport": "running",
        "type": "running",
        "distance_km": round(minutes * 60 / pace_s, 2),
        "duration_s": minutes * 60,
        "duration_min": minutes,
        "avg_pace_s_per_km": pace_s,
        "avg_hr": hr,
        "avg_cadence_spm": cadence,
        "tss": tss,
    }


@pytest.fixture
def history():
    """6 semanas de carreras (4 por semana) hasta el 2026-10-16."""
    end = date(2026, 10, 16)
    acts = []
    for i in range(42):
        d = end - timedelta(days=i)
        if d.weekday() in (1, 3, 5, 6):
            acts.append(make_activity(d, minutes=70 if d.weekday() == 6 else 45, tss=60 if d.weekday() == 6 else 40))
    return acts
