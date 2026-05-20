import logging
import threading
import uuid
from typing import Dict, Set

logger = logging.getLogger(__name__)

class CapacityLimiter:
    """
    Manages the available capacity for queueing operations, enforcing transactional integrity
    and idempotency for individual items.
    It supports acquiring capacity as 'pending' before committing it as 'in-use',
    allowing for transaction rollbacks by tracking capacity per unique item ID.
    Thread-safe to handle concurrent requests.
    """
    def __init__(self, total_capacity: int):
        if not isinstance(total_capacity, int) or total_capacity < 0:
            raise ValueError("Total capacity must be a non-negative integer.")
        self._total_capacity = total_capacity
        self._lock = threading.Lock() # For thread-safety

        # Track pending and in-use capacity by item_id.
        # Value is the amount of capacity consumed by that item (typically 1 for jobs).
        self._pending_acquisitions: Dict[str, int] = {}
        self._in_use_acquisitions: Dict[str, int] = {}

        # Aggregate counts for quick checks and debugging
        self._current_total_pending = 0
        self._current_total_in_use = 0

    def _calculate_available(self) -> int:
        """Calculates truly available capacity."""
        return self._total_capacity - self._current_total_in_use - self._current_total_pending

    def acquire_capacity(self, item_id: str, amount: int = 1) -> bool:
        """
        Attempts to acquire 'amount' of capacity for a specific item_id.
        This reserves capacity in a 'pending' state. If the overall transaction fails,
        this pending capacity *must* be explicitly released using release_capacity.
        The operation is idempotent: if capacity for item_id is already pending,
        it returns True without re-acquiring.
        Returns True if capacity was successfully acquired or already pending, False otherwise.
        """
        if amount <= 0:
            raise ValueError("Amount to acquire must be positive.")
        if not item_id:
            raise ValueError("item_id must be a non-empty string for transactional capacity management.")

        with self._lock:
            # Idempotency check: if this item_id already has pending capacity,
            # consider it already acquired.
            if item_id in self._pending_acquisitions:
                logger.debug(f"Acquire for item '{item_id}': capacity already pending. Idempotent success.")
                return True

            # Idempotency check: if this item_id is already in-use,
            # it implies it was successfully enqueued previously.
            # This is generally a logical error if we're trying to *acquire* for it again.
            if item_id in self._in_use_acquisitions:
                logger.warning(
                    f"Acquire for item '{item_id}': capacity already in use. "
                    "This suggests a retry for an already committed item. "
                    "Idempotently returning True, but caller should verify retry logic."
                )
                return True

            if self._calculate_available() >= amount:
                self._pending_acquisitions[item_id] = amount
                self._current_total_pending += amount
                logger.debug(
                    f"Acquired {amount} pending capacity for item '{item_id}'. "
                    f"Total pending: {self._current_total_pending}, Available: {self._calculate_available()}"
                )
                return True
            logger.warning(
                f"Failed to acquire {amount} capacity for item '{item_id}'. "
                f"Only {self._calculate_available()} available."
            )
            return False

    def commit_capacity(self, item_id: str):
        """
        Commits previously acquired pending capacity for a specific item_id, moving it to 'in-use'.
        This should be called when the transaction associated with the acquisition is successful.
        The operation is idempotent: if capacity for item_id is already in-use, it returns without error.
        """
        if not item_id:
            raise ValueError("item_id must be a non-empty string for transactional capacity management.")

        with self._lock:
            # Idempotency check: if this item is already in-use, consider it committed.
            if item_id in self._in_use_acquisitions:
                logger.debug(f"Commit for item '{item_id}': capacity already in use. Idempotent success.")
                return

            if item_id not in self._pending_acquisitions:
                raise ValueError(
                    f"Commit for item '{item_id}': Attempted to commit capacity but it was not found "
                    "in pending acquisitions. This indicates a logic error (e.g., committing capacity not acquired "
                    "or already committed/released)."
                )

            amount_to_commit = self._pending_acquisitions.pop(item_id)
            self._current_total_pending -= amount_to_commit

            self._in_use_acquisitions[item_id] = amount_to_commit
            self._current_total_in_use += amount_to_commit

            logger.debug(
                f"Committed {amount_to_commit} capacity for item '{item_id}'. "
                f"Total in-use: {self._current_total_in_use}, Total pending: {self._current_total_pending}"
            )

    def release_capacity(self, item_id: str):
        """
        Releases capacity for a specific item_id. This can be either pending or in-use capacity.
        Typically used to release pending capacity on transaction rollback,
        or in-use capacity when a job/task is completed.
        The operation is idempotent: if capacity for item_id is not found, it logs a warning but takes no action.
        """
        if not item_id:
            raise ValueError("item_id must be a non-empty string for transactional capacity management.")

        with self._lock:
            released_amount = 0
            if item_id in self._pending_acquisitions:
                released_amount = self._pending_acquisitions.pop(item_id)
                self._current_total_pending -= released_amount
                logger.debug(
                    f"Release for item '{item_id}': released {released_amount} pending capacity. "
                    f"Total pending: {self._current_total_pending}"
                )
            elif item_id in self._in_use_acquisitions:
                released_amount = self._in_use_acquisitions.pop(item_id)
                self._current_total_in_use -= released_amount
                logger.debug(
                    f"Release for item '{item_id}': released {released_amount} in-use capacity. "
                    f"Total in-use: {self._current_total_in_use}"
                )
            else:
                # If capacity for this item_id is not found, it implies a logical error
                # or a duplicate release call (idempotent release).
                logger.warning(
                    f"Release for item '{item_id}': Capacity not found in pending or in-use acquisitions. "
                    "This suggests a logic error or redundant release. No capacity counts were affected."
                )
            # No change to counts if not found, preserving idempotency for release.

    def get_status(self) -> Dict:
        """Returns a dictionary with current capacity stats, including detailed item tracking."""
        with self._lock:
            return {
                "total_capacity": self._total_capacity,
                "current_in_use_count": self._current_total_in_use,
                "pending_acquired_count": self._current_total_pending,
                "available_capacity": self._calculate_available(),
                "details_pending_acquisitions": self._pending_acquisitions.copy(), # Return copies to prevent external modification
                "details_in_use_acquisitions": self._in_use_acquisitions.copy(),
            }

class QueueManager:
    """
    Manages the overall queueing process, including capacity management and transactional enqueue.
    It integrates with CapacityLimiter to ensure transactional integrity and idempotency.
    """
    def __init__(self, capacity_limiter: CapacityLimiter):
        self.capacity_limiter = capacity_limiter
        # Simulate an actual queue/database interface; replace with real persistence.
        # Using a dictionary for O(1) item lookup, crucial for idempotency checks.
        self._internal_queue_db: Dict[str, Dict] = {}

    def enqueue_item_transactionally(self, item_data: Dict) -> str:
        """
        Enqueues an item, ensuring capacity is managed transactionally and idempotently.
        A unique item_id is used for all transactional steps.
        If any part of the enqueue process (e.g., database write, state update)
        fails, the acquired capacity is rolled back.

        Args:
            item_data: Dictionary containing data for the item to be enqueued.
                       If 'id' is present, it will be used as the item_id; otherwise, a UUID is generated.

        Returns:
            A unique ID for the enqueued item if successful.

        Raises:
            CapacityExceededError: If no capacity is available.
            EnqueueTransactionError: If any step within the transaction fails.
        """
        # Generate or use a unique item_id early for all transactional steps.
        # This ID is critical for idempotency and tracking.
        item_id = item_data.get('id')
        if not item_id:
            item_id = f"item_{uuid.uuid4()}_{item_data.get('type', 'generic')}"
            item_data['id'] = item_id # Ensure item_data consistently holds the ID

        # 1. First, attempt to acquire capacity. This is the initial step of the transaction.
        #    If this fails, no rollback is needed for capacity as it wasn't acquired.
        #    CapacityLimiter's acquire_capacity handles idempotency for the item_id.
        if not self.capacity_limiter.acquire_capacity(item_id=item_id, amount=1):
            raise CapacityExceededError(
                f"Cannot enqueue item '{item_id}': Queue capacity limit reached."
            )

        # --- Transaction boundary starts here ---
        # The 'try' block encapsulates the operations that constitute the "transaction".
        # If any exception occurs within this block, the 'except' block will handle rollback.
        try:
            logger.info(f"Starting transactional enqueue for item: {item_id}")

            # Idempotency check for the actual queue storage:
            # If the item is already in our simulated queue, it means a previous attempt
            # successfully added it, and we might be retrying after a partial success
            # (e.g., commit_capacity succeeded, but the return statement failed).
            if item_id in self._internal_queue_db:
                logger.warning(
                    f"Item '{item_id}' already found in internal queue. "
                    "Assuming idempotent retry of enqueue operation. "
                    "Verifying capacity commitment and returning existing item ID."
                )
                # Even if the item is already in the queue, ensure its capacity is marked as committed.
                # The commit_capacity method itself handles idempotency, so calling it again is safe.
                self.capacity_limiter.commit_capacity(item_id=item_id)
                return item_id # Item already enqueued successfully

            # Placeholder for actual persistence logic or other transactional operations:
            # - Write item data to a persistent store (e.g., database)
            # - Update associated lifecycle states (agent run, task, handler)
            # - Any other logic that might fail before committing capacity
            self._internal_queue_db[item_id] = {'id': item_id, 'data': item_data, 'status': 'enqueued'}

            # If all preceding transaction steps are successful, commit the acquired capacity.
            # This moves it from 'pending' to 'in-use', signaling it's officially consumed.
            # CapacityLimiter's commit_capacity handles idempotency for the item_id.
            self.capacity_limiter.commit_capacity(item_id=item_id)

            logger.info(f"Successfully enqueued and committed capacity for item: {item_id}")
            return item_id

        except Exception as e:
            # --- TRANSACTION FAILURE / ROLLBACK PATH ---
            # THIS IS THE CORE FIX:
            # If any part of the transaction fails, the previously acquired *pending* capacity
            # must be released to prevent a stale capacity state, effectively rolling it back.
            logger.error(
                f"Transactional enqueue failed for item '{item_id}'. "
                "Rolling back acquired capacity due to transaction failure."
            )
            # The bug fix is implemented here: ensure capacity is released on rollback.
            # Pass item_id for explicit, idempotent release.
            self.capacity_limiter.release_capacity(item_id=item_id)
            raise EnqueueTransactionError(
                f"Failed to complete transactional enqueue for item '{item_id}': {e}"
            ) from e
        # --- Transaction boundary ends here ---

# Custom exceptions for clarity
class CapacityExceededError(Exception):
    """Raised when queue capacity limits are reached."""
    pass

class EnqueueTransactionError(Exception):
    """Raised when a transactional enqueue operation fails."""
    pass