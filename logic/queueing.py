from typing import Any, Optional
import threading

from core.db import queue_items as db_queue_items
from core.config import get_config
from logic.jobs.models import INVALID_CATEGORY_VALUES, ProcessingJobRequest as ProcessingJobRequest
from logic.queueing_mixin_1 import _QueueServiceMixinPart1
from logic.queueing_mixin_2 import _QueueServiceMixinPart2
from logic.queueing_mixin_3 import _QueueServiceMixinPart3
from logic.queueing_mixin_4 import _QueueServiceMixinPart4
from logic.queueing_mixin_5 import _QueueServiceMixinPart5

class QueueServiceMixin(_QueueServiceMixinPart1, _QueueServiceMixinPart2, _QueueServiceMixinPart3, _QueueServiceMixinPart4, _QueueServiceMixinPart5):
    _QUEUE_INVALID_CATEGORY_VALUES = INVALID_CATEGORY_VALUES
    def _initialize_queue_state(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._processes: dict[str, list[Any]] = {}
        self._queue_processing_paused = False
        self._queue_scheduler_stop = threading.Event()
        self._queue_scheduler_thread: Optional[threading.Thread] = None
        self._queue_items: list[dict[str, Any]] = db_queue_items.db_load_queue()

        state_dir = get_config().script_dir / "data" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        self._jobs_state_path = state_dir / "job_queue_state.json"
        self._jobs_state_backup_path = state_dir / "job_queue_state.json.bak"

        with self._lock:
            self._restore_jobs_from_disk_locked()

        self._queue_scheduler_thread = threading.Thread(target=self._queue_scheduler_loop, daemon=True)
        self._queue_scheduler_thread.start()
        self._try_start_queued()

