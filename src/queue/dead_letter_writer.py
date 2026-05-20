import logging
import datetime
from typing import Any, Dict, Optional, Set, Literal

logger = logging.getLogger(__name__)

# --- Interfaces/Abstractions ---

# Define Literal types for lifecycle states for better type hinting and clarity.
# In a real-world system, these would likely come from an enum, shared constants,
# or a configuration service to ensure consistency across components.
LifecycleState = Literal[
    "PENDING",
    "PROCESSING",
    "COMPLETED",
    "FAILED",
    "DEAD_LETTERED",
    "CANCELLED",
    "ARCHIVED",
    "REJECTED",
    "RETRYING",
    "UNKNOWN_STATE" # Added for robustness if an unexpected state is encountered
]

class ItemStateManagerError(Exception):
    """Base exception for ItemStateManager operations."""
    pass

class ItemNotFoundError(ItemStateManagerError):
    """Raised when an item is not found in the state manager."""
    pass

class StateConflictError(ItemStateManagerError):
    """
    Raised when an optimistic lock check fails due to a state or version mismatch,
    indicating a concurrent modification. This is key for idempotent retries.
    """
    pass

class ItemStateManager:
    """
    Manages the authoritative lifecycle state of items (e.g., agent runs, tasks, handlers).
    This component is crucial for enforcing idempotency by providing the
    current authoritative state from the primary system of record and enabling
    transactional updates.
    """
    def get_current_item_state(self, item_id: str) -> Dict[str, Any]:
        """
        Retrieves the current state information of an item by its ID.
        Expected return dictionary structure:
        {'state': LifecycleState, 'version': N, 'last_updated': TIMESTAMP_UTC, ...}
        Raises ItemNotFoundError if the item does not exist.
        Raises ItemStateManagerError for other retrieval issues (e.g., database connection error).
        """
        raise NotImplementedError("ItemStateManager.get_current_item_state must be implemented.")

    def mark_item_dead_lettered_transactionally(
        self,
        item_id: str,
        expected_version: int,
        current_state_at_read: LifecycleState,
        dlq_timestamp: str,
        reason: Dict[str, Any]
    ) -> None:
        """
        Atomically updates the item's state to 'DEAD_LETTERED' if and only if
        its current state matches `current_state_at_read` AND its version matches `expected_version`.
        This operation serves as the "durable claim/ack" part of the transaction,
        ensuring that the primary system of record is updated consistently and idempotently.

        Args:
            item_id: The unique identifier of the item.
            expected_version: The version of the item's state at the time it was read,
                              used for optimistic concurrency control.
            current_state_at_read: The state of the item at the time it was read,
                                   also used for optimistic concurrency to prevent
                                   invalid state transitions.
            dlq_timestamp: The UTC timestamp when the dead-letter decision was made.
            reason: Additional context about the dead-lettering, including original
                    triggering state and attempt ID, to be stored with the item's state.

        Raises:
            ItemNotFoundError: If the item does not exist at the time of update.
            StateConflictError: If the expected version or current state does not match,
                                indicating a concurrent modification.
            ItemStateManagerError: For other failures during the update process.
        """
        raise NotImplementedError("ItemStateManager.mark_item_dead_lettered_transactionally must be implemented.")

class DeadLetterQueueClient:
    """
    Interface for interacting with the underlying dead-letter queue mechanism.
    Implementations are expected to handle their own internal idempotency
    (e.g., deduplication by item_id and a unique `dlq_attempt_id` if supported by the DLQ technology).
    """
    def enqueue(self, item_id: str, payload: Dict[str, Any]):
        """
        Enqueues an item's details into the dead-letter queue.
        This operation should be robust; any failures here must be handled by the caller.
        The underlying DLQ mechanism should ideally offer its own deduplication guarantees
        for repeated attempts to enqueue the same `item_id` with the same `dlq_attempt_id`.
        """
        raise NotImplementedError("DeadLetterQueueClient.enqueue must be implemented.")

# --- The DeadLetterWriter Component ---

class DeadLetterWriter:
    """
    Handles writing items to the dead-letter queue, ensuring idempotency
    to prevent stale, duplicate, or policy-violating transitions. This is
    especially critical during acknowledgement retries of failed processing attempts.
    The component enforces state consistency through interaction with ItemStateManager
    and manages the dead-lettering process.
    """

    # Define a set of lifecycle states that signify an item has reached a final
    # or irrevocably handled status. If an item is in one of these states, and
    # a dead-letter request arrives for an earlier state, it's considered stale.
    _TERMINAL_STATES: Set[LifecycleState] = {
        "COMPLETED",
        "FAILED",
        "DEAD_LETTERED",
        "CANCELLED",
        "ARCHIVED",
        "REJECTED"
    }

    def __init__(self, item_state_manager: ItemStateManager, dlq_client: DeadLetterQueueClient):
        # Enforce type contracts at initialization for critical dependencies
        if not isinstance(item_state_manager, ItemStateManager):
            raise TypeError("item_state_manager must be an instance of ItemStateManager")
        if not isinstance(dlq_client, DeadLetterQueueClient):
            raise TypeError("dlq_client must be an instance of DeadLetterQueueClient")

        # Verify critical methods are implemented upon initialization to fail fast
        # if the provided implementations are incomplete.
        for method_name in ['get_current_item_state', 'mark_item_dead_lettered_transactionally']:
            if not hasattr(item_state_manager, method_name) or not callable(getattr(item_state_manager, method_name)):
                raise NotImplementedError(
                    f"ItemStateManager must implement '{method_name}' for DeadLetterWriter to function reliably. "
                    f"Check the ItemStateManager implementation."
                )
        if not hasattr(dlq_client, 'enqueue') or not callable(getattr(dlq_client, 'enqueue')):
            raise NotImplementedError(
                f"DeadLetterQueueClient must implement 'enqueue' for DeadLetterWriter to function reliably. "
                f"Check the DeadLetterQueueClient implementation."
            )

        self._item_state_manager = item_state_manager
        self._dlq_client = dlq_client

    def write_dead_letter(
        self,
        item_id: str,
        error_details: Dict[str, Any],
        triggering_lifecycle_state: LifecycleState,
        attempt_id: str,
    ) -> bool:
        """
        Attempts to write an item to the dead-letter queue, strictly enforcing idempotency
        and transactional integrity. This method includes robust checks against stale,
        duplicate, or policy-violating state transitions, especially during retries.

        Args:
            item_id: The unique identifier of the item (e.g., agent run ID, task ID, handler ID).
                     Must not be empty.
            error_details: A dictionary containing comprehensive details about the error or failure
                           that led to this dead-lettering request. Must not be empty.
            triggering_lifecycle_state: The precise lifecycle state of the item *at the moment
                                        the original failure occurred*. This is mandatory and
                                        critical for determining if the current DLQ write is still relevant.
            attempt_id: A unique identifier for the specific processing/acknowledgement attempt
                        that failed. This is mandatory for logging, auditing, and potential
                        DLQ-level deduplication.

        Returns:
            True if the item was successfully written to the dead-letter queue, or
            if the write was deemed redundant/stale and safely skipped (idempotently).
            False if a critical, unrecoverable error occurred during the process,
            preventing the item from being dead-lettered due to system issues or
            fundamental inconsistencies, which requires operator intervention.
        """
        # Validate mandatory input arguments upfront
        if not item_id or not triggering_lifecycle_state or not attempt_id or not error_details:
            logger.error(
                f"DEAD_LETTER_WRITE_REJECTED: Missing required arguments. "
                f"item_id='{item_id}', triggering_state='{triggering_lifecycle_state}', "
                f"attempt_id='{attempt_id}', error_details_present={bool(error_details)}. "
                f"Cannot perform dead-letter write."
            )
            return False

        log_context = (
            f"item_id='{item_id}', "
            f"triggering_state='{triggering_lifecycle_state}', "
            f"attempt_id='{attempt_id}'"
        )
        logger.info(f"DEAD_LETTER_WRITE_INITIATED: Initiating dead-letter write for {log_context}.")

        dlq_timestamp_utc = self._get_current_timestamp_utc()

        try:
            # 1. Retrieve the *current* authoritative state of the item from the system of record.
            # This is the primary mechanism to detect truly stale or policy-violating transitions.
            current_item_state_info = self._item_state_manager.get_current_item_state(item_id)
            current_lifecycle_state: LifecycleState = current_item_state_info.get("state", "UNKNOWN_STATE") # type: ignore
            current_version: int = current_item_state_info.get("version", 0) # type: ignore

            if current_lifecycle_state == "UNKNOWN_STATE":
                logger.error(
                    f"DEAD_LETTER_WRITE_FAILED: Retrieved item state for {item_id} has no valid 'state' field. "
                    f"Cannot reliably determine lifecycle status. {log_context}"
                )
                return False

            # 2. Core Idempotency Enforcement: Compare current state with the triggering state.
            # If the item's state has already progressed beyond the state where the
            # failure occurred, or if it's in a terminal state, this dead-letter write
            # for the *past* failure is stale and should be skipped to prevent duplicates
            # or policy violations.

            if current_lifecycle_state == "DEAD_LETTERED":
                logger.warning(
                    f"DEAD_LETTER_WRITE_SKIPPED: Item is already marked as 'DEAD_LETTERED'. "
                    f"Skipping redundant dead-letter write. {log_context}"
                )
                return True # Idempotent skip (already handled)
            elif (current_lifecycle_state in self._TERMINAL_STATES) and \
                 (current_lifecycle_state != triggering_lifecycle_state):
                # If the item has reached any other terminal state (e.g., COMPLETED, FAILED, CANCELLED)
                # AND this state is different from the state that *triggered* this DLQ request,
                # then this DLQ request is stale. The item's lifecycle has already moved on.
                logger.warning(
                    f"DEAD_LETTER_WRITE_REJECTED: Item's current state ('{current_lifecycle_state}') "
                    f"is a terminal state and differs from triggering state ('{triggering_lifecycle_state}'). "
                    f"Rejecting stale dead-letter write. {log_context}"
                )
                return True # Idempotent skip (stale request)
            elif current_lifecycle_state == triggering_lifecycle_state:
                logger.debug(
                    f"DEAD_LETTER_STATE_MATCH: Current state matches triggering state ('{current_lifecycle_state}'). "
                    f"Proceeding with dead-letter queueing. {log_context}"
                )
            else:
                # The item is in a non-terminal state, but it's different from the triggering state.
                # This means it might have been retried, transitioned to an intermediate state,
                # or is still actively being processed. Rejecting this DLQ write as it's not
                # for the current state, and the item is not yet in a terminal state.
                # This prevents policy-violating transitions and ensures the DLQ reflects
                # the *actual* state at failure.
                logger.warning(
                    f"DEAD_LETTER_WRITE_REJECTED: Item's current state ('{current_lifecycle_state}') "
                    f"differs from triggering state ('{triggering_lifecycle_state}') and is not "
                    f"yet terminal. Rejecting dead-letter write to preserve expected lifecycle state. {log_context}"
                )
                return True # Idempotent skip (policy-violating or out-of-order request)

            # 3. Prepare payload for the Dead-Letter Queue.
            payload = {
                "item_id": item_id,
                "error_details": error_details,
                "failed_state_at_trigger": triggering_lifecycle_state,
                "current_state_at_dlq_attempt": current_lifecycle_state, # State when DLQ decision is made
                "dlq_attempt_id": attempt_id,
                "dlq_timestamp_utc": dlq_timestamp_utc
            }

            # 4. Enqueue to DLQ and atomically update item state in a single logical transaction.
            # This implements the "durable claim/enqueue/ack transaction" requirement.
            # We assume ItemStateManager.mark_item_dead_lettered_transactionally
            # will handle the actual state update with optimistic locking.
            # The order here is important: first enqueue, then mark. If mark fails, the DLQ
            # still has the message, which can be reprocessed, but the ItemStateManager is authoritative.
            # The `mark_item_dead_lettered_transactionally` should consider the enqueue
            # successful *before* committing the state change.

            self._dlq_client.enqueue(item_id, payload)
            logger.info(
                f"DEAD_LETTER_ENQUEUED: Successfully enqueued item to dead-letter queue. "
                f"Proceeding to mark item as DEAD_LETTERED in state manager. {log_context}"
            )

            # Atomically mark the item as dead-lettered in the primary system of record.
            # This must use optimistic concurrency control (`expected_version` and `current_state_at_read`)
            # to ensure no concurrent state changes have occurred since we retrieved `current_item_state_info`.
            self._item_state_manager.mark_item_dead_lettered_transactionally(
                item_id=item_id,
                expected_version=current_version,
                current_state_at_read=current_lifecycle_state,
                dlq_timestamp=dlq_timestamp_utc,
                reason={"triggering_state": triggering_lifecycle_state, "attempt_id": attempt_id, **error_details}
            )
            logger.info(
                f"DEAD_LETTER_WRITE_SUCCESS: Successfully marked item as 'DEAD_LETTERED' in state manager. "
                f"Dead-letter write process complete. {log_context}"
            )
            return True

        except ItemNotFoundError as e:
            logger.error(
                f"DEAD_LETTER_WRITE_FAILED: Item not found in state manager during dead-letter write. "
                f"This indicates a fundamental data inconsistency or race condition. {log_context}: {e}",
                exc_info=True
            )
            return False # Critical failure, cannot proceed reliably

        except StateConflictError as e:
            logger.warning(
                f"DEAD_LETTER_WRITE_SKIPPED: Item's state or version changed concurrently while attempting "
                f"to mark as dead-lettered (optimistic lock failed). This is expected during retries under contention "
                f"and means another process has already handled the item's state transition. "
                f"Treating as an idempotent skip. {log_context}: {e}",
                exc_info=True
            )
            # This is a successful idempotent skip from the perspective of the DLQ write.
            # The item's state has already moved on in a way that makes this DLQ attempt invalid,
            # or it was already dead-lettered by a concurrent process.
            return True

        except ItemStateManagerError as e:
            logger.error(
                f"DEAD_LETTER_WRITE_FAILED: Critical ItemStateManager error during dead-letter write. "
                f"Cannot reliably update item state. This requires urgent investigation. {log_context}: {e}",
                exc_info=True
            )
            return False # Critical failure

        except Exception as e:
            logger.error(
                f"DEAD_LETTER_WRITE_FAILED: An unexpected error occurred during dead-letter write. "
                f"Failed to enqueue to DLQ or update item state. This requires urgent investigation. {log_context}: {e}",
                exc_info=True
            )
            return False # Unexpected critical failure

    def _get_current_timestamp_utc(self) -> str:
        """Helper to get a current UTC timestamp string in ISO 8601 format with milliseconds and 'Z' suffix."""
        return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds') + 'Z'