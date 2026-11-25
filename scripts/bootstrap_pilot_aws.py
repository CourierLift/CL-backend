"""Create the pilot storage foundation in a signed-in AWS CloudShell session."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REGION = "us-west-2"
STACK = "courierlifts-pilot-storage-v1"
OPS = Path(__file__).resolve().parents[1] / "ops"


def read_config(folder, name):
    return json.loads((OPS / folder / name).read_text())


def bucket_arn(value, token, logical):
    prefix = f"arn:aws:s3:::{token}"
    if value != prefix and not value.startswith(prefix + "/"):
        raise ValueError(f"Unexpected bucket ARN for {logical}")
    return {"Fn::Sub": "${" + logical + ".Arn}" + value[len(prefix):]}


def storage_template():
    settings = read_config("pilot-storage", "bucket-settings.json")
    props = {
        "VersioningConfiguration": settings["versioning"],
        "PublicAccessBlockConfiguration": settings["public_access_block"],
        "OwnershipControls": settings["ownership_controls"],
        "BucketEncryption": {"ServerSideEncryptionConfiguration": [
            {"ServerSideEncryptionByDefault": rule["ApplyServerSideEncryptionByDefault"]}
            for rule in settings["default_encryption"]["Rules"]
        ]},
    }
    resources = {}
    outputs = {}
    retain = {"DeletionPolicy": "RetainExceptOnCreate", "UpdateReplacePolicy": "Retain"}
    for logical, purpose in (("Proofs", "proofs"), ("Backups", "backups"), ("Recovery", "recovery")):
        name = {"Fn::Sub": f"courierlifts-pilot-{purpose}-${{AWS::AccountId}}-${{AWS::Region}}"}
        resources[logical] = {
            "Type": "AWS::S3::Bucket", **retain,
            "Properties": {**props, "BucketName": name, "Tags": [{"Key": "Project", "Value": "CourierLifts"}]},
        }
        is_backup = logical == "Backups"
        folder = "pilot-backup" if is_backup else "pilot-storage"
        token = "__BACKUP_BUCKET__" if is_backup else "__BUCKET_NAME__"
        document = read_config(folder, "bucket-policy.template.json")
        for statement in document["Statement"]:
            arns = statement["Resource"]
            if isinstance(arns, list):
                statement["Resource"] = [bucket_arn(arn, token, logical) for arn in arns]
            else:
                statement["Resource"] = bucket_arn(arns, token, logical)
        resources[logical + "Policy"] = {
            "Type": "AWS::S3::BucketPolicy", **retain,
            "Properties": {"Bucket": {"Ref": logical}, "PolicyDocument": document},
        }
        outputs[logical] = {"Value": {"Ref": logical}}
    return {"AWSTemplateFormatVersion": "2010-09-09", "Resources": resources, "Outputs": outputs}


class AWSFailure(RuntimeError):
    def __init__(self, message):
        super().__init__(message)
        match = re.search(r"An error occurred \(([^)]+)\)", message)
        self.code = match.group(1) if match else ""


def aws(*args):
    result = subprocess.run(
        ["aws", "--region", REGION, "--output", "json", "--no-cli-pager", *args],
        text=True, capture_output=True, check=False,
    )
    if result.returncode:
        raise AWSFailure(result.stderr.strip() or "The AWS CLI did not complete the request.")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def ensure_storage(template):
    body = json.dumps(template, separators=(",", ":"))
    aws("cloudformation", "validate-template", "--template-body", body)
    try:
        created = aws("cloudformation", "create-stack", "--stack-name", STACK, "--template-body", body,
                      "--enable-termination-protection", "--tags", "Key=Project,Value=CourierLifts")
        stack_id = created["StackId"]
    except AWSFailure as exc:
        if exc.code != "AlreadyExistsException":
            raise
        stack_id = aws("cloudformation", "describe-stacks", "--stack-name", STACK)["Stacks"][0]["StackId"]
        original = aws("cloudformation", "get-template", "--stack-name", stack_id,
                       "--template-stage", "Original")["TemplateBody"]
        if isinstance(original, str):
            original = json.loads(original)
        if original != template:
            raise RuntimeError("A different stack already uses this name. No update was made.") from exc
    print("Waiting for the private storage stack in Oregon…", flush=True)
    aws("cloudformation", "wait", "stack-create-complete", "--stack-name", stack_id)
    return aws("cloudformation", "describe-stacks", "--stack-name", stack_id)["Stacks"][0]


def ensure_sender(address):
    try:
        identity = aws("sesv2", "get-email-identity", "--email-identity", address)
    except AWSFailure as exc:
        if exc.code != "NotFoundException":
            raise
        try:
            aws("sesv2", "create-email-identity", "--email-identity", address)
        except AWSFailure as create_error:
            if create_error.code != "AlreadyExistsException":
                raise
        identity = aws("sesv2", "get-email-identity", "--email-identity", address)
    return identity.get("VerifiedForSendingStatus", False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", metavar="SENDER_EMAIL",
                        help="Create the buckets and verify this single sender in the current AWS account.")
    args = parser.parse_args()
    template = storage_template()
    if not args.apply:
        print(json.dumps(template, indent=2))
        return
    if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", args.apply):
        parser.error("--apply requires an individual email address.")
    account = aws("sts", "get-caller-identity")["Account"]
    print(f"Using AWS account {account}, region {REGION}.", flush=True)
    stack = ensure_storage(template)
    print(f"Storage stack: {stack['StackStatus']}", flush=True)
    verified = ensure_sender(args.apply)
    print("Email sender verified." if verified else f"Email verification pending. Check {args.apply}.")
    print("The application deployment and backup schedule are separate steps.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        sys.exit(str(error))
