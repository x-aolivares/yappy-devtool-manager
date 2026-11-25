import { HttpErrorResponse } from '@angular/common/http';

export const AWS_SESSION_EXPIRED_CODE = 'aws_session_expired';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export function isAwsSessionExpiredError(err: unknown): boolean {
  if (err instanceof ApiError) return err.code === AWS_SESSION_EXPIRED_CODE;
  if (err instanceof HttpErrorResponse) {
    const detail = (err.error as { detail?: unknown } | null)?.detail;
    return !!detail && typeof detail === 'object' && !Array.isArray(detail) && (detail as { code?: unknown }).code === AWS_SESSION_EXPIRED_CODE;
  }
  return false;
}

export function toApiError(err: unknown): ApiError {
  if (err instanceof HttpErrorResponse) {
    const body = err.error as { detail?: unknown } | null;
    const detail = body?.detail;
    if (typeof detail === 'string') {
      return new ApiError(detail, err.status);
    }
    if (Array.isArray(detail)) {
      const msgs = detail
        .map((d) => (typeof d === 'object' && d !== null && 'msg' in d ? String(d.msg) : String(d)))
        .join('; ');
      return new ApiError(msgs || err.statusText || `Error ${err.status}`, err.status);
    }
    if (detail && typeof detail === 'object') {
      const code = (detail as { code?: unknown }).code;
      const message = (detail as { message?: unknown }).message;
      if (typeof message === 'string' && message.trim()) {
        return new ApiError(message, err.status, typeof code === 'string' ? code : undefined);
      }
    }
    return new ApiError(err.statusText || `Error ${err.status}`, err.status);
  }
  return new ApiError(err instanceof Error ? err.message : String(err));
}