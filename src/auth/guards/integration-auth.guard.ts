import { Injectable, CanActivate, ExecutionContext, UnauthorizedException } from '@nestjs/common';
import { Request } from 'express'; // Assuming an Express-based context for request object
import { TokenExpiredError, JsonWebTokenError } from 'jsonwebtoken'; // Assuming `jsonwebtoken` library is used internally by AuthService for token verification

// --- Interfaces for clarity and type safety ---
// These interfaces should ideally reside in a common place within your project,
// e.g., `src/common/interfaces/user.interface.ts` and `src/auth/interfaces/jwt-payload.interface.ts`.
interface User {
  id: string;
  email: string;
  isActive: boolean; // Crucial property to check if the user is enabled/active
  // Add other user properties relevant for potential future authorization checks (e.g., roles)
}

interface JwtPayload {
  userId: string;
  // Potentially other fields like clientId, scopes, etc., included in the JWT token for authorization
  // Example: scopes?: string[];
}
// -----------------------------------------------------------

// Assuming these services are responsible for core authentication logic and user management.
// Example paths: `src/auth/auth.service.ts`, `src/user/user.service.ts`
import { AuthService } from '../auth.service';
import { UserService } from '../../user/user.service';

@Injectable()
export class IntegrationAuthGuard implements CanActivate {
  constructor(
    private readonly authService: AuthService,
    private readonly userService: UserService,
  ) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const request = context.switchToHttp().getRequest<Request>();
    const token = this.extractTokenFromHeader(request);

    if (!token) {
      throw new UnauthorizedException('Authentication token missing. Access denied.');
    }

    try {
      // 1. Validate the token: Verify signature, integrity, and check for expiration.
      // The `authService.verifyIntegrationToken` method is expected to throw specific errors
      // (e.g., `TokenExpiredError`, `JsonWebTokenError`) if the token is invalid or expired.
      const payload: JwtPayload = await this.authService.verifyIntegrationToken(token);

      if (!payload || !payload.userId) {
        throw new UnauthorizedException('Invalid or malformed authentication token payload.');
      }

      // 2. Check for explicit token revocation.
      // This is a critical security measure to invalidate tokens immediately (e.g., user logout, password reset, security incident).
      if (await this.authService.isTokenRevoked(token)) {
        throw new UnauthorizedException('Authentication token has been explicitly revoked. Access denied.');
      }

      // 3. Fetch the user/principal associated with the token's `userId`.
      // This ensures that we operate on the most current user state, rather than relying solely on potentially stale token data.
      const user: User | null = await this.userService.findById(payload.userId);

      if (!user) {
        // User not found implies the account no longer exists or the userId in the token is invalid.
        throw new UnauthorizedException('User account associated with the token not found or no longer exists.');
      }

      // 4. CRITICAL FIX: Ensure the user account is active and not disabled.
      // This directly addresses the reported bug: "Block disabled users from webhook management".
      // An `isActive: false` status indicates a disabled, suspended, or otherwise inactive account.
      // This check is vital for rejecting access where a user's status has changed since their token was issued.
      if (!user.isActive) {
        throw new UnauthorizedException('User account is disabled or inactive. Access denied.');
      }

      // --- Consideration for "Insufficiently Scoped Principals" (if applicable) ---
      // If the bug report's acceptance criteria for "insufficiently scoped principals"
      // or "correct workspace role" requires checks at this level, they would typically follow here.
      // However, it's often best practice to handle granular authorization (roles, permissions, scopes)
      // in separate, dedicated Guards (e.g., `RolesGuard`, `PermissionsGuard`) that run after
      // the authentication guard has successfully attached the authenticated user to the request.
      // Example:
      // if (!this.authService.userHasRequiredScopes(user, payload.scopes, request.route.path)) {
      //   throw new ForbiddenException('Insufficient privileges for this action.');
      // }
      // -----------------------------------------------------------------------------

      // If all authentication and user status checks pass, attach the authenticated and active
      // user object to the request. This makes the current user's full, up-to-date profile
      // available to subsequent request handlers (controllers, other guards, interceptors).
      request['user'] = user;

      return true; // Access granted
    } catch (error: unknown) { // Use 'unknown' for safer, type-checked error handling
      // Re-throw specific `UnauthorizedException` instances that might have been generated
      // earlier in the authentication flow (e.g., by `authService` itself).
      if (error instanceof UnauthorizedException) {
        throw error;
      }

      // Handle common token-related errors by checking specific error instances from `jsonwebtoken`.
      if (error instanceof TokenExpiredError) {
        throw new UnauthorizedException('Authentication token has expired. Please re-authenticate.');
      }
      if (error instanceof JsonWebTokenError) {
        // This catches generic JWT errors like 'invalid signature', 'malformed token', etc.
        throw new UnauthorizedException('Invalid authentication token. Access denied.');
      }

      // Fallback for any other unexpected errors during the authentication process.
      // Avoid exposing detailed internal error messages to the client for security reasons.
      throw new UnauthorizedException('Authentication failed due to an unexpected error. Access denied.');
    }
  }

  /**
   * Extracts the bearer token from the 'Authorization' header of the incoming request.
   * Expected header format: "Bearer <token>"
   */
  private extractTokenFromHeader(request: Request): string | undefined {
    const [type, token] = request.headers.authorization?.split(' ') ?? [];
    return type === 'Bearer' ? token : undefined;
  }
}