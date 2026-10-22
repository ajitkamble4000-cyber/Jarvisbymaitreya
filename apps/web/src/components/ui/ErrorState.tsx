import { Button } from "./Button";

export interface ErrorStateProps {
  title?: string;
  message: string;
  onRetry?: () => void;
  retryLabel?: string;
}

/** Shows a real error with its message. Announced as an alert so that screen readers hear it. */
export function ErrorState({ title = "Something went wrong", message, onRetry, retryLabel = "Try again" }: ErrorStateProps) {
  return (
    <div role="alert" className="flex flex-col gap-3 rounded-card border border-danger/50 bg-danger/10 p-4">
      <p className="text-sm font-semibold text-danger">{title}</p>
      <p className="text-sm text-ink">{message}</p>
      {onRetry ? (
        <div>
          <Button size="sm" variant="secondary" onClick={onRetry}>
            {retryLabel}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
