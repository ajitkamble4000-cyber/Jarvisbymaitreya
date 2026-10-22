import { useState } from "react";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { ErrorState } from "../../components/ui/ErrorState";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { useToast } from "../../components/ui/Toast";
import { useDisconnectIntegration, type DisconnectResult } from "./integrationsQueries";
import { DISCONNECT_PHRASE, disconnectDescription, errorText, isDisconnectPhraseMatch, type IntegrationItem } from "./integrationsModel";

export interface DisconnectDialogProps {
  item: IntegrationItem | null;
  onClose: () => void;
  onDisconnected: (item: IntegrationItem, result: DisconnectResult) => void;
}

/**
 * Confirms a disconnect with the typed word. The button stays disabled until the word matches
 * exactly. The description is specific to the provider, so GitHub's grant is never implied to be gone.
 */
export function DisconnectDialog({ item, onClose, onDisconnected }: DisconnectDialogProps) {
  return (
    <Dialog open={item !== null} onClose={onClose} title={item ? `Disconnect ${item.name}?` : "Disconnect"} description={item ? "Type the word below to confirm." : undefined}>
      {item ? <DisconnectForm key={item.id} item={item} onClose={onClose} onDisconnected={onDisconnected} /> : null}
    </Dialog>
  );
}

function DisconnectForm({ item, onClose, onDisconnected }: { item: IntegrationItem; onClose: () => void; onDisconnected: DisconnectDialogProps["onDisconnected"] }) {
  const disconnect = useDisconnectIntegration();
  const { toast } = useToast();
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const matches = isDisconnectPhraseMatch(typed);

  const submit = (): void => {
    if (!matches || disconnect.isPending) return;
    setError(null);
    disconnect.mutate(item.id, {
      onSuccess: (result) => {
        onDisconnected(item, result);
        toast({ tone: "success", title: `${item.name} disconnected`, description: result.message });
      },
      onError: (cause) => setError(errorText(cause)),
    });
  };

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <p className="text-sm text-ink">{disconnectDescription(item)}</p>
      <Field label={`Type ${DISCONNECT_PHRASE} to confirm`}>
        {(control) => (
          <Input
            {...control}
            value={typed}
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            onChange={(event) => setTyped(event.target.value)}
          />
        )}
      </Field>
      {error ? <ErrorState title="Not disconnected" message={error} /> : null}
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="secondary" disabled={disconnect.isPending} onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" variant="danger" disabled={!matches || disconnect.isPending} loading={disconnect.isPending}>
          Disconnect {item.name}
        </Button>
      </div>
    </form>
  );
}
