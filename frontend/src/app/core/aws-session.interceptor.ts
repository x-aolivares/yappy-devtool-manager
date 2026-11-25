import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, throwError } from 'rxjs';
import { AwsSessionService } from './services/aws-session.service';

export const awsSessionInterceptor: HttpInterceptorFn = (req, next) => {
  const awsSession = inject(AwsSessionService);

  return next(req).pipe(
    catchError((err: unknown) => {
      if (err instanceof HttpErrorResponse && err.status === 401) {
        const detail = err.error?.detail;
        if (detail && typeof detail === 'object' && detail.code === 'aws_session_expired') {
          awsSession.requestReauth({
            message: typeof detail.message === 'string' ? detail.message : null,
            profile: typeof detail.profile === 'string' ? detail.profile : null,
          });
        }
      }
      return throwError(() => err);
    }),
  );
};