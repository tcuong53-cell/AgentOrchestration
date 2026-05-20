import logging
import time
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# --- State Management Components (Mocked for demonstration, in a real system these would interact with a durable store) ---

class RunStatus:
    """Defines possible statuses for a run within the orchestration system."""
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    RETRYING = "RETRYING" # Indicates the run is actively undergoing retry processing

class RunState:
    """
    Represents the current durable state of a run.
    This object would typically be deserialized from a database record or cache.
    """
    def __init__(self, run_id: str, status: str,
                 last_updated_timestamp: float,
                 last_processed_event_id: Optional[str] = None):
        self.run_id = run_id
        self.status = status
        self.last_updated_timestamp = last_updated_timestamp
        self.last_processed_event_id = last_processed_event_id

    def is_terminal(self) -> bool:
        """
        Determines if the run is in a final, non-retryable state.
        Once a run reaches one of these states, no further processing (including retries)
        should alter its outcome.
        """
        return self.status in [RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED]

    def __repr__(self):
        return (f"RunState(run_id='{self.run_id}', status='{self.status}', "
                f"last_updated_timestamp={self.last_updated_timestamp}, "
                f"last_processed_event_id='{self.last_processed_event_id}')")

# In-memory mock for run states. In a production environment, this would be a database or distributed cache.
_RUN_STATES: Dict[str, RunState] = {}

def get_run_state(run_id: str) -> RunState:
    """
    Mocks fetching the current run state from a persistent store.
    If the run_id is not found, it simulates a new run starting in PENDING state.
    """
    # Simulate fetching from storage, defaulting to PENDING if not found.
    # In a real system, this would be a read operation from a database.
    return _RUN_STATES.get(run_id, RunState(run_id, RunStatus.PENDING, time.time()))

def update_run_state(run_id: str, new_status: str,
                     event_timestamp: float, event_id: Optional[str] = None) -> RunState:
    """
    Mocks updating run state in a persistent store with optimistic concurrency control.
    This function acts as a critical section for state transitions, ensuring that:
    1. Only newer events can update the state (based on event_timestamp).
    2. Exact duplicate events are not re-processed unnecessarily (based on event_id).
    3. The `last_updated_timestamp` reflects the actual time of this write operation.

    Returns the newly updated RunState or the current RunState if no update occurred due to concurrency checks.
    """
    # In a real system, this would involve a transactional update (e.g., SQL "UPDATE ... WHERE version = :old_version").
    # For this mock, we perform checks before updating the in-memory dictionary.
    
    current_state = _RUN_STATES.get(run_id)
    
    # Pre-check: If current_state exists, apply optimistic locking rules.
    if current_state:
        # Prevent updating with an event that is strictly older than the state's last recorded update.
        # This is the primary guard against out-of-order events overwriting newer state.
        if event_timestamp < current_state.last_updated_timestamp:
            logger.warning(
                f"[{run_id}] Attempted to update with stale event (new_status={new_status}). "
                f"Event ts: {event_timestamp}, Current state ts: {current_state.last_updated_timestamp}. "
                "Skipping update to prevent overwriting newer state."
            )
            return current_state
        
        # Prevent re-processing the exact same event if its ID matches and its timestamp
        # is not strictly newer than the last processed event's timestamp.
        # This handles cases where the same event might be re-delivered.
        if event_id and event_id == current_state.last_processed_event_id and \
           event_timestamp <= current_state.last_updated_timestamp:
            logger.info(
                f"[{run_id}] Duplicate event {event_id} at timestamp {event_timestamp}. "
                "Skipping state update as this event has already been processed or a newer state exists."
            )
            return current_state

    # If checks pass, perform the state update.
    # The `last_updated_timestamp` is set to the current system time, reflecting when the state was *persisted*.
    # The `last_processed_event_id` tracks the specific event that successfully caused this state transition.
    new_run_state = RunState(run_id, new_status, time.time(), event_id)
    _RUN_STATES[run_id] = new_run_state
    logger.info(f"[{run_id}] State transitioned to {new_status} (event_id: {event_id if event_id else 'N/A'}).")
    return new_run_state

# --- Core Retry Runtime Logic ---

def process_retry_event(run_id: str, event_payload: Dict[str, Any]) -> None:
    """
    Processes a retry event for a given run within the agent orchestration runtime.
    This function implements crucial safeguards to ensure idempotency, correct ordering,
    and prevents any activity for runs that have already reached a terminal state.

    Args:
        run_id: The unique identifier of the run to process.
        event_payload: A dictionary containing details about the retry event.
                       It is expected to include 'timestamp' (float) and optionally 'event_id' (str).
    """
    logger.debug(f"[{run_id}] Received retry event with payload: {event_payload}")

    # Extract event metadata for idempotency and ordering checks.
    # It's crucial that `event_timestamp` reflects the logical time the event occurred.
    event_timestamp = event_payload.get('timestamp')
    event_id = event_payload.get('event_id')

    if event_timestamp is None:
        logger.error(f"[{run_id}] Retry event missing required 'timestamp' field. Cannot ensure ordering. Skipping event.")
        return

    # Fetch the current durable state of the run. This must be the most up-to-date state.
    current_run_state = get_run_state(run_id)

    # --- 1. State-machine guard: Stop retry loop after terminal run state ---
    # This is the primary fix for the reported bug. If a run has already completed,
    # failed, or been cancelled, no further retry processing should occur.
    if current_run_state.is_terminal():
        logger.warning(
            f"[{run_id}] Run is already in a terminal state ({current_run_state.status}). "
            "Skipping retry processing to prevent duplicate work or state corruption."
        )
        return

    # --- 2. Idempotency and Ordering Guards ---
    # These checks prevent processing stale or exact duplicate events, crucial for distributed systems.

    # Prevent processing of stale events: If the incoming event's timestamp is
    # older than the last update timestamp of the current state, it's an out-of-order event.
    if event_timestamp < current_run_state.last_updated_timestamp:
        logger.warning(
            f"[{run_id}] Received stale retry event. Event ts: {event_timestamp}, "
            f"Current state ts: {current_run_state.last_updated_timestamp}. "
            "Skipping processing to prevent overwriting newer state."
        )
        return

    # Prevent re-processing the exact same event: If the event_id matches the last processed
    # and the event timestamp is not strictly newer, it's a duplicate of an already handled event.
    if event_id and event_id == current_run_state.last_processed_event_id and \
       event_timestamp <= current_run_state.last_updated_timestamp:
        logger.info(
            f"[{run_id}] Received duplicate retry event {event_id} at timestamp {event_timestamp}. "
            "Skipping processing as it has already been handled or a newer state exists."
        )
        return

    # --- 3. Execute Actual Idempotent Retry Logic ---
    # This block contains the core business logic to react to a retry event.
    # It must be designed to be idempotent and handle concurrent updates robustly.
    try:
        logger.info(f"[{run_id}] Initiating actual retry logic (current_state={current_run_state.status}).")

        # Example: Transition the run to a 'RETRYING' state if it's not already.
        # This explicitly signals that the system is actively working on re-evaluating the run.
        if current_run_state.status != RunStatus.RETRYING:
            # Persist the state change *before* any potential side effects or re-queueing attempts.
            # This ensures durable state reflects the system's intent.
            current_run_state = update_run_state(run_id, RunStatus.RETRYING, event_timestamp, event_id)
            if current_run_state.status != RunStatus.RETRYING:
                # This could happen if another concurrent update won a race condition.
                # In such a case, we should re-evaluate or gracefully exit if the new state is terminal.
                logger.warning(f"[{run_id}] State changed unexpectedly during retry processing to {current_run_state.status}. Re-evaluating.")
                if current_run_state.is_terminal():
                    logger.info(f"[{run_id}] Run became terminal during retry attempt. Aborting.")
                    return # Exit if it turned terminal concurrently

        # Placeholder: This is where the specific logic for *what to do* on a retry event lives.
        # This could involve:
        # - Checking retry counts and backoff policies (for "bounded" retries).
        # - Re-evaluating run conditions.
        # - Emitting a new event to re-queue the run for a worker.
        # - Updating specific internal retry attempt counters.

        # For this example, if the run is now in RETRYING, we might decide to re-queue it.
        if current_run_state.status == RunStatus.RETRYING:
            target_status = RunStatus.QUEUED # Example: re-queue the run for another attempt
            if current_run_state.status != target_status:
                logger.info(f"[{run_id}] Determined to re-queue the run due to retry event.")
                current_run_state = update_run_state(run_id, target_status, event_timestamp, event_id)
                logger.info(f"[{run_id}] Run has been transitioned to {target_status}.")
            else:
                logger.debug(f"[{run_id}] Run is already in {target_status} state; no status change required for this retry event.")
        else:
            logger.info(f"[{run_id}] Run is no longer in {RunStatus.RETRYING} state ({current_run_state.status}), deferring to other handlers or concurrent operations.")

    except Exception as e:
        logger.critical(f"[{run_id}] Unhandled exception during retry processing: {e}", exc_info=True)
        # In case of an unexpected system error, transition the run to a FAILED state
        # to prevent it from getting stuck and to trigger appropriate alerts.
        # This state update should be attempted even in error conditions to maintain consistency.
        update_run_state(run_id, RunStatus.FAILED, event_timestamp, event_id)

    logger.debug(f"[{run_id}] Finished processing retry event. Final state: {current_run_state.status}")