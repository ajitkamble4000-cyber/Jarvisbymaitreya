import { useState } from "react";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input, Select } from "../../components/ui/Input";
import { StatusDot } from "../../components/ui/StatusDot";
import { formatDateTime, formatRelative, humanize } from "../../services/format";
import { useDeleteDevice, useRevokeDevice, useUpdateDevice, usePingDevice, useToolsQuery } from "../../services/queries";
import type { DeviceOut, PermissionMode, PingResult } from "../../types/api";
import { PERMISSION_LABEL, checkLabels, deviceStatus, permissionOptions, resourceLabels } from "./deviceModel";

function RenameDialog({ device, open, onClose }: { device: DeviceOut; open: boolean; onClose: () => void }) {
  const update = useUpdateDevice();
  const [name, setName] = useState(device.name);
  const [error, setError] = useState<string | null>(null);

  const submit = async (): Promise<void> => {
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Enter a name.");
      return;
    }
    setError(null);
    try {
      await update.mutateAsync({ id: device.id, patch: { name: trimmed } });
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The name could not be saved.");
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Rename device"
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={update.isPending} onClick={() => void submit()}>
            Save name
          </Button>
        </>
      }
    >
      <Field label="Device name" error={error}>
        {(control) => <Input {...control} value={name} maxLength={80} onChange={(event) => setName(event.target.value)} />}
      </Field>
    </Dialog>
  );
}

function RevokeDialog({ device, open, onClose }: { device: DeviceOut; open: boolean; onClose: () => void }) {
  const revoke = useRevokeDevice();
  const [error, setError] = useState<string | null>(null);
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`Revoke ${device.name}?`}
      description="Revoking closes the connection and invalidates the device secret. JARVIS will stop sending commands to this PC until it is paired again."
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Keep device
          </Button>
          <Button
            variant="danger"
            loading={revoke.isPending}
            onClick={async () => {
              setError(null);
              try {
                await revoke.mutateAsync({ id: device.id });
                onClose();
              } catch (cause) {
                setError(cause instanceof Error ? cause.message : "The device could not be revoked.");
              }
            }}
          >
            Revoke device
          </Button>
        </>
      }
    >
      {error ? (
        <p role="alert" className="text-sm font-medium text-danger">
          {error}
        </p>
      ) : null}
    </Dialog>
  );
}

export interface DeviceCardProps {
  device: DeviceOut;
}

/** One paired device: status, capabilities, resource labels, permissions, default, rename, ping and revoke. */
export function DeviceCard({ device }: DeviceCardProps) {
  const tools = useToolsQuery();
  const update = useUpdateDevice();
  const ping = usePingDevice();
  const remove = useDeleteDevice();
  const [pingResult, setPingResult] = useState<PingResult | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const status = deviceStatus(device);
  const revoked = device.pairing_status === "revoked";
  const labels = resourceLabels(device);
  const checks = checkLabels(device);

  const setPermission = (capability: string, mode: PermissionMode): void => {
    setActionError(null);
    update.mutate(
      { id: device.id, patch: { permissions: { [capability]: { mode } } } },
      { onError: (cause) => setActionError(cause.message) },
    );
  };

  const runPing = (): void => {
    setActionError(null);
    ping.mutate(device.id, {
      onSuccess: (result) => setPingResult(result),
      onError: (cause) => setActionError(cause.message),
    });
  };

  return (
    <Card className="flex min-w-0 flex-col gap-4">
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <h2 className="truncate text-lg font-semibold text-ink">{device.name}</h2>
            <p className="text-sm text-muted">
              {humanize(device.device_type)} · {humanize(device.os)} {device.os_version} · agent {device.agent_version}
            </p>
          </div>
          <StatusDot tone={status.tone} label={status.label} />
        </div>
        <div className="flex flex-wrap gap-2">
          {device.is_default ? <Badge tone="accent">Default device</Badge> : null}
          {revoked ? <Badge tone="danger">Revoked</Badge> : null}
        </div>
      </header>

      <dl className="grid grid-cols-2 gap-3 text-sm">
        <div>
          <dt className="text-muted">Last seen</dt>
          <dd className="text-ink">{formatRelative(device.last_seen_at)}</dd>
        </div>
        <div>
          <dt className="text-muted">Paired</dt>
          <dd className="text-ink">{formatDateTime(device.paired_at)}</dd>
        </div>
      </dl>

      <section aria-label={`Capabilities of ${device.name}`} className="flex flex-col gap-2">
        <h3 className="text-sm font-semibold text-ink">Capabilities</h3>
        {device.capabilities.length === 0 ? (
          <p className="text-sm text-muted">The agent reports no capabilities.</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {device.capabilities.map((capability) => {
              const current = device.permissions[capability]?.mode ?? "ask";
              const options = permissionOptions(capability, tools.data);
              return (
                <li key={capability} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                  <span className="text-ink">{humanize(capability)}</span>
                  <div className="flex items-center gap-2">
                    <Select
                      aria-label={`Permission for ${humanize(capability)}`}
                      className="h-8 w-44 text-xs"
                      value={current}
                      disabled={revoked || update.isPending}
                      onChange={(event) => setPermission(capability, event.target.value as PermissionMode)}
                    >
                      {options.map((mode) => (
                        <option key={mode} value={mode}>
                          {PERMISSION_LABEL[mode]}
                        </option>
                      ))}
                    </Select>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        {!tools.data ? <p className="text-xs text-muted">"Allow without asking" is offered only for low-risk capabilities once the tool list loads.</p> : null}
      </section>

      {labels.length > 0 || checks.length > 0 ? (
        <section aria-label={`Resources on ${device.name}`} className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold text-ink">Approved resources</h3>
          {labels.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {labels.map((label) => (
                <Badge key={label}>{label}</Badge>
              ))}
            </div>
          ) : null}
          {checks.length > 0 ? (
            <div className="flex flex-col gap-1.5">
              <h4 className="text-xs font-medium text-muted">Checks</h4>
              <ul aria-label="Approved checks" className="flex flex-wrap gap-1.5">
                {checks.map((label) => (
                  <li key={label}>
                    <Badge tone="accent">{label}</Badge>
                  </li>
                ))}
              </ul>
              <p className="text-xs text-muted">Every check needs your approval, even when this device is set to allow.</p>
            </div>
          ) : null}
        </section>
      ) : null}

      {pingResult ? (
        <p role="status" className="text-sm text-muted">
          {pingResult.online ? "Online" : "Offline"}
          {pingResult.latency_ms !== null ? `, ${pingResult.latency_ms} ms` : ""}. {pingResult.message}
        </p>
      ) : null}
      {actionError ? (
        <p role="alert" className="text-sm font-medium text-danger">
          {actionError}
        </p>
      ) : null}

      <div className="flex flex-wrap gap-2 border-t border-line pt-4">
        {!revoked && !device.is_default ? (
          <Button size="sm" variant="secondary" loading={update.isPending && update.variables?.patch.is_default === true} onClick={() => update.mutate({ id: device.id, patch: { is_default: true } })}>
            Make default
          </Button>
        ) : null}
        {!revoked ? (
          <Button size="sm" variant="secondary" loading={ping.isPending} onClick={runPing}>
            Ping
          </Button>
        ) : null}
        {!revoked ? (
          <Button size="sm" variant="secondary" onClick={() => setRenaming(true)}>
            Rename
          </Button>
        ) : null}
        {!revoked ? (
          <Button size="sm" variant="danger" onClick={() => setRevoking(true)}>
            Revoke
          </Button>
        ) : (
          <Button size="sm" variant="secondary" loading={remove.isPending} onClick={() => remove.mutate(device.id, { onError: (cause) => setActionError(cause.message) })}>
            Remove
          </Button>
        )}
      </div>

      {renaming ? <RenameDialog device={device} open={renaming} onClose={() => setRenaming(false)} /> : null}
      {revoking ? <RevokeDialog device={device} open={revoking} onClose={() => setRevoking(false)} /> : null}
    </Card>
  );
}
