import os

class Config:
    """
    Manages application configuration, allowing overrides from environment variables.

    Environment variables starting with 'AO_' are automatically loaded and
    can override default configuration values. Numeric-looking environment
    variable values (integers or floats) are automatically coerced to their
    respective numeric types.
    """
    def __init__(self):
        self._config = {}
        self._load_default_config()
        self._load_env_overrides()

    def _load_default_config(self):
        """
        Loads the default configuration values.
        This method should be implemented to set initial configuration.
        """
        # Example default values (adjust as per actual project needs)
        self._config['app_port'] = 8080
        self._config['debug_mode'] = False
        self._config['threshold'] = 0.75
        self._config['database_url'] = "sqlite:///./app.db"
        # Add other default configuration items here as needed by the project
        # For instance:
        # self._config['api_key'] = "default_api_key"

    def _load_env_overrides(self):
        """
        Loads environment variables that start with 'AO_' and applies them
        as overrides to the configuration.
        
        Values that appear to be integers or floats are coerced to their
        respective numeric types. Otherwise, they remain strings.
        """
        for key, value in os.environ.items():
            if key.startswith("AO_"):
                # Remove 'AO_' prefix and convert to lowercase for the config key
                config_key = key[3:].lower()

                # Attempt to coerce numeric-looking strings to int or float.
                # Prioritize int over float if possible, but allow float for numbers
                # that are explicitly floating-point (e.g., "1.0", "3.14").
                coerced_value = value
                try:
                    # Try converting to int first (e.g., "8080", "-123")
                    coerced_value = int(value)
                except ValueError:
                    try:
                        # If not an int, try converting to float (e.g., "3.14", "1.0", "1e-5")
                        coerced_value = float(value)
                    except ValueError:
                        # If neither int nor float, keep the value as a string.
                        # This handles non-numeric strings or empty strings.
                        pass
                
                self._config[config_key] = coerced_value

    def get(self, key: str, default=None):
        """
        Retrieves a configuration value by key.

        Args:
            key (str): The configuration key.
            default: The default value to return if the key is not found.

        Returns:
            The configuration value or the default if not found.
        """
        return self._config.get(key, default)

    def set(self, key: str, value):
        """
        Sets a configuration value by key. This can be used for runtime
        configuration changes, though environment overrides take precedence
        during initial load.

        Args:
            key (str): The configuration key.
            value: The value to set.
        """
        self._config[key] = value

    def __getattr__(self, name):
        """
        Allows accessing config values as attributes (e.g., config.app_port).
        """
        if name in self._config:
            return self._config[name]
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

    def __repr__(self):
        """
        Returns a string representation of the Config object for debugging.
        """
        return f"Config({self._config})"

    def __str__(self):
        """
        Returns a human-readable string representation of the Config object.
        """
        return str(self._config)