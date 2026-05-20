import time
import threading
from typing import Dict, Any, Tuple, Optional

class AuthorizationCache:
    """
    Manages caching of authorization resolutions to reduce policy engine load.

    This cache includes mechanisms to detect and invalidate entries that are
    stale based on a Time-To-Live (TTL) or when global permission changes
    have been signaled. It is designed to be thread-safe.
    """
    def __init__(self, ttl: int = 300):
        """
        Initializes the authorization cache.

        Args:
            ttl: Time-To-Live in seconds for cached entries. Entries older than this
                 will be considered stale.
        """
        # Stores (resource_id, principal_id, action) -> (authorized_status: bool, timestamp: float)
        self._cache: Dict[Tuple[str, str, str], Tuple[bool, float]] = {}
        # Timestamp of the last global permission change event.
        # Any cache entry created *before* this timestamp is considered potentially stale,
        # irrespective of its individual TTL, if a re-check is desired.
        self._permission_last_changed_timestamp: float = time.time()
        self._ttl = ttl
        self._lock = threading.Lock() # Added for thread-safety

    def _is_stale_by_ttl(self, cached_timestamp: float) -> bool:
        """
        Checks if a cached entry is stale based on its Time-To-Live (TTL).
        This method does NOT acquire a lock, assuming the caller holds it
        or that `time.time()` is thread-safe for reading.
        """
        return (time.time() - cached_timestamp) > self._ttl

    def _has_global_permission_change_occurred(self, cached_timestamp: float, current_permission_timestamp: float) -> bool:
        """
        Checks if a global permission change has been signaled since the
        given cache entry was created. If so, the entry might be invalid.
        This method receives the current permission timestamp, assuming the caller
        retrieved it under the necessary lock.
        """
        return current_permission_timestamp > cached_timestamp

    def get(self, resource_id: str, principal_id: str, action: str) -> Optional[bool]:
        """
        Retrieves an authorization result from the cache.

        Returns None if the entry is not found, is stale by TTL, or if global
        permissions have changed since the entry was cached.
        """
        key = (resource_id, principal_id, action)
        with self._lock: # Acquire lock for consistent cache access
            if key in self._cache:
                authorized, entry_timestamp = self._cache[key]
                # Read _permission_last_changed_timestamp *while* holding the lock
                current_permission_timestamp = self._permission_last_changed_timestamp

                if not self._is_stale_by_ttl(entry_timestamp) and \
                   not self._has_global_permission_change_occurred(entry_timestamp, current_permission_timestamp):
                    return authorized
                else:
                    # Entry is stale or globally invalidated, remove it.
                    del self._cache[key] # Safe deletion within the lock
        return None

    def set(self, resource_id: str, principal_id: str, action: str, authorized: bool):
        """
        Stores an authorization result in the cache with the current timestamp.
        """
        key = (resource_id, principal_id, action)
        with self._lock: # Acquire lock for consistent cache modification
            self._cache[key] = (authorized, time.time())

    def signal_permission_change(self):
        """
        Signals that a global permission change has occurred.

        This updates the internal timestamp, causing all cache entries
        created *before* this signal to be treated as potentially stale
        on subsequent lookups, even if their individual TTL has not expired.
        This is crucial for enforcing permission changes promptly.
        """
        with self._lock: # Acquire lock for consistent timestamp update
            self._permission_last_changed_timestamp = time.time()
        # No need to clear cache immediately; 'get' method will invalidate on demand.

class PolicyEngine:
    """
    Represents the core policy resolution logic.

    In a real system, this class would query a policy database, an external
    authorization service (e.g., OPA), or evaluate internal rulesets.
    For this example, it contains hardcoded simple policies.
    """
    def resolve(self, resource_id: str, principal_id: str, action: str) -> bool:
        """
        Resolves whether a principal is authorized to perform an action on a resource.

        Args:
            resource_id: The identifier of the resource.
            principal_id: The identifier of the entity requesting authorization.
            action: The action being attempted.

        Returns:
            True if authorized, False otherwise.
        """
        # Example hardcoded policies:
        if principal_id == "admin":
            return True # Admins are always authorized
        if principal_id == "user1" and action == "read" and resource_id == "resourceA":
            return True
        # Service agents can schedule, route, and transition tasks
        if principal_id == "service_agent" and resource_id.startswith("task_"):
            if action in ["schedule", "route", "transition_running", "transition_completed"]:
                return True
        return False

class AuthorizationManager:
    """
    Manages authorization checks for registry components, integrating
    a cache and a policy engine. This is the primary interface for
    components needing to perform authorization.
    """
    def __init__(self, policy_engine: Optional[PolicyEngine] = None, cache_ttl: int = 300):
        """
        Initializes the AuthorizationManager.

        Args:
            policy_engine: An instance of PolicyEngine. If None, a default one is created.
            cache_ttl: The Time-To-Live for cached authorization results in seconds.
        """
        self.policy_engine = policy_engine if policy_engine else PolicyEngine()
        self.cache = AuthorizationCache(ttl=cache_ttl)

    def check_permission(self,
                         resource_id: str,
                         principal_id: str,
                         action: str,
                         force_recheck: bool = False) -> bool:
        """
        Checks if a principal has permission to perform an action on a resource.

        This method attempts to use the cache first, but can be forced to
        bypass the cache and query the policy engine directly for critical operations.

        Args:
            resource_id: The ID of the resource (e.g., "task_123", "queue_A").
            principal_id: The ID of the requesting entity (e.g., "user1", "service_agent").
            action: The action being attempted (e.g., "read", "write", "schedule").
            force_recheck: If True, bypass the cache and query the policy engine directly.
                           This is crucial for operations where a stale cached resolution
                           could lead to policy violations (e.g., state transitions,
                           scheduling, routing).

        Returns:
            True if authorized, False otherwise.
        """
        if force_recheck:
            # Forcing a re-check means we always go to the authoritative policy engine.
            # This is the direct fix for the "recheck authorization on cached resolution
            # is not enforced" bug in critical paths.
            authorized = self.policy_engine.resolve(resource_id, principal_id, action)
            self.cache.set(resource_id, principal_id, action, authorized) # Update cache with fresh result
            return authorized

        # Attempt to retrieve from cache first for non-forced checks.
        cached_result = self.cache.get(resource_id, principal_id, action)
        if cached_result is not None:
            return cached_result

        # Cache miss or stale/globally invalidated entry, resolve via policy engine.
        authorized = self.policy_engine.resolve(resource_id, principal_id, action)
        self.cache.set(resource_id, principal_id, action, authorized)
        return authorized

    def handle_permission_change_event(self):
        """
        This method should be called when a global permission change event occurs.
        It signals the authorization cache to treat existing entries as potentially stale.

        This ensures that subsequent authorization checks will either hit the
        policy engine or re-evaluate cached entries against the new permission state.
        """
        # In a real system, this would be invoked by an event listener
        # when a user role changes, a policy is updated, etc.
        self.cache.signal_permission_change()

class RegistryComponent:
    """
    A simulated registry component that manages tasks, agents, and their lifecycle.
    It relies on AuthorizationManager for access control.
    """
    def __init__(self, auth_manager: AuthorizationManager):
        self.auth_manager = auth_manager
        # Simulate internal state for tasks, queues, etc.
        self.tasks: Dict[str, Any] = {}
        self.queues: Dict[str, Any] = {}
        self.workflows: Dict[str, Any] = {}

    def _validate_lifecycle_transition(self,
                                       principal_id: str,
                                       resource_id: str,
                                       new_state: str) -> bool:
        """
        Internal helper to validate critical lifecycle state transitions.

        This method is a key control point where authorization *must* be re-checked
        to prevent stale or policy-violating transitions.
        """
        print(f"DEBUG: Validating transition for resource '{resource_id}' to '{new_state}' by '{principal_id}'.")

        # FIX: Enforce re-check authorization on cached resolution.
        # For critical state changes, always perform a fresh authorization check.
        # This addresses the bug where stale cached permissions could allow
        # forbidden transitions.
        if not self.auth_manager.check_permission(
            resource_id=resource_id,
            principal_id=principal_id,
            action=f"transition_{new_state}", # Example action for state transition
            force_recheck=True # <-- CRITICAL FIX: Ensure fresh authorization check
        ):
            print(f"ERROR: Authorization failed for '{principal_id}' to transition '{resource_id}' to '{new_state}'.")
            return False

        print(f"DEBUG: Authorization granted for transition of '{resource_id}' to '{new_state}'.")
        return True

    def transition_task_state(self, principal_id: str, task_id: str, new_state: str) -> bool:
        """
        Attempts to transition a task's lifecycle state.

        Args:
            principal_id: The ID of the agent/handler requesting the transition.
            task_id: The ID of the task to transition.
            new_state: The target lifecycle state (e.g., "running", "completed", "failed").

        Returns:
            True if the state transition was successful and authorized, False otherwise.
        """
        if task_id not in self.tasks:
            print(f"ERROR: Task '{task_id}' not found.")
            return False

        if self._validate_lifecycle_transition(principal_id, f"task_{task_id}", new_state):
            print(f"INFO: Committing task '{task_id}' state to '{new_state}'.")
            self.tasks[task_id]["state"] = new_state
            # Simulate other state commitment logic (e.g., database update, event emission)
            return True
        return False

    def schedule_task(self, principal_id: str, task_details: Dict[str, Any]) -> Optional[str]:
        """
        Simulates scheduling a new task into the registry.

        Args:
            principal_id: The ID of the entity requesting to schedule the task.
            task_details: A dictionary containing details for the new task.

        Returns:
            The ID of the newly scheduled task if authorized, None otherwise.
        """
        # Generate a dummy task_id for demonstration
        task_id = f"task_{hash(frozenset(task_details.items()))}_{int(time.time())}"

        # FIX: Ensure scheduling, routing, and workflow operations also
        # enforce a re-check to prevent stale permissions from allowing
        # new resource allocations/modifications.
        if self.auth_manager.check_permission(
            resource_id=f"task_{task_id}", # Resource could be the new task or the scheduling system itself
            principal_id=principal_id,
            action="schedule",
            force_recheck=True # <-- CRITICAL FIX
        ):
            print(f"INFO: '{principal_id}' authorized to schedule task '{task_id}'.")
            self.tasks[task_id] = {"details": task_details, "state": "scheduled"}
            # Simulate queueing, resource allocation, etc.
            return task_id
        else:
            print(f"ERROR: '{principal_id}' is NOT authorized to schedule tasks.")
            return None

    def route_task_to_queue(self, principal_id: str, task_id: str, target_queue: str) -> bool:
        """
        Simulates routing an existing task to a specific queue.

        Args:
            principal_id: The ID of the entity requesting the routing.
            task_id: The ID of the task to route.
            target_queue: The name of the target queue.

        Returns:
            True if the task was successfully routed, False otherwise.
        """
        if task_id not in self.tasks:
            print(f"ERROR: Task '{task_id}' not found for routing.")
            return False
        if target_queue not in self.queues:
            self.queues[target_queue] = [] # Auto-create queue for demo
        
        # FIX: Routing is a critical state change, force re-check.
        if self.auth_manager.check_permission(
            resource_id=f"queue_{target_queue}", # Resource being the target queue
            principal_id=principal_id,
            action="route",
            force_recheck=True # <-- CRITICAL FIX
        ):
            print(f"INFO: '{principal_id}' authorized to route task '{task_id}' to queue '{target_queue}'.")
            self.tasks[task_id]["current_queue"] = target_queue
            self.queues[target_queue].append(task_id) # Add task to queue
            return True
        else:
            print(f"ERROR: '{principal_id}' is NOT authorized to route tasks to queue '{target_queue}'.")
            return False

    # Other registry methods would follow a similar pattern,
    # calling auth_manager.check_permission with force_recheck=True for
    # any operation that commits scheduling, routing, queue, or workflow state.