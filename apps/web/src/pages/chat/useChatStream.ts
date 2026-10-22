import { useCallback, useEffect, useReducer, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, fallbackMessage, isAbortError } from "../../services/api";
import { streamConversationMessage, streamRegeneration, type ChatStreamEvent } from "../../services/chatStream";
import { qk, useCreateConversation, useEditUserMessage } from "../../services/queries";
import { titleFromMessage } from "../../services/format";
import { chatStreamReducer, INITIAL_CHAT_STREAM_STATE } from "./chatStreamState";

export interface UseChatStreamOptions {
  /** Called once a new conversation exists, before the reply finishes streaming. */
  onConversationCreated?: (conversationId: string) => void;
}

function toStreamError(error: unknown): { code: string; message: string } {
  if (error instanceof ApiError) return { code: error.code, message: error.message };
  return { code: "stream_error", message: fallbackMessage(0) };
}

/**
 * Drives one send, regeneration, or edit at a time. Stop aborts the fetch; the server keeps the
 * partial text and marks the message cancelled. Leaving the page also stops the stream.
 */
export function useChatStream(options: UseChatStreamOptions = {}) {
  const queryClient = useQueryClient();
  const createConversation = useCreateConversation();
  const editMessage = useEditUserMessage();
  const [state, dispatch] = useReducer(chatStreamReducer, INITIAL_CHAT_STREAM_STATE);
  const controllerRef = useRef<AbortController | null>(null);
  const callbacksRef = useRef(options);

  useEffect(() => {
    callbacksRef.current = options;
  }, [options]);

  useEffect(() => {
    return () => controllerRef.current?.abort();
  }, []);

  const run = useCallback(
    async (conversationId: string, source: (signal: AbortSignal) => AsyncGenerator<ChatStreamEvent>, pendingUser: string | null) => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;
      dispatch({ type: "start", conversationId, pendingUser });
      try {
        for await (const event of source(controller.signal)) {
          dispatch({ type: "event", event });
        }
        if (controller.signal.aborted) dispatch({ type: "stopped" });
      } catch (error) {
        if (isAbortError(error) || controller.signal.aborted) dispatch({ type: "stopped" });
        else dispatch({ type: "failed", error: toStreamError(error) });
      } finally {
        if (controllerRef.current === controller) controllerRef.current = null;
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: qk.conversation(conversationId) }),
          queryClient.invalidateQueries({ queryKey: ["conversations", "list"] }),
        ]);
        dispatch({ type: "finished" });
      }
    },
    [queryClient],
  );

  /** Sends a message. Creates the conversation first when none is given. */
  const send = useCallback(
    async (content: string, conversationId?: string): Promise<void> => {
      const text = content.trim();
      if (!text) return;
      let target = conversationId;
      if (!target) {
        const created = await createConversation.mutateAsync(titleFromMessage(text));
        target = created.id;
        callbacksRef.current.onConversationCreated?.(created.id);
      }
      const resolved = target;
      await run(resolved, (signal) => streamConversationMessage(resolved, { content: text }, signal), text);
    },
    [createConversation, run],
  );

  /** Regenerates the reply to an assistant message, or to the user message it answers. */
  const regenerate = useCallback(
    async (conversationId: string, messageId: string): Promise<void> => {
      await run(conversationId, (signal) => streamRegeneration(conversationId, messageId, signal), null);
    },
    [run],
  );

  /** Saves an edited user message, then regenerates the reply to it. The server removes later messages. */
  const editAndRegenerate = useCallback(
    async (conversationId: string, messageId: string, content: string): Promise<void> => {
      const text = content.trim();
      if (!text) return;
      await editMessage.mutateAsync({ conversationId, messageId, content: text });
      await run(conversationId, (signal) => streamRegeneration(conversationId, messageId, signal), null);
    },
    [editMessage, run],
  );

  const stop = useCallback((): void => {
    controllerRef.current?.abort();
  }, []);

  return {
    state,
    isStreaming: state.status === "streaming",
    isCreating: createConversation.isPending,
    send,
    regenerate,
    editAndRegenerate,
    stop,
    editError: editMessage.isError ? editMessage.error.message : null,
  };
}
