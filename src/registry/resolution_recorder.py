import threading
import logging
from typing import Dict, Optional, Any

# Configure logging for better auditability
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Custom exceptions for clearer error handling
class PolicyViolationError(Exception):
    """Raised when a mid-run registry update invariant is violated."""
    pass

class HandlerNotFoundError(ValueError):
    """Raised when a handler type is not found in the registry."""
    pass

class AttemptNotPinnedError(ValueError):
    """Raised when an operation requires a pinned handler for an attempt, but none is found."""
    pass

class HandlerConfig:
    """Represents configuration for a specific handler."""
    def __init__(self, id: str, params: Dict[str, Any]):
        self.id = id
        self.params = params

    def __eq__(self, other):
        if not isinstance(other, HandlerConfig):
            return NotImplemented
        return self.id == other.id and self.params == other.params

    def __hash__(self):
        # Use frozenset for params items to make dictionary hashable
        return hash((self.id, frozenset(self.params.items())))

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "params": self.params}

class ResolvedHandler:
    """
    Represents a handler resolved by the registry.
    This stores the handler's type and its configuration, which is pinned for an attempt.
    """
    def __init__(self, handler_type: str, config: HandlerConfig):
        self.handler_type = handler_type
        self.config = config

    def __eq__(self, other):
        if not isinstance(other, ResolvedHandler):
            return NotImplemented
        return self.handler_type == other.handler_type and self.config == other.config

    def __hash__(self):
        return hash((self.handler_type, self.config))


class Registry:
    """
    Simulates the core registry that maps handler types to configurations.
    This registry can be updated mid-run.
    """
    def __init__(self):
        # Maps handler_type (str) to HandlerConfig
        self._handlers: Dict[str, HandlerConfig] = {}
        self._lock = threading.RLock() # For thread-safe updates to the registry itself

    def register_handler(self, handler_type: str, config: HandlerConfig):
        """Registers or updates a handler configuration."""
        with self._lock:
            self._handlers[handler_type] = config
            logger.info(f"Handler '{handler_type}' registered/updated in registry.")

    def get_handler_config(self, handler_type: str) -> Optional[HandlerConfig]:
        """Retrieves a handler configuration."""
        with self._lock:
            return self._handlers.get(handler_type)

    def update_registry(self, new_handler_configs: Dict[str, HandlerConfig]):
        """
        Updates the registry with new handler configurations.
        This is the "mid-run registry updates path".
        """
        with self._lock:
            for handler_type, config in new_handler_configs.items():
                self._handlers[handler_type] = config # Overwrite or add
            logger.info(f"Registry updated with {len(new_handler_configs)} new/updated handler configurations.")
            # In a production system, this might also involve versioning or
            # more sophisticated atomic update mechanisms.


class RegistryResolutionRecorder:
    """
    Records and pins resolved handlers for specific attempts.
    This component ensures that once a handler is resolved for an attempt,
    it remains consistent for that attempt, even if the main Registry updates.
    It enforces the "pin resolved handler per attempt" invariant.
    """
    def __init__(self, registry: Registry):
        self._registry = registry
        # Stores {attempt_id: ResolvedHandler} -- the *pinned* resolution for an attempt.
        self._pinned_attempt_resolutions: Dict[str, ResolvedHandler] = {}
        # Stores {attempt_id} -- set of attempts whose resolution is currently "locked"
        # during a critical lifecycle state change, preventing external influence.
        self._locked_attempts: set[str] = set()
        self._lock = threading.RLock() # Lock for _pinned_attempt_resolutions and _locked_attempts

    def resolve_and_pin_handler_for_attempt(self, attempt_id: str, handler_type: str) -> ResolvedHandler:
        """
        Resolves a handler for a given attempt and pins its configuration.
        Subsequent calls for the same attempt_id will return the initially pinned handler,
        unless the attempt's resolution is explicitly cleared or unlocked.

        This method is the primary mechanism to enforce the 'pin resolved handler per attempt' invariant.
        It should be called once when an attempt is initiated and needs a handler.

        Raises HandlerNotFoundError if the handler_type is not found in the registry.
        """
        with self._lock:
            if attempt_id in self._pinned_attempt_resolutions:
                # Handler is already pinned for this attempt, return it.
                logger.debug(f"Handler for attempt '{attempt_id}' already pinned. Returning existing resolution.")
                return self._pinned_attempt_resolutions[attempt_id]

            # Resolve from the current (potentially latest) registry state
            handler_config = self._registry.get_handler_config(handler_type)
            if not handler_config:
                logger.error(f"Handler of type '{handler_type}' not found in registry for attempt '{attempt_id}'.")
                raise HandlerNotFoundError(f"Handler of type '{handler_type}' not found in registry for attempt '{attempt_id}'.")

            resolved_handler = ResolvedHandler(handler_type=handler_type, config=handler_config)
            self._pinned_attempt_resolutions[attempt_id] = resolved_handler
            logger.info(f"Handler '{handler_type}' pinned for attempt '{attempt_id}'.")
            return resolved_handler

    def get_pinned_handler_for_attempt(self, attempt_id: str) -> Optional[ResolvedHandler]:
        """
        Retrieves the currently pinned handler for an attempt.
        Returns None if no handler is pinned for the given attempt_id.
        """
        with self._lock:
            return self._pinned_attempt_resolutions.get(attempt_id)

    def lock_attempt_resolution(self, attempt_id: str):
        """
        Locks the resolution for an attempt. This should be called by lifecycle
        management components (e.g., agent, task, handler manager) when an
        attempt is entering a critical lifecycle state (e.g., executing,
        transitioning, committing to a new state).

        Once locked, the system implicitly expects that the pinned handler
        is the immutable source of truth for this attempt's operations and
        any associated state changes. This actively enforces the mid-run
        registry updates invariant by safeguarding against accidental
        re-resolution or validation against a changing registry.

        Raises AttemptNotPinnedError if no handler is pinned for the attempt.
        """
        with self._lock:
            if attempt_id not in self._pinned_attempt_resolutions:
                logger.error(f"Attempt '{attempt_id}' cannot be locked: no handler pinned.")
                raise AttemptNotPinnedError(f"Cannot lock resolution for attempt '{attempt_id}': no handler pinned.")
            self._locked_attempts.add(attempt_id)
            logger.info(f"Resolution for attempt '{attempt_id}' locked.")

    def unlock_attempt_resolution(self, attempt_id: str):
        """
        Unlocks and clears the resolution for an attempt.
        This should be called when an attempt's lifecycle concludes
        (e.g., success, failure, timeout, cancellation).
        """
        with self._lock:
            if attempt_id in self._locked_attempts:
                self._locked_attempts.discard(attempt_id)
                logger.info(f"Resolution for attempt '{attempt_id}' unlocked.")
            # Always remove the pinned resolution to free up memory and allow
            # a fresh resolution if the attempt is retried later.
            if attempt_id in self._pinned_attempt_resolutions:
                self._pinned_attempt_resolutions.pop(attempt_id)
                logger.info(f"Pinned handler for attempt '{attempt_id}' cleared.")


    def is_attempt_resolution_locked(self, attempt_id: str) -> bool:
        """
        Checks if an attempt's resolution is currently locked.
        """
        with self._lock:
            return attempt_id in self._locked_attempts

    def enforce_invariant_before_state_commit(self, attempt_id: str, proposed_state: Dict[str, Any]):
        """
        Enforces the mid-run registry updates invariant before committing
        any scheduling, routing, queue, or workflow state for a given attempt.

        This method should be called by components managing the lifecycle of attempts
        *before* committing a state change. If an attempt's resolution is locked,
        it signifies that it is in a critical state. This check ensures that the
        system acknowledges and respects the already pinned handler for this attempt.

        Raises PolicyViolationError if an invalid transition or inconsistency
        is detected, or if the attempt is locked without a pinned handler.
        """
        with self._lock:
            if attempt_id not in self._locked_attempts:
                logger.debug(f"Attempt '{attempt_id}' is not locked. Invariant check passed implicitly for non-critical state.")
                return # Invariant is mainly for locked states

            # If locked, we MUST have a pinned handler.
            pinned_handler = self._pinned_attempt_resolutions.get(attempt_id)
            if not pinned_handler:
                logger.critical(f"CRITICAL ERROR: Attempt '{attempt_id}' is locked but has no pinned resolution! "
                                "This indicates a severe lifecycle management bug. Rejecting state commit.")
                raise PolicyViolationError(f"Attempt '{attempt_id}' is locked but no handler is pinned. "
                                           "Cannot commit state.")

            # Enforce that the proposed state is compatible with the pinned handler.
            # This is where the core policy enforcement for "stale, duplicate, or policy-violating transition" happens.
            if not self._is_state_compatible_with_handler(proposed_state, pinned_handler):
                logger.warning(f"Policy violation for attempt '{attempt_id}': Proposed state incompatible "
                               f"with pinned handler '{pinned_handler.handler_type}'. Rejecting state commit.")
                raise PolicyViolationError(f"Proposed state for attempt '{attempt_id}' is incompatible "
                                           f"with pinned handler '{pinned_handler.handler_type}' (ID: {pinned_handler.config.id}).")

            logger.info(f"Invariant upheld for attempt '{attempt_id}'. Proposed state compatible with pinned handler.")
            return

    def _is_state_compatible_with_handler(self, proposed_state: Dict[str, Any], pinned_handler: ResolvedHandler) -> bool:
        """
        Validates a proposed state change against the configuration of the pinned handler.
        This is a crucial placeholder for specific business logic to prevent stale,
        duplicate, or policy-violating transitions.

        For example:
        - Ensure that if `proposed_state` explicitly mentions a handler_id or handler_type
          for its next step, it matches the pinned handler.
        - Validate scheduling parameters (e.g., target queue, resource requirements) in
          `proposed_state` against what the `pinned_handler.config` dictates or allows.
        - Check if the proposed state implies a new resolution process for a locked attempt.

        A simple example: if the proposed_state contains a 'target_handler_id' or 'handler_type'
        field, it must match the pinned handler. If it doesn't specify a handler, it's generally
        considered compatible (as it's implicitly acting on the current pinned one).
        """
        # Example 1: If the proposed state explicitly points to a different handler ID.
        if 'handler_id' in proposed_state and proposed_state['handler_id'] != pinned_handler.config.id:
            logger.debug(f"Policy check failed: Proposed state handler ID '{proposed_state['handler_id']}' mismatches pinned handler ID '{pinned_handler.config.id}'.")
            return False

        # Example 2: If the proposed state explicitly points to a different handler type.
        if 'handler_type' in proposed_state and proposed_state['handler_type'] != pinned_handler.handler_type:
            logger.debug(f"Policy check failed: Proposed state handler type '{proposed_state['handler_type']}' mismatches pinned handler type '{pinned_handler.handler_type}'.")
            return False

        # Example 3: More complex policy validation (placeholder)
        # e.g., if pinned_handler.config specifies 'exclusive_resource_pool': 'gpu-cluster-1',
        # then proposed_state must not try to schedule it on 'cpu-cluster-2'.
        # if 'resource_pool' in proposed_state and 'exclusive_resource_pool' in pinned_handler.config.params:
        #     if proposed_state['resource_pool'] != pinned_handler.config.params['exclusive_resource_pool']:
        #         logger.debug(f"Policy check failed: Proposed state resource pool '{proposed_state['resource_pool']}' conflicts with pinned handler's exclusive pool '{pinned_handler.config.params['exclusive_resource_pool']}'.")
        #         return False

        # Default: If no explicit conflicts are found based on common fields, consider it compatible.
        # This function should be extended with actual business logic relevant to scheduling, routing, etc.
        return True