import os

class ConfigError(Exception):
    """Custom exception for configuration errors within the Config class."""
    pass

class Config:
    """
    A class to manage application configuration, allowing for nested settings
    and environment variable overrides with safety checks.
    """
    def __init__(self, initial_config: dict = None):
        """
        Initializes the Config object with an optional initial dictionary.

        Args:
            initial_config: An optional dictionary to pre-populate the configuration.
        """
        # Ensure that _config is always a dictionary to maintain consistency
        self._config = initial_config if initial_config is not None else {}
        if not isinstance(self._config, dict):
            raise TypeError("Initial configuration must be a dictionary.")

    def _set_nested(self, current_node: dict, key_path: str, value: any):
        """
        Recursively sets a value in a nested dictionary structure based on a dot-separated key path.

        This method includes robust checks to detect and prevent common configuration errors,
        specifically targeting the issue where scalar environment variables
        can silently override entire nested configuration branches.

        Args:
            current_node: The current dictionary node being processed during recursion.
            key_path: The dot-separated path to the configuration key (e.g., "app.name").
            value: The value to set at the specified key_path.

        Raises:
            ConfigError:
                - If an attempt is made to navigate through a path where an
                  intermediate segment is a scalar instead of a dictionary.
                - If an attempt is made to override an existing dictionary branch
                  with a scalar value, which is the core bug fixed here.
        """
        keys = key_path.split('.')
        for i, key in enumerate(keys):
            # Ensure the current node being processed is a dictionary.
            # If an intermediate path segment is a scalar, further navigation is impossible.
            if not isinstance(current_node, dict):
                raise ConfigError(
                    f"Configuration path segment '{'.'.join(keys[:i])}' is a scalar and cannot be navigated. "
                    f"Cannot set '{key_path}'."
                )

            if i < len(keys) - 1:
                # Not the final key; we are navigating to a nested dictionary.
                if key not in current_node:
                    current_node[key] = {}  # Create a new dictionary for the next level if the key doesn't exist.
                elif not isinstance(current_node[key], dict):
                    # An intermediate key exists but is a scalar (not a dictionary).
                    # For example, if 'app' was set to "my_app", but we then try to set 'app.name'.
                    raise ConfigError(
                        f"Configuration path segment '{'.'.join(keys[:i+1])}' (value: '{current_node[key]}') "
                        f"is a scalar and cannot be treated as a dictionary. "
                        f"Cannot navigate further to set '{key_path}'."
                    )
                current_node = current_node[key]  # Move deeper into the config structure.
            else:
                # This is the final key where the value needs to be set.

                # Core fix for the reported bug: Detect and prevent a scalar value
                # from silently overwriting an existing dictionary branch.
                if key in current_node and isinstance(current_node[key], dict) and not isinstance(value, dict):
                    raise ConfigError(
                        f"Attempted to override configuration branch '{key_path}' (currently a dictionary) "
                        f"with a scalar value '{value}'. This operation is not allowed as it would "
                        f"silently remove nested settings. Existing value: {current_node[key]}"
                    )
                current_node[key] = value  # Set the value at the final key.

    def load_from_env(self, prefix: str = 'AO_'):
        """
        Loads configuration from environment variables.
        Environment variable names starting with the specified prefix are processed.
        Double underscores (__) in environment variable names are converted to dots (.)
        for nested key paths, and keys are lowercased (e.g., AO_APP__NAME -> app.name).
        """
        for env_key, env_value in os.environ.items():
            if env_key.startswith(prefix):
                # Convert 'AO_APP__NAME' to 'app.name'
                config_key_path = env_key[len(prefix):].replace('__', '.').lower()
                converted_value = self._convert_env_value(env_value)
                self._set_nested(self._config, config_key_path, converted_value)

    def _convert_env_value(self, value_str: str) -> any:
        """
        Attempts to convert string values from environment variables to appropriate types
        (boolean, integer, float). If conversion fails, the original string is returned.
        """
        value_lower = value_str.lower()
        if value_lower == 'true':
            return True
        if value_lower == 'false':
            return False
        # Check for integers
        if value_str.isdigit() or (value_str.startswith('-') and value_str[1:].isdigit()):
            return int(value_str)
        try:
            # Attempt float conversion for decimals or scientific notation
            return float(value_str)
        except ValueError:
            pass  # Not a float, continue to return as string
        return value_str  # Default to string if no other conversion applies

    def get(self, key_path: str, default: any = None) -> any:
        """
        Retrieves a nested configuration value using a dot-separated path.

        Args:
            key_path: The dot-separated path to the configuration key (e.g., "app.name").
            default: The default value to return if the key_path is not found.

        Returns:
            The value at the specified key_path, or the default value if not found.
        """
        keys = key_path.split('.')
        node = self._config
        for key in keys:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                return default
        return node

    def __getitem__(self, key_path: str) -> any:
        """
        Allows dictionary-like access using bracket notation (e.g., config['app.name']).
        Raises KeyError if the path is not found.
        """
        # Use a unique sentinel value to distinguish between a stored None and a missing key.
        SENTINEL = object()
        value = self.get(key_path, default=SENTINEL)
        if value is SENTINEL:
            raise KeyError(f"Configuration key '{key_path}' not found.")
        return value

    def to_dict(self) -> dict:
        """
        Returns a deep copy of the entire configuration as a dictionary.
        This prevents external modifications from affecting the Config object's internal state.
        """
        import copy
        return copy.deepcopy(self._config)