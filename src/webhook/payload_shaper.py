import json
from typing import Any, Dict, List, Union

class PayloadShaper:
    """
    Manages the shaping and filtering of event payloads before they are exposed
    externally, such as via webhooks, logs, or external APIs.
    """

    # A frozenset of field names that are considered sensitive internal operational metadata.
    # These fields will be removed from payloads before external exposure to prevent
    # accidental data leakage. This list should be carefully curated based on
    # system architecture and privacy requirements. Using frozenset ensures immutability
    # and optimal performance for 'in' lookups.
    SENSITIVE_FIELDS = frozenset({
        '_internal_run_id',         # Internal identifier for a specific execution run
        '_processor_context',       # Contextual data used by internal processors
        '_debug_info',              # Debugging information (e.g., timings, stack traces)
        '_private_metadata',        # Generic placeholder for internal-only metadata
        '_raw_payload_unfiltered',  # Original payload before any processing or redaction
        '__system_state',           # Fields indicating internal system state or configuration
        'webhook_delivery_attempts',# Operational metric of delivery retries
        'last_delivery_error_code', # Internal error code from the last delivery attempt
        'security_token_internal',  # Any internal tokens, API keys, or credentials
        'internal_tags',            # Tags used for internal routing or categorization
        'source_ip_internal',       # Internal source IP addresses, not for public
        '_tenant_id_raw',           # Raw tenant ID if it contains sensitive internal parts
        '__version_control',        # Internal versioning or deployment metadata
        '_processor_config',        # Configuration details for internal components
        'database_record_id',       # Internal database identifiers
        'internal_correlation_id'   # Internal correlation ID for tracing
    })

    @staticmethod
    def _recursively_filter(data: Union[Dict, List, Any]) -> Union[Dict, List, Any]:
        """
        Recursively filters sensitive fields from dictionaries and lists.
        Fields whose keys are present in SENSITIVE_FIELDS are removed.
        This method creates new dictionary and list objects to ensure the original
        payload remains immutable and side-effect free, promoting functional purity.
        Using comprehensions enhances readability and can offer C-level performance
        for these operations.
        """
        if isinstance(data, dict):
            return {
                key: PayloadShaper._recursively_filter(value)
                for key, value in data.items()
                if key not in PayloadShaper.SENSITIVE_FIELDS
            }
        elif isinstance(data, list):
            return [PayloadShaper._recursively_filter(item) for item in data]
        else:
            # Base case: return non-dict/non-list values as is.
            return data

    @classmethod
    def shape_and_filter_payload(cls, event_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main entry point for shaping and filtering event payloads.
        This method ensures that sensitive internal operational metadata is removed
        before the payload is exposed externally (e.g., via webhooks, logs, APIs).

        Args:
            event_payload: The raw event payload, potentially containing sensitive fields.
                           Expected to be a dictionary for effective filtering based on keys.

        Returns:
            A new dictionary representing the filtered and shaped payload, safe for external use.
            If the input is not a dictionary, it's returned as-is (as key filtering is not applicable).
            This guards against type errors for non-dictionary root payloads while ensuring filtering
            is applied correctly to dictionary structures.
        """
        if not isinstance(event_payload, dict):
            # If the top-level payload isn't a dictionary, key-based filtering
            # cannot be applied directly. Return as-is, assuming non-dictionary
            # types at the root level are either inherently safe or are handled
            # by other redaction mechanisms.
            return event_payload

        # Step 1: Recursively remove sensitive fields from the payload.
        # This is the primary security-focused shaping step.
        filtered_payload = cls._recursively_filter(event_payload)

        # Step 2: (Optional) Apply any other non-security-related shaping logic here.
        # This section is reserved for transformations like adding standard headers,
        # renaming fields for external consumption, flattening structures, or
        # adapting data formats to specific integration requirements.
        # Example (commented out as per original draft, kept for structural guidance):
        # if 'timestamp_utc' in filtered_payload:
        #     filtered_payload['timestamp'] = filtered_payload.pop('timestamp_utc')
        # filtered_payload['api_version'] = '2023-10-27'

        return filtered_payload