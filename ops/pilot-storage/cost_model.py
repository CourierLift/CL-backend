#!/usr/bin/env python3
"""Reproducible planning estimate; no credentials, network calls or bill changes.

Rates are reviewed 2026-10-22. Object sizes, traffic and job durations are explicit
assumptions, not measured production usage. This deliberately charges S3 egress
without assuming an unverified account's free allowance or same-region waiver.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path


GIB = 2**30
MIB = 2**20
DECIMAL_GB = 10**9


@dataclass(frozen=True)
class Scenario:
    name: str
    deliveries_per_month: int = 500
    months_of_history: int = 1
    photos_per_delivery: int = 1
    stored_photo_bytes: int = 1_000_000
    monthly_photo_views: int = 1500
    compressed_database_bytes: int = 100 * MIB
    days: int = 30
    retained_checkpoint_upper_bound: int = 14
    marker_bytes_per_photo: int = 512
    manifest_bytes_per_photo: int = 1024
    artifact_base_bytes: int = 2048
    encrypted_size_factor: float = 1.01
    freshness_markers_per_run: int = 1
    backup_minutes_each: float = 10
    monitor_minutes_each: float = 1
    retention_minutes_each: float = 10
    render_other_monthly_outbound_gb: float = 1.0
    aws_free_egress_gib_available: float = 0.0
    extra_stored_version_gib: float = 0.0
    restore_minutes: float = 30.0


def estimate(s: Scenario) -> dict:
    """Thirty-day, uniform-growth estimate with worst-case 14-checkpoint union.

    HEAD/GET request counts reflect the reviewed normal successful path, rounded
    up for control operations. No retries, repeated failures, console browsing,
    version proliferation or paid builds are assumed. See companion documentation.
    """
    if min(s.deliveries_per_month, s.months_of_history, s.photos_per_delivery,
           s.stored_photo_bytes, s.days, s.retained_checkpoint_upper_bound) < 1:
        raise ValueError("Positive scenario sizes and durations are required")
    if s.monthly_photo_views < 0 or s.freshness_markers_per_run < 1:
        raise ValueError("Views must be nonnegative and freshness must read a marker")
    new_photos = s.deliveries_per_month * s.photos_per_delivery
    average_photos = new_photos * (s.months_of_history - 0.5)
    end_photos = new_photos * s.months_of_history
    monitors = 24 * s.days
    checkpoints = s.retained_checkpoint_upper_bound
    marker_bytes = s.artifact_base_bytes + average_photos * s.marker_bytes_per_photo
    manifest_bytes = (s.artifact_base_bytes + average_photos * s.manifest_bytes_per_photo) * s.encrypted_size_factor
    encrypted_dump_bytes = s.compressed_database_bytes * s.encrypted_size_factor

    storage = {
        "runtime_photos_gib": average_photos * s.stored_photo_bytes / GIB,
        "encrypted_photo_archive_gib": average_photos * s.stored_photo_bytes * s.encrypted_size_factor / GIB,
        "retained_encrypted_dumps_gib": checkpoints * encrypted_dump_bytes / GIB,
        "retained_encrypted_manifests_gib": checkpoints * manifest_bytes / GIB,
        "retained_complete_markers_gib": checkpoints * marker_bytes / GIB,
        "extra_versions_gib": s.extra_stored_version_gib,
    }
    marker_reads = monitors * s.freshness_markers_per_run + s.days * checkpoints
    transferred = {
        "photo_views_gib": s.monthly_photo_views * s.stored_photo_bytes / GIB,
        "new_source_photos_archived_gib": new_photos * s.stored_photo_bytes / GIB,
        "new_encrypted_photos_read_back_gib": new_photos * s.stored_photo_bytes * s.encrypted_size_factor / GIB,
        "daily_encrypted_dump_read_back_gib": s.days * encrypted_dump_bytes / GIB,
        "daily_manifest_and_marker_read_back_gib": s.days * (manifest_bytes + marker_bytes) / GIB,
        "freshness_and_retention_marker_reads_gib": marker_reads * marker_bytes / GIB,
    }
    # Listing may need one extra page until expired checkpoints have been pruned.
    # Prefix-scoped absent-photo HEAD can fall back to one LIST per new photo.
    list_count = 2 * (monitors + s.days) + new_photos
    put_count = 2 * new_photos + 3 * s.days
    # Existing archives: 3 HEADs per backup, plus one retention verification/day.
    # New archives, per-checkpoint uploads, marker reads and source controls are
    # budgeted additionally; this slightly overcounts the new-photo successful path.
    head_get_count = (
        4 * s.days * average_photos
        + 6 * new_photos
        + s.monthly_photo_views
        + 3 * marker_reads
        + s.days * (12 + 2 * checkpoints)
        + 2 * (monitors + s.days)
    )
    render_views_gb = s.monthly_photo_views * s.stored_photo_bytes / DECIMAL_GB
    render_allowance_remaining = max(0.0, 5.0 - s.render_other_monthly_outbound_gb)
    render_incremental_gb = max(0.0, render_views_gb - render_allowance_remaining)
    aws_egress_gib = sum(transferred.values())
    costs = {
        "existing_courier_render_usd": 13.30,
        "backup_cron_usd": max(1.0, s.days * s.backup_minutes_each * 0.00016),
        "monitor_and_retention_cron_usd": max(1.0, (monitors * s.monitor_minutes_each + s.days * s.retention_minutes_each) * 0.00016),
        "s3_storage_usd": sum(storage.values()) * 0.023,
        "s3_put_and_list_requests_usd": (put_count + list_count) * 0.000005,
        "s3_head_and_get_requests_usd": head_get_count * 0.0000004,
        "s3_egress_usd": max(0.0, aws_egress_gib - s.aws_free_egress_gib_available) * 0.09,
        "incremental_render_browser_egress_usd": render_incremental_gb * 0.15,
    }
    courier = sum(costs.values())
    # One full restore: encrypted archive + dump download; uploaded recovery
    # photos read back once by the job and once by the before-start verifier.
    # Receipt metadata/requests are budgeted, not exact per-account meter data.
    restore_egress_gib = (
        encrypted_dump_bytes + manifest_bytes + marker_bytes
        + end_photos * s.stored_photo_bytes * (s.encrypted_size_factor + 2)
    ) / GIB
    restore_cost = {
        "compute_usd": s.restore_minutes * 0.00016,
        "s3_transfer_usd_without_free_allowance": restore_egress_gib * 0.09,
        "s3_requests_usd": (end_photos + 2) * 0.000005 + (8 * end_photos + 20) * 0.0000004,
        "extra_recovery_photo_storage_usd_for_one_full_month": end_photos * s.stored_photo_bytes / GIB * 0.023,
    }
    return {
        "assumptions": asdict(s),
        "average_retained_photos": average_photos,
        "end_month_retained_photos": end_photos,
        "monthly_average_stored_gib": storage,
        "monthly_s3_outbound_gib": transferred,
        "request_estimates": {"put": put_count, "list": list_count, "get_and_head": head_get_count},
        "monthly_cost_components_usd": costs,
        "courier_total_monthly_usd": courier,
        "additional_to_current_courier_monthly_usd": courier - 13.30,
        "whole_existing_workspace_plus_proposal_usd": courier + 7.25,
        "courier_if_aws_transfer_is_fully_free_usd": courier - costs["s3_egress_usd"],
        "courier_if_render_shared_allowance_is_already_exhausted_usd": courier + (render_views_gb - render_incremental_gb) * 0.15,
        "optional_single_recovery_drill": {
            "s3_outbound_gib": restore_egress_gib,
            "cost_components_usd": restore_cost,
            "one_time_compute_requests_transfer_usd": sum(value for key, value in restore_cost.items() if "storage" not in key),
            "new_paid_database_or_plan_increase_included": False,
        },
    }


def report() -> dict:
    scenarios = [
        Scenario(name="500_deliveries_first_month_1MB_photos"),
        Scenario(name="500_deliveries_month_12_1MB_photos", months_of_history=12),
        Scenario(name="500_deliveries_month_12_4MB_photos_more_views", months_of_history=12,
                 stored_photo_bytes=4_000_000, monthly_photo_views=2000),
    ]
    return {
        "reviewed_on": "2026-10-22",
        "currency": "USD",
        "operational_status": "planning only; no resources or credentials created",
        "rates_file": "ops/pilot-storage/pricing-2026-10-22.json",
        "notes": [
            "14 retained checkpoints is a conservative union upper bound, not 14 guaranteed snapshots every month.",
            "Freshness reads the single newest complete marker; daily retention verifies all retained checkpoints.",
            "S3 egress is conservatively charged at the paid rate; actual same-region exemption or remaining AWS free transfer can reduce it.",
            "Render 5 GB is shared across the existing workspace; default example reserves 1 GB for other responses.",
            "S3 bytes use GiB; Render browser-egress examples use decimal GB conservatively pending the account meter.",
            "Age ciphertext is budgeted at 1.01 times plaintext; exact overhead depends on object size.",
            "No VAT/taxes, paid build overages, failed-run orphans, repeated retries, key rotation, storage growth beyond each scenario, or paid recovery DB is included.",
            "Photo sizes, view counts, DB dump size and runtimes are planning inputs, not measured hosted values.",
            "$10/month additional is a planning target, not a spending approval or hard cap.",
        ],
        "scenarios": [estimate(scenario) for scenario in scenarios],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    rendered = json.dumps(report(), indent=2, sort_keys=True) + "\n"
    if arguments.output:
        arguments.output.write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
