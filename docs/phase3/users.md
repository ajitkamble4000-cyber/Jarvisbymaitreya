# Phase 3: member accounts managed by the owner

Scope: the owner creates, lists, deactivates and resets member accounts. Members use the same product with their own data and cannot reach any `/users` route. The rest of the API is in `docs/api.md`, which this document does not edit.

## Roles

| Role | How it is created | Notes |
| --- | --- | --- |
| `owner` | `POST /api/v1/auth/setup`, once, while no user exists | Exactly one. Cannot be deactivated through `/users`. Cannot be created through `/users`. Cannot have its password set through `/users`. |
| `member` | `POST /api/v1/users` by the owner | Same product, own data. No access to `/users`. |

The role is the existing `users.role` column. No migration was added.

## Endpoints

All four routes require the owner. Base path `/api/v1/users`. Unsafe methods need the usual `Origin` and CSRF headers.

| Method | Path | Body | Success |
| --- | --- | --- | --- |
| GET | `/users` | none | 200 `{"items": [AdminUser]}` |
| POST | `/users` | `{"email", "display_name", "password", "role": "member"}` | 201 `AdminUser` |
| PATCH | `/users/{id}` | `{"is_active"?: bool, "display_name"?: string}` | 200 `AdminUser` |
| POST | `/users/{id}/password` | `{"new_password"}` | 204, no body |

`AdminUser`:

```json
{
  "id": "uuid",
  "email": "member@example.com",
  "display_name": "Sam",
  "role": "member",
  "is_active": true,
  "created_at": "2026-10-21T09:00:00Z",
  "last_login_at": null
}
```

`password_hash` is never returned.

### Body rules

- Unknown fields are rejected with 422 `validation_error`. A body that sends `role` other than `"member"` (including `"owner"`) is rejected, and `role` is not accepted by PATCH at all.
- `email` is trimmed, lower-cased and validated. It must be unique, compared case-insensitively.
- `display_name` is trimmed. It must be 1 to 120 characters after trimming and must contain no control characters.
- `password` and `new_password` must be 12 to 256 characters, the same bounds as `validate_new_password`.

## Error codes

| Status | Code | When |
| --- | --- | --- |
| 401 | `not_authenticated` | No session cookie. |
| 401 | `session_expired` | The session is revoked or expired. This is what a cookie returns after deactivation or a password reset. |
| 401 | `account_unavailable` | The session is live but its user is inactive. |
| 403 | `forbidden` | A signed-in member calls any `/users` route. |
| 404 | `user_not_found` | The id is well formed but no user has it. |
| 409 | `email_taken` | Create with an email that already has an account. Shown only on the owner's create call. |
| 422 | `validation_error` | Bad body, unknown field, malformed id, or a role other than `member`. |
| 422 | `owner_protected` | PATCH deactivates the owner, or the password route targets the owner. |
| 429 | `rate_limited` | More than `LOGIN_RATE_LIMIT_PER_MINUTE` creates per owner per minute. |

Authorisation runs before validation. A member sending a malformed body gets 403, not 422.

## Behaviour

- **Create** adds a member with `is_active: true` and `last_login_at: null`. The owner sets the initial password and shares it out of band.
- **Deactivate** (`is_active: false`) sets the flag and revokes every live session of that user. Sign-in then returns the usual generic 401 `invalid_credentials`. An existing cookie returns 401 `session_expired`.
- **Reactivate** (`is_active: true`) restores sign-in. Sessions revoked earlier stay revoked, so the member signs in again.
- **Reset password** sets the new hash and revokes that member's sessions. The old password stops working at once.
- **Rename** changes `display_name` only.
- An empty PATCH body is a no-op that returns the current user.

## Audit

Each change records one audit event. `user_id` is the owner, `target_type` is `user`, and `target_id` is the member's id.

| Action | Data |
| --- | --- |
| `users.created` | `{"role": "member"}` |
| `users.updated` | `{"is_active"?: bool, "display_name_set"?: true, "sessions_revoked": int}` |
| `users.password_reset` | `{"sessions_revoked": int}` |

Audit data never includes a password, an email or a token. The audit writer also drops any field whose name looks like a credential.

## Isolation

Every existing user-scoped route already filters by `user_id`. Phase 3 adds tests, not filters. `tests/integration/test_member_isolation.py` checks that a member sees none of the owner's devices, tasks, conversations, memories or audit events, and that one member sees none of another member's conversations, memories or tasks. Reads by id return 404, and writes return 404.

## Environment

| Variable | Effect |
| --- | --- |
| `LOGIN_RATE_LIMIT_PER_MINUTE` | Also bounds member creation, per owner. Default 10. |

No other variable is added.

## Security notes

- `require_owner` in `app/api/deps.py` is the single gate for `/users`. A member gets 403 and a client with no session gets 401.
- Login stays generic. An unknown email, a wrong password, and a deactivated account all return the same `invalid_credentials` message. Members cannot reach `/users`, so they cannot learn whether another email exists.
- Passwords are hashed with Argon2id through `hash_password`. They are never returned, logged or audited.
- Concurrent creates for the same email are caught by the unique constraint and return 409 `email_taken`.

## Known limitations

- No invitation email. The owner sets each member's first password and shares it out of band.
- No email verification.
- Members cannot be deleted, only deactivated. Their rows are kept.
- No role change through the API. A member cannot become owner.
- The create rate limit is in-process, like the other rate limits in this API.
- Not owner-only yet, and outside this workstream: `GET /api/v1/developer/logs` and `GET /api/v1/developer/config-check` accept any signed-in user. The log buffer is process-wide and does not record which user a line belongs to, so a member can read log lines from other users' activity. Both routes should use `require_owner`. This is a change for the developer route owner.

## Tests

- `tests/integration/test_users_admin.py`: authorisation on every route for members and anonymous clients, create (including duplicate, invalid and rate-limited input), deactivation and cookie revocation, the `is_active` check on its own, reactivation, owner protection, password reset, audit, and generic login failures.
- `tests/integration/test_member_isolation.py`: owner data invisible to members, member-to-member isolation, and member audit listing.
