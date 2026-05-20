import os
import re

class PathBuilder:
    """
    Manages the construction of file paths for registry artifacts,
    ensuring security against path traversal and symlink vulnerabilities.
    """
    def __init__(self, base_registry_path: str):
        """
        Initializes the PathBuilder with a base directory for the registry.

        The base path is normalized, resolved to an absolute path, and
        any symbolic links are fully resolved to their canonical path.

        Args:
            base_registry_path: The root directory for all registry artifacts.

        Raises:
            ValueError: If `base_registry_path` is empty or the resolved path is not a directory.
            OSError: If the base_registry_path cannot be resolved to a real path
                     due to system errors (e.g., permissions).
        """
        if not base_registry_path:
            raise ValueError("base_registry_path cannot be empty.")

        try:
            # Resolve the base path to its absolute and canonical form,
            # resolving any symbolic links. This is critical for security
            # to ensure all subsequent path comparisons are against a true,
            # fixed reference point in the filesystem.
            self.base_registry_path = os.path.realpath(base_registry_path)
            # Ensure the resolved base path actually exists and is a directory.
            # This prevents issues if the root path itself is invalid or missing.
            if not os.path.isdir(self.base_registry_path):
                raise ValueError(
                    f"Resolved base registry path '{self.base_registry_path}' "
                    "does not exist or is not a directory."
                )
        except OSError as e:
            raise OSError(f"Failed to resolve base registry path '{base_registry_path}': {e}") from e

    def _sanitize_path_component(self, component_name: str) -> str:
        """
        Sanitizes a string to be used as a single path component, preventing path traversal.

        This method strictly checks and sanitizes the input to ensure it cannot escape
        its intended directory or refer to special directory entries ('.', '..').

        Args:
            component_name: The string to sanitize (e.g., a handler name).

        Returns:
            The sanitized string, safe to be used as a single directory or file name.

        Raises:
            ValueError: If the component_name is empty, contains path separators,
                        is a restricted directory reference ('.', '..'), or
                        becomes empty/restricted after sanitization.
        """
        if not isinstance(component_name, str) or not component_name:
            raise ValueError("Path component must be a non-empty string.")

        # Explicitly check for path separators (forward and backward slashes)
        if '/' in component_name or '\\' in component_name:
            raise ValueError(
                f"Path component '{component_name}' contains path separators ('/' or '\\') "
                "and is considered unsafe for direct use."
            )

        # Explicitly check for restricted directory references (current and parent directory)
        if component_name == '.' or component_name == '..':
            raise ValueError(
                f"Path component '{component_name}' is a restricted directory reference ('.' or '..') "
                "and is considered unsafe."
            )

        # Further sanitize to remove any characters that are generally unsafe or
        # problematic for filenames across different file systems.
        # This regex allows alphanumeric characters, underscore, hyphen, and period.
        # It implicitly rejects control characters, reserved characters, etc.
        sanitized = re.sub(r'[^\w.-]', '', component_name)

        if not sanitized:
            raise ValueError(
                f"Path component '{component_name}' resulted in an empty string after sanitization. "
                "This typically happens if the input contains only disallowed characters."
            )

        # Re-check after broad sanitization for cases where a complex string might
        # simplify into a restricted reference (e.g., '...' becoming '..').
        if sanitized == '.' or sanitized == '..':
            raise ValueError(
                f"Path component '{component_name}' sanitized into a restricted directory reference ('{sanitized}')."
            )

        return sanitized

    def get_handler_metadata_path(self, handler_name: str) -> str:
        """
        Constructs the absolute and canonical file path for the local plugin metadata
        of a given handler, enforcing robust security measures.

        This method prevents path traversal and symlink vulnerabilities by:
        1. Sanitizing the handler name to ensure it's a safe, single component.
        2. Constructing the full path using `os.path.join`.
        3. Resolving the constructed path to its canonical form, following all symlinks.
        4. Crucially, verifying that the *canonical* resolved path remains strictly
           a sub-path of the *canonical* base registry directory.

        Args:
            handler_name: The name of the handler for which to build the metadata path.
                          This name is treated as a single directory component and sanitized.

        Returns:
            The absolute and securely validated canonical path to the handler's metadata file.

        Raises:
            ValueError: If the handler_name is invalid or unsafe according to sanitization rules.
            PermissionError: If an attempted path traversal or symlink escape is detected,
                             meaning the constructed canonical path would resolve outside
                             the defined canonical base registry directory.
            OSError: If there's a system-level error during path resolution (e.g., permissions).
        """
        try:
            # 1. Sanitize the handler_name to prevent it from containing path traversal elements.
            #    The handler_name is expected to be a simple identifier, not a path.
            sanitized_handler_name = self._sanitize_path_component(handler_name)
        except ValueError as e:
            raise ValueError(f"Invalid handler name '{handler_name}' detected during path construction: {e}") from e

        # 2. Construct the intended path using the sanitized component.
        #    os.path.join handles OS-specific path separators.
        #    This path is constructed relative to the *canonical* base_registry_path.
        intended_path = os.path.join(
            self.base_registry_path,
            "handlers",
            sanitized_handler_name,
            "metadata.json"
        )

        try:
            # 3. Resolve the constructed path to its absolute and canonical form,
            #    following all symbolic links. This is critical for preventing
            #    symlink-based path traversal attacks.
            abs_canonical_path = os.path.realpath(intended_path)
        except OSError as e:
            # Catch potential system errors during realpath resolution (e.g., permission denied
            # to traverse a directory, even if the path exists).
            raise OSError(f"Failed to resolve canonical path for handler '{handler_name}': {e}") from e

        # 4. Critical security check: Ensure the resolved canonical path remains
        #    strictly a sub-path of the canonical base registry directory.
        #    By appending os.sep to the base, we ensure that `base_path.startswith(base_path)`
        #    is false (which is correct as the metadata file must be *within* a subdirectory),
        #    and correctly identifies `base_path/sub` as a subpath, while rejecting `base_path_sibling`.
        if not abs_canonical_path.startswith(self.base_registry_path + os.sep):
            raise PermissionError(
                f"Attempted path traversal or symlink escape detected for handler '{handler_name}'. "
                f"Resolved canonical path '{abs_canonical_path}' is outside the allowed base directory "
                f"'{self.base_registry_path}'."
            )

        return abs_canonical_path