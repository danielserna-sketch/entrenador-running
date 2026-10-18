"""Envío del reporte por Amazon SES: HTML con imágenes inline (CID) + el HTML como adjunto."""

from __future__ import annotations

from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import boto3

from coach.config import get_settings


def build_mime(report: dict, sender: str, recipient: str, subject: str) -> MIMEMultipart:
    root = MIMEMultipart("mixed")
    root["Subject"] = subject
    root["From"] = sender
    root["To"] = recipient

    related = MIMEMultipart("related")
    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText("Tu plan de entrenamiento actualizado está en este correo (versión HTML).",
                                "plain", "utf-8"))
    alternative.attach(MIMEText(report["html_email"], "html", "utf-8"))
    related.attach(alternative)
    for cid, png in report["images"].items():
        img = MIMEImage(png, "png")
        img.add_header("Content-ID", f"<{cid}>")
        img.add_header("Content-Disposition", "inline", filename=f"{cid}.png")
        related.attach(img)
    root.attach(related)

    attachment = MIMEApplication(report["html_file"].encode("utf-8"), "html")
    attachment.add_header("Content-Disposition", "attachment", filename=f"plan_v{report['number']:03d}.html")
    root.attach(attachment)
    return root


def send_report(report: dict, subject: str, ses=None) -> str:
    settings = get_settings()
    if not settings.report_email or not settings.sender_email:
        raise RuntimeError("Configura REPORT_EMAIL y SENDER_EMAIL para enviar el reporte.")
    mime = build_mime(report, settings.sender_email, settings.report_email, subject)
    ses = ses or boto3.client("ses")
    resp = ses.send_raw_email(Source=settings.sender_email, Destinations=[settings.report_email],
                              RawMessage={"Data": mime.as_string()})
    return resp["MessageId"]
