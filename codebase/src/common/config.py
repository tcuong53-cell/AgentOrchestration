import os
import re
import logging

logger = logging.getLogger(__name__)

class Config:
    """
    A configuration class that loads default values and allows overriding
    them with environment variables. Keys are normalized (lowercased and
    non-alphanumeric converted to underscores) for internal storage and access.
    """
    # Pre-compile the regex pattern for efficiency, as it's used repeatedly.
    _NON_ALPHANUMERIC_PATTERN = re.compile(r'[^a-z0-9_]')

    def __init__(self, default_config: dict = None):
        """
        Initializes the Config object.

        Args:
            default_config (dict, optional): A dictionary of default configuration
                                             values. Defaults to None.
        """
        self._config_data = default_config if default_config is not None else {}
        self._load_env_overrides()

    def _normalize_key(self, key: str) -> str:
        """
        Normalizes a configuration key. Converts to lowercase and replaces
        any non-alphanumeric character (except underscore) with an underscore.

        Args:
            key (str): The input key.

        Returns:
            str: The normalized key.
        """
        # Use the pre-compiled pattern for better performance.
        return self._NON_ALPHANUMERIC_PATTERN.sub('_', key.lower())

    def _load_env_overrides(self):
        """
        Loads configuration overrides from environment variables.
        Environment variables starting with "AO_" are considered.
        Detects and raises a ValueError if different-cased environment variables
        normalize to the same internal configuration key, ensuring deterministic
        behavior.
        """
        # This dictionary will store the *first* original environment variable key
        # that normalized to a specific config key. This is crucial for detecting
        # case-colliding overrides.
        normalized_key_to_original_env_key = {}

        # Sort environment variable keys to ensure deterministic processing order.
        # This guarantees that collision detection and error messages are consistent
        # across different environments or Python versions, regardless of the
        # underlying OS's environment variable iteration order.
        sorted_env_keys = sorted(os.environ.keys())

        for key in sorted_env_keys:
            # Only process environment variables that are intended as overrides.
            if key.startswith("AO_"):
                # Retrieve value only for relevant keys, optimizing lookups.
                value = os.environ[key]
                normalized_key = self._normalize_key(key)

                # Check for existing normalized keys to detect collisions.
                if normalized_key in normalized_key_to_original_env_key:
                    existing_original_env_key = normalized_key_to_original_env_key[normalized_key]

                    # If a different original environment variable key normalizes
                    # to the same internal key, it's an ambiguous collision.
                    if existing_original_env_key != key:
                        error_msg = (
                            f"Configuration collision detected: Environment variables "
                            f"'{existing_original_env_key}' and '{key}' both normalize to "
                            f"'{normalized_key}'. This leads to ambiguous configuration. "
                            f"Please resolve the naming conflict by using only one of them."
                        )
                        logger.error(error_msg)
                        raise ValueError(error_msg)
                    # If existing_original_env_key == key, it means the exact same
                    # environment variable key (case-sensitive) was encountered again.
                    # This case is generally not expected with os.environ.keys() unique entries.
                    # If it were to happen, the value would simply be updated below.
                else:
                    # This is the first time we've encountered an env var that normalizes to this key.
                    # Record the original key for future collision checks.
                    normalized_key_to_original_env_key[normalized_key] = key

                # Apply the override. If a collision was detected and raised,
                # this line for the colliding key would not be reached.
                # If multiple valid (non-colliding) env vars map to the same
                # normalized key, the one appearing last in `sorted_env_keys` wins.
                self._config_data[normalized_key] = value

    def get(self, key: str, default=None):
        """
        Retrieves a configuration value by its key.

        Args:
            key (str): The configuration key (can be in original casing).
            default: The default value to return if the key is not found.

        Returns:
            The configuration value or the default value if not found.
        """
        return self._config_data.get(self._normalize_key(key), default)

    def __getitem__(self, key: str):
        """
        Allows dictionary-like access to configuration values (e.g., config['my_key']).

        Args:
            key (str): The configuration key (can be in original casing).

        Returns:
            The configuration value.

        Raises:
            KeyError: If the normalized key is not found.
        """
        return self._config_data[self._normalize_key(key)]

    def __setitem__(self, key: str, value):
        """
        Allows dictionary-like setting of configuration values (e.g., config['my_key'] = 'value').

        Args:
            key (str): The configuration key (can be in original casing).
            value: The value to set.
        """
        self._config_data[self._normalize_key(key)] = value

    def to_dict(self) -> dict:
        """
        Returns a copy of the internal configuration data dictionary.

        Returns:
            dict: A copy of the configuration dictionary.
        """
        return self._config_data.copy()

    def __repr__(self) -> str:
        """
        Returns a string representation of the Config object.
        """
        return f"Config({self._config_data})"

    def __str__(self) -> str:
        """
        Returns a string representation of the Config object for printing.
        """
        return str(self._config_data)