import { isRouteErrorResponse, Link, useRouteError } from "react-router-dom";
import { ErrorState } from "../components/ui/ErrorState";

/** Route-level error element. Shows a safe message and never a stack trace. */
export function RouteErrorBoundary() {
  const error = useRouteError();
  const message = isRouteErrorResponse(error)
    ? error.status === 404
      ? "That page does not exist."
      : `The page could not be loaded (${error.status}).`
    : "The page could not be loaded. Reload to try again.";
  return (
    <main id="main-content" className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-4 px-4 py-10">
      <ErrorState title="Something went wrong" message={message} onRetry={() => window.location.reload()} retryLabel="Reload" />
      <Link to="/overview" className="text-center text-sm text-accent underline underline-offset-4">
        Back to Overview
      </Link>
    </main>
  );
}
