from fastapi import APIRouter, Depends, HTTPException, Query, status

router = APIRouter()

# Define the maximum depth for nested filters
MAX_NESTED_DEPTH = 5

async def validate_nested_depth(query: dict, max_depth: int) -> None:
    # Helper function to check if a dictionary has a valid nested structure
    def is_valid_dict(d):
        if not isinstance(d, dict):
            return False
        for key, value in d.items():
            if is_valid_dict(value):
                return True
        return len(d) <= max_depth

    # Validate the input dictionary
    if not is_valid_dict(query):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nested filter depth exceeds the maximum allowed limit"
        )

@router.get("/traces")
async def get_traces(filter_query: dict = Query(...)):
    validate_nested_depth(filter_query, MAX_NESTED_DEPTH)
    
    # ... rest of the function ...