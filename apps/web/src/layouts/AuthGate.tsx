import { useEffect } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../hooks/useAuth";
import { UNAUTHORIZED_EVENT } from "../services/api";
import { qk } from "../services/queries";
import { safeReturnPath } from "../pages/auth/authForms";
import { ErrorState } from "../components/ui/ErrorState";
import { Skeleton } from "../components/ui/Skeleton";

/**
 * Auth gate. Checks GET /auth/status and routes the viewer:
 *   setup_required  -> /setup
 *   not signed in   -> /login (the original path is kept so sign-in can return to it)
 *   signed in       -> the requested page, and /setup or /login redirect to /overview
 */
export function AuthGate() {
  const auth = useAuth();
  const location = useLocation();
  const queryClient = useQueryClient();

  useEffect(() => {
    const onUnauthorized = (): void => {
      void queryClient.invalidateQueries({ queryKey: qk.authStatus });
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [queryClient]);

  const status = auth.status;

  if (!status) {
    if (auth.error) {
      return (
        <main id="main-content" className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-4 px-4">
          <ErrorState
            title="JARVIS could not be reached"
            message="The sign-in status could not be loaded. Check that the API is running, then try again."
            onRetry={() => void auth.retry()}
          />
        </main>
      );
    }
    return (
      <div className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-4" role="status" aria-label="Checking sign-in status">
        <span className="sr-only">Checking sign-in status</span>
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  const path = location.pathname;
  if (status.setup_required) {
    return path === "/setup" ? <Outlet /> : <Navigate to="/setup" replace />;
  }
  if (!status.authenticated) {
    if (path === "/login") return <Outlet />;
    return <Navigate to="/login" replace state={{ from: `${location.pathname}${location.search}` }} />;
  }
  if (path === "/setup" || path === "/login") {
    return <Navigate to={safeReturnPath(location.state)} replace />;
  }
  return <Outlet />;
}
