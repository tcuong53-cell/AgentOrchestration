from typing import Dict, Any, Optional, Set, Protocol
import logging

# Configure logging for the module
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Custom Exceptions for Authorization ---
class AuthorizationError(Exception):
    """Base exception for authorization failures."""
    pass

class PermissionDeniedError(AuthorizationError):
    """Raised when the principal lacks the required permission."""
    pass

class ResourceNotFoundInScopeError(AuthorizationError):
    """Raised when a requested resource is not found within the principal's authorized scope."""
    pass

class DataIntegrityError(Exception):
    """Raised when a critical data mismatch or security violation is detected,
    potentially indicating a bug in the data accessor or a malicious attempt."""
    pass

# --- Data Accessor Protocol ---
class ScopedDataAccessor(Protocol):
    """
    Protocol defining the required interface for data access methods
    that enforce scoping (e.g., by workspace_id).
    Implementations of this protocol are responsible for filtering data
    based on the provided scope parameters *at the data source level*.
    """
    def get_object_by_id_and_scope(self, object_id: str, scope_key: str, scope_value: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves an object by its ID, ensuring it belongs to the given scope.
        Returns the object details if found within the scope, otherwise None.
        Example: get_object_by_id_and_scope("run-123", "workspace_id", "ws-alpha")
        should only return run-123 if its workspace_id is "ws-alpha".
        """
        ...

    def get_run_by_id_and_workspace(self, run_id: str, workspace_id: str) -> Optional[Dict[str, Any]]:
        """
        Specifically retrieves a run by its ID, scoped to a particular workspace.
        This is a specialized version of get_object_by_id_and_scope for 'run' objects.
        """
        ...

# --- Operator Token and Auth Context ---
class OperatorToken:
    """
    Represents a validated operator token with its claims.
    This object is created *after* successful token authentication and parsing.
    It encapsulates the identity and privileges derived from the token.
    """
    def __init__(self, token_id: str, workspace_id: str, role: str, permissions: Set[str]):
        if not token_id or not workspace_id or not role:
            raise ValueError("Token ID, workspace ID, and role cannot be empty.")
        self.token_id = token_id
        self.workspace_id = workspace_id
        self.role = role
        self.permissions = permissions if permissions is not None else set()

    def has_permission(self, permission: str) -> bool:
        """Checks if the token's assigned role has a specific permission."""
        return permission in self.permissions

class AuthContext:
    """
    Encapsulates the authenticated context for a request, derived from
    a validated operator token. This context is immutable and should be
    passed down to authorization checks and data access layers to ensure
    all operations are performed within the principal's scope.
    """
    def __init__(self, operator_token: OperatorToken):
        if not operator_token:
            raise ValueError("OperatorToken cannot be None for AuthContext.")
        self.operator_token = operator_token
        self.workspace_id = operator_token.workspace_id
        self.role = operator_token.role
        self.permissions = operator_token.permissions

# --- Operator Token Guard (Central RBAC and Scoping Enforcement) ---
class OperatorTokenGuard:
    """
    Enforces RBAC (Role-Based Access Control) and least-privilege scoping
    for operations performed using operator tokens.

    This guard ensures that every lookup, mutation, and dispatch decision
    is rigorously scoped to the authenticated workspace and active role,
    preventing unauthorized access to resources outside the principal's
    assigned scope.
    """

    def __init__(self, data_accessor: ScopedDataAccessor):
        """
        Initializes the guard with a data accessor capable of enforcing scope.
        The `data_accessor` is a critical dependency; it's expected to perform
        queries that inherently filter data based on scope parameters (e.g.,
        'SELECT ... WHERE id = ? AND workspace_id = ?').
        """
        if not data_accessor:
            raise ValueError("ScopedDataAccessor dependency cannot be None.")
        self.data_accessor = data_accessor

    def _simulate_authenticate_token(self, raw_token: str) -> Optional[AuthContext]:
        """
        [PLACEHOLDER FOR ACTUAL TOKEN AUTHENTICATION LOGIC]
        This method simulates token authentication for demonstration purposes.
        In production, it would handle full token validation, expiry, revocation,
        and claim extraction.
        """
        if raw_token == "op_token_ws1_admin":
            return AuthContext(OperatorToken("op1", "ws-alpha", "admin", {"run:read", "run:cancel", "run:manage_permissions"}))
        elif raw_token == "op_token_ws1_viewer":
            return AuthContext(OperatorToken("op2", "ws-alpha", "viewer", {"run:read", "run:view_logs"}))
        elif raw_token == "op_token_ws2_admin":
            return AuthContext(OperatorToken("op3", "ws-beta", "admin", {"run:read", "run:cancel"}))
        return None

    def authorize_run_cancellation(self, auth_context: AuthContext, run_id: str) -> bool:
        """
        Authorizes the cancellation of a specific run, ensuring it belongs to the
        caller's authenticated workspace and that the caller has the necessary
        permissions.

        This method directly addresses the described bug: "the handler trusts an
        object identifier without tying it to the caller's workspace and current role."

        The core fix involves:
        1. **Requiring an authenticated context:** Ensures every operation is linked to a verified principal.
        2. **Permission Check:** Verifies the `AuthContext` has the explicit `run:cancel` permission.
        3. **Scoped Data Lookup:** The target `run_id` is *never* trusted in isolation. It's always
           fetched using a `data_accessor` method that *enforces scoping* by the
           `auth_context.workspace_id`. This prevents an attacker from manipulating `run_id`
           to access or modify resources in other workspaces.
        4. **Strict Enforcement:** If the run isn't found *within the authenticated workspace*
           (as per the scoped lookup), it's treated as unauthorized or non-existent for that caller.
           This method now raises specific exceptions instead of returning False for clarity and robust error handling.
        """
        if not auth_context:
            logger.warning("Authorization failed for run cancellation: No authentication context provided.")
            raise AuthorizationError("Authentication context is missing.")

        principal_id = auth_context.operator_token.token_id
        principal_workspace = auth_context.workspace_id
        principal_role = auth_context.role

        # Step 1: Enforce permission check based on the authenticated role
        if not auth_context.operator_token.has_permission("run:cancel"):
            logger.warning(
                f"Principal '{principal_id}' (Role: '{principal_role}') in workspace '{principal_workspace}' "
                f"lacks 'run:cancel' permission for run '{run_id}'."
            )
            raise PermissionDeniedError(f"Principal '{principal_id}' lacks 'run:cancel' permission.")

        # Step 2: Retrieve the run details, CRITICALLY scoping the lookup
        #         to the authenticated principal's workspace.
        run_details = self.data_accessor.get_run_by_id_and_workspace(
            run_id=run_id,
            workspace_id=principal_workspace
        )

        if not run_details:
            # If `run_details` is None, it implies either:
            # a) The `run_id` does not exist at all.
            # b) The `run_id` exists, but *not* within the authenticated principal's workspace.
            # In either case, the operation is unauthorized from this principal's perspective,
            # and we avoid leaking information about other workspaces by raising a generic error.
            logger.warning(
                f"Run '{run_id}' not found or does not belong to workspace '{principal_workspace}' "
                f"for principal '{principal_id}'."
            )
            raise ResourceNotFoundInScopeError(f"Run '{run_id}' not found in authorized scope.")

        # Step 3: (Defensive check) Double-check the workspace ID from the retrieved object.
        #         This guards against potential bugs in the `data_accessor` implementation
        #         or if an object's workspace ID could somehow be mismatched after retrieval.
        if run_details.get("workspace_id") != principal_workspace:
            logger.critical(
                f"SECURITY-ALERT: Mismatched workspace for run '{run_id}' during authorization. "
                f"Expected '{principal_workspace}', but retrieved object has '{run_details.get('workspace_id')}' "
                f"(from data accessor). This indicates a potential `data_accessor` bug or data integrity issue."
            )
            raise DataIntegrityError(f"Workspace mismatch for run '{run_id}'. Potential data accessor bug or tampering.")

        logger.info(
            f"Principal '{principal_id}' in workspace '{principal_workspace}' is authorized to "
            f"cancel run '{run_id}'."
        )
        return True # Authorization successful

    def authorize_object_mutation(self, auth_context: AuthContext, object_id: str, object_type: str, required_permission: str, scope_key: str = "workspace_id") -> bool:
        """
        A more generic authorization method for mutating any object type.
        This demonstrates the pattern of enforcing scope for other operations beyond 'run cancellation'.
        This method now raises specific exceptions instead of returning False for clarity and robust error handling.
        """
        if not auth_context:
            logger.warning(f"Authorization failed for {object_type} mutation: No authentication context provided.")
            raise AuthorizationError("Authentication context is missing.")

        principal_id = auth_context.operator_token.token_id
        principal_workspace = auth_context.workspace_id
        principal_role = auth_context.role

        if not auth_context.operator_token.has_permission(required_permission):
            logger.warning(
                f"Principal '{principal_id}' (Role: '{principal_role}') in workspace '{principal_workspace}' "
                f"lacks '{required_permission}' permission for {object_type} '{object_id}'."
            )
            raise PermissionDeniedError(f"Principal '{principal_id}' lacks '{required_permission}' permission.")

        # Generic scoped lookup using the data_accessor
        object_details = self.data_accessor.get_object_by_id_and_scope(
            object_id=object_id,
            scope_key=scope_key,
            scope_value=principal_workspace # Assuming primary scope is workspace for generic mutations
        )

        if not object_details:
            logger.warning(
                f"{object_type} '{object_id}' not found or does not belong "
                f"to scope '{scope_key}={principal_workspace}' for principal '{principal_id}'."
            )
            raise ResourceNotFoundInScopeError(
                f"{object_type} '{object_id}' not found in authorized scope '{scope_key}={principal_workspace}'."
            )

        if object_details.get(scope_key) != principal_workspace:
            logger.critical(
                f"SECURITY-ALERT: Mismatched {scope_key} for {object_type} '{object_id}' during authorization. "
                f"Expected '{principal_workspace}', got '{object_details.get(scope_key)}'. "
                f"This indicates a potential `data_accessor` bug or data integrity issue."
            )
            raise DataIntegrityError(f"{scope_key} mismatch for {object_type} '{object_id}'. Potential data accessor bug or tampering.")

        logger.info(
            f"Principal '{principal_id}' in workspace '{principal_workspace}' is authorized to "
            f"perform '{required_permission}' on {object_type} '{object_id}'."
        )
        return True # Authorization successful