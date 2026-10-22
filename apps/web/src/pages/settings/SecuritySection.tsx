import { useState, type FormEvent } from "react";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { ErrorState } from "../../components/ui/ErrorState";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { SkeletonList } from "../../components/ui/Skeleton";
import { useToast } from "../../components/ui/Toast";
import { useAuth } from "../../hooks/useAuth";
import { ApiError } from "../../services/api";
import { formatDateTime, formatRelative } from "../../services/format";
import { SettingsCard } from "./SettingsShared";
import { useChangePassword, useRevokeSession, useSessionsQuery, type SessionItem } from "./securityQueries";

export const MIN_PASSWORD_LENGTH = 12;

/** Checks a password change before it is sent. Returns the first problem, or null. */
export function passwordProblem(input: { current: string; next: string; confirm: string }): string | null {
  if (!input.current) return "Enter your current password.";
  if (input.next.length < MIN_PASSWORD_LENGTH) return `Use at least ${MIN_PASSWORD_LENGTH} characters for the new password.`;
  if (input.next === input.current) return "Choose a password that differs from the current one.";
  if (input.next !== input.confirm) return "The new passwords do not match.";
  return null;
}

function SessionRow({ session, onRevoked }: { session: SessionItem; onRevoked: () => void }) {
  const revoke = useRevokeSession();
  const { toast } = useToast();
  return (
    <li className="flex flex-col gap-2 rounded-lg border border-line bg-raised p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-ink">{session.user_agent ? truncate(session.user_agent, 60) : "Unknown browser"}</span>
          {session.current ? <Badge tone="accent">This session</Badge> : null}
        </div>
        {session.current ? null : (
          <Button
            size="sm"
            variant="danger"
            loading={revoke.isPending}
            onClick={() =>
              revoke.mutate(session.id, {
                onSuccess: () => {
                  toast({ tone: "success", title: "Session signed out" });
                  onRevoked();
                },
                onError: (cause) => toast({ tone: "error", title: "Session not signed out", description: cause.message }),
              })
            }
          >
            Sign out
          </Button>
        )}
      </div>
      <p className="text-xs text-muted">
        Last seen {formatRelative(session.last_seen_at)} · Started {formatDateTime(session.created_at)}
        {session.ip_address ? ` · ${session.ip_address}` : ""}
      </p>
    </li>
  );
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** Sessions, password change and sign out. Revoking another session never affects this one. */
export function SecuritySection() {
  const sessions = useSessionsQuery();
  const auth = useAuth();
  const change = useChangePassword();
  const { toast } = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  const onChangePassword = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const found = passwordProblem({ current, next, confirm });
    setProblem(found);
    if (found) return;
    change.mutate(
      { current_password: current, new_password: next },
      {
        onSuccess: () => {
          setCurrent("");
          setNext("");
          setConfirm("");
          toast({ tone: "success", title: "Password changed", description: "Other sessions were signed out." });
        },
        onError: (cause) => setProblem(cause instanceof ApiError ? cause.message : "The password could not be changed."),
      },
    );
  };

  return (
    <div className="flex flex-col gap-6">
      <SettingsCard title="Signed-in sessions" description="Where your account is signed in. Sign out any session you do not recognise.">
        {sessions.isPending ? <SkeletonList rows={2} label="Loading sessions" /> : null}
        {sessions.isError ? <ErrorState message={sessions.error.message} onRetry={() => void sessions.refetch()} /> : null}
        {sessions.data ? (
          <ul aria-label="Signed-in sessions" className="flex flex-col gap-2">
            {sessions.data.map((session) => (
              <SessionRow key={session.id} session={session} onRevoked={() => void sessions.refetch()} />
            ))}
          </ul>
        ) : null}
        <div className="flex flex-wrap justify-end gap-2 border-t border-line pt-4">
          <Button variant="secondary" loading={auth.logout.isPending} onClick={() => auth.logout.mutate()}>
            Sign out of this browser
          </Button>
        </div>
      </SettingsCard>

      <SettingsCard title="Change password" description={`At least ${MIN_PASSWORD_LENGTH} characters. Other sessions are signed out when it changes.`}>
        <form noValidate onSubmit={onChangePassword} aria-label="Change password" className="flex flex-col gap-4">
          {problem ? <ErrorState title="Password not changed" message={problem} /> : null}
          <Field label="Current password">
            {(control) => (
              <Input {...control} type="password" autoComplete="current-password" value={current} onChange={(event) => setCurrent(event.target.value)} />
            )}
          </Field>
          <Field label="New password" hint={`At least ${MIN_PASSWORD_LENGTH} characters.`}>
            {(control) => (
              <Input {...control} type="password" autoComplete="new-password" value={next} onChange={(event) => setNext(event.target.value)} />
            )}
          </Field>
          <Field label="Confirm new password">
            {(control) => (
              <Input {...control} type="password" autoComplete="new-password" value={confirm} onChange={(event) => setConfirm(event.target.value)} />
            )}
          </Field>
          <div className="flex justify-end">
            <Button type="submit" variant="primary" loading={change.isPending}>
              Change password
            </Button>
          </div>
        </form>
      </SettingsCard>
    </div>
  );
}
