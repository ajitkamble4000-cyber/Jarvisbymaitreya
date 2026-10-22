import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { connectEventStream, type ConnectionStatus } from "../services/api";
import { invalidationsForEvent } from "../services/queries";
import type { LiveEvent } from "../types/api";

export interface UseEventStreamOptions {
  /** Called for every event after the matching queries are invalidated. */
  onEvent?: (event: LiveEvent) => void;
}

/**
 * Keeps the events stream open for the signed-in shell. Each event invalidates the queries it
 * affects, so pages refetch real REST data. Returns the connection status for the shell indicator.
 */
export function useEventStream(options: UseEventStreamOptions = {}): ConnectionStatus {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const handlerRef = useRef(options.onEvent);

  useEffect(() => {
    handlerRef.current = options.onEvent;
  }, [options.onEvent]);

  useEffect(() => {
    const close = connectEventStream(
      (event) => {
        for (const queryKey of invalidationsForEvent(event)) {
          void queryClient.invalidateQueries({ queryKey });
        }
        handlerRef.current?.(event);
      },
      { onStatusChange: setStatus },
    );
    return close;
  }, [queryClient]);

  return status;
}
