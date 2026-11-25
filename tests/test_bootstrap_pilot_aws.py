from unittest.mock import Mock

import pytest

from scripts import bootstrap_pilot_aws as setup


def aws_error(code):
    return setup.AWSFailure(f"An error occurred ({code}) when calling AWS")


def test_template_limits_creation_and_carries_audited_bucket_rules():
    resources = setup.storage_template()["Resources"]
    assert len(resources) == 6
    for name in ("Proofs", "Backups", "Recovery"):
        bucket = resources[name]
        assert bucket["Type"] == "AWS::S3::Bucket"
        assert bucket["UpdateReplacePolicy"] == "Retain"
        assert bucket["DeletionPolicy"] == "RetainExceptOnCreate"
        props = bucket["Properties"]
        assert len(props["PublicAccessBlockConfiguration"]) == 4
        assert all(props["PublicAccessBlockConfiguration"].values())
        assert props["VersioningConfiguration"]["Status"] == "Enabled"
        assert props["OwnershipControls"]["Rules"] == [{"ObjectOwnership": "BucketOwnerEnforced"}]
        assert props["BucketEncryption"]["ServerSideEncryptionConfiguration"] == [
            {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
        ]
        assert "LifecycleConfiguration" not in props
        policy = resources[name + "Policy"]
        assert policy["Properties"]["Bucket"] == {"Ref": name}
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
            assert statement["Effect"] == "Deny"
            refs = statement["Resource"]
            for ref in refs if isinstance(refs, list) else [refs]:
                assert ref["Fn::Sub"].startswith("${" + name + ".Arn}")
    backup_rules = resources["BackupsPolicy"]["Properties"]["PolicyDocument"]["Statement"]
    assert {s["Sid"] for s in backup_rules} == {
        s["Sid"] for s in setup.read_config("pilot-backup", "bucket-policy.template.json")["Statement"]
    }


def test_storage_retry_compares_template_before_reusing_stack(monkeypatch):
    template = setup.storage_template()
    stack = {"StackId": "same-stack", "StackStatus": "CREATE_COMPLETE"}
    aws = Mock(side_effect=[{}, aws_error("AlreadyExistsException"), {"Stacks": [stack]},
                            {"TemplateBody": template}, {}, {"Stacks": [stack]}])
    monkeypatch.setattr(setup, "aws", aws)
    assert setup.ensure_storage(template) == stack
    assert [call.args[1] for call in aws.call_args_list] == [
        "validate-template", "create-stack", "describe-stacks", "get-template", "wait", "describe-stacks"
    ]


def test_foreign_stack_is_never_updated(monkeypatch):
    aws = Mock(side_effect=[{}, aws_error("AlreadyExistsException"), {"Stacks": [{"StackId": "other"}]},
                            {"TemplateBody": {"Resources": {}}}])
    monkeypatch.setattr(setup, "aws", aws)
    with pytest.raises(RuntimeError, match="different stack"):
        setup.ensure_storage(setup.storage_template())
    assert aws.call_count == 4


@pytest.mark.parametrize(("responses", "verified"), [
    ([{"VerifiedForSendingStatus": True}], True),
    ([{"VerifiedForSendingStatus": False}], False),
    ([aws_error("NotFoundException"), {}, {"VerifiedForSendingStatus": False}], False),
    ([aws_error("NotFoundException"), aws_error("AlreadyExistsException"), {"VerifiedForSendingStatus": True}], True),
])
def test_sender_setup_verifies_new_pending_or_existing_identities(monkeypatch, responses, verified):
    aws = Mock(side_effect=responses)
    monkeypatch.setattr(setup, "aws", aws)
    assert setup.ensure_sender("pilot@example.org") is verified
    assert aws.call_count == len(responses)


def test_insufficient_sender_permissions_stop_without_writing(monkeypatch):
    aws = Mock(side_effect=aws_error("AccessDeniedException"))
    monkeypatch.setattr(setup, "aws", aws)
    with pytest.raises(setup.AWSFailure):
        setup.ensure_sender("pilot@example.org")
    assert aws.call_count == 1
