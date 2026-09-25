"""The headless argument parser."""

from __future__ import annotations

import argparse

from cli.output import _non_negative_int, _restart_delay
from version import __version__


def build_headless_parser() -> argparse.ArgumentParser:
    """Build the argument parser for headless mode."""
    parser = argparse.ArgumentParser(
        prog="nzbpostarr --headless",
        description="NZBPostarr Headless CLI - Upload automation without the WebUI.",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # ── upload ──
    p_upload = sub.add_parser("upload", help="Run an upload job")
    p_upload.add_argument(
        "category",
        help="Category to upload (tv, movies, misc, all, both)",
    )
    p_upload.add_argument(
        "--limit",
        "-l",
        type=int,
        default=None,
        help="Max items to process",
    )
    p_upload.add_argument(
        "--test",
        "-t",
        action="store_true",
        help="Test mode - prepare and submit but skip actual NNTP upload",
    )
    p_upload.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Force re-upload (skip duplicate checking)",
    )
    p_upload.add_argument(
        "--indexer",
        "-i",
        default=None,
        help="Target a specific indexer by ID (e.g., geek, planet)",
    )
    p_upload.add_argument(
        "--skip-packs",
        action="store_true",
        help="Skip season packs (TV only)",
    )
    p_upload.add_argument(
        "--skip-episodes",
        action="store_true",
        help="Skip individual episodes (TV only)",
    )
    p_upload.add_argument(
        "--path",
        "-p",
        default=None,
        help="Upload a specific file or folder path",
    )
    p_upload.add_argument(
        "--json",
        action="store_true",
        help="Output the job result as JSON",
    )

    # ── stream ──
    p_stream = sub.add_parser("stream", help="Queue direct NZB stream/repost jobs")
    p_stream.add_argument("source", help="Server-side NZB file or folder path")
    p_stream.add_argument(
        "--category",
        "-c",
        default="misc",
        help="Target category label for the queued stream job (default: misc)",
    )
    p_stream.add_argument(
        "--release-name",
        default=None,
        help="Override the release/job name for a single NZB source",
    )
    p_stream.add_argument(
        "--submit-mode",
        choices=["post_and_submit", "post_only"],
        default="post_and_submit",
        help="Post only, or post and submit to indexers (default: post_and_submit)",
    )
    p_stream.add_argument(
        "--posting-server",
        default=None,
        help="Specific enabled NNTP posting server name to use",
    )
    p_stream.add_argument(
        "--indexer",
        "-i",
        default=None,
        help="Submit to a specific indexer ID instead of all enabled indexers",
    )
    p_stream.add_argument(
        "--test",
        "-t",
        action="store_true",
        help="Prepare the stream job but skip the actual NNTP upload",
    )
    p_stream.add_argument(
        "--skip-duplicate-check",
        action="store_true",
        help="Skip duplicate checking before the repost job runs",
    )
    p_stream.add_argument(
        "--monitor",
        action="store_true",
        help="Save a watched-folder stream monitor instead of queuing immediate jobs",
    )
    p_stream.add_argument(
        "--json",
        action="store_true",
        help="Output the queued job or monitor result as JSON",
    )

    # ── status ──
    p_status = sub.add_parser(
        "status",
        help="Show system status (config, tools, indexers). Exit 1 if a required tool is missing",
    )
    p_status.add_argument(
        "--json",
        action="store_true",
        help="Output the status snapshot as JSON",
    )

    # ── pending ──
    p_pending = sub.add_parser("pending", help="Show items pending upload")
    p_pending.add_argument(
        "category",
        nargs="?",
        default=None,
        help="Filter by category (optional)",
    )
    p_pending.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="List individual pending items",
    )
    p_pending.add_argument(
        "--folder",
        default=None,
        help="Restrict results to a configured folder path or folder name",
    )
    p_pending.add_argument(
        "--limit",
        type=_non_negative_int,
        default=None,
        help="Maximum pending items listed per category when using --verbose",
    )
    p_pending.add_argument(
        "--json",
        action="store_true",
        help="Output the pending summary as JSON",
    )

    # ── history ──
    p_history = sub.add_parser("history", help="Show recent job history")
    p_history.add_argument(
        "--limit",
        "-l",
        type=int,
        default=20,
        help="Number of jobs to show (default: 20)",
    )
    p_history.add_argument(
        "--json",
        action="store_true",
        help="Output job history as JSON",
    )

    # ── issues ──
    p_issues = sub.add_parser(
        "issues",
        help="Show failed indexer submissions grouped into a known-issues list",
    )
    p_issues.add_argument(
        "--destination",
        default="all",
        help="Limit to one indexer ID, or 'all' (default)",
    )
    p_issues.add_argument(
        "--limit",
        "-l",
        type=int,
        default=50,
        help="Maximum number of issue groups to show (default: 50)",
    )
    p_issues.add_argument(
        "--since-days",
        type=int,
        default=None,
        help="Only count failures from the last N days (default: all history)",
    )
    p_issues.add_argument(
        "--json",
        action="store_true",
        help="Output grouped issues as JSON",
    )

    # ── indexers ──
    p_indexers = sub.add_parser(
        "indexers",
        help="Show indexer details and stats. Exit 1 if no indexer is enabled",
    )
    p_indexers.add_argument(
        "--json",
        action="store_true",
        help="Output indexer details as JSON",
    )

    # ── stream monitors ──
    p_stream_monitors = sub.add_parser(
        "stream-monitors",
        help="List or remove saved stream monitor definitions",
    )
    sm_sub = p_stream_monitors.add_subparsers(dest="monitor_command", help="Monitor commands")
    p_stream_monitors.add_argument(
        "--json",
        action="store_true",
        help="Output monitor listings as JSON",
    )
    sm_sub.add_parser("list", help="List saved stream monitor definitions")
    p_monitor_remove = sm_sub.add_parser("remove", help="Remove a saved stream monitor definition")
    p_monitor_remove.add_argument("monitor_id", help="Stream monitor ID to remove")

    # ── stats ──
    p_stats = sub.add_parser("stats", help="Show an on-demand live system stats snapshot")
    p_stats.add_argument(
        "--json",
        action="store_true",
        help="Output the snapshot as JSON",
    )
    p_stats.add_argument(
        "--watch",
        action="store_true",
        help="Continuously print refreshed snapshots until Ctrl+C",
    )
    p_stats.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Seconds to wait between refreshes when using --watch (default: 2.0)",
    )
    p_stats.add_argument(
        "--sample-seconds",
        type=float,
        default=0.25,
        help="Sampling window used to estimate CPU/network/disk rates (default: 0.25)",
    )

    # ── queue ──
    p_queue = sub.add_parser(
        "queue",
        help="Inspect or control the shared upload queue (same actions as the WebUI)",
    )
    queue_sub = p_queue.add_subparsers(dest="queue_command", help="Queue commands")

    p_queue_status = queue_sub.add_parser("status", help="Show running, queued, and finished jobs (default)")
    p_queue_status.add_argument("--json", action="store_true", help="Output the queue snapshot as JSON")

    p_queue_pause = queue_sub.add_parser("pause", help="Pause queue processing and the active job")
    p_queue_pause.add_argument("--json", action="store_true", help="Output the result as JSON")

    p_queue_resume = queue_sub.add_parser("resume", help="Resume queue processing")
    p_queue_resume.add_argument("--json", action="store_true", help="Output the result as JSON")

    p_queue_stop = queue_sub.add_parser("stop", help="Stop the active job and pause the queue")
    p_queue_stop.add_argument(
        "--clear",
        action="store_true",
        help="Also clear queued jobs and hide the active row while it stops",
    )
    p_queue_stop.add_argument("--json", action="store_true", help="Output the result as JSON")

    p_queue_clear = queue_sub.add_parser("clear", help="Cancel all queued (not yet running) jobs")
    p_queue_clear.add_argument("--json", action="store_true", help="Output the result as JSON")

    p_queue_revalidate = queue_sub.add_parser(
        "revalidate",
        help="Re-scan queued/paused jobs against the current classification rules",
    )
    p_queue_revalidate.add_argument(
        "--skip-paused",
        action="store_true",
        help="Only revalidate queued jobs; leave paused jobs untouched",
    )
    p_queue_revalidate.add_argument("--json", action="store_true", help="Output the result as JSON")

    p_queue_job = queue_sub.add_parser(
        "job",
        help="Control a single job by ID (same actions as the per-job WebUI buttons)",
    )
    job_sub = p_queue_job.add_subparsers(dest="job_command", help="Per-job commands")

    p_job_pause = job_sub.add_parser("pause", help="Pause a running job")
    p_job_pause.add_argument("job_id", help="Job ID")
    p_job_pause.add_argument("--json", action="store_true", help="Output the result as JSON")

    p_job_resume = job_sub.add_parser("resume", help="Resume a paused job")
    p_job_resume.add_argument("job_id", help="Job ID")
    p_job_resume.add_argument("--json", action="store_true", help="Output the result as JSON")

    p_job_stop = job_sub.add_parser("stop", help="Stop a job")
    p_job_stop.add_argument("job_id", help="Job ID")
    p_job_stop.add_argument(
        "--clear",
        action="store_true",
        help="Also remove the job from the queue once stopped",
    )
    p_job_stop.add_argument("--json", action="store_true", help="Output the result as JSON")

    p_job_retry = job_sub.add_parser("retry", help="Queue a new attempt from a failed job's saved request")
    p_job_retry.add_argument("job_id", help="Job ID")
    p_job_retry.add_argument("--json", action="store_true", help="Output the result as JSON")

    p_job_promote = job_sub.add_parser("promote", help="Move a queued job to the front of the queue")
    p_job_promote.add_argument("job_id", help="Job ID")
    p_job_promote.add_argument("--json", action="store_true", help="Output the result as JSON")

    # ── config ──
    p_config = sub.add_parser("config", help="Read or update a configuration value")
    config_sub = p_config.add_subparsers(dest="config_command", help="Configuration commands")
    p_config_get = config_sub.add_parser("get", help="Read a dotted configuration key")
    p_config_get.add_argument("key", help="Configuration key, for example host or api_keys.geek")
    p_config_get.add_argument("--json", action="store_true", help="Output the result as JSON")
    p_config_set = config_sub.add_parser("set", help="Update a dotted configuration key")
    p_config_set.add_argument("key", help="Configuration key, for example debug or api_keys.geek")
    p_config_set.add_argument("value", help="YAML scalar, list, or mapping value")
    p_config_set.add_argument("--json", action="store_true", help="Output the result as JSON")

    # ── system ──
    p_system = sub.add_parser("system", help="Run update and service lifecycle controls")
    system_sub = p_system.add_subparsers(dest="system_command", help="System commands")
    p_system_update = system_sub.add_parser("update", help="Check, install, or roll back application updates")
    update_sub = p_system_update.add_subparsers(dest="update_command", help="Update commands")
    p_update_check = update_sub.add_parser("check", help="Check GitHub for an update now")
    p_update_check.add_argument("--json", action="store_true", help="Output the result as JSON")
    p_update_install = update_sub.add_parser("install", help="Install the latest or selected GitHub release")
    p_update_install.add_argument("--version", default=None, help="Release version or tag to install")
    p_update_install.add_argument("--no-restart", action="store_true", help="Do not restart after installation")
    p_update_install.add_argument("--json", action="store_true", help="Output the result as JSON")
    p_update_rollback = update_sub.add_parser("rollback", help="Restore a local updater backup")
    p_update_rollback.add_argument("backup_id", help="Backup ID returned by the updater")
    p_update_rollback.add_argument("--no-restart", action="store_true", help="Do not restart after rollback")
    p_update_rollback.add_argument("--json", action="store_true", help="Output the result as JSON")
    p_system_restart = system_sub.add_parser("restart", help="Stop work safely and schedule a restart")
    p_system_restart.add_argument(
        "--delay",
        type=_restart_delay,
        default=2.0,
        help="Seconds to wait between stopping and starting a managed daemon (default: 2)",
    )
    p_system_restart.add_argument("--no-stop", action="store_true", help="Do not stop active or queued jobs first")
    p_system_restart.add_argument(
        "--force",
        action="store_true",
        help="Restart a managed daemon even when graceful job shutdown times out",
    )
    p_system_restart.add_argument("--keep-staged-items", action="store_true", help="Keep staged temporary items")
    p_system_restart.add_argument("--wait-timeout", type=float, default=30.0, help="Seconds to wait for jobs to stop")
    p_system_restart.add_argument("--json", action="store_true", help="Output the result as JSON")
    p_system_stop = system_sub.add_parser("stop-all", help="Stop active jobs, clear waiting work, and wait until quiet")
    p_system_stop.add_argument("--keep-staged-items", action="store_true", help="Keep staged temporary items")
    p_system_stop.add_argument("--wait-timeout", type=float, default=30.0, help="Seconds to wait for jobs to stop")
    p_system_stop.add_argument("--json", action="store_true", help="Output the result as JSON")

    # ── logs ──
    p_logs = sub.add_parser("logs", help="Show recent console log lines from the shared in-memory buffer")
    p_logs.add_argument(
        "--lines",
        "-n",
        type=int,
        default=100,
        help="Number of recent lines to show (default: 100)",
    )
    p_logs.add_argument(
        "--json",
        action="store_true",
        help="Output log entries as JSON",
    )

    # ── version ──
    parser.add_argument(
        "--version",
        action="version",
        version=f"NZBPostarr {__version__}",
        help="Show the NZBPostarr version and exit",
    )

    return parser
