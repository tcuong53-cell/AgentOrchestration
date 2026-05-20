from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Dict, Optional, Set

# --- Models ---
class CurrentUser(BaseModel):
    user_id: str
    workspace_id: str
    role: str # e.g., "admin", "viewer", "editor"

class SearchQuery(BaseModel):
    query_id: str = Field(..., example="sq-workspace_alpha-001")
    workspace_id: str # The workspace this query belongs to
    query_string: str
    is_indexed: bool = False

class RunSearchResponse(BaseModel):
    message: str
    indexed_query: Optional[SearchQuery] = None

# --- Mock Data (Simulates a database or persistent storage) ---
# This data is essential for demonstrating tenant isolation.
_mock_search_queries_db: Dict[str, SearchQuery] = {
    "sq-workspace_alpha-001": SearchQuery(query_id="sq-workspace_alpha-001", workspace_id="workspace_alpha", query_string="SELECT * FROM logs", is_indexed=False),
    "sq-workspace_alpha-002": SearchQuery(query_id="sq-workspace_alpha-002", workspace_id="workspace_alpha", query_string="ERROR messages", is_indexed=True),
    "sq-workspace_beta-001": SearchQuery(query_id="sq-workspace_beta-001", workspace_id="workspace_beta", query_string="payment failures", is_indexed=False),
    "sq-workspace_gamma-001": SearchQuery(query_id="sq-workspace_gamma-001", workspace_id="workspace_gamma", query_string="user signups", is_indexed=False),
}

# --- Dependencies ---
def get_current_user() -> CurrentUser:
    """
    Dependency that returns the authenticated user's context,
    including their workspace_id and role.

    NOTE: In a real application, this function would perform actual
    authentication logic (e.g., decode a JWT, validate API key) to securely
    determine the current user's identity, associated workspace, and role.
    For this bounty, we assume secure authentication has already provided
    this context and this function reliably retrieves it.

    To test different scenarios, modify the hardcoded return value:
    - `workspace_alpha` admin: Can index queries in `workspace_alpha`.
    - `workspace_beta` viewer: Cannot index anything, cannot see `workspace_alpha` data.
    """
    # Placeholder for actual authentication and authorization logic.
    # For demonstration:
    # - User from 'workspace_alpha' with 'admin' role.
    # return CurrentUser(user_id="auth_user_123", workspace_id="workspace_alpha", role="admin")
    # - User from 'workspace_beta' with 'viewer' role (lacks indexing permission).
    return CurrentUser(user_id="auth_user_456", workspace_id="workspace_beta", role="viewer")
    # - User from 'workspace_gamma' with 'editor' role.
    # return CurrentUser(user_id="auth_user_789", workspace_id="workspace_gamma", role="editor")


# --- Service Layer ---
# This layer encapsulates business logic and data access,
# ensuring security checks are applied consistently.
class SearchService:
    def __init__(self, db: Dict[str, SearchQuery]):
        # In a real application, this would be an ORM session or a database client.
        self._db = db

    def _get_search_query_by_id_internal(self, query_id: str) -> Optional[SearchQuery]:
        """Internal helper to retrieve a query without authorization checks."""
        return self._db.get(query_id)

    def get_scoped_search_query(self, query_id: str, current_user: CurrentUser) -> SearchQuery:
        """
        Retrieves a search query, applying tenant-scoping and basic validation.
        This function acts as the primary 'guard' for resource access.
        """
        if not query_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Search query ID cannot be empty."
            )

        search_query = self._get_search_query_by_id_internal(query_id)

        # Security check: Does the query exist AND belong to the current user's workspace?
        # Return 404 (Not Found) for non-existent or cross-tenant queries to prevent
        # information leakage about resources in other tenants.
        if not search_query or search_query.workspace_id != current_user.workspace_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Search query not found or not accessible within your workspace."
            )

        return search_query

    def run_search_indexing(self, query_id: str, current_user: CurrentUser) -> SearchQuery:
        """
        Executes the search indexing operation for a specific query.
        This function includes both tenant-scoping (via get_scoped_search_query)
        and role-based authorization for the specific action.
        """
        # Step 1: Validate input and scope the lookup to the user's workspace.
        # This prevents unauthorized access across tenants.
        search_query = self.get_scoped_search_query(query_id, current_user)

        # Step 2: Role-based authorization for the 'indexing' action.
        # Only users with 'admin' or 'editor' roles can perform indexing.
        allowed_roles_for_indexing = {"admin", "editor"}
        if current_user.role not in allowed_roles_for_indexing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User does not have sufficient permissions to index search queries."
            )

        # Step 3: Perform the mutation if all checks pass.
        if search_query.is_indexed:
            # Operation is idempotent; if already indexed, just return it.
            return search_query

        # Simulate the indexing process (e.g., calling an external service, updating status)
        search_query.is_indexed = True
        self._db[query_id] = search_query # Update the mock database

        return search_query

# Initialize the service with our mock data.
# In a real app, dependency injection frameworks might manage this.
search_service = SearchService(_mock_search_queries_db)

# --- FastAPI Router ---
# This defines the API endpoint and integrates dependencies and service logic.
router = APIRouter(prefix="/api/v1") # Example prefix for API versioning

@router.post(
    "/run-search/{query_id}/index",
    response_model=RunSearchResponse,
    status_code=status.HTTP_200_OK,
    summary="Initiate indexing for a specific search query.",
    description="Scopes the indexing request to the authenticated user's workspace and verifies their role."
)
async def run_search_indexing_api(
    query_id: str,
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Handles the API request to trigger indexing of a search query.
    All authorization and scoping guards are performed in the service layer.
    """
    try:
        updated_query = search_service.run_search_indexing(query_id, current_user)
        return RunSearchResponse(
            message=f"Search query '{query_id}' successfully indexed for workspace '{current_user.workspace_id}'.",
            indexed_query=updated_query
        )
    except HTTPException as e:
        # Re-raise HTTPExceptions, FastAPI will handle them correctly.
        raise e
    except Exception as e:
        # Catch any unexpected errors to provide a generic 500 response
        # and prevent leaking internal details. Log the actual error internally.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during search indexing. Please try again later."
        )

# Example of how to run this FastAPI application (requires uvicorn and fastapi)
# To run:
# 1. Save the code as main.py
# 2. Install dependencies: pip install fastapi uvicorn pydantic
# 3. Run: uvicorn main:router --reload (assuming this file is 'main.py' and 'router' is imported)
# Or, if this is part of a larger app, it would be registered with a main FastAPI app instance.
#
# from fastapi import FastAPI
# app = FastAPI()
# app.include_router(router)
#
# Then run: uvicorn main:app --reload