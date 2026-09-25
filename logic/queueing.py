from logic.queueing_base import (
    Any as Any, BaseModel as BaseModel, ConfigDict as ConfigDict, Field as Field, JobState as JobState, Optional as Optional, Path as Path,
    ProcessingJobRequest as ProcessingJobRequest, QueueStartSummary as QueueStartSummary, StreamJobRequest as StreamJobRequest,
    _FINISHED_JOB_RETENTION_COUNT as _FINISHED_JOB_RETENTION_COUNT, _FINISHED_JOB_RETENTION_DAYS as _FINISHED_JOB_RETENTION_DAYS,
    _QUEUE_SOURCE_TOKEN_RE as _QUEUE_SOURCE_TOKEN_RE, _TERMINAL_JOB_STATUSES as _TERMINAL_JOB_STATUSES, atomic_write_text as atomic_write_text,
    database as database, dataclass as dataclass, datetime as datetime, get_config as get_config, json as json, log_info as log_info,
    log_success as log_success, logger as logger, looks_like_generic_tv_season_folder as looks_like_generic_tv_season_folder,
    normalize_submission_category as normalize_submission_category, os as os, psutil as psutil, re as re, reset_thread_job as reset_thread_job,
    set_thread_job as set_thread_job, shutil as shutil, threading as threading, time as time, timedelta as timedelta, timezone as timezone,
    usenet_stream as usenet_stream, uuid as uuid,
)
from logic.queueing_mixin_1 import _QueueServiceMixinPart1
from logic.queueing_mixin_2 import _QueueServiceMixinPart2
from logic.queueing_mixin_3 import _QueueServiceMixinPart3
from logic.queueing_mixin_4 import _QueueServiceMixinPart4
from logic.queueing_mixin_5 import _QueueServiceMixinPart5

class QueueServiceMixin(_QueueServiceMixinPart1, _QueueServiceMixinPart2, _QueueServiceMixinPart3, _QueueServiceMixinPart4, _QueueServiceMixinPart5):
    _QUEUE_INVALID_CATEGORY_VALUES = {"", "external", "null", "none", "unknown", "undefined"}
    def _initialize_queue_state(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._processes: dict[str, list[Any]] = {}
        self._queue_processing_paused = False
        self._queue_scheduler_stop = threading.Event()
        self._queue_scheduler_thread: Optional[threading.Thread] = None
        self._queue_items: list[dict[str, Any]] = database.db_load_queue()

        state_dir = get_config().script_dir / "data" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        self._jobs_state_path = state_dir / "job_queue_state.json"
        self._jobs_state_backup_path = state_dir / "job_queue_state.json.bak"

        with self._lock:
            self._restore_jobs_from_disk_locked()

        self._queue_scheduler_thread = threading.Thread(target=self._queue_scheduler_loop, daemon=True)
        self._queue_scheduler_thread.start()
        self._try_start_queued()

