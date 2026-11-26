"""Give the Courier Lifts operations branch read-only access to pilot settings."""

import argparse
import json
import re
import sys
import time

from bootstrap_pilot_aws import (
    AWSFailure, REGION, STACK as STORAGE_STACK, aws, ensure_sender, ensure_storage, storage_template,
)

ISSUER = "token.actions.githubusercontent.com"
SUBJECT = "repo:CourierLift/CL-backend:ref:refs/heads/ops/courier-pilot-aws"
AUDIENCE = "sts.amazonaws.com"
STACK = "courierlifts-pilot-verifier-v1"
ROLE = "CourierLiftsPilotVerifier"


def sub(value):
    return {"Fn::Sub": value}


def role_template():
    buckets = [sub("arn:${AWS::Partition}:s3:::courierlifts-pilot-" + purpose + "-${AWS::AccountId}-us-west-2")
               for purpose in ("proofs", "backups", "recovery")]
    stacks = [sub("arn:${AWS::Partition}:cloudformation:us-west-2:${AWS::AccountId}:stack/" + name + "/*")
              for name in (STACK, STORAGE_STACK)]
    trust = {"Effect": "Allow", "Action": "sts:AssumeRoleWithWebIdentity",
             "Principal": {"Federated": sub("arn:${AWS::Partition}:iam::${AWS::AccountId}:oidc-provider/" + ISSUER)},
             "Condition": {"StringEquals": {ISSUER + ":aud": AUDIENCE, ISSUER + ":sub": SUBJECT}}}
    reads = [
        {"Effect": "Allow", "Action": ["cloudformation:DescribeStacks"], "Resource": stacks},
        {"Effect": "Allow", "Action": ["s3:GetBucketVersioning", "s3:GetBucketPublicAccessBlock",
                                       "s3:GetEncryptionConfiguration"], "Resource": buckets},
        {"Effect": "Allow", "Action": ["ses:GetEmailIdentity"],
         "Resource": sub("arn:${AWS::Partition}:ses:us-west-2:${AWS::AccountId}:identity/${PilotSender}")},
    ]
    role = {"RoleName": ROLE, "Description": "Checks Courier Lifts pilot settings from one GitHub branch",
            "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [trust]},
            "Policies": [{"PolicyName": "ReadPilotSettings", "PolicyDocument": {"Version": "2012-10-17", "Statement": reads}}]}
    return {"AWSTemplateFormatVersion": "2010-09-09",
            "Parameters": {"PilotSender": {"Type": "String"}},
            "Resources": {"Verifier": {"Type": "AWS::IAM::Role", "Properties": role}}}


def ensure_provider(account):
    arn = f"arn:aws:iam::{account}:oidc-provider/{ISSUER}"
    read = ("iam", "get-open-id-connect-provider", "--open-id-connect-provider-arn", arn)
    try:
        provider = aws(*read)
    except AWSFailure as exc:
        if exc.code != "NoSuchEntity":
            raise
        try:
            aws("iam", "create-open-id-connect-provider", "--url", "https://" + ISSUER, "--client-id-list", AUDIENCE)
        except AWSFailure as conflict:
            if conflict.code != "EntityAlreadyExists":
                raise
        for delay in (0, 1, 2, 4, 8):
            time.sleep(delay)
            try:
                provider = aws(*read)
                break
            except AWSFailure as pending:
                if pending.code != "NoSuchEntity" or delay == 8:
                    raise
    if provider["Url"].removeprefix("https://").rstrip("/") != ISSUER or AUDIENCE not in provider["ClientIDList"]:
        raise RuntimeError("The existing GitHub identity provider differs from the required settings. It was left in place.")


def ensure_verifier(address):
    template = role_template()
    body = json.dumps(template, separators=(",", ":"))
    aws("cloudformation", "validate-template", "--template-body", body)
    try:
        stack_id = aws("cloudformation", "create-stack", "--stack-name", STACK, "--template-body", body,
                       "--parameters", json.dumps([{"ParameterKey": "PilotSender", "ParameterValue": address}]),
                       "--capabilities", "CAPABILITY_NAMED_IAM")["StackId"]
    except AWSFailure as exc:
        if exc.code != "AlreadyExistsException":
            raise
        existing = aws("cloudformation", "describe-stacks", "--stack-name", STACK)["Stacks"][0]
        stack_id = existing["StackId"]
        original = aws("cloudformation", "get-template", "--stack-name", stack_id, "--template-stage", "Original")["TemplateBody"]
        if isinstance(original, str):
            original = json.loads(original)
        parameters = {p["ParameterKey"]: p.get("ParameterValue") for p in existing.get("Parameters", [])}
        if original != template or parameters.get("PilotSender") != address:
            raise RuntimeError("A different verifier stack exists. No update was made.") from exc
    aws("cloudformation", "wait", "stack-create-complete", "--stack-name", stack_id)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true", help="Set up storage, sender and the verifier role in the signed-in account.")
    p.add_argument("--expected-account")
    p.add_argument("--sender")
    args = p.parse_args()
    if not args.apply:
        print(json.dumps(role_template(), indent=2))
        return
    if not re.fullmatch(r"[0-9]{12}", args.expected_account or ""):
        p.error("--expected-account requires the intended 12-digit account ID.")
    if not re.fullmatch(r"[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", args.sender or ""):
        p.error("--sender requires a single email address.")
    who = aws("sts", "get-caller-identity")
    if who["Account"] != args.expected_account or not who["Arn"].startswith("arn:aws:"):
        raise RuntimeError("The signed-in AWS account differs from the expected account. No changes were made.")
    print(f"Setting up the Courier Lifts pilot in {REGION}.", flush=True)
    ensure_storage(storage_template())
    verified = ensure_sender(args.sender)
    print("Email sender verified." if verified else "Check the sender inbox for the AWS verification message.", flush=True)
    ensure_provider(who["Account"])
    ensure_verifier(args.sender)
    print("Pilot access ready. Return to the Courier Lifts conversation.")
    print("To revoke GitHub access, delete the CloudFormation stack " + STACK + ". The storage stack is separate.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        sys.exit(str(error))
