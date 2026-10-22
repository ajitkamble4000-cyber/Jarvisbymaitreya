import { useState } from "react";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardTitle } from "../../components/ui/Card";
import { Dialog } from "../../components/ui/Dialog";
import { ErrorState } from "../../components/ui/ErrorState";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { ApiError } from "../../services/api";
import { EMERGENCY_STOP_PHRASE, useEmergencyResume, useEmergencyStop } from "./automationQueries";

export interface EmergencyStopControlProps {
  stopped: boolean;
}

function failureText(error: unknown): string {
  return error instanceof ApiError ? error.message : "The request did not complete. Try again.";
}

/**
 * Emergency stop. Stopping needs the exact word STOP typed into the dialog, and the same word is
 * sent to the API. Resuming is a single button because it only re-allows automation.
 */
export function EmergencyStopControl({ stopped }: EmergencyStopControlProps) {
  const stop = useEmergencyStop();
  const resume = useEmergencyResume();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const confirmed = typed === EMERGENCY_STOP_PHRASE;

  const close = (): void => {
    setOpen(false);
    setTyped("");
    stop.reset();
  };

  const confirmStop = (): void => {
    if (!confirmed) return;
    stop.mutate(undefined, {
      onSuccess: () => {
        setOpen(false);
        setTyped("");
      },
    });
  };

  return (
    <Card className="flex flex-col gap-4" aria-labelledby="emergency-stop-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <CardTitle id="emergency-stop-title">Emergency stop</CardTitle>
        <Badge tone={stopped ? "danger" : "success"}>{stopped ? "Stopped" : "Automation running"}</Badge>
      </div>

      {stopped ? (
        <>
          <p className="text-sm text-ink">
            Automation is stopped. New tasks and scheduled runs are blocked until you resume.
          </p>
          <div>
            <Button variant="secondary" loading={resume.isPending} onClick={() => resume.mutate()}>
              Resume automation
            </Button>
          </div>
        </>
      ) : (
        <>
          <p className="text-sm text-muted">
            Stops every queued and waiting task, asks running tasks to cancel, and blocks new automation until you resume.
          </p>
          <div>
            <Button variant="danger" onClick={() => setOpen(true)}>
              Emergency stop
            </Button>
          </div>
        </>
      )}

      {resume.isError ? <ErrorState title="Not resumed" message={failureText(resume.error)} /> : null}

      <Dialog
        open={open}
        onClose={close}
        title="Stop all automation?"
        description="This cancels queued, waiting and running tasks and blocks new automation until you resume."
        footer={
          <>
            <Button variant="ghost" onClick={close}>
              Keep running
            </Button>
            <Button variant="danger" disabled={!confirmed} loading={stop.isPending} onClick={confirmStop}>
              Stop automation
            </Button>
          </>
        }
      >
        <form
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            confirmStop();
          }}
          className="flex flex-col gap-4"
        >
          <Field label={`Type ${EMERGENCY_STOP_PHRASE} to confirm`} hint="Capitals only.">
            {(control) => <Input {...control} autoComplete="off" value={typed} onChange={(event) => setTyped(event.target.value)} />}
          </Field>
          {stop.isError ? <ErrorState message={failureText(stop.error)} /> : null}
        </form>
      </Dialog>
    </Card>
  );
}
