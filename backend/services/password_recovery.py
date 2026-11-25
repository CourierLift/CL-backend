"""Short-lived email codes bound to the current pilot account password."""
import base64
import hashlib
import hmac
import logging
import time

import boto3
from botocore.config import Config

from ..models import User
from ..settings import settings

LIFETIME = 15 * 60
logger = logging.getLogger(__name__)


def make_code(user: User, issued: int) -> str:
    # A separate purpose binds these codes to this account and password state.
    message = f"cl:password-code:v1:{user.id}:{user.email}:{user.password_hash}:{issued}"
    mac = hmac.digest(settings.CL_SECRET_KEY.encode(), message.encode(), "sha256")
    return f"{issued:08X}-{base64.b32encode(mac[:10]).decode()}"


def valid_code(user: User, code: str) -> bool:
    code = code.strip().upper()
    if len(code) != 25 or not code.isascii() or code[8] != "-":
        return False
    try:
        issued = int(code[:8], 16)
    except ValueError:
        return False
    return 0 <= int(time.time()) - issued < LIFETIME and hmac.compare_digest(make_code(user, issued), code)


def recipient_key(email: str) -> str:
    return hmac.new(settings.CL_SECRET_KEY.encode(), email.encode(), hashlib.sha256).hexdigest()


def mail_configured() -> bool:
    return all((settings.CL_PASSWORD_MAIL_FROM, settings.CL_PASSWORD_MAIL_REGION,
                settings.CL_PASSWORD_MAIL_AWS_ACCESS_KEY_ID, settings.CL_PASSWORD_MAIL_AWS_SECRET_ACCESS_KEY))


def send_code(email: str, code: str) -> None:
    try:
        client = boto3.client("sesv2", region_name=settings.CL_PASSWORD_MAIL_REGION,
                              aws_access_key_id=settings.CL_PASSWORD_MAIL_AWS_ACCESS_KEY_ID,
                              aws_secret_access_key=settings.CL_PASSWORD_MAIL_AWS_SECRET_ACCESS_KEY,
                              config=Config(connect_timeout=3, read_timeout=6, retries={"total_max_attempts": 2}))
        client.send_email(
            FromEmailAddress=str(settings.CL_PASSWORD_MAIL_FROM), Destination={"ToAddresses": [email]},
            Content={"Simple": {"Subject": {"Data": "Your Courier Lifts password code"},
                                "Body": {"Text": {"Data": (
                                    f"Your code is {code}\n\n"
                                    f"Open {settings.CL_FRONTEND_ORIGIN.rstrip('/')}/#recover and choose "
                                    "I already have a code. Enter your email, the code and a new password. "
                                    "The code expires in 15 minutes. Keep it private. "
                                    "If you did not request this email, you can ignore it."
                                )}}}})
    except Exception:
        # SDK exceptions can contain recipient details. Never log codes or addresses.
        logger.error("Courier Lifts password email delivery failed.")
