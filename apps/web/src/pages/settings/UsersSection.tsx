import { useState, type FormEvent } from "react";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { ErrorState } from "../../components/ui/ErrorState";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { SkeletonList } from "../../components/ui/Skeleton";
import { useToast } from "../../components/ui/Toast";
import { ApiError } from "../../services/api";
import { useAdminUsersQuery, useCreateUser, useResetUserPassword, useUpdateUser } from "../../services/queries";
import type { AdminUser } from "../../types/api";
import { SettingsCard } from "./SettingsShared";
import { canManageUser, lastSignInText, userStatusText, validateMember, validatePassword, type MemberDraft, type MemberErrors } from "./usersModel";

const EMPTY_DRAFT: MemberDraft = { email: "", displayName: "", password: "" };

function messageFor(error: unknown): string {
  return error instanceof Error && error.message ? error.message : "The request could not be completed.";
}

/**
 * Owner only: lists the accounts, creates members, deactivates and reactivates them, and resets a
 * member's password. The server enforces the same rule (docs/phase3/users.md). Errors are shown as text.
 */
export function UsersSection() {
  const users = useAdminUsersQuery();

  return (
    <div className="flex flex-col gap-6">
      <SettingsCard
        title="Accounts"
        description="Members sign in to the same JARVIS with their own conversations, tasks and memories. You can deactivate or reset them here. You cannot see their data."
      >
        {users.isPending ? <SkeletonList rows={3} label="Loading accounts" /> : null}
        {users.isError ? <ErrorState message={messageFor(users.error)} onRetry={() => void users.refetch()} /> : null}
        {users.data ? (
          <ul aria-label="Accounts" className="flex flex-col gap-3">
            {users.data.items.map((user) => (
              <UserRow key={user.id} user={user} />
            ))}
          </ul>
        ) : null}
      </SettingsCard>

      <CreateMemberCard />
    </div>
  );
}

function UserRow({ user }: { user: AdminUser }) {
  const update = useUpdateUser();
  const { toast } = useToast();
  const [resetOpen, setResetOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const manageable = canManageUser(user);
  const deactivating = user.is_active;

  const toggleActive = (): void => {
    update.mutate(
      { id: user.id, patch: { is_active: !user.is_active } },
      {
        onSuccess: () => {
          setConfirmOpen(false);
          toast({ tone: "success", title: deactivating ? `${user.email} deactivated` : `${user.email} reactivated` });
        },
        onError: (error) => toast({ tone: "error", title: "Account not changed", description: messageFor(error) }),
      },
    );
  };

  return (
    <li className="flex flex-col gap-3 rounded-lg border border-line bg-raised p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="truncate text-sm font-medium text-ink">{user.display_name || user.email}</span>
          <span className="truncate text-xs text-muted">{user.email}</span>
          <span className="text-xs text-muted">{lastSignInText(user)}</span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={user.role === "owner" ? "accent" : "neutral"}>{user.role === "owner" ? "Owner" : "Member"}</Badge>
          <Badge tone={user.is_active ? "success" : "warning"}>{userStatusText(user)}</Badge>
        </div>
      </div>

      {update.isError ? (
        <p role="alert" className="text-xs font-medium text-danger">
          {messageFor(update.error)}
        </p>
      ) : null}

      {manageable ? (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant={deactivating ? "danger" : "secondary"} onClick={() => setConfirmOpen(true)} disabled={update.isPending}>
            {deactivating ? "Deactivate" : "Reactivate"}
          </Button>
          <Button size="sm" variant="secondary" onClick={() => setResetOpen(true)}>
            Reset password
          </Button>
        </div>
      ) : (
        <p className="text-xs text-muted">The owner account is managed in the server settings, not here.</p>
      )}

      <Dialog
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        title={deactivating ? `Deactivate ${user.email}?` : `Reactivate ${user.email}?`}
        description={
          deactivating
            ? "They are signed out at once and cannot sign in until you reactivate them. Their data is kept."
            : "They can sign in again with their current password."
        }
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirmOpen(false)} disabled={update.isPending}>
              Cancel
            </Button>
            <Button variant={deactivating ? "danger" : "primary"} loading={update.isPending} onClick={toggleActive}>
              {deactivating ? "Deactivate account" : "Reactivate account"}
            </Button>
          </>
        }
      >
        <span className="sr-only">Confirm the change.</span>
      </Dialog>

      {resetOpen ? <ResetPasswordDialog user={user} onClose={() => setResetOpen(false)} /> : null}
    </li>
  );
}

function ResetPasswordDialog({ user, onClose }: { user: AdminUser; onClose: () => void }) {
  const reset = useResetUserPassword();
  const { toast } = useToast();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);

  const onSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const problem = validatePassword(password);
    setError(problem);
    if (problem) return;
    setServerError(null);
    reset.mutate(
      { id: user.id, newPassword: password },
      {
        onSuccess: () => {
          toast({ tone: "success", title: `Password reset for ${user.email}`, description: "Their current sessions were signed out." });
          onClose();
        },
        onError: (cause) => setServerError(messageFor(cause)),
      },
    );
  };

  return (
    <Dialog open onClose={onClose} title={`Reset password for ${user.email}`} description="Choose a new password, then share it with them in person or by another private channel.">
      <form className="flex flex-col gap-4" onSubmit={onSubmit} noValidate>
        <Field label="New password" hint="At least 12 characters. Their current sessions are signed out." error={error}>
          {(control) => (
            <Input {...control} type="password" autoComplete="new-password" invalid={Boolean(error)} value={password} onChange={(event) => setPassword(event.target.value)} />
          )}
        </Field>
        {serverError ? (
          <p role="alert" className="text-sm font-medium text-danger">
            {serverError}
          </p>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={reset.isPending}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" loading={reset.isPending}>
            Reset password
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/**
 * Creates a member. The password the owner types is shown once after the account is made, with a note
 * that it has to be shared out of band. JARVIS sends no email.
 */
function CreateMemberCard() {
  const create = useCreateUser();
  const [draft, setDraft] = useState<MemberDraft>(EMPTY_DRAFT);
  const [errors, setErrors] = useState<MemberErrors>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [created, setCreated] = useState<{ email: string; password: string } | null>(null);

  const onSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const found = validateMember(draft);
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    setServerError(null);
    const email = draft.email.trim().toLowerCase();
    create.mutate(
      { email, display_name: draft.displayName.trim(), password: draft.password, role: "member" },
      {
        onSuccess: () => {
          setCreated({ email, password: draft.password });
          setDraft(EMPTY_DRAFT);
        },
        onError: (cause) => setServerError(cause instanceof ApiError && cause.code === "email_taken" ? "An account with this email already exists." : messageFor(cause)),
      },
    );
  };

  return (
    <SettingsCard title="Add a member" description="The member signs in with the email and password you set. Share the password with them outside JARVIS.">
      {created ? (
        <div role="status" className="flex flex-col gap-2 rounded-lg border border-warning/50 bg-warning/10 p-3 text-sm">
          <p className="font-medium text-ink">Member created: {created.email}</p>
          <p className="text-muted">Share this password with them now. It is shown only once and is not stored in readable form.</p>
          <code className="select-all break-all rounded border border-line bg-canvas px-2 py-1 font-mono text-ink">{created.password}</code>
          <div>
            <Button size="sm" variant="secondary" onClick={() => setCreated(null)}>
              Done
            </Button>
          </div>
        </div>
      ) : null}

      <form className="flex flex-col gap-4" onSubmit={onSubmit} noValidate>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Email" error={errors.email ?? null}>
            {(control) => (
              <Input
                {...control}
                type="email"
                autoComplete="off"
                spellCheck={false}
                invalid={Boolean(errors.email)}
                value={draft.email}
                onChange={(event) => setDraft({ ...draft, email: event.target.value })}
              />
            )}
          </Field>
          <Field label="Display name" error={errors.displayName ?? null}>
            {(control) => (
              <Input
                {...control}
                autoComplete="off"
                maxLength={120}
                invalid={Boolean(errors.displayName)}
                value={draft.displayName}
                onChange={(event) => setDraft({ ...draft, displayName: event.target.value })}
              />
            )}
          </Field>
        </div>
        <Field label="Initial password" hint="At least 12 characters." error={errors.password ?? null}>
          {(control) => (
            <Input
              {...control}
              type="password"
              autoComplete="new-password"
              invalid={Boolean(errors.password)}
              value={draft.password}
              onChange={(event) => setDraft({ ...draft, password: event.target.value })}
            />
          )}
        </Field>
        {serverError ? (
          <p role="alert" className="text-sm font-medium text-danger">
            {serverError}
          </p>
        ) : null}
        <div className="flex justify-end">
          <Button type="submit" variant="primary" loading={create.isPending}>
            Create member
          </Button>
        </div>
      </form>
    </SettingsCard>
  );
}
