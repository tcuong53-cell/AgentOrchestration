import os

class Config:
    def __init__(self):
        self._config_data = {}
        self._load_defaults()
        self._load_env_overrides()

    def _load_defaults(self):
        """
        Loads default configuration settings.
        """
        self._config_data = {
            "feature_enabled": False,
            "log_level": "INFO",
            "database_url": "sqlite:///./app.db",
        }

    def _coerce_value(self, value: str):
        """
        Attempts to coerce a string value from an environment variable
        into a more appropriate Python type, specifically handling booleans
        'true' and 'false'.
        """
        lower_value = value.lower()
        if lower_value == 'true':
            return True
        elif lower_value == 'false':
            return False
        # Future enhancement: Add logic here for other types like integers, floats, or JSON.
        # Example for integers:
        # if value.isdigit():
        #     return int(value)
        # Example for floats:
        # try:
        #     return float(value)
        # except ValueError:
        #     pass
        # Example for JSON:
        # import json
        # try:
        #     return json.loads(value)
        # except json.JSONDecodeError:
        #     pass
        return value # Return original value if no specific coercion is applied

    def _load_env_overrides(self):
        """
        Overrides configuration settings based on environment variables.
        Looks for variables prefixed with 'AO_' (e.g., AO_FEATURE_ENABLED).
        Handles boolean coercion for 'true'/'false' strings.
        """
        ENV_PREFIX = "AO_"
        for key, value in os.environ.items():
            if key.startswith(ENV_PREFIX):
                # Convert environment variable name (e.g., AO_FEATURE_ENABLED)
                # to a consistent config key format (e.g., feature_enabled).
                config_key = key[len(ENV_PREFIX):].lower()
                self._config_data[config_key] = self._coerce_value(value)

    def get(self, key: str, default=None):
        """
        Retrieves a configuration value by key.
        """
        return self._config_data.get(key, default)

    def __getitem__(self, key: str):
        """
        Allows dictionary-like access to config values (e.g., config['key']).
        """
        return self._config_data[key]

    def __setitem__(self, key: str, value):
        """
        Allows setting config values (e.g., config['key'] = value).
        """
        self._config_data[key] = value

    def to_dict(self) -> dict:
        """
        Returns a copy of the internal configuration dictionary.
        """
        return self._config_data.copy()