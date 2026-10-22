import { useState, type FormEvent } from "react";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input, Select, Textarea } from "../../components/ui/Input";
import { useToast } from "../../components/ui/Toast";
import { ApiError } from "../../services/api";
import { useCreateTask, useDevicesQuery, useToolsQuery } from "../../services/queries";
import type { TaskOut } from "../../types/api";

export interface ParsedArguments {
  value: Record<string, unknown> | null;
  error: string | null;
}

/** Parses the arguments box. It must be a JSON object. Pure. */
export function parseArguments(text: string): ParsedArguments {
  const trimmed = text.trim();
  if (!trimmed) return { value: {}, error: null };
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return { value: null, error: "Arguments must be a JSON object, for example {\"app_id\": \"chrome\"}." };
    }
    return { value: parsed as Record<string, unknown>, error: null };
  } catch {
    return { value: null, error: "Arguments are not valid JSON." };
  }
}

export interface CreateTaskDialogProps {
  open: boolean;
  onClose: () => void;
  onCreated: (task: TaskOut) => void;
}

/**
 * Creates a task through POST /tasks. The server applies policy: device tools may wait for
 * approval, and the dialog says so rather than promising that the action will run.
 */
export function CreateTaskDialog({ open, onClose, onCreated }: CreateTaskDialogProps) {
  const tools = useToolsQuery();
  const devices = useDevicesQuery();
  const create = useCreateTask();
  const { toast } = useToast();
  const [toolName, setToolName] = useState("");
  const [deviceId, setDeviceId] = useState("");
  const [title, setTitle] = useState("");
  const [argumentsText, setArgumentsText] = useState("{}");
  const [errors, setErrors] = useState<{ tool?: string; device?: string; args?: string; form?: string }>({});

  const selectedTool = tools.data?.find((tool) => tool.name === toolName);

  const submit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    const parsed = parseArguments(argumentsText);
    const next: typeof errors = {};
    if (!toolName) next.tool = "Choose a tool.";
    if (selectedTool?.requires_device && !deviceId) next.device = "Choose a device for this tool.";
    if (parsed.error || parsed.value === null) next.args = parsed.error ?? "Arguments are required.";
    setErrors(next);
    if (Object.keys(next).length > 0 || parsed.value === null) return;

    try {
      const task = await create.mutateAsync({
        tool_name: toolName,
        arguments: parsed.value,
        ...(deviceId ? { device_id: deviceId } : {}),
        ...(title.trim() ? { title: title.trim() } : {}),
      });
      toast({ tone: "success", title: "Task created", description: `Status: ${task.status.replace(/_/g, " ")}.` });
      setToolName("");
      setDeviceId("");
      setTitle("");
      setArgumentsText("{}");
      onCreated(task);
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "The task could not be created.";
      setErrors({ form: message });
    }
  };

  return (
    <Dialog open={open} onClose={onClose} title="New task" description="Choose a tool and its arguments. Device actions follow the approval rules set for that device.">
      <form className="flex flex-col gap-4" onSubmit={(event) => void submit(event)} noValidate>
        <Field label="Tool" error={errors.tool}>
          {(control) => (
            <Select {...control} value={toolName} onChange={(event) => setToolName(event.target.value)} required>
              <option value="">Choose a tool</option>
              {(tools.data ?? []).map((tool) => (
                <option key={tool.name} value={tool.name}>
                  {tool.title} ({tool.risk_level} risk)
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label={selectedTool?.requires_device ? "Device" : "Device (optional)"} error={errors.device}>
          {(control) => (
            <Select {...control} value={deviceId} onChange={(event) => setDeviceId(event.target.value)}>
              <option value="">No specific device</option>
              {(devices.data ?? []).map((device) => (
                <option key={device.id} value={device.id}>
                  {device.name} ({device.connection_status})
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Title (optional)">
          {(control) => <Input {...control} value={title} onChange={(event) => setTitle(event.target.value)} maxLength={200} />}
        </Field>
        <Field label="Arguments (JSON object)" error={errors.args} hint={'Use the keys the tool expects, for example {"app_id": "chrome"}.'}>
          {(control) => <Textarea {...control} value={argumentsText} onChange={(event) => setArgumentsText(event.target.value)} className="font-mono text-xs" rows={4} />}
        </Field>
        {errors.form ? (
          <p role="alert" className="text-sm font-medium text-danger">
            {errors.form}
          </p>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" loading={create.isPending}>
            Create task
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
